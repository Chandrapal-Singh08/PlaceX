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

  $("score").textContent = Math.round(score) + "%";
  $("result").hidden = false;
  requestAnimationFrame(() => { $("fg").style.strokeDashoffset = 326.7 * (1 - Math.min(100, score) / 100); });
  $("tips").innerHTML = tips.length
    ? tips.map((tip, index) => `<div class="glass tip"><i>${index + 1}</i><p>${PX.esc(typeof tip === "string" ? tip : JSON.stringify(tip))}</p></div>`).join("")
    : `<div class="empty">No suggestions for this report.</div>`;

  if (general) {
    const categories = result.by_category || {};
    const missingCategories = result.missing_categories || [];
    const covered = Object.keys(categories).length;
    const areaCount = covered + missingCategories.length;
    $("sub").textContent = "General ATS score · not targeted at a specific job";
    $("verdict").textContent = score >= 75 ? "Well-rounded resume" : score >= 40 ? "Partly covered" : "Needs more skills shown";
    $("verdictSub").textContent = `Your resume shows skills in ${covered} of ${areaCount} core areas.`;
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
