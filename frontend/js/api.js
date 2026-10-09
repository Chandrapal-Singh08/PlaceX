/* PlaceX API client. Every network call in the app goes through here.
   Only endpoints that exist in placex/api.py are used. */
(function () {
  const C = window.PLACEX_CONFIG;
  const BASE = C.API_BASE_URL;

  class ApiError extends Error {
    constructor(message, status, kind) { super(message); this.status = status; this.kind = kind || "http"; }
  }

  function friendly(status, detail) {
    const d = typeof detail === "string" ? detail : "";
    switch (status) {
      case 400: return d || "That request wasn't valid. Check your input and try again.";
      case 401: return d || "You're not signed in. Sign in again to continue.";
      case 404: return d || "We couldn't find that item.";
      case 409: return d || "That action isn't possible right now (the job may already be running or finished).";
      case 413: return d || "That file is too large.";
      case 415: return d || "That file type isn't supported. Use PDF, DOCX, TXT or MD.";
      case 422: return d || "Some fields are missing or invalid. Check the form and try again.";
      case 429: return "Too many requests. Wait a moment and try again.";
      case 503: return "The server is busy. Retrying in a moment usually works.";
      default:  return status >= 500 ? "Something went wrong on the server. Try again shortly." : (d || "Request failed.");
    }
  }

  async function request(path, opts = {}) {
    const headers = new Headers(opts.headers || {});
    const token = window.PX?.session.token();
    if (token) headers.set("Authorization", "Bearer " + token);
    opts = { ...opts, headers };
    let res;
    try {
      res = await fetch(BASE + path, opts);
    } catch (e) {
      throw new ApiError("Can't reach the PlaceX backend at " + BASE + ". Make sure it's running and CORS allows this origin.", 0, "network");
    }
    let body = null;
    const text = await res.text();
    if (text) { try { body = JSON.parse(text); } catch { body = text; } }
    if (!res.ok) {
      let detail = body && body.detail;
      if (Array.isArray(detail)) detail = null;        // pydantic validation list -> generic message
      throw new ApiError(friendly(res.status, detail), res.status);
    }
    return body;
  }

  const json = (method, data) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

  const API = {
    ApiError,
    login: (email, password) => request("/auth/login", json("POST", { email, password })),
    logout: () => request("/auth/logout", { method: "POST" }),
    jobDescriptions: () => request("/job-descriptions"),
    createJd: ({ text, file }) => {
      const form = new FormData();
      if (file) form.append("file", file);
      else form.append("text", text || "");
      return request("/job-descriptions", { method: "POST", body: form });
    },
    createUser: (email, full_name, roll_no, password) => request("/users", json("POST", { email, full_name, roll_no: roll_no || null, password })),
    uploadResume: (userId, file) => { const f = new FormData(); f.append("file", file); return request(`/users/${userId}/resumes`, { method: "POST", body: f }); },
    async downloadResume(resumeId, format) {
      const headers = new Headers();
      const token = window.PX?.session.token();
      if (token) headers.set("Authorization", "Bearer " + token);
      let res;
      try {
        res = await fetch(API.resumeDownloadUrl(resumeId, format), { headers });
      } catch {
        throw new ApiError("Can't reach the PlaceX backend at " + BASE + ". Make sure it's running and CORS allows this origin.", 0, "network");
      }
      if (!res.ok) {
        const text = await res.text();
        let body = null;
        if (text) { try { body = JSON.parse(text); } catch { body = text; } }
        let detail = body && body.detail;
        if (Array.isArray(detail)) detail = null;
        throw new ApiError(friendly(res.status, detail), res.status);
      }
      return res.blob();
    },
    submitJob: (user_id, job_type, payload, priority = 5) => request("/jobs", json("POST", { user_id, job_type, payload, priority })),
    getJob: (id) => request(`/jobs/${id}`),
    cancelJob: (id, userId) => request(`/jobs/${id}` + (userId ? `?user_id=${userId}` : ""), { method: "DELETE" }),
    dashboard: (userId) => request(`/users/${userId}/dashboard`),
    companies: () => request("/companies"),
    system: () => request("/system"),

    /* Poll GET /jobs/{id} until DONE / FAILED / CANCELLED. onTick(job, elapsedMs) drives the UI.
       The backend re-queues failed attempts as READY (retry), so READY after an error is not final. */
    async waitForJob(jobId, onTick) {
      const start = Date.now();
      for (;;) {
        const job = await API.getJob(jobId);
        if (onTick) onTick(job, Date.now() - start);
        if (job.status === "DONE") return job;
        if (job.status === "FAILED") throw new ApiError(job.error || "The job failed.", 0, "job");
        if (job.status === "CANCELLED") throw new ApiError("The job was cancelled.", 0, "job");
        if (Date.now() - start > C.POLL_TIMEOUT_MS) throw new ApiError("This is taking longer than expected. Open the result again from your dashboard in a moment.", 0, "timeout");
        await new Promise(r => setTimeout(r, C.POLL_INTERVAL_MS));
      }
    },

    /* Submit, remember in local history, then wait. Returns the finished job row. */
    async runJob(user_id, job_type, payload, onTick, priority = 5) {
      const j = await API.submitJob(user_id, job_type, payload, priority);
      PX.history.add(j.id, job_type);
      if (onTick) onTick(j, 0);
      return API.waitForJob(j.id, onTick);
    },

    resumeDownloadUrl(resumeId, format) {
      return C.RESUME_DOWNLOAD_PATH
        ? BASE + C.RESUME_DOWNLOAD_PATH.replace("{id}", resumeId).replace("{format}", format)
        : null;
    }
  };
  window.API = API;
})();
