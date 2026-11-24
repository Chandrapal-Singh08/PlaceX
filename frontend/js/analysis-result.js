(async function () {
  const $ = id => document.getElementById(id);
  const uid = PX.session.userId(); if (!uid) return;
  const id = new URLSearchParams(location.search).get("job");
  if (!id) { $("sub").textContent = ""; $("error").innerHTML = `<div class="empty">No analysis selected. <a href="analyze.html">Analyze a resume</a> first.</div>`; return; }
  let job;
  try {
    const progress = PX.progress($("progress"), "Loading analysis");
    job = await API.waitForJob(id, progress.tick); progress.done();
  } catch (error) {
    $("progress").hidden = true; $("sub").textContent = "";
    $("error").innerHTML = `<div class="alert">${PX.esc(error.message)}</div>`;
    return;
  }
  if (job.job_type !== "RESUME_ANALYSIS") { $("error").innerHTML = `<div class="alert">That job is not a resume analysis.</div>`; return; }

  const result = job.result || {};
  const general = result.mode === "GENERAL";
  const tips = result.suggestions || [];
  const score = Number(result.ats_score ?? 0);
  const matched = result.matched || [], missing = result.missing || [];
  const total = matched.length + missing.length;
  const chips = (items, kind) => items.length
    ? `<div class="chips">${items.map(value => `<span class="chip ${kind}">${PX.esc(value)}</span>`).join("")}</div>`
    : `<p class="muted">None</p>`;
  const overview = items => {
    $("overview").innerHTML = items.map(([label, value]) =>
      `<div class="glass ov"><b>${PX.esc(value)}</b><span>${PX.esc(label)}</span></div>`).join("");
  };
  const renderMatchingReport = report => {
    if (!report) return;
    const labels = {
      exact_skill_match: "Exact skill match",
      semantic_skill_match: "Semantic skill match",
      experience_match: "Experience match",
      project_relevance: "Project relevance",
      responsibility_match: "Responsibility match",
      eligibility_match: "Education and seniority",
    };
    const scoreText = value => value == null ? "Not assessed" : `${Number(value).toFixed(1)}%`;
    const components = Object.entries(report.components || {}).map(([key, item]) =>
      `<article class="glass tip"><b>${PX.esc(labels[key] || key)}</b>
        <p>${PX.esc(scoreText(item.score))}</p>
        <small class="muted">${PX.esc(item.explanation || item.status || "")}</small></article>`
    ).join("");
    const requirements = (report.requirements || []).map(item => {
      const evidence = (item.evidence || []).map(source =>
        `<p><small class="muted">${PX.esc(source.source === "resume" ? "Resume evidence" : "Job requirement")}:</small>
        ${PX.esc(source.quote)}</p>`
      ).join("");
      return `<article class="tip"><b>${PX.esc(item.requirement)}</b>
        <span class="chip ${item.status === "matched" ? "ok" : item.status === "not_evidenced" ? "bad" : ""}">${PX.esc(String(item.status || "not_assessed").replace(/_/g, " "))}</span>
        ${evidence}</article>`;
    }).join("");
    const partial = (report.partially_matched_skills || []).map(item =>
      `<li><b>${PX.esc(item.skill)}</b> · ${PX.esc((Number(item.similarity) * 100).toFixed(1))}% semantic similarity
       <p class="muted">${PX.esc(item.evidence?.quote || "")}</p></li>`
    ).join("");
    const extractionStatus = report.evidence_extraction_status || "unavailable";
    const research = report.external_research || {};
    $("matchingReport").hidden = false;
    $("matchingReport").innerHTML = `
      <h3>Overall match: ${PX.esc(scoreText(report.overall_match_score))}</h3>
      <p class="muted">${PX.esc(report.score_explanation || "")}</p>
      <p class="muted">Structured evidence extraction: ${PX.esc(extractionStatus)}. Quoted evidence is checked against the supplied resume and job description; unavailable dimensions are excluded from the score.</p>
      <div class="grid g3">${components}</div>
      ${partial ? `<h3>Potential partial skill matches</h3><ul>${partial}</ul>` : ""}
      ${requirements ? `<h3>Requirement evidence</h3><div class="tips">${requirements}</div>` : ""}
      <section class="glass tip">
        <h3>External role research · ${PX.esc(research.status || "not available")}</h3>
        <p class="muted">External research informs explanations only; it does not establish resume evidence or change matching scores. ${PX.esc(research.notice || "")}</p>
        ${(research.sources || []).map(source => `<p>
          <a href="${PX.esc(source.url)}" target="_blank" rel="noopener noreferrer">${PX.esc(source.title || source.source_domain || "Research source")}</a>
          · ${PX.esc(source.source_classification || "secondary source")}
          · Retrieved ${PX.esc(source.retrieved_at || "date unavailable")}
        </p>`).join("") || `<p class="muted">No external sources were available.</p>`}
      </section>
      <p class="muted">${PX.esc(report.eligibility_note || "")}</p>`;
  };

  $("score").textContent = Math.round(score) + "%";
  $("result").hidden = false;
  requestAnimationFrame(() => { $("fg").style.strokeDashoffset = 326.7 * (1 - Math.min(100, score) / 100); });
  $("tips").innerHTML = tips.length
    ? tips.map((tip, index) => `<div class="glass tip"><i>${index + 1}</i><p>${PX.esc(typeof tip === "string" ? tip : JSON.stringify(tip))}</p></div>`).join("")
    : `<div class="empty">No suggestions for this report.</div>`;
  if (result.ai_notice) {
    $("tips").innerHTML += `<div class="alert">${PX.esc(result.ai_notice)}</div>`;
  }
  if (result.ai_explanations?.length) {
    $("tips").innerHTML += `<section class="glass tip"><h3>AI explanations for skill gaps</h3>
      <p class="muted">These suggestions do not change your deterministic ATS score.</p>
      ${result.ai_explanations.map(item => `<article><b>${PX.esc(item.skill)}</b><p>${PX.esc(item.reason)}</p><p>${PX.esc(item.action)}</p></article>`).join("")}</section>`;
  }
  if (result.ai_suggestions?.length) {
    $("tips").innerHTML += `<section class="glass tip"><h3>AI resume improvements</h3>
      ${result.ai_suggestions.map(item => `<p>${PX.esc(item)}</p>`).join("")}</section>`;
  }
  if (result.potentially_relevant_skills?.length) {
    $("tips").innerHTML += `<section class="glass tip"><h3>Potentially relevant skills to verify</h3>
      <p>${PX.esc(result.potentially_relevant_skills.join(", "))}</p>
      <small class="muted">These are not counted as matched skills or included in the ATS score.</small></section>`;
  }

  if (general) {
    $("matchingReport").hidden = true;
    $("score").nextElementSibling.textContent = "General readiness score";
    const categories = result.by_category || {};
    const missingCategories = result.missing_categories || [];
    const covered = Object.keys(categories).length;
    const areaCount = covered + missingCategories.length;
    $("sub").textContent = "General ATS score · not targeted at a specific job";
    $("verdict").textContent = score >= 75 ? "Well-rounded resume" : score >= 40 ? "Partly covered" : "Needs more skills shown";
    $("verdictSub").textContent = result.score_explanation ||
      `Your resume shows skills in ${covered} of ${areaCount} core areas.`;
    $("toPrep").hidden = true;
    $("skills").previousElementSibling.querySelector("h2").textContent = "Skills by area";
    const actions = $("toPrep").parentElement;
    let compare = actions.querySelector("#toJd");
    if (!compare) {
      compare = document.createElement("a");
      compare.id = "toJd";
      compare.className = "btn";
      compare.href = "analyze.html?mode=jd";
      compare.textContent = "Compare with a job description";
      actions.prepend(compare);
    }
    overview([["Skills found", (result.found || []).length], ["Areas covered", covered], ["Areas missing", missingCategories.length], ["Suggestions", tips.length]]);
    $("skills").innerHTML = Object.entries(categories).map(([category, names]) =>
      `<div class="cat"><b>${PX.esc(category.replace(/_/g, " "))}</b>${chips(names, "ok")}</div>`).join("")
      + (missingCategories.length ? `<div class="cat"><b>Not detected</b>${chips(missingCategories, "bad")}</div>` : "")
      || `<p class="muted">No known skills were detected in this resume.</p>`;
    return;
  }

  $("score").nextElementSibling.textContent = "Exact-skill ATS baseline";
  renderMatchingReport(result.matching_report);
  PX.history.setLastReport(result.report_id);
  $("sub").textContent = `Report #${result.report_id} · job #${job.id}`;
  $("verdict").textContent = score >= 75 ? "Strong match" : score >= 50 ? "Partial match" : "Needs work";
  $("verdictSub").textContent = total ? `Your resume covers ${matched.length} of ${total} skills for this role.` : "No skills are listed for this job description.";
  $("toPrep").href = "prep-plan.html?report=" + result.report_id;

  const coverage = total ? Math.round(100 * matched.length / total) : null;
  overview([["Skills found", matched.length], ["Missing skills", missing.length], ["Suggestions", tips.length], ["JD coverage", coverage === null ? "–" : coverage + "%"]]);
  const gap = result.gap || [];
  const percent = count => total ? Math.round(100 * count / total) : 0;
  const rows = isMatched => gap.filter(item => item.matched === isMatched)
    .sort((a, b) => b.importance - a.importance)
    .map(item => `<div class="imp"><span>${PX.esc(item.skill)}</span><div class="meter ${isMatched ? "ok" : "bad"}"><i style="width:${item.importance * 20}%"></i></div><small>importance ${item.importance}/5${item.required ? " · required" : ""}</small></div>`).join("");
  $("skills").innerHTML = gap.length
    ? `<div class="skillrow"><div class="hd"><span>Matched skills</span><span>${matched.length} of ${total} · ${percent(matched.length)}%</span></div><div class="meter ok"><i style="width:${percent(matched.length)}%"></i></div>${rows(true) || `<p class="muted">None</p>`}</div>
       <div class="skillrow" style="margin:0"><div class="hd"><span>Missing skills</span><span>${missing.length} of ${total} · ${percent(missing.length)}%</span></div><div class="meter bad"><i style="width:${percent(missing.length)}%"></i></div>${rows(false) || `<p class="muted">None</p>`}</div>`
    : `<div class="skillrow"><div class="hd"><span>Matched skills</span></div>${chips(matched, "ok")}</div><div class="skillrow"><div class="hd"><span>Missing skills</span></div>${chips(missing, "bad")}</div>`;
})();
