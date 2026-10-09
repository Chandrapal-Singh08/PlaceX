(async function () {
  const $ = id => document.getElementById(id);
  const uid = PX.session.userId(); if (!uid) return;
  const qs = new URLSearchParams(location.search);
  const jd = JDInput.mount($("jd"), { title: "Provide the job description" });
  $("reportId").value = qs.get("report") || PX.history.lastReport() || "";
  $("resumeHint").textContent = PX.history.lastResume()
    ? `Leave empty to use your last resume${PX.history.lastResumeName() ? " (" + PX.history.lastResumeName() + ")" : ""}.`
    : "Upload a resume to compare with the job description.";

  /* known reports = finished analyses submitted from this browser */
  const hist = PX.history.all().filter(h => h.type === "RESUME_ANALYSIS").slice(0, 6);
  const jobs = (await Promise.all(hist.map(h => API.getJob(h.id).catch(() => null)))).filter(j => j && j.status === "DONE");
  const ids = [...new Set(jobs.map(j => j.result && j.result.report_id).filter(Boolean))];
  $("known").innerHTML = ids.map(i => `<button type="button" class="chip" data-r="${i}" style="cursor:pointer;color:inherit">Report #${i}</button>`).join("");
  $("known").addEventListener("click", e => { const b = e.target.closest("[data-r]"); if (b) $("reportId").value = b.dataset.r; });
  $("pickHint").innerHTML = ids.length ? "Pick a report from your recent analyses or type an ID." : `No analyses found in this browser. <a href="analyze.html" style="color:var(--cyan);font-weight:700">Analyze a resume</a> first.`;

  function render(job) {
    const r = job.result || {}, items = r.items || [];
    $("result").hidden = false;
    if (!items.length) { $("result").innerHTML = `<div class="glass empty" style="margin-top:1rem">${PX.esc(r.note || "No data available yet.")}</div>`; return; }
    const req = items.filter(i => i.required).length, max = Math.max(...items.map(i => i.est_hours));
    const resource = value => value && /^https?:\/\//i.test(value.url || "")
      ? `<small><a href="${PX.esc(value.url)}" target="_blank" rel="noopener noreferrer">${PX.esc(value.title)}${value.kind ? " · " + PX.esc(value.kind) : ""}</a></small>` : "";
    $("result").innerHTML = `<section class="grid g4" style="margin-top:1rem">
      <div class="glass stat"><span>Total hours</span><b>${r.total_hours}</b></div><div class="glass stat"><span>Skills to learn</span><b>${items.length}</b></div>
      <div class="glass stat"><span>Required skills</span><b>${req}</b></div><div class="glass stat"><span>Optional skills</span><b>${items.length - req}</b></div></section>
      <div class="road">${items.map(i => `<div class="glass hover rm"><span class="no">${String(i.position).padStart(2, "0")}</span>
        <div><b>${PX.esc(i.skill)}</b><span class="req ${i.required ? "y" : "n"}">${i.required ? "Required" : "Optional"}</span>${resource(i.resource)}</div>
        <div class="hrs">${i.est_hours}<small> hours</small></div><div class="bar2"><i style="width:${Math.max(6, 100 * i.est_hours / max)}%"></i></div></div>`).join("")}</div>
      <p class="muted" style="margin-top:1rem;font-size:.85rem">Roadmap #${r.roadmap_id}. <a href="journey-guide.html" style="color:var(--cyan);font-weight:700">See the interview rounds</a></p>`;
  }

  async function runPlan(reportId) {
    $("picker").hidden = true; $("result").hidden = true;
    const p = PX.progress($("progress"), "Preparing your prep plan");
    try {
      if (!reportId) {
        let resumeId = PX.history.lastResume();
        const resumeFile = $("resumeFile").files[0];
        if (resumeFile) {
          p.label("Uploading your resume");
          const resume = await API.uploadResume(uid, resumeFile);
          resumeId = resume.id;
          PX.history.setLastResume(resume.id, resumeFile.name);
        }
        if (!resumeId) throw new Error("Upload your resume first.");
        if (!jd.isFilled()) throw new Error("Choose or provide the job description first.");
        p.label("Reading the job description");
        const jdId = (await jd.resolve()).id;
        p.label("Comparing your resume with the job");
        const analysis = await API.runJob(uid, "RESUME_ANALYSIS", {
          resume_id: Number(resumeId), mode: "JD_COMPARISON", jd_id: jdId
        }, p.tick, 5);
        reportId = analysis.result.report_id;
        PX.history.setLastReport(reportId);
      }
      p.label("Building your roadmap");
      const job = await API.runJob(uid, "PREP_PLAN", { report_id: Number(reportId) }, p.tick, 5);
      p.done(); $("picker").hidden = qs.has("report"); render(job); history.replaceState(null, "", "?job=" + job.id);
      PX.toast("Prep plan ready.", "success");
    } catch (e) { p.done(); $("picker").hidden = false; PX.toast(e.kind === "job" ? "Job failed: " + e.message : e.message, "error", 7000); }
  }

  $("go").addEventListener("click", () => {
    const reportId = Number($("reportId").value);
    if (reportId > 0) return runPlan(reportId);
    if (!jd.isFilled()) return PX.toast("Choose or provide the job description first.", "error");
    if (!$("resumeFile").files[0] && !PX.history.lastResume()) return PX.toast("Upload your resume first.", "error");
    runPlan(null);
  });

  const q = qs.get("job");
  if (q) { try { const j = await API.getJob(q); if (j.job_type === "PREP_PLAN" && j.status === "DONE") render(j); } catch (e) { PX.toast(e.message, "error"); } }
})();
