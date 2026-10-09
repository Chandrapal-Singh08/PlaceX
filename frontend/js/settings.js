(async function () {
  const $ = id => document.getElementById(id);
  $("sess").innerHTML = `<p>User ID: <b>${PX.session.userId()}</b></p><p>Name: <b>${PX.esc(PX.session.name() || "–")}</b></p><p>Email: <b>${PX.esc(PX.session.email() || "–")}</b></p>
    <p class="muted" style="margin:.8rem 0;font-size:.85rem">Demo session stored in this browser only. The backend does not verify identity yet.</p><a class="btn ghost sm" href="login.html?switch=1">Switch user</a>`;
  try {
    const s = await API.system();
    $("sys").innerHTML = `<p>API: <b>${PX.esc(PLACEX_CONFIG.API_BASE_URL)}</b> <span class="badge ok">Online</span></p>
      <p>Queue: <b>${s.queue?.size ?? s.queue?.depth ?? "–"}</b></p><p>Throttled: <b>${s.throttled ? "yes" : "no"}</b></p>
      <p>Jobs: ${Object.entries(s.jobs_by_status || {}).map(([k, v]) => `${PX.badge(k)} ${v}`).join(" ") || "–"}</p>`;
  } catch (e) { $("sys").innerHTML = `<p>API: <b>${PX.esc(PLACEX_CONFIG.API_BASE_URL)}</b> <span class="badge bad">Offline</span></p><p class="muted">${PX.esc(e.message)}</p>`; }
})();
