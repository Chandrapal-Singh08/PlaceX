/* Password-backed account flow. */
(function () {
  const form = document.getElementById("authForm");
  const btn = document.getElementById("submitBtn");
  const signup = location.pathname.endsWith("signup.html");
  let message = document.getElementById("formMsg");

  if (!message) {
    message = document.createElement("p");
    message.id = "formMsg";
    message.className = "muted";
    message.setAttribute("role", "alert");
    message.hidden = true;
    form.insertBefore(message, btn);
  }
  if (PX.session.userId() && !location.search.includes("switch")) location.replace("dashboard.html");

  let submitting = false;
  const fail = text => {
    message.textContent = text;
    message.hidden = false;
    submitting = false;
    btn.disabled = false;
    btn.textContent = btn.dataset.label;
  };

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submitting) return;
    submitting = true;
    message.hidden = true;
    const data = new FormData(form);
    const email = (data.get("email") || "").trim();
    const fullName = (data.get("full_name") || "").trim();
    const password = data.get("password") || "";
    if (!/^\S+@\S+\.\S+$/.test(email)) return fail("Enter a valid email address.");
    if (signup && !fullName) return fail("Enter your full name.");
    if (signup && password.length < 12) return fail("Use a password with at least 12 characters.");
    if (!password) return fail("Enter your password.");

    btn.disabled = true;
    btn.textContent = "Please wait…";
    try {
      const session = signup
        ? await API.createUser(email, fullName, (data.get("roll_no") || "").trim(), password)
        : await API.login(email, password);
      PX.session.start(session.user_id, session.full_name, session.email, session.access_token);
      location.href = "dashboard.html";
    } catch (error) {
      fail(error.message);
    }
  });
})();
