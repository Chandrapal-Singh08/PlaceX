(async function () {
  const list = document.getElementById("list");
  let cs;
  try { cs = await API.companies(); } catch (e) { list.innerHTML = `<div class="alert" style="grid-column:1/-1">${PX.esc(e.message)}</div>`; return; }
  if (!cs.length) { list.innerHTML = `<div class="empty" style="grid-column:1/-1">No data available yet.</div>`; return; }
  const site = w => /^https?:\/\//i.test(w || "") ? w : null;           // only link real http(s) URLs
  list.innerHTML = cs.map(c => `<article class="glass hover co"><div class="top"><div class="logo">${PX.esc((c.name || "?")[0].toUpperCase())}</div><div><h3>${PX.esc(c.name)}</h3>${c.industry ? `<small class="muted">${PX.esc(c.industry)}</small>` : ""}</div></div>
    <div class="rows">${c.industry ? `<div>Industry: <b>${PX.esc(c.industry)}</b></div>` : ""}${site(c.website) ? `<div>Website: <a class="site" href="${PX.esc(site(c.website))}" target="_blank" rel="noopener noreferrer">${PX.esc(c.website)}</a></div>` : ""}
    <div>Roles, location and rounds load when you open the journey guide.</div></div>
    <div class="foot"><a class="btn sm" href="journey-guide.html?company=${c.id}">Explore</a></div></article>`).join("");
})();
