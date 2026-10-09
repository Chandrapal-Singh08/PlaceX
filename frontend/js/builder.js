(async function () {
  const $ = id => document.getElementById(id);
  const uid = PX.session.userId(); if (!uid) return;
  const skills = [];
  const jd = JDInput.mount($("jd"), { title: "Job description" });
  $("pName").value = PX.session.name(); $("pEmail").value = PX.session.email();

  /* skill tags */
  function drawTags() {
    $("tagbox").querySelectorAll(".tag").forEach(t => t.remove());
    skills.forEach((s, i) => { const t = document.createElement("span"); t.className = "tag"; t.innerHTML = `${PX.esc(s)}<button type="button" aria-label="Remove ${PX.esc(s)}" data-i="${i}">×</button>`; $("tagbox").insertBefore(t, $("tagIn")); });
  }
  function addTag(v) { v = v.trim(); if (v && !skills.some(s => s.toLowerCase() === v.toLowerCase())) { skills.push(v); drawTags(); } $("tagIn").value = ""; }
  $("tagIn").addEventListener("keydown", e => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTag($("tagIn").value); } else if (e.key === "Backspace" && !$("tagIn").value) { skills.pop(); drawTags(); } });
  $("tagIn").addEventListener("blur", () => addTag($("tagIn").value));
  $("tagbox").addEventListener("click", e => { const b = e.target.closest("button[data-i]"); if (b) { skills.splice(+b.dataset.i, 1); drawTags(); } });

  /* projects */
  function addProject() {
    const d = document.createElement("div"); d.className = "proj";
    d.innerHTML = `<div class="proj-head"><b>Project</b><button type="button" class="btn ghost sm" data-rm>Remove</button></div>
      <label class="field"><span>Name</span><input data-pn></label><label class="field" style="margin:0"><span>Description</span><textarea data-pd placeholder="What you built and the tools you used"></textarea></label>`;
    d.querySelector("[data-rm]").addEventListener("click", () => d.remove()); $("projects").appendChild(d);
  }
  $("addProj").addEventListener("click", addProject); addProject();

  const lines = v => v.split("\n").map(s => s.trim()).filter(Boolean);

  $("form").addEventListener("submit", async e => {
    e.preventDefault(); addTag($("tagIn").value);
    if (!jd.isFilled()) return PX.toast("Choose or provide a job description first.", "error");
    const projects = [...document.querySelectorAll(".proj")].map(p => ({ name: p.querySelector("[data-pn]").value.trim(), description: p.querySelector("[data-pd]").value.trim() })).filter(p => p.name || p.description);
    const experience = lines($("pExp").value);
    if (!skills.length && !projects.length && !experience.length) return PX.toast("Add at least one skill, project or experience item.", "error");
    const profile = { skills: [...skills], projects, experience };
    if ($("pName").value.trim()) profile.name = $("pName").value.trim();
    if ($("pEmail").value.trim()) profile.email = $("pEmail").value.trim();
    if ($("pEdu").value.trim()) profile.education = lines($("pEdu").value);
    if ($("pCert").value.trim()) profile.certifications = lines($("pCert").value);
    if ($("pAchievements").value.trim()) profile.achievements = lines($("pAchievements").value);
    if ($("pCoursework").value.trim()) profile.coursework = lines($("pCoursework").value);
    if ($("pFocus").value.trim()) profile.focus_areas = $("pFocus").value.split(",").map(s => s.trim()).filter(Boolean);

    $("work").hidden = true;
    const p = PX.progress($("progress"), "Building your resume");
    try {
      const jdId = (await jd.resolve()).id;
      p.label("Building your resume");
      const job = await API.runJob(uid, "RESUME_BUILD", { jd_id: jdId, profile }, p.tick, 5);
      p.done(); $("work").hidden = false; show(job);
      history.replaceState(null, "", "?job=" + job.id);
      PX.toast("Resume generated.", "success");
    } catch (err) { p.done(); $("work").hidden = false; PX.toast(err.kind === "job" ? "Job failed: " + err.message : err.message, "error", 7000); }
  });

  function show(job) {
    const r = job.result || {}, m = r.matched_skills || [], x = r.missing_skills || [];
    const chips = (a, c) => a.length ? `<div class="chips">${a.map(s => `<span class="chip ${c}">${PX.esc(s)}</span>`).join("")}</div>` : `<p class="muted">None</p>`;
    $("out").innerHTML = `<h2>Generated resume</h2>
      <div class="resume-meta"><strong>Resume #${PX.esc(r.resume_id)}</strong><span class="badge ok">Generated</span></div>
      <div class="sec"><b>Matched skills (${m.length})</b>${chips(m, "ok")}</div>
      <div class="sec"><b>Missing skills (${x.length})</b>${chips(x, "bad")}</div>
      <div class="resume-downloads">
        <button class="btn" type="button" data-format="pdf">Download PDF</button>
        <button class="btn ghost" type="button" data-format="md">Download Markdown</button>
      </div>
      <a class="btn ghost sm" href="prep-plan.html">Plan how to close the gaps</a>`;
    $("out").querySelectorAll("[data-format]").forEach(button => {
      button.addEventListener("click", async () => {
        const format = button.dataset.format;
        button.disabled = true;
        try {
          const blob = await API.downloadResume(r.resume_id, format);
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = `resume-${r.resume_id}.${format}`;
          document.body.appendChild(link);
          link.click();
          link.remove();
          window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) {
          PX.toast(error.message, "error");
        } finally {
          button.disabled = false;
        }
      });
    });
  }

  /* reopen a finished build from ?job= */
  const q = new URLSearchParams(location.search).get("job");
  if (q) { try { const j = await API.getJob(q); if (j.job_type === "RESUME_BUILD" && j.status === "DONE") show(j); } catch (e) { PX.toast(e.message, "error"); } }
})();
