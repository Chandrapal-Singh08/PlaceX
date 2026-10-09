(async function () {
  const $ = id => document.getElementById(id);
  const uid = PX.session.userId(); if (!uid) return;
  const qs = new URLSearchParams(location.search);
  const companies = await PX.loadCompanies();
  const byId = Object.fromEntries(companies.map(c => [c.id, c]));
  const jd = JDInput.mount($("jd"), { title: "Provide the job description" });

  function unavailable(message) {
    $("picker").hidden = false;
    $("result").hidden = false;
    $("result").innerHTML = `<div class="glass empty" style="margin-top:1rem"><b>No hiring-process data available.</b><br>${PX.esc(message)}<br><a href="journey-guide.html">Try another job description</a></div>`;
  }

  function render(job) {
    const r = job.result || {}, rounds = r.rounds || [], co = byId[r.company_id];
    $("result").hidden = false;
    if (!rounds.length) return unavailable("No configured hiring rounds were returned for this company.");
    $("result").innerHTML = `<p class="muted" style="margin-top:1.4rem">${PX.esc(co ? co.name : "Company #" + r.company_id)} · ${r.total_rounds} round${r.total_rounds === 1 ? "" : "s"}</p>
      <div class="timeline">${rounds.map(x => `<article class="glass hover round"><span class="num">${String(x.no).padStart(2, "0")}</span>
        <h3>${PX.esc(x.name)}</h3><div class="meta">${x.type ? `<span class="chip">${PX.esc(String(x.type).replace(/_/g, " "))}</span>` : ""}${x.minutes ? `<span class="chip">${x.minutes} min</span>` : ""}</div>
        <dl>${x.what_to_expect ? `<div><dt>What to expect</dt><dd>${PX.esc(x.what_to_expect)}</dd></div>` : ""}${x.tips ? `<div class="tipbox"><dt>Tip</dt><dd>${PX.esc(x.tips)}</dd></div>` : ""}</dl></article>`).join("")}</div>`;
  }

  async function run(payloadFactory) {
    $("result").hidden = true;
    const p = PX.progress($("progress"), "Preparing the hiring journey");
    try {
      const payload = await payloadFactory(p);
      if (!payload) { p.done(); return; }
      p.label("Loading configured hiring rounds");
      const job = await API.runJob(uid, "JOURNEY_GUIDE", payload, p.tick, 5);
      p.done(); render(job); history.replaceState(null, "", "?job=" + job.id); PX.toast("Journey guide ready.", "success");
    } catch (e) {
      p.done();
      if (/no hiring-round data/i.test(e.message)) return unavailable("No hiring rounds are configured for this company.");
      PX.toast(e.kind === "job" ? "Job failed: " + e.message : e.message, "error", 7000);
    }
  }

  $("go").addEventListener("click", () => {
    if (!jd.isFilled()) return PX.toast("Choose or provide a job description first.", "error");
    run(async progress => {
      progress.label("Reading the job description");
      const value = await jd.resolve();
      if (value.company_matched === false) {
        unavailable("This job description does not name a company with hiring data in PlaceX.");
        return null;
      }
      return { jd_id: value.id };
    });
  });

  const q = qs.get("job");
  if (q) { try { const j = await API.getJob(q); if (j.job_type === "JOURNEY_GUIDE" && j.status === "DONE") render(j); } catch (e) { PX.toast(e.message, "error"); } }
  else if (qs.get("company")) run(async () => ({ company_id: Number(qs.get("company")) }));
})();
