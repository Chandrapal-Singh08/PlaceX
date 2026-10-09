/* PlaceX shared runtime: session, job history, toasts, sidebar shell, helpers. Loaded on every page. */
(function () {
  const K = { uid: "placex_user_id", name: "placex_user_name", email: "placex_user_email", token: "placex_access_token", hist: "placex_job_history", report: "placex_last_report_id", resume: "placex_last_resume_id", rname: "placex_last_resume_name" };
  const get = k => { try { return localStorage.getItem(k); } catch { return null; } };
  const set = (k, v) => { try { localStorage.setItem(k, v); } catch {} };
  const del = k => { try { localStorage.removeItem(k); } catch {} };

  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---- Bearer-token session ---- */
  const session = {
    userId() { const v = get(K.uid); return v ? Number(v) : null; },
    name() { return get(K.name) || ""; },
    email() { return get(K.email) || ""; },
    token() { return get(K.token) || ""; },
    start(id, name, email, token) { set(K.uid, String(id)); if (name) set(K.name, name); if (email) set(K.email, email); if (token) set(K.token, token); },
    end() { [K.uid, K.name, K.email, K.token, K.hist, K.report, K.resume, K.rname].forEach(del); },
    require() { if (!session.userId() || !session.token()) { session.end(); location.replace("login.html"); return false; } return true; }
  };

  /* ---- job history: the backend has no "list jobs" endpoint, so we remember job ids submitted from this browser ---- */
  const history = {
    all() { try { return JSON.parse(get(K.hist) || "[]"); } catch { return []; } },
    add(id, type) { const h = history.all().filter(x => x.id !== id); h.unshift({ id, type, at: Date.now() }); set(K.hist, JSON.stringify(h.slice(0, 30))); },
    lastReport() { return get(K.report); }, setLastReport(id) { set(K.report, String(id)); },
    lastResume() { return get(K.resume); },
    lastResumeName() { return get(K.rname) || ""; },
    setLastResume(id, name) { set(K.resume, String(id)); if (name) set(K.rname, name); }
  };

  /* ---- toasts ---- */
  function toast(msg, kind = "info", ms = 4500) {
    let box = document.getElementById("toasts");
    if (!box) { box = document.createElement("div"); box.id = "toasts"; box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    const t = document.createElement("div");
    t.className = "toast " + kind; t.textContent = msg; box.appendChild(t);
    setTimeout(() => { t.classList.add("out"); setTimeout(() => t.remove(), 300); }, ms);
  }

  const STATUS_CLASS = { DONE: "ok", RUNNING: "run", READY: "wait", FAILED: "bad", CANCELLED: "off" };
  const badge = s => `<span class="badge ${STATUS_CLASS[s] || "off"}">${esc(s)}</span>`;
  const TYPE_LABEL = { RESUME_ANALYSIS: "Resume analysis", RESUME_BUILD: "Resume build", JOURNEY_GUIDE: "Journey guide", PREP_PLAN: "Prep plan" };
  const TYPE_PAGE = { RESUME_ANALYSIS: "analysis-result.html", RESUME_BUILD: "build-resume.html", JOURNEY_GUIDE: "journey-guide.html", PREP_PLAN: "prep-plan.html" };

  const timeAgo = iso => {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago"; return Math.floor(s / 86400) + " d ago";
  };
  const fmtBytes = n => n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(2) + " MB";

  /* ---- sidebar shell for authenticated pages ---- */
  const NAV = [
    ["dashboard", "dashboard.html", "Dashboard", '<path d="M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z"/>'],
    ["analyze", "analyze.html", "Analyze Resume", '<path d="M6 2h9l5 5v15H6zM14 2v6h6M9 13h8M9 17h6"/>'],
    ["build", "build-resume.html", "Build Resume", '<path d="M12 5v14M5 12h14"/><rect x="3" y="3" width="18" height="18" rx="4"/>'],
    ["prep", "prep-plan.html", "Prep Plan", '<path d="M4 6h16M4 12h10M4 18h6M17 14l2 2 3-4"/>'],
    ["journey", "journey-guide.html", "Journey Guide", '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="M6 8.5v3a4 4 0 0 0 4 4h5.5"/>'],
    ["companies", "companies.html", "Companies", '<path d="M4 21V7l8-4 8 4v14M9 21v-6h6v6M8 10h2M14 10h2"/>']
  ];
  const svg = p => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;

  function mountShell() {
    const page = document.body.dataset.page;
    const side = document.getElementById("sidebar");
    if (!side) return;
    const links = NAV.map(([k, href, label, ico]) => `<a href="${href}" class="${k === page ? "active" : ""}" ${k === page ? 'aria-current="page"' : ""}>${svg(ico)}<span>${label}</span></a>`).join("");
    side.innerHTML = `
      <a class="brand" href="dashboard.html"><span class="logo-mark"></span>PlaceX</a>
      <nav>${links}</nav>
      <div class="side-foot">
        <a href="settings.html" class="${page === "settings" ? "active" : ""}">${svg('<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>')}<span>Settings</span></a>
        <a href="#" id="logout">${svg('<path d="M9 4H5v16h4M16 8l4 4-4 4M20 12H9"/>')}<span>Logout</span></a>
      </div>`;
    document.getElementById("logout").addEventListener("click", async e => {
      e.preventDefault();
      await API.logout().catch(() => {});
      session.end();
      location.href = "login.html";
    });

    const bar = document.createElement("div");
    bar.className = "mobile-bar";
    bar.innerHTML = `<a class="brand" href="dashboard.html"><span class="logo-mark"></span>PlaceX</a><button id="menuBtn" aria-label="Open menu" aria-expanded="false">${svg('<path d="M4 7h16M4 12h16M4 17h16"/>')}</button>`;
    document.body.prepend(bar);
    const scrim = document.createElement("div"); scrim.className = "scrim"; document.body.appendChild(scrim);
    const toggle = open => { document.body.classList.toggle("menu-open", open); document.getElementById("menuBtn").setAttribute("aria-expanded", open); };
    document.getElementById("menuBtn").addEventListener("click", () => toggle(!document.body.classList.contains("menu-open")));
    scrim.addEventListener("click", () => toggle(false));
    document.addEventListener("keydown", e => { if (e.key === "Escape") toggle(false); });
  }

  /* ---- shared job-progress panel ---- */
  function progress(el, title) {
    el.hidden = false;
    el.innerHTML = `<div class="glass loader-card" role="status"><div class="orbit"><i></i><i></i><i></i></div><h3>${esc(title)}</h3><p class="muted" data-msg>Queued…</p><div class="bar"><span></span></div></div>`;
    const msg = el.querySelector("[data-msg]");
    return {
      label(title) { el.querySelector("h3").textContent = title; },
      tick(job, ms) {
        const s = Math.round(ms / 1000);
        msg.textContent = job.status === "RUNNING" ? `Running (job #${job.id}) · ${s}s` : job.status === "READY" ? (job.attempts > 0 ? `Retrying (job #${job.id}, attempt ${job.attempts + 1})…` : `Waiting in queue (job #${job.id})…`) : `${job.status} (job #${job.id})`;
      },
      done() { el.hidden = true; el.innerHTML = ""; }
    };
  }

  /* Companies are fetched once per page; used by job pickers. */
  async function loadCompanies() { try { return await API.companies(); } catch (e) { toast(e.message, "error"); return []; } }

  /* Job-description picker. The backend has no endpoint to list JDs, so the id is entered
     directly (default = seeded JD 1) and the real companies from GET /companies are shown for context. */
  async function jdPicker(host, defaultId) {
    host.innerHTML = `<label class="field"><span>Job description ID</span>
      <input type="number" min="1" id="jdId" value="${defaultId ?? PLACEX_CONFIG.DEFAULT_JD_ID}" inputmode="numeric">
      <small class="muted" id="jdHint">Loading companies…</small></label>`;
    const cs = await loadCompanies();
    host.querySelector("#jdHint").textContent = cs.length
      ? "Companies in the database: " + cs.map(c => `${c.name}`).join(", ") + ". The seed data links JD 1 to the first company, JD 2 to the second and so on."
      : "No companies available yet.";
    return () => Number(host.querySelector("#jdId").value);
  }

  window.PX = { esc, session, history, toast, badge, timeAgo, fmtBytes, progress, loadCompanies, jdPicker, TYPE_LABEL, TYPE_PAGE, K };
  document.addEventListener("DOMContentLoaded", () => {
    if (document.body.dataset.auth === "required" && !session.require()) return;
    mountShell();
  });
})();
