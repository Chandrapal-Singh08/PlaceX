(async function () {
  const $ = id => document.getElementById(id);
  const uid = PX.session.userId(); if (!uid) return;
  const num = v => (v === null || v === undefined ? null : Number(v));

  const [dash, companies] = await Promise.all([
    API.dashboard(uid).catch(e => { PX.toast(e.message, "error"); return null; }),
    API.companies().catch(() => null)
  ]);

  if (companies) $("sCompanies").textContent = companies.length;

  if (dash && !dash.user_id) {
    $("notice").innerHTML = `<div class="alert"><b>User ${uid} doesn't exist in the database.</b> The dashboard needs a real user row. <a href="login.html?switch=1" style="text-decoration:underline">Sign in with an email</a> to create one.</div>`;
    $("hello").textContent = "Welcome 👋";
  } else if (dash) {
    const name = dash.full_name || PX.session.name();
    $("hello").textContent = "Hi, " + (name ? name.split(" ")[0] : "there") + " 👋";
    if (dash.full_name) PX.session.start(uid, dash.full_name, PX.session.email());
    $("sResumes").textContent = dash.resumes ?? 0;
    $("sReports").textContent = dash.reports ?? 0;
    $("sItems").textContent = `${dash.roadmap_items_done ?? 0}/${dash.roadmap_items_total ?? 0}`;
    $("sItemsSub").textContent = "done / total";

    const latest = num(dash.latest_ats), best = num(dash.best_ats);
    if (latest !== null) {
      $("atsVal").textContent = Math.round(latest) + "%"; $("atsLbl").textContent = "Latest ATS score";
      $("ringFg").style.strokeDashoffset = 326.7 * (1 - Math.min(100, latest) / 100);
      if (best !== null) $("atsSub").textContent = `Best so far: ${Math.round(best)}%`;
    } else {
      $("atsSub").innerHTML = `<a href="analyze.html" style="color:var(--cyan);font-weight:700">Analyze a resume</a> to get your first score.`;
    }

    const total = dash.roadmap_items_total ?? 0, pct = num(dash.roadmap_progress_pct) ?? 0;
    $("prep").innerHTML = total
      ? `<div style="display:flex;justify-content:space-between"><span class="muted">${dash.roadmap_items_done} of ${total} skills done</span><b>${Math.round(pct)}%</b></div><div class="meter"><i style="width:${pct}%"></i></div><a class="btn ghost sm" href="prep-plan.html">Open prep plan</a>`
      : `<div class="empty">No data available yet. <a href="prep-plan.html">Generate a prep plan</a> after analyzing a resume.</div>`;
    if (dash.pending_jobs) $("notice").innerHTML = `<div class="alert" style="border-color:rgba(96,165,250,.4);background:rgba(96,165,250,.08)">${dash.pending_jobs} job(s) are still queued or running.</div>`;
  }

  /* Recent activity: jobs submitted from this browser, read back live from GET /jobs/{id}. */
  const hist = PX.history.all().slice(0, 6);
  const jobs = (await Promise.all(hist.map(h => API.getJob(h.id).catch(() => null)))).filter(Boolean);
  $("activity").innerHTML = jobs.length ? jobs.map(j => `
    <div class="act"><div><b>${PX.esc(PX.TYPE_LABEL[j.job_type] || j.job_type)}</b><small>Job #${j.id} · ${PX.timeAgo(j.created_at)}</small></div>${PX.badge(j.status)}
    ${j.status === "DONE" ? `<a class="go" href="${PX.TYPE_PAGE[j.job_type]}?job=${j.id}">View</a>` : ""}</div>`).join("")
    : `<div class="empty">No data available yet. <a href="analyze.html">Analyze your first resume</a>.</div>`;

  const lastAn = jobs.find(j => j.job_type === "RESUME_ANALYSIS" && j.status === "DONE");
  if (lastAn) { $("qAnalyze").href = "analysis-result.html?job=" + lastAn.id; PX.history.setLastReport(lastAn.result.report_id); }

  /* Upcoming journey: the most recent finished JOURNEY_GUIDE job. */
  const jg = jobs.find(j => j.job_type === "JOURNEY_GUIDE" && j.status === "DONE");
  $("journey").innerHTML = jg && jg.result.rounds?.length
    ? jg.result.rounds.map(r => `<div class="mini-round"><span class="n">${r.no}</span><div><b>${PX.esc(r.name)}</b><br><small class="muted">${PX.esc(r.type || "")} · ${r.minutes ?? "?"} min</small></div></div>`).join("") + `<a class="btn ghost sm" href="journey-guide.html?job=${jg.id}">Open journey guide</a>`
    : `<div class="empty">No data available yet. <a href="journey-guide.html">Generate a journey guide</a> for a company.</div>`;

  const safeHttpUrl = value => {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  };
  const moduleLinks = job => {
    if (!job.jd_id) return "";
    const jd = encodeURIComponent(job.jd_id);
    const company = job.company_id ? `&company=${encodeURIComponent(job.company_id)}` : "";
    return `<a href="analyze.html?mode=jd&jd_id=${jd}">Analyze</a>
      <a href="build-resume.html?jd_id=${jd}">Tailor resume</a>
      <a href="prep-plan.html?jd_id=${jd}">Plan preparation</a>
      ${job.company_id ? `<a href="journey-guide.html?company=${encodeURIComponent(job.company_id)}&jd_id=${jd}">Interview guide</a>` : ""}`;
  };
  const opportunityCard = (job, recommendation = false) => {
    const url = safeHttpUrl(job.source_url);
    const category = recommendation
      ? `<span class="chip">${PX.esc((job.category || "MATCH").replaceAll("_", " "))} · ${Math.round(Number(job.match_score) || 0)}% match</span>`
      : `<span class="chip">${job.is_verified ? "Verified by source" : "Source-reported; verify listing"}</span>`;
    const skills = recommendation
      ? `<p><strong>Matching skills:</strong> ${PX.esc((job.matching_skills || []).join(", ") || "No direct skill matches identified")}</p>
        ${(job.partially_matched_skills || []).length ? `<p><strong>Potentially related evidence:</strong> ${PX.esc(job.partially_matched_skills.map(item => `${item.skill} (${Math.round(item.similarity * 100)}%)`).join(", "))}</p>` : ""}
        <p><strong>Skill gaps:</strong> ${PX.esc((job.missing_skills || []).join(", ") || "No skill gaps identified")}</p>
        <p><strong>Eligibility:</strong> ${PX.esc(job.eligibility_information?.status || "not assessed")}</p>
        ${job.matching_basis === "skills_only_preliminary" ? `<p class="muted">Preliminary skill-only estimate. Run the Analyzer for experience, project and eligibility evidence.</p>` : ""}
        ${job.matching_details?.score_explanation ? `<p class="muted">${PX.esc(job.matching_details.score_explanation)}</p>` : ""}
        ${(job.reasons || []).map(reason => `<p class="muted">${PX.esc(reason)}</p>`).join("")}`
      : "";
    const savedControl = recommendation
      ? `<button class="btn ghost sm" type="button" data-dismiss-recommendation="${PX.esc(job.recommendation_id)}">Dismiss</button>`
      : "";
    return `<article class="glass opportunity-card">
      <h3>${PX.esc(job.role_title)}</h3>
      <p>${PX.esc(job.company_name)}${job.location ? ` · ${PX.esc(job.location)}` : ""}${job.is_remote ? " · Remote" : ""}</p>
      <div class="opportunity-meta">${category}
        ${recommendation ? `<span class="chip">${job.is_verified ? "Verified by source" : "Source-reported; verify listing"}</span>` : ""}
        ${job.employment_type ? `<span class="chip">${PX.esc(job.employment_type)}</span>` : ""}
        ${job.experience_level ? `<span class="chip">${PX.esc(job.experience_level)}</span>` : ""}
      </div>
      ${skills}
      <small class="muted">Source: ${PX.esc(job.source_name || "Not recorded")} · Retrieved ${PX.esc(job.retrieved_at ? new Date(job.retrieved_at).toLocaleDateString() : "date unavailable")}</small>
      <div class="opportunity-actions">
        ${url ? `<a href="${PX.esc(url)}" target="_blank" rel="noopener noreferrer">Open source listing</a>` : ""}
        ${moduleLinks(job)}
        <button class="btn sm" type="button" data-save-job="${PX.esc(job.id)}">Save opportunity</button>
        ${savedControl}
      </div>
    </article>`;
  };

  async function loadRecommendations() {
    $("recommendations").innerHTML = `<p class="muted">Loading recommendations…</p>`;
    try {
      const result = await API.recommendations(uid);
      $("recommendationMessage").textContent = result.message || "";
      $("recommendations").innerHTML = result.recommendations?.length
        ? result.recommendations.map(job => opportunityCard(job, true)).join("")
        : `<div class="empty">${PX.esc(result.message || "No recommendations yet. Search and discover jobs to build your personalized matches.")}</div>`;
      $("recommendations").querySelectorAll("[data-dismiss-recommendation]").forEach(button => {
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            await API.dismissRecommendation(uid, Number(button.dataset.dismissRecommendation));
            await loadRecommendations();
          } catch (error) {
            button.disabled = false;
            PX.toast(error.message, "error");
          }
        });
      });
    } catch (error) {
      $("recommendationMessage").textContent = error.message;
      $("recommendations").innerHTML = `<div class="empty">Recommendations could not be loaded.</div>`;
    }
  }

  async function loadApplications() {
    try {
      const [items, dashboard, resumes] = await Promise.all([
        API.applications(uid), API.placementDashboard(uid), API.resumes(uid)
      ]);
      const stats = dashboard.statistics || {};
      const upcoming = dashboard.upcoming_actions || [];
      $("upcomingActions").innerHTML = upcoming.length
        ? upcoming.map(action => `<div class="glass opportunity-card">
            <strong>${PX.esc(action.role_title || "Application")}</strong> · ${PX.esc(action.company_name || "Company unavailable")}
            <p class="muted">${PX.esc(action.status.replaceAll("_", " "))} · ${PX.esc(new Date(action.next_action_at).toLocaleString())}</p>
          </div>`).join("")
        : `<p class="muted">No upcoming actions scheduled. Add a next-action date to a tracked opportunity.</p>`;
      $("applicationStats").innerHTML = [
        ["Saved", stats.saved], ["Applied", stats.applied],
        ["Interviews", (stats.online_assessment || 0) + (stats.technical_interview || 0) + (stats.hr_interview || 0)],
        ["Offers", stats.offers]
      ].map(([label, value]) => `<div class="glass stat"><span>${label}</span><b>${Number(value || 0)}</b></div>`).join("");
      const next = {
        SAVED: ["APPLIED"],
        APPLIED: ["ONLINE_ASSESSMENT", "TECHNICAL_INTERVIEW", "HR_INTERVIEW", "REJECTED", "OFFER_RECEIVED"],
        ONLINE_ASSESSMENT: ["TECHNICAL_INTERVIEW", "HR_INTERVIEW", "REJECTED", "OFFER_RECEIVED"],
        TECHNICAL_INTERVIEW: ["HR_INTERVIEW", "REJECTED", "OFFER_RECEIVED"],
        HR_INTERVIEW: ["REJECTED", "OFFER_RECEIVED"],
        REJECTED: [],
        OFFER_RECEIVED: []
      };
      const localDateTime = value => value ? new Date(value).toISOString().slice(0, 16) : "";
      $("applications").innerHTML = items.length ? items.map(item => `
        <article class="glass application-card" data-application="${PX.esc(item.id)}">
          <h3>${PX.esc(item.role_title || "Job opportunity")}</h3>
          <p>${PX.esc(item.company_name || "Company unavailable")} · ${PX.esc(item.status.replaceAll("_", " "))}</p>
          ${item.source_url && safeHttpUrl(item.source_url) ? `<a href="${PX.esc(safeHttpUrl(item.source_url))}" target="_blank" rel="noopener noreferrer">Open listing source</a>` : ""}
          <div class="application-fields">
            <label>Application status
              <select data-status>
                <option value="${PX.esc(item.status)}" selected>${PX.esc(item.status.replaceAll("_", " "))}</option>
                ${(next[item.status] || []).map(status => `<option value="${status}">${status.replaceAll("_", " ")}</option>`).join("")}
              </select>
            </label>
            <label>Application date
              <input type="datetime-local" data-applied value="${PX.esc(localDateTime(item.applied_at))}">
            </label>
            <label>Next action
              <input type="datetime-local" data-next-action value="${PX.esc(localDateTime(item.next_action_at))}">
            </label>
            <label>Resume version
              <select data-resume><option value="">No resume selected</option>
                ${resumes.map(resume => `<option value="${PX.esc(resume.id)}" ${item.related_resume_id === resume.id ? "selected" : ""}>${PX.esc(resume.title)}</option>`).join("")}
              </select>
            </label>
            <label class="wide">Notes
              <textarea data-notes maxlength="5000">${PX.esc(item.notes || "")}</textarea>
            </label>
            <label>Interview type
              <input data-interview-kind maxlength="100" placeholder="Technical interview">
            </label>
            <label>Interview date
              <input type="datetime-local" data-interview-date>
            </label>
            <label class="wide">Interview notes
              <input data-interview-notes maxlength="2000" placeholder="Preparation notes or follow-up">
            </label>
          </div>
          ${(item.interview_details || []).length ? `<ul>${item.interview_details.map(interview => `<li>${PX.esc(interview.kind)} · ${PX.esc(interview.scheduled_at)}${interview.notes ? ` — ${PX.esc(interview.notes)}` : ""}</li>`).join("")}</ul>` : ""}
          <div class="application-actions">
            <button class="btn ghost sm" type="button" data-save-application>Save tracker details</button>
            <button class="btn ghost sm" type="button" data-add-interview>Add interview</button>
            <button class="btn ghost sm" type="button" data-show-events>Activity history</button>
          </div>
          <div data-events class="application-events" hidden></div>
        </article>`).join("") : `<div class="empty">No saved or tracked opportunities yet. Save a job above to start your application journey.</div>`;

      $("applications").querySelectorAll("[data-application]").forEach(card => {
        const appId = Number(card.dataset.application);
        const update = async payload => {
          const button = card.querySelector("[data-save-application]");
          if (button) button.disabled = true;
          try {
            await API.updateApplication(appId, payload);
            PX.toast("Application details saved.", "success");
            await loadApplications();
          } catch (error) {
            PX.toast(error.message, "error");
            if (button) button.disabled = false;
          }
        };
        card.querySelector("[data-save-application]").addEventListener("click", () => {
          const date = card.querySelector("[data-applied]").value;
          const nextDate = card.querySelector("[data-next-action]").value;
          const resumeId = card.querySelector("[data-resume]").value;
          update({
            status: card.querySelector("[data-status]").value,
            applied_at: date ? new Date(date).toISOString() : null,
            next_action_at: nextDate ? new Date(nextDate).toISOString() : null,
            related_resume_id: resumeId ? Number(resumeId) : null,
            notes: card.querySelector("[data-notes]").value
          });
        });
        card.querySelector("[data-add-interview]").addEventListener("click", async () => {
          const kind = card.querySelector("[data-interview-kind]").value.trim();
          const at = card.querySelector("[data-interview-date]").value;
          if (!kind || !at) return PX.toast("Add an interview type and date.", "error");
          const existing = (items.find(item => item.id === appId).interview_details || []);
          await update({
            interview_details: [...existing, {
              kind,
              scheduled_at: new Date(at).toISOString(),
              notes: card.querySelector("[data-interview-notes]").value.trim()
            }]
          });
        });
        card.querySelector("[data-show-events]").addEventListener("click", async event => {
          const host = card.querySelector("[data-events]");
          event.currentTarget.disabled = true;
          try {
            const history = await API.applicationEvents(appId);
            host.hidden = false;
            host.innerHTML = history.length
              ? `<ul>${history.map(entry => `<li>${PX.esc(new Date(entry.occurred_at).toLocaleString())}: ${PX.esc(entry.event_type.replaceAll("_", " "))}${entry.to_status ? ` · ${PX.esc(entry.to_status.replaceAll("_", " "))}` : ""}${entry.notes ? ` — ${PX.esc(entry.notes)}` : ""}</li>`).join("")}</ul>`
              : `<p class="muted">No activity recorded.</p>`;
          } catch (error) {
            PX.toast(error.message, "error");
          } finally {
            event.currentTarget.disabled = false;
          }
        });
      });
    } catch (error) {
      $("applications").innerHTML = `<div class="empty">Application tracker could not be loaded: ${PX.esc(error.message)}</div>`;
    }
  }

  $("jobSearch").addEventListener("submit", async event => {
    event.preventDefault();
    const button = $("searchJobs");
    button.disabled = true;
    $("jobSearchMessage").textContent = "Searching the configured job source…";
    $("jobListings").innerHTML = "";
    try {
      const result = await API.discoverJobs({
        query: $("jobQuery").value.trim() || null,
        company: $("jobCompany").value.trim() || null,
        location: $("jobLocation").value.trim() || null,
        remote: $("jobRemote").checked ? true : null,
        limit: 20
      });
      $("jobSearchMessage").textContent = result.provider_configured
        ? `${result.fetched} source listing(s) retrieved from ${result.provider}.`
        : result.message || "No live job source is configured.";
      $("jobListings").innerHTML = result.jobs?.length
        ? result.jobs.map(job => opportunityCard(job)).join("")
        : `<div class="empty">${PX.esc(result.message || "No jobs matched those filters.")}</div>`;
      $("jobListings").querySelectorAll("[data-save-job]").forEach(save => {
        save.addEventListener("click", async () => {
          save.disabled = true;
          try {
            await API.createApplication(uid, { external_job_id: Number(save.dataset.saveJob) });
            PX.toast("Opportunity saved to your tracker.", "success");
            await loadApplications();
          } catch (error) {
            save.disabled = false;
            PX.toast(error.message, "error");
          }
        });
      });
    } catch (error) {
      $("jobSearchMessage").textContent = error.message;
      $("jobListings").innerHTML = `<div class="empty">Job search failed. Check the source configuration and try again.</div>`;
    } finally {
      button.disabled = false;
    }
  });

  $("jobPreferences").addEventListener("submit", async event => {
    event.preventDefault();
    const split = id => $(id).value.split(",").map(value => value.trim()).filter(Boolean).slice(0, 20);
    try {
      await API.saveJobPreferences(uid, {
        preferred_roles: split("preferredRoles"),
        preferred_locations: split("preferredLocations"),
        remote_preference: $("remotePreference").value,
        employment_types: [],
        experience_level: null
      });
      PX.toast("Job preferences saved.", "success");
      await loadRecommendations();
    } catch (error) {
      PX.toast(error.message, "error");
    }
  });

  const initialLoad = async () => {
    try {
      const preferences = await API.jobPreferences(uid);
      $("preferredRoles").value = (preferences.preferred_roles || []).join(", ");
      $("preferredLocations").value = (preferences.preferred_locations || []).join(", ");
      $("remotePreference").value = preferences.remote_preference || "ANY";
    } catch (error) {
      $("recommendationMessage").textContent = error.message;
    }
    await Promise.all([loadRecommendations(), loadApplications()]);
  };
  void initialLoad();
})();
