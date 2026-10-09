(function () {
  const $ = id => document.getElementById(id);
  const uid = PX.session.userId(); if (!uid) return;
  const ALLOWED = [".pdf", ".docx", ".txt", ".md"], MAX = 5 * 1048576;
  let file = null, resumeId = null, reuseId = null;
  const jd = JDInput.mount($("jd"), { title: "Job description" });
  const modeHost = document.createElement("div");
  modeHost.className = "modes";
  modeHost.innerHTML = `<label class="mode"><input type="radio" name="analysis-mode" value="general"><div class="glass hover"><span class="radio"></span><div><b>General ATS Score</b><small>Analyze your resume without a specific job.</small></div></div></label>
    <label class="mode"><input type="radio" name="analysis-mode" value="jd" checked><div class="glass hover"><span class="radio"></span><div><b>Job Description Comparison</b><small>Compare skills and get targeted suggestions.</small></div></div></label>`;
  $("work").before(modeHost);
  const jdSection = $("jd").parentElement;
  const actionRow = document.createElement("div");
  actionRow.className = "actions";
  actionRow.style.justifyContent = "center";
  actionRow.append($("analyzeBtn"), $("analyzeHint"));
  $("work").after(actionRow);
  const mode = () => document.querySelector('input[name="analysis-mode"]:checked').value;

  function refresh() {
    const general = mode() === "general";
    jdSection.hidden = general;
    $("work").classList.toggle("single", general);
    $("analyzeBtn").disabled = !(resumeId || reuseId);
  }
  modeHost.querySelectorAll('input[name="analysis-mode"]').forEach(radio => radio.addEventListener("change", refresh));
  if (new URLSearchParams(location.search).get("mode") === "jd") modeHost.querySelector('input[value="jd"]').checked = true;

  const step = n => ["st1", "st2", "st3"].forEach((id, i) => { $(id).className = i + 1 < n ? "done" : i + 1 === n ? "on" : ""; });
  function setFile(f) {
    if (!f) { file = null; resumeId = null; $("prev").hidden = true; $("uploadBtn").disabled = true; refresh(); return; }
    const ext = "." + f.name.split(".").pop().toLowerCase();
    if (!ALLOWED.includes(ext)) return PX.toast("Unsupported file type. Use PDF, DOCX, TXT or MD.", "error");
    if (f.size > MAX) return PX.toast("That file is over the 5 MB limit.", "error");
    if (!f.size) return PX.toast("That file is empty.", "error");
    file = f; resumeId = null; reuseId = null; refresh(); $("analyzeHint").textContent = "Upload the resume first.";
    $("fName").textContent = f.name; $("fSize").textContent = PX.fmtBytes(f.size);
    $("prev").hidden = false; $("uploadBtn").disabled = false; step(1);
  }
  const drop = $("drop"), input = $("file");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => setFile(input.files[0]));
  ["dragover", "dragenter"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", e => setFile(e.dataTransfer.files[0]));
  $("clear").addEventListener("click", () => { input.value = ""; setFile(null); });

  function readyToAnalyze(id, label) {
    resumeId = id; reuseId = null; PX.history.setLastResume(id, file ? file.name : PX.history.lastResumeName());
    $("uploaded").hidden = false; $("uploaded").innerHTML = `✅ ${PX.esc(label)} <b>(resume #${id})</b>`;
    $("analyzeHint").textContent = ""; step(2); refresh();
  }

  $("uploadBtn").addEventListener("click", async () => {
    if (!file) return;
    $("uploadBtn").disabled = true; $("uploadBtn").textContent = "Uploading…";
    try {
      const r = await API.uploadResume(uid, file);                    // POST /users/{id}/resumes
      PX.toast("Upload successful.", "success");
      readyToAnalyze(r.id, "Uploaded " + (r.title || file.name));
      if (!r.raw_text) PX.toast("No text could be read from this file, so analysis may fail. Try DOCX or TXT.", "error", 7000);
    } catch (e) { PX.toast(e.message, "error"); $("uploadBtn").disabled = false; }
    $("uploadBtn").textContent = "Upload resume";
  });

  $("analyzeBtn").addEventListener("click", async () => {
    const general = mode() === "general";
    if (!general && !jd.isFilled()) return PX.toast("Choose or provide a job description first.", "error");
    $("work").hidden = true;
    const p = PX.progress($("progress"), "Analyzing your resume");
    try {
      resumeId = resumeId || reuseId;
      let payload = { resume_id: Number(resumeId), mode: "GENERAL" };
      if (!general) {
        p.label("Reading the job description");
        const selectedJd = await jd.resolve();
        payload = { resume_id: Number(resumeId), mode: "JD_COMPARISON", jd_id: selectedJd.id };
      }
      const job = await API.runJob(uid, "RESUME_ANALYSIS", payload, p.tick, 5);
      p.done(); if (job.result.report_id) PX.history.setLastReport(job.result.report_id);
      PX.toast("Analysis completed.", "success");
      location.href = "analysis-result.html?job=" + job.id;
    } catch (e) {
      p.done(); $("work").hidden = false; step(2);
      PX.toast(e.kind === "job" ? "Job failed: " + e.message : e.message, "error", 7000);
    }
  });

  const prev = PX.history.lastResume();
  if (prev) { $("uploaded").hidden = false; $("uploaded").innerHTML = `Last uploaded: <b>${PX.esc(PX.history.lastResumeName() || "resume #" + prev)}</b>. <a href="#" id="reuse" style="color:var(--cyan);font-weight:700">Use it again</a>`;
    $("reuse").addEventListener("click", e => { e.preventDefault(); reuseId = Number(prev); resumeId = null; $("analyzeHint").textContent = ""; refresh(); step(2); }); }
  refresh();
})();
