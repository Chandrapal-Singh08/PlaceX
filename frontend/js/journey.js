(async function () {
  const $ = id => document.getElementById(id);
  const userId = PX.session.userId();
  if (!userId) return;

  const form = $("journeyForm");
  const button = $("go");
  let companies = [];
  let roles = [];
  let submitting = false;

  function errorMessage(error) {
    if (error?.kind === "network") {
      return "PlaceX is not reachable right now. Confirm the backend is running, then retry.";
    }
    if (error?.kind === "database_unavailable") {
      return "The PlaceX database is temporarily unavailable. Your form is unchanged; retry shortly.";
    }
    if (error?.kind === "timeout") {
      return "The request timed out. If a guide was already submitted, retry its status check below.";
    }
    return error?.message || "Please retry shortly.";
  }

  function dateLabel(value) {
    if (!value) return "Not recorded";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Not recorded" : date.toLocaleString();
  }

  function list(items, emptyText = "No information available.") {
    return items?.length
      ? `<ul>${items.map(item => `<li>${PX.esc(item)}</li>`).join("")}</ul>`
      : `<p class="muted">${PX.esc(emptyText)}</p>`;
  }

  function resourceLink(resource) {
    const url = safeLink(resource.url);
    if (!url) return "";
    return `<article class="glass journey-resource">
      <h5><a href="${PX.esc(url)}" target="_blank" rel="noopener noreferrer">${PX.esc(resource.title)}</a></h5>
      <p>${PX.esc(resource.description || "")}</p>
      <div class="journey-meta">
        <span class="chip">${PX.esc(resource.category || "Role-specific learning")}</span>
        <span class="chip">${PX.esc(resource.difficulty || "varies")}</span>
        <span class="muted">${PX.esc(resource.source_classification_label || sourceLabel(resource.source_classification))}</span>
      </div>
      ${resource.topics?.length ? `<small>Topics: ${PX.esc(resource.topics.join(", "))}</small>` : ""}
      <small class="muted">${PX.esc(resource.source_domain || "")}${resource.retrieved_at ? ` · Retrieved ${PX.esc(dateLabel(resource.retrieved_at))}` : ""}</small>
    </article>`;
  }

  function safeLink(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password
        ? url.href
        : "";
    } catch {
      return "";
    }
  }

  function sourceLink(url, label) {
    const safeUrl = safeLink(url);
    return safeUrl
      ? `<a href="${PX.esc(safeUrl)}" target="_blank" rel="noopener noreferrer">${PX.esc(label)}</a>`
      : PX.esc(label);
  }

  function sourceLabel(classification) {
    return ({
      verified: "Officially sourced",
      source_reported: "Secondary-source reported",
      secondary_source: "Secondary source · unverified",
      company_domain_candidate: "Possible company-domain source · unverified",
      community_or_interview_source: "Community source · unverified",
      placex_resource: "PlaceX learning resource",
      user_provided: "Provided for this guide",
    })[classification] || "Unverified source";
  }

  function cards(items, renderItem, emptyText = "") {
    return items?.length
      ? `<div class="journey-card-grid">${items.map(renderItem).join("")}</div>`
      : emptyText ? `<p class="muted">${PX.esc(emptyText)}</p>` : "";
  }

  function render(guide) {
    const result = $("result");
    const database = guide.database_information || {};
    const rounds = (database.hiring_rounds || guide.rounds || []).filter(round =>
      ["verified", "source_reported"].includes(round.data_classification)
    );
    const descriptions = (database.job_descriptions || []).filter(item =>
      ["verified", "source_reported"].includes(item.data_classification) && safeLink(item.source_url)
    );
    const sources = (guide.source_references || []).filter(source =>
      ["verified", "source_reported"].includes(source.data_classification) && safeLink(source.url)
    );
    const externalResearch = guide.external_research || {};
    const researchSources = externalResearch.sources || [];
    const personalization = guide.personalization || {};
    const advice = guide.ai_guidance || {};
    const projects = personalization.projects || [];
    const resources = (guide.resources || []).filter(resource => safeLink(resource.url));
    const resourceGroups = [...new Set(resources.map(resource => resource.category || "Role-specific learning"))];
    const assessmentLabels = {
      demonstrated: "Demonstrated in available evidence",
      partially_demonstrated: "Partially demonstrated",
      not_demonstrated_in_available_information: "Not demonstrated in available information",
      requires_student_confirmation: "Confirm with your experience",
    };

    result.hidden = false;
    result.innerHTML = `<section class="glass journey-result">
      <div class="journey-result-head">
        <div><small class="chip">Saved to your account</small>
          <h2>${PX.esc(guide.company_name || "Company")} · ${PX.esc(guide.job_role || guide.role_title || "Role")}</h2>
          <p class="muted">${guide.location ? `Location: ${PX.esc(guide.location)}` : "Location not specified"}
            ${guide.experience_level ? ` · ${PX.esc(guide.experience_level)}` : ""}
            ${guide.employment_type ? ` · ${PX.esc(guide.employment_type)}` : ""}</p>
        </div>
        <small>${PX.esc(dateLabel(guide.created_at))}</small>
      </div>
      ${guide.ai_notice ? `<div class="alert">${PX.esc(guide.ai_notice)}</div>` : ""}

      <section class="journey-block">
        <h3>AI-generated role overview</h3>
        <p class="muted">Preparation context based on the role details and retrieved evidence; it is not an official employer statement.</p>
        <p>${PX.esc(advice.company_role_overview || "")}</p>
        ${advice.role_responsibilities?.length ? `<h4>Responsibilities</h4>${list(advice.role_responsibilities)}` : ""}
        ${advice.relevant_technologies?.length ? `<h4>Relevant technologies</h4>${list(advice.relevant_technologies)}` : ""}
        ${advice.required_skills?.length ? `<h4>Potential role skills · AI-generated</h4>${list(advice.required_skills)}` : ""}
        ${advice.desirable_skills?.length ? `<h4>Additional useful skills</h4>${list(advice.desirable_skills)}` : ""}
        ${advice.estimated_preparation_hours ? `<p><strong>Estimated effort:</strong> ${PX.esc(advice.estimated_preparation_hours)} study hours (estimate)</p>` : ""}
      </section>

      ${rounds.length || descriptions.length ? `<section class="journey-block">
        <h3>Company information and sources</h3>
        <p class="muted">Company-specific information below is labeled by its source. Preparation suggestions elsewhere are not confirmed interview rounds.</p>
        ${rounds.length ? `<h4>Reported hiring information</h4>
          <div class="timeline">${rounds.map(round => `<article class="glass hover round">
            <span class="num">${PX.esc(round.round_no ?? round.no)}</span>
            <h4>${PX.esc(round.name || "Unnamed round")}</h4>
            <small class="chip">${PX.esc(sourceLabel(round.data_classification))} · not guaranteed current</small>
            <p>${PX.esc(round.description || round.what_to_expect || "No description stored.")}</p>
            ${round.duration_min || round.minutes ? `<small>${PX.esc(round.duration_min || round.minutes)} minutes</small>` : ""}
            ${round.tips ? `<p><strong>Preparation notes:</strong> ${PX.esc(round.tips)}</p>` : ""}
          </article>`).join("")}</div>`
          : ""}
        ${descriptions.length ? `<h4>Role requirements</h4>
          ${descriptions.map(item => `<article class="glass journey-record">
            <strong>${PX.esc(item.role_title)}</strong> · ${PX.esc(item.company_name || guide.company_name)}
            <small class="chip">${PX.esc(sourceLabel(item.data_classification))}</small>
            ${item.description ? `<p>${PX.esc(item.description)}</p>` : ""}
            ${item.requirements?.length ? `<strong>Stored requirements</strong><ul>${item.requirements.map(requirement => `<li>${PX.esc(requirement.skill)} · ${requirement.required ? "required" : "preferred"} · importance ${PX.esc(requirement.importance)}/5</li>`).join("")}</ul>` : ""}
            ${item.source_url ? `<p>${sourceLink(item.source_url, "Database source")}</p>` : ""}
            <small class="muted">Retrieved: ${PX.esc(dateLabel(item.retrieved_at))}${item.last_verified_at ? ` · Verified: ${PX.esc(dateLabel(item.last_verified_at))}` : ""}</small>
          </article>`).join("")}`
          : ""}
      </section>` : ""}

      <section class="journey-block">
        <h3>Preparation overview</h3>
        ${advice.preparation_sequence?.length ? `<p class="journey-sequence">${advice.preparation_sequence.map((item, index) => `<span>${index + 1}. ${PX.esc(item)}</span>`).join("")}</p>` : ""}
        ${advice.preparation_phases?.length ? cards(advice.preparation_phases, phase => `<article class="glass journey-resource">
          <small class="chip">${PX.esc(phase.timeframe)}</small><h4>${PX.esc(phase.name)}</h4>
          ${list(phase.priorities)}<p>${PX.esc(phase.reason)}</p>
        </article>`) : ""}
        ${(advice.potential_stages || []).length ? `<h4>Preparation stages · AI-generated guidance</h4>
          <div class="timeline">${advice.potential_stages.map((stage, index) => `<details class="glass hover round journey-stage">
            <summary><span class="num">${index + 1}</span>${PX.esc(stage.stage_name)} · AI-generated preparation</summary>
            ${stage.expectation ? `<p>${PX.esc(stage.expectation)}</p>` : ""}
            ${stage.preparation_topics?.length ? `<strong>Preparation topics</strong>${list(stage.preparation_topics)}` : ""}
            ${stage.practical_tasks?.length ? `<strong>Practice tasks</strong>${list(stage.practical_tasks)}` : ""}
            ${stage.common_mistakes?.length ? `<strong>Common mistakes to avoid</strong>${list(stage.common_mistakes)}` : ""}
            ${stage.practice_questions?.length ? `<strong>Practice questions</strong>${list(stage.practice_questions)}` : ""}
          </details>`).join("")}</div>` : ""}
      </section>

      <section class="journey-block">
        <h3>Technical subjects</h3>
        ${cards(advice.technical_subjects, subject => `<article class="glass journey-resource">
          <span class="chip">${PX.esc(subject.priority)} priority</span><h4>${PX.esc(subject.name)}</h4>
          <p>${PX.esc(subject.why_it_matters)}</p><p><strong>Practice:</strong> ${PX.esc(subject.practice_action)}</p>
        </article>`)}
      </section>

      ${advice.coding_roadmap?.length ? `<section class="journey-block"><h3>Coding and problem-solving roadmap</h3>
        ${cards(advice.coding_roadmap, topic => `<article class="glass journey-resource">
          <span class="chip">${PX.esc(topic.difficulty)}</span><h4>${PX.esc(topic.topic)}</h4>
          ${topic.patterns?.length ? `<p><strong>Patterns:</strong> ${PX.esc(topic.patterns.join(", "))}</p>` : ""}
          ${list(topic.practice_tasks)}<p><strong>Weekly target:</strong> ${PX.esc(topic.weekly_target)}</p>
        </article>`)}
      </section>` : ""}

      ${advice.technical_questions?.length ? `<section class="journey-block"><h3>Technical interview practice</h3>
        ${cards(advice.technical_questions, item => `<article class="glass journey-resource">
          <span class="chip">${PX.esc(item.difficulty)} · ${PX.esc(item.category)}</span>
          <h4>${PX.esc(item.question)}</h4><p><strong>Strong answers should cover:</strong> ${PX.esc(item.answer_guidance)}</p>
        </article>`)}
      </section>` : ""}

      ${advice.project_preparation?.length ? `<section class="journey-block"><h3>Prepare your projects</h3>
        ${cards(advice.project_preparation.map(item => ({...item, project: projects[item.project_index]})).filter(item => item.project), item => `<article class="glass journey-resource">
          <h4>${PX.esc(item.project.evidence)}</h4><p>${PX.esc(item.relevance)}</p>
          <strong>Concepts to review</strong>${list(item.concepts_to_review)}
          <strong>Discussion questions</strong>${list(item.discussion_questions)}
          <strong>Possible improvements</strong>${list(item.improvements)}
        </article>`)}
      </section>` : ""}

      ${advice.skill_assessment?.length ? `<section class="journey-block"><h3>Your role skill assessment</h3>
        <p class="muted">Not demonstrated means it was not found in the information used for this guide; it does not mean you lack the skill.</p>
        ${cards(advice.skill_assessment, item => `<article class="glass journey-resource">
          <span class="chip">${PX.esc(assessmentLabels[item.status] || assessmentLabels.requires_student_confirmation)}</span>
          <h4>${PX.esc(item.skill)}</h4>${item.evidence?.length ? `<p><strong>Evidence:</strong> ${PX.esc(item.evidence.join(" · "))}</p>` : ""}
          <p><strong>Next step:</strong> ${PX.esc(item.next_action)}</p>
        </article>`)}
      </section>` : ""}

      ${advice.behavioral_competencies?.length || advice.behavioral_questions?.length ? `<section class="journey-block"><h3>Behavioral and HR preparation</h3>
        ${advice.behavioral_competencies?.length ? `<p><strong>Focus:</strong> ${PX.esc(advice.behavioral_competencies.join(", "))}</p>` : ""}
        ${cards(advice.behavioral_questions, item => `<article class="glass journey-resource">
          <span class="chip">${PX.esc(item.competency)}</span><h4>${PX.esc(item.question)}</h4>
          <p><strong>STAR answer guide:</strong> ${PX.esc(item.star_guidance)}</p>
        </article>`)}
      </section>` : ""}

      ${advice.practical_assignments?.length ? `<section class="journey-block"><h3>Practical assignments</h3>
        ${cards(advice.practical_assignments, item => `<article class="glass journey-resource">
          <h4>${PX.esc(item.title)}</h4><p>${PX.esc(item.description)}</p>
          ${item.deliverables?.length ? `<strong>Deliverables</strong>${list(item.deliverables)}` : ""}
          <small>Estimated effort: ${PX.esc(item.estimated_hours)} hours</small>
        </article>`)}
      </section>` : ""}

      ${resourceGroups.length ? `<section class="journey-block"><h3>Learning resources</h3>
        <p class="muted">Links below come from retrieved PlaceX records or external search results. Source classification describes the source, not a guarantee of its claims.</p>
        ${resourceGroups.map(group => `<h4>${PX.esc(group)}</h4>${cards(resources.filter(item => (item.category || "Role-specific learning") === group), resourceLink)}`).join("")}
      </section>` : ""}

      <section class="journey-block">
        <h3>Sources and verification</h3>
        ${sources.length || researchSources.length ? `<ul class="journey-sources">${[
          ...sources.map(source => ({...source, title: source.name, source_classification: source.data_classification})),
          ...researchSources,
        ].filter(source => safeLink(source.url)).map(source => `<li>
          ${sourceLink(source.url, source.title || source.source_domain || "Source")}
          <span class="chip">${PX.esc(sourceLabel(source.source_classification))}</span>
          ${source.source_domain ? ` · ${PX.esc(source.source_domain)}` : ""}
          ${source.retrieved_at ? ` · Retrieved ${PX.esc(dateLabel(source.retrieved_at))}` : ""}
          ${source.snippet ? `<p>${PX.esc(source.snippet)}</p>` : ""}
        </li>`).join("")}</ul>` : `<p class="muted">No source links were available for this guide. The preparation plan is general role guidance, not a confirmed company process.</p>`}
        ${externalResearch.searched_for_this_request === false ? `<p class="muted">No external sources were available for this guide; recommendations use the role information provided.</p>` : ""}
      </section>

      ${guide.guide_id ? `<section class="journey-block journey-plan-builder">
        <h3>Turn this guide into your Prep Planner</h3>
        <p>Save the prioritized tasks as a trackable plan. Your progress is kept separately from existing roadmaps.</p>
        <div class="journey-plan-controls">
          <label class="field"><span>Study time per day</span><input id="planDailyHours" type="number" min="1" max="8" value="2"></label>
          <label class="field"><span>Preparation deadline</span><select id="planDeadline">
            <option value="">No deadline</option><option value="7">7 days</option><option value="15">15 days</option>
            <option value="30">30 days</option><option value="60">60 days</option>
          </select></label>
          <button class="btn" type="button" data-create-roadmap="${PX.esc(guide.guide_id)}">Add to Prep Planner</button>
        </div>
        <p class="muted" data-plan-status aria-live="polite"></p>
      </section>
      ` : ""}

      ${!personalization.resume_available ? `<p class="muted">Want deeper personalization? <a href="analyze.html">Upload and analyze a resume</a> to include your skills and project evidence.</p>` : ""}
    </section>`;
    const createRoadmapButton = result.querySelector("[data-create-roadmap]");
    if (createRoadmapButton) {
      createRoadmapButton.addEventListener("click", async () => {
        createRoadmapButton.disabled = true;
        const status = result.querySelector("[data-plan-status]");
        status.textContent = "Saving your tasks to Prep Planner…";
        try {
          const dailyHours = Number($("planDailyHours").value);
          const deadlineDays = $("planDeadline").value;
          if (!Number.isInteger(dailyHours) || dailyHours < 1 || dailyHours > 8) {
            throw new Error("Choose between 1 and 8 study hours per day.");
          }
          const created = await API.journeyRoadmap(Number(createRoadmapButton.dataset.createRoadmap), {
            daily_study_hours: dailyHours,
            deadline_days: deadlineDays ? Number(deadlineDays) : null,
          });
          status.textContent = created.message || "Your plan is ready.";
          window.location.href = `prep-plan.html?roadmap_id=${encodeURIComponent(created.id)}`;
        } catch (error) {
          status.textContent = error.message || "Could not save this guide as a preparation plan.";
          createRoadmapButton.disabled = false;
        }
      });
    }
  }

  function renderSavedGuides(guides) {
    const host = $("savedGuides");
    if (!guides.length) {
      host.innerHTML = `<p class="muted">Generated guides are saved here automatically.</p>`;
      return;
    }
    host.innerHTML = `<div class="saved-guide-list">${guides.map(guide => `
      <button class="btn ghost sm saved-guide" type="button" data-guide-id="${PX.esc(guide.id)}">
        ${PX.esc(guide.company_name)} · ${PX.esc(guide.job_role)}
        <small>${PX.esc(dateLabel(guide.created_at))}</small>
      </button>`).join("")}</div>`;
    host.querySelectorAll("[data-guide-id]").forEach(button => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const saved = await API.journeyGuide(Number(button.dataset.guideId));
          render({
            ...(saved.guidance || {}),
            guide_id: saved.id,
            created_at: saved.created_at,
          });
          history.replaceState(null, "", "?guide=" + saved.id);
        } catch (error) {
          PX.toast(error.message, "error");
        } finally {
          button.disabled = false;
        }
      });
    });
  }

  function showSavedGuidesError(error) {
    const host = $("savedGuides");
    host.innerHTML = `<p class="muted">${PX.esc(errorMessage(error))}</p>
      <button class="btn ghost sm" type="button" id="retrySavedGuides">Retry saved guides</button>`;
    $("retrySavedGuides").addEventListener("click", async event => {
      event.currentTarget.disabled = true;
      try {
        await loadSavedGuides();
      } catch (retryError) {
        showSavedGuidesError(retryError);
      }
    });
  }

  async function loadSavedGuides() {
    const guides = await API.journeyGuides(userId);
    renderSavedGuides(guides);
    return guides;
  }

  async function loadSuggestions() {
    const [companyResult, roleResult] = await Promise.allSettled([
      API.companies(),
      API.jobDescriptions(),
    ]);
    companies = companyResult.status === "fulfilled" ? companyResult.value : [];
    roles = roleResult.status === "fulfilled" ? roleResult.value : [];
    $("companySuggestions").innerHTML = companies
      .map(company => `<option value="${PX.esc(company.name)}"></option>`).join("");
    $("roleSuggestions").innerHTML = roles
      .map(role => `<option value="${PX.esc(role.role_title)}"></option>`).join("");
    if (companyResult.status === "rejected" || roleResult.status === "rejected") {
      const failures = [companyResult, roleResult]
        .filter(result => result.status === "rejected")
        .map(result => result.reason);
      const databaseUnavailable = failures.some(error => error?.kind === "database_unavailable");
      const backendUnavailable = failures.some(error => error?.kind === "network");
      $("suggestionStatus").textContent =
        `${databaseUnavailable
          ? "The database is temporarily unavailable."
          : backendUnavailable
            ? "PlaceX could not be reached."
            : "Some optional suggestions could not be loaded."
        } You can still enter any company and role.`;
    } else {
      $("suggestionStatus").textContent =
        "Optional suggestions are from PlaceX records and do not restrict what you can enter.";
    }
  }

  function showJourneyError(error) {
    const retryButton = error.jobId
      ? `<button class="btn ghost sm" type="button" id="retryJourneyPoll">Retry status check</button>`
      : "";
    $("result").hidden = false;
    $("result").innerHTML = `<section class="glass empty">
      <h2>Could not complete the hiring journey</h2>
      <p>${PX.esc(errorMessage(error))}</p>${retryButton}
    </section>`;
    if (error.jobId) {
      $("retryJourneyPoll").addEventListener("click", async event => {
        event.currentTarget.disabled = true;
        submitting = true;
        button.disabled = true;
        const progress = PX.progress($("progress"), "Checking the submitted journey guide");
        try {
          const job = await API.waitForJob(error.jobId, progress.tick);
          progress.done();
          render(job.result || {});
          try {
            await loadSavedGuides();
          } catch (loadError) {
            showSavedGuidesError(loadError);
          }
          history.replaceState(null, "", "?job=" + job.id);
        } catch (retryError) {
          progress.done();
          if (retryError.kind !== "job") retryError.jobId = error.jobId;
          showJourneyError(retryError);
        } finally {
          submitting = false;
          button.disabled = false;
        }
      }, { once: true });
    }
  }

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submitting) return;
    const companyName = $("companyName").value.trim();
    const jobRole = $("jobRole").value.trim();
    if (!companyName || !jobRole) {
      PX.toast("Enter both a company name and a job role.", "error");
      return;
    }
    const payload = {
      company_name: companyName,
      job_role: jobRole,
      location: $("location").value.trim() || null,
      experience_level: $("experienceLevel").value.trim() || null,
      employment_type: $("employmentType").value.trim() || null,
      job_description: $("jobDescription").value.trim() || null,
    };
    submitting = true;
    button.disabled = true;
    button.textContent = "Generating your journey…";
    $("result").hidden = true;
    const progress = PX.progress($("progress"), "Retrieving PlaceX information and preparing guidance");
    try {
      const job = await API.generateJourneyGuide(payload, progress.tick);
      progress.done();
      render(job.result || {});
      try {
        await loadSavedGuides();
      } catch (error) {
        showSavedGuidesError(error);
      }
      history.replaceState(null, "", "?job=" + job.id);
    } catch (error) {
      progress.done();
      showJourneyError(error);
    } finally {
      submitting = false;
      button.disabled = false;
      button.textContent = "Generate Hiring Journey";
    }
  });

  const [savedResult] = await Promise.allSettled([loadSavedGuides(), loadSuggestions()]);
  if (savedResult.status === "rejected") {
    showSavedGuidesError(savedResult.reason);
  }

  const params = new URLSearchParams(location.search);
  const companyId = Number(params.get("company"));
  const jdId = Number(params.get("jd_id"));
  const linkedCompany = companies.find(company => company.id === companyId);
  const linkedRole = roles.find(role => role.id === jdId);
  if (linkedCompany) $("companyName").value = linkedCompany.name;
  if (linkedRole) $("jobRole").value = linkedRole.role_title;

  const guideId = Number(params.get("guide"));
  const jobId = Number(params.get("job"));
  try {
    if (guideId) {
      const saved = await API.journeyGuide(guideId);
      render({
        ...(saved.guidance || {}),
        guide_id: saved.id,
        created_at: saved.created_at,
      });
    } else if (jobId) {
      const job = await API.getJob(jobId);
      if (job.job_type === "JOURNEY_GUIDE" && job.status === "DONE") render(job.result || {});
    }
  } catch (error) {
    PX.toast(error.message, "error");
  }
})();
