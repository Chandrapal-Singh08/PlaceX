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
})();
