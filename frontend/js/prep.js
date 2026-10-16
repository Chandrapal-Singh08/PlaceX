(async function () {
  const $ = id => document.getElementById(id);
  const userId = PX.session.userId();
  if (!userId) return;

  const MAX_FILE_BYTES = 5 * 1024 * 1024;
  const ALLOWED_FILE_TYPES = new Set([".pdf", ".docx", ".txt", ".md"]);
  const methodInputs = [...document.querySelectorAll('input[name="targetMethod"]')];
  let jobDescriptions = [];
  let resumes = [];
  let roadmaps = [];
  let selectedRoadmapId = null;
  let generating = false;

  function message(text, kind = "info") {
    const element = $("formMessage");
    element.textContent = text;
    element.className = `form-message ${kind}`;
    element.hidden = !text;
  }

  function emptyResult(text, action = "") {
    $("result").hidden = false;
    $("result").innerHTML = `<section class="glass empty">
      <p>${PX.esc(text)}</p>${action}
    </section>`;
  }

  function selectedMethod() {
    return document.querySelector('input[name="targetMethod"]:checked')?.value || "existing";
  }

  function updateTargetPanel() {
    const method = selectedMethod();
    $("targetExisting").hidden = method !== "existing";
    $("targetPaste").hidden = method !== "paste";
    $("targetUpload").hidden = method !== "upload";
    if (method === "existing" && !jobDescriptions.length) {
      $("jdEmpty").hidden = false;
    }
  }

  methodInputs.forEach(input => input.addEventListener("change", updateTargetPanel));
  $("deadlineChoice").addEventListener("change", () => {
    $("deadlineDateField").hidden = $("deadlineChoice").value !== "custom";
  });
  $("newPlan").addEventListener("click", () => {
    $("createPlan").scrollIntoView({ behavior: "smooth", block: "start" });
    $("createPlan").classList.add("focused");
    window.setTimeout(() => $("createPlan").classList.remove("focused"), 1200);
  });

  function formatDate(value) {
    if (!value) return "Date unavailable";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleDateString();
  }

  function progressMarkup(roadmap) {
    const total = roadmap.items.length;
    const completed = Number(roadmap.completed_items || 0);
    const percent = Number(roadmap.completion_pct || 0);
    return `<div class="saved-progress">
      <div class="saved-progress-label"><span>${completed} of ${total} tasks done</span><strong>${percent}%</strong></div>
      <div class="road-progress" role="progressbar" aria-label="Roadmap completion" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}">
        <i style="width:${percent}%"></i>
      </div>
    </div>`;
  }

  function renderSavedRoadmaps() {
    const host = $("savedRoadmaps");
    if (!roadmaps.length) {
      host.innerHTML = `<div class="saved-empty">
        <p>You do not have a saved preparation plan yet.</p>
        <button class="btn sm" type="button" data-create-first>Create your first plan</button>
      </div>`;
      host.querySelector("[data-create-first]").addEventListener("click", () => $("newPlan").click());
      return;
    }

    host.innerHTML = `<div class="saved-list">${roadmaps.map(roadmap => `
      <article class="glass saved-card">
        <div class="saved-card-copy">
          <h3>${PX.esc(roadmap.title)}</h3>
          <p>${PX.esc(roadmap.company_name || "Company not identified")} · ${PX.esc(roadmap.role_title || "Role unavailable")}</p>
          <small class="muted">Created ${PX.esc(formatDate(roadmap.created_at))}</small>
        </div>
        <div class="saved-card-progress">${progressMarkup(roadmap)}</div>
        <button class="btn ghost sm" type="button" data-open-roadmap="${PX.esc(roadmap.id)}">Continue plan</button>
      </article>`).join("")}</div>`;
    host.querySelectorAll("[data-open-roadmap]").forEach(button => {
      button.addEventListener("click", () => {
        const roadmap = roadmaps.find(item => item.id === Number(button.dataset.openRoadmap));
        if (roadmap) showRoadmap(roadmap);
      });
    });
  }

  function workload(roadmap, remainingHours) {
    const dailyHours = Number(roadmap.daily_study_hours);
    const deadlineDate = roadmap.deadline_date;
    let deadlineDays = roadmap.deadline_days ? Number(roadmap.deadline_days) : null;
    if (!deadlineDays && deadlineDate) {
      const target = new Date(`${deadlineDate}T00:00:00`);
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      deadlineDays = Math.max(1, Math.ceil((target.getTime() - today.getTime()) / 86400000));
    }
    const parts = [];
    if (Number.isInteger(dailyHours) && dailyHours > 0) {
      const remainingDays = Math.ceil(remainingHours / dailyHours);
      parts.push(`${remainingDays} study day${remainingDays === 1 ? "" : "s"} at ${dailyHours} h/day for the remaining work`);
    }
    if (deadlineDays) {
      const required = remainingHours / deadlineDays;
      const feasible = Number.isInteger(dailyHours) && required <= dailyHours;
      const deadlineLabel = roadmap.deadline_days
        ? `${deadlineDays}-day deadline`
        : `deadline of ${formatDate(deadlineDate)}`;
      parts.push(`${required.toFixed(1)} h/day needed to meet the ${deadlineLabel}${feasible ? "" : " (above your selected daily availability)"}`);
    }
    return parts.length
      ? `<p class="workload"><strong>Suggested workload:</strong> ${PX.esc(parts.join(" · "))}</p>`
      : `<p class="workload muted">Choose daily study time and a deadline when creating a new plan to see a workload estimate.</p>`;
  }

  function showRoadmap(roadmap) {
    selectedRoadmapId = roadmap.id;
    const items = roadmap.items || [];
    const completed = Number(roadmap.completed_items || 0);
    const totalHours = Number(roadmap.total_hours || 0);
    const completedHours = Number(roadmap.completed_hours || 0);
    const remainingHours = Number(roadmap.remaining_hours || 0);
    const percent = Number(roadmap.completion_pct || 0);
    const matchedSkills = roadmap.matched_skills || [];
    const result = $("result");
    result.hidden = false;

    if (!items.length) {
      result.innerHTML = `<section class="glass roadmap-head">
        <p class="muted">${PX.esc(roadmap.company_name || "Company not identified")} · ${PX.esc(roadmap.role_title || "Role unavailable")}</p>
        <h2>${PX.esc(roadmap.title)}</h2>
        <p>This saved roadmap has no study tasks. There may have been no missing target skills in the analysis that created it.</p>
        ${matchedSkills.length ? `<p><strong>Matched skills:</strong> ${PX.esc(matchedSkills.map(item => item.skill).join(", "))}</p>` : ""}
        <button class="btn ghost sm" type="button" data-create-plan>Compare another target</button>
      </section>`;
      result.querySelector("[data-create-plan]").addEventListener("click", () => $("newPlan").click());
      result.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

    const resourceMarkup = resource => resource && /^https?:\/\//i.test(resource.url || "")
      ? `<a href="${PX.esc(resource.url)}" target="_blank" rel="noopener noreferrer">${PX.esc(resource.title)}${resource.kind ? ` · ${PX.esc(resource.kind)}` : ""}</a>`
      : `<span class="muted">No learning resource is stored for this skill.</span>`;
    result.innerHTML = `<section class="glass roadmap-head">
        <p class="muted">${PX.esc(roadmap.company_name || "Company unavailable")} · ${PX.esc(roadmap.role_title || "Role unavailable")}</p>
        <h2>${PX.esc(roadmap.title)}</h2>
        <div class="road-summary">
          <div class="glass stat"><span>Estimated total</span><b>${totalHours} h</b></div>
          <div class="glass stat"><span>Completed hours</span><b>${completedHours} h</b></div>
          <div class="glass stat"><span>Remaining hours</span><b>${remainingHours} h</b></div>
          <div class="glass stat"><span>Tasks complete</span><b>${completed}/${items.length}</b></div>
        </div>
        <div class="saved-progress-label"><span>Overall completion</span><strong>${percent}%</strong></div>
        <div class="road-progress" role="progressbar" aria-label="Roadmap completion" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}">
          <i style="width:${percent}%"></i>
        </div>
        ${workload(roadmap, remainingHours)}
        ${matchedSkills.length ? `<div class="matched-skills"><strong>Skills already matched</strong><p>${matchedSkills.map(item => PX.esc(item.skill)).join(", ")}</p></div>` : ""}
        <small class="muted">Hours are estimates from the stored skill catalog. Priority and requirement labels come from the selected job description.</small>
      </section>
      <div class="road">${items.map(item => {
        const importance = item.importance == null ? "" : ` · importance ${PX.esc(item.importance)}/5`;
        const relevance = item.required === true
          ? `Required by this job description${importance}.`
          : item.required === false
            ? `Preferred skill in this job description${importance}.`
            : "Requirement priority is unavailable.";
        return `<article class="glass hover rm">
          <span class="no">${String(item.position).padStart(2, "0")}</span>
          <div class="road-item-main">
            <b>${PX.esc(item.skill)}</b>
            <small>${PX.esc(relevance)}</small>
            <small>${resourceMarkup(item.resource)}</small>
          </div>
          <div class="hrs">~${PX.esc(item.est_hours)}<small> estimated hours</small></div>
          <label class="item-status"><span class="sr-only">Status for ${PX.esc(item.skill)}</span>
            <select data-item-id="${PX.esc(item.id)}" aria-label="Status for ${PX.esc(item.skill)}">
              ${["TODO", "IN_PROGRESS", "DONE"].map(status =>
                `<option value="${status}" ${item.status === status ? "selected" : ""}>${status.replace("_", " ")}</option>`
              ).join("")}
            </select>
          </label>
        </article>`;
      }).join("")}</div>
      <p class="muted roadmap-id">This roadmap is saved to your account. Need interview-round information? <a href="journey-guide.html">Open Journey Guide</a>.</p>`;
    result.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function loadRoadmaps({ showSelected = true } = {}) {
    roadmaps = await API.roadmaps(userId);
    renderSavedRoadmaps();
    if (!roadmaps.length) {
      selectedRoadmapId = null;
      if (showSelected) emptyResult(
        "No saved roadmap is available yet. Generate a plan from a resume and target job description.",
        `<button class="btn sm" type="button" data-create-plan>Create your first plan</button>`
      );
      const action = $("result").querySelector("[data-create-plan]");
      if (action) action.addEventListener("click", () => $("newPlan").click());
      return;
    }

    if (showSelected) {
      const selected = roadmaps.find(item => item.id === selectedRoadmapId) || roadmaps[0];
      showRoadmap(selected);
    }
  }

  $("result").addEventListener("change", async event => {
    const control = event.target.closest("select[data-item-id]");
    if (!control) return;
    const roadmap = roadmaps.find(item => item.id === selectedRoadmapId);
    const previous = roadmap?.items.find(item => item.id === Number(control.dataset.itemId))?.status;
    control.disabled = true;
    message("Saving task progress…");
    try {
      const updated = await API.updateRoadmapItem(Number(control.dataset.itemId), control.value);
      await loadRoadmaps({ showSelected: false });
      const savedRoadmap = roadmaps.find(item => item.id === updated.roadmap_id);
      if (!savedRoadmap) throw new Error("Progress was saved, but the roadmap could not be reloaded. Refresh the page to see it.");
      showRoadmap(savedRoadmap);
      message("Progress saved.", "success");
    } catch (error) {
      if (previous) control.value = previous;
      control.disabled = false;
      message(error.message || "Could not save this task status. Try again.", "error");
    }
  });

  function validateFile(file, label) {
    if (!file) return `${label} is required.`;
    const extension = "." + file.name.split(".").pop().toLowerCase();
    if (!ALLOWED_FILE_TYPES.has(extension)) return `${label} must be PDF, DOCX, TXT, or MD.`;
    if (!file.size) return `${label} cannot be empty.`;
    if (file.size > MAX_FILE_BYTES) return `${label} must be no larger than 5 MB.`;
    return null;
  }

  async function resolveJobDescription() {
    const method = selectedMethod();
    if (method === "existing") {
      const id = Number($("jdSelect").value);
      if (!id || !jobDescriptions.some(item => item.id === id)) {
        throw new Error(jobDescriptions.length
          ? "Choose a job description from the list."
          : "No saved job descriptions are available. Paste or upload a description instead.");
      }
      return jobDescriptions.find(item => item.id === id);
    }
    if (method === "paste") {
      const text = $("jdText").value.trim();
      if (!text) throw new Error("Paste a job description before generating your plan.");
      if (text.length > 50000) throw new Error("The job description exceeds the 50,000 character limit.");
      return API.createJd({ text });
    }
    const file = $("jdFile").files[0];
    const problem = validateFile(file, "Job description file");
    if (problem) throw new Error(problem);
    return API.createJd({ file });
  }

  async function resolveResume() {
    const file = $("resumeFile").files[0];
    if (file) {
      const problem = validateFile(file, "Resume file");
      if (problem) throw new Error(problem);
      message("Uploading your resume…");
      const uploaded = await API.uploadResume(userId, file);
      if (!uploaded.raw_text || !uploaded.raw_text.trim()) {
        throw new Error("The file was uploaded, but PlaceX could not extract text. Try a text-based PDF, DOCX, TXT, or MD file, or use Resume Analyzer.");
      }
      await loadResumes(uploaded.id);
      return uploaded;
    }
    const resumeId = Number($("resumeSelect").value);
    const resume = resumes.find(item => item.id === resumeId);
    if (!resume) {
      throw new Error("Choose an existing resume or upload one. Resume Analyzer can also upload and analyze a resume.");
    }
    return resume;
  }

  function selectedPreferences() {
    const dailyStudyHours = Number($("dailyHours").value);
    const deadlineChoice = $("deadlineChoice").value;
    const preferences = { daily_study_hours: dailyStudyHours };
    if (deadlineChoice === "custom") {
      const date = $("deadlineDate").value;
      if (!date) throw new Error("Choose your preparation deadline date, or select No deadline.");
      if (date <= new Date().toISOString().slice(0, 10)) {
        throw new Error("Preparation deadline must be at least one day from today.");
      }
      preferences.deadline_date = date;
    } else if (deadlineChoice) {
      preferences.deadline_days = Number(deadlineChoice);
    }
    return preferences;
  }

  async function generatePlan() {
    if (generating) return;
    generating = true;
    const button = $("generatePlan");
    button.disabled = true;
    button.textContent = "Preparing your plan…";
    message("");
    $("result").hidden = true;
    const progress = PX.progress($("progress"), "Preparing your personalized study plan");

    try {
      const preferences = selectedPreferences();
      progress.label("Loading your target job description");
      const target = await resolveJobDescription();
      progress.label("Selecting a resume");
      const resume = await resolveResume();

      progress.label("Checking for a matching saved analysis");
      let report = await API.matchingAnalysis(userId, resume.id, target.id);
      if (!report) {
        progress.label("Comparing your resume with the selected role");
        const analysis = await API.runJob(userId, "RESUME_ANALYSIS", {
          resume_id: Number(resume.id), mode: "JD_COMPARISON", jd_id: Number(target.id)
        }, progress.tick, 5);
        report = { id: analysis.result?.report_id };
        if (!report.id) throw new Error("The comparison finished without a skill-gap report. Run Resume Analyzer and try again.");
        PX.history.setLastReport(report.id);
      }

      progress.label("Building your prioritized skill-gap plan");
      const job = await API.runJob(userId, "PREP_PLAN", {
        report_id: Number(report.id),
        ...preferences,
      }, progress.tick, 5);
      progress.done();
      const result = job.result || {};
      if (result.roadmap_id) {
        await loadRoadmaps({ showSelected: false });
        const saved = roadmaps.find(item => item.id === result.roadmap_id);
        if (!saved) throw new Error("The plan was generated but could not be loaded. Refresh the page to find it in Your Saved Plans.");
        showRoadmap(saved);
        message(result.reused_existing ? "Continued your existing plan for this analysis." : "Your preparation plan is saved.", "success");
      } else {
        message("");
        const matched = result.matched_skills || [];
        const matchedMarkup = matched.length
          ? `<p><strong>Skills already matched:</strong> ${PX.esc(matched.join(", "))}</p>`
          : "";
        emptyResult(
          result.note || "No skill-gap information was available to create a plan. Try another resume or target role.",
          matchedMarkup
        );
      }
    } catch (error) {
      progress.done();
      const safeMessage = error.kind === "job"
        ? "Plan generation failed. Check that your resume has readable text and the selected job description contains recognizable skills, then try again."
        : error.message || "Could not generate your plan. Check your connection and try again.";
      message(safeMessage, "error");
    } finally {
      progress.done();
      generating = false;
      button.disabled = false;
      button.textContent = "Generate preparation plan";
    }
  }

  $("generatePlan").addEventListener("click", generatePlan);

  async function loadJobDescriptions() {
    jobDescriptions = await API.jobDescriptions();
    const select = $("jdSelect");
    select.innerHTML = jobDescriptions.length
      ? `<option value="">Choose a target role</option>` + jobDescriptions.map(item =>
          `<option value="${PX.esc(item.id)}">${PX.esc(item.company_name || "Company not identified")} · ${PX.esc(item.role_title)}</option>`
        ).join("")
      : `<option value="">No saved job descriptions</option>`;
    $("jdEmpty").hidden = jobDescriptions.length > 0;
    if (!jobDescriptions.length) {
      document.querySelector('input[name="targetMethod"][value="paste"]').checked = true;
      updateTargetPanel();
      $("jdEmpty").hidden = false;
    }
  }

  async function loadResumes(selectedId) {
    resumes = await API.resumes(userId);
    const select = $("resumeSelect");
    select.innerHTML = resumes.length
      ? `<option value="">Choose a resume</option>` + resumes.map(resume =>
          `<option value="${PX.esc(resume.id)}" ${resume.id === Number(selectedId) ? "selected" : ""}>${PX.esc(resume.title)} · ${PX.esc(resume.kind.toLowerCase())} · ${PX.esc(formatDate(resume.created_at))}</option>`
        ).join("")
      : `<option value="">No readable resumes found</option>`;
    $("resumeMessage").textContent = resumes.length
      ? "PlaceX reuses a saved analysis only when it matches this resume and target job description."
      : "No resume with extractable text was found. Upload one below or open Resume Analyzer.";
  }

  try {
    await Promise.all([loadRoadmaps({ showSelected: false }), loadJobDescriptions(), loadResumes()]);
    if (!roadmaps.length) {
      emptyResult(
        "No saved roadmap yet. Create a plan from a resume and a target job description.",
        `<button class="btn sm" type="button" data-create-plan>Create your first plan</button>`
      );
      $("result").querySelector("[data-create-plan]").addEventListener("click", () => $("newPlan").click());
    } else {
      showRoadmap(roadmaps[0]);
    }
  } catch (error) {
    message(error.message || "Could not load your saved plans or selections. Refresh the page or check your connection.", "error");
    if (!roadmaps.length) emptyResult("Saved plans could not be loaded. You can retry by refreshing this page.");
  }
})();
