(async function () {
  const $ = id => document.getElementById(id);
  const uid = PX.session.userId();
  if (!uid) return;

  const companySelect = $("company");
  const roleSelect = $("role");
  const roleOptions = [];
  const companiesById = new Map();

  function render(result) {
    const rounds = [...(result.rounds || [])].sort((a, b) => a.no - b.no);
    $("result").hidden = false;
    if (!result.data_available || !rounds.length) {
      $("result").innerHTML = `<section class="glass empty">
        <h2>${PX.esc(result.company_name || "Company")}</h2>
        ${result.role_title ? `<p>Selected role: ${PX.esc(result.role_title)}</p>` : ""}
        <p>${PX.esc(result.note || "No hiring-round information is currently stored for this company.")}</p>
        <p class="muted">This means PlaceX has no round records to show; it does not mean the company has no hiring process.</p>
      </section>`;
      return;
    }

    const missing = "Not provided in the stored hiring-round data.";
    $("result").innerHTML = `<section class="glass">
      <h2>${PX.esc(result.company_name)}</h2>
      ${result.role_title ? `<p>Role: <strong>${PX.esc(result.role_title)}</strong></p>` : ""}
      <p class="muted">Showing ${rounds.length} configured hiring round${rounds.length === 1 ? "" : "s"} in sequence. These records may not reflect current company requirements.</p>
      <div class="timeline">${rounds.map(round => {
        const type = round.type ? round.type.replaceAll("_", " ") : null;
        return `<article class="glass hover round">
          <span class="num">${PX.esc(round.no)}</span>
          <h3>${PX.esc(round.name || "Unnamed round")}</h3>
          <div class="meta">
            <span class="chip">${type ? PX.esc(type) : "Round type not provided"}</span>
            <span class="chip">${round.minutes ? `${PX.esc(round.minutes)} min` : "Duration not provided"}</span>
          </div>
          <dl>
            <div><dt>Description / expectations</dt><dd>${PX.esc(round.what_to_expect || missing)}</dd></div>
            <div class="tipbox"><dt>Preparation tips</dt><dd>${PX.esc(round.tips || missing)}</dd></div>
          </dl>
        </article>`;
      }).join("")}</div>
    </section>`;
  }

  function updateRoles() {
    const companyId = Number(companySelect.value);
    const roles = roleOptions.filter(role => role.company_id === companyId);
    roleSelect.innerHTML = `<option value="">All company roles</option>` +
      roles.map(role => `<option value="${role.id}">${PX.esc(role.role_title)}</option>`).join("");
    roleSelect.disabled = !companyId || roles.length === 0;
    $("go").disabled = !companyId;
  }

  try {
    const [companies, jobDescriptions] = await Promise.all([
      API.companies(),
      API.jobDescriptions()
    ]);
    companies.forEach(company => companiesById.set(company.id, company));
    roleOptions.push(...jobDescriptions);
    companySelect.innerHTML = `<option value="">Choose a company</option>` +
      companies.map(company => `<option value="${company.id}">${PX.esc(company.name)}</option>`).join("");
    if (!companies.length) {
      companySelect.innerHTML = `<option value="">No companies are available</option>`;
      $("result").hidden = false;
      $("result").innerHTML = `<section class="glass empty">No company records are currently available in PlaceX.</section>`;
    }
  } catch (error) {
    companySelect.innerHTML = `<option value="">Could not load companies</option>`;
    $("result").hidden = false;
    $("result").innerHTML = `<section class="glass empty">${PX.esc(error.message)}</section>`;
  }

  companySelect.addEventListener("change", updateRoles);
  $("go").addEventListener("click", async () => {
    const companyId = Number(companySelect.value);
    if (!companyId || !companiesById.has(companyId)) return;
    const payload = { company_id: companyId };
    if (roleSelect.value) payload.jd_id = Number(roleSelect.value);

    $("result").hidden = true;
    const progress = PX.progress($("progress"), "Loading configured hiring rounds");
    try {
      const job = await API.runJob(uid, "JOURNEY_GUIDE", payload, progress.tick, 5);
      progress.done();
      render(job.result || {});
      history.replaceState(null, "", "?job=" + job.id);
    } catch (error) {
      progress.done();
      PX.toast(error.kind === "job" ? "Could not load this hiring process: " + error.message : error.message, "error", 7000);
      $("result").hidden = false;
      $("result").innerHTML = `<section class="glass empty">${PX.esc(error.message)}</section>`;
    }
  });

  const jobId = new URLSearchParams(location.search).get("job");
  if (jobId) {
    try {
      const job = await API.getJob(jobId);
      if (job.job_type !== "JOURNEY_GUIDE" || job.status !== "DONE") return;
      const result = job.result || {};
      if (result.company_id && companiesById.has(result.company_id)) {
        companySelect.value = String(result.company_id);
        updateRoles();
        if (result.jd_id) roleSelect.value = String(result.jd_id);
      }
      render(result);
    } catch (error) {
      PX.toast(error.message, "error");
    }
  } else {
    const companyId = Number(new URLSearchParams(location.search).get("company"));
    if (companyId && companiesById.has(companyId)) {
      companySelect.value = String(companyId);
      updateRoles();
      $("go").click();
    }
  }
})();
