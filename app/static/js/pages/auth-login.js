window.NB.ready(function initLoginPage() {
  const controller = new AbortController();
  const options = { signal: controller.signal };
  const toggle = document.getElementById("togglePassword");
  const password = document.getElementById("passwordInput");
  const icon = document.getElementById("toggleIcon");
  const form = document.getElementById("loginForm");
  const submit = document.getElementById("submitBtn");

  toggle?.addEventListener("click", () => {
    const show = password.type === "password";
    password.type = show ? "text" : "password";
    icon?.classList.toggle("bi-eye", !show);
    icon?.classList.toggle("bi-eye-slash", show);
  }, options);

  form?.addEventListener("submit", () => {
    submit.disabled = true;
    submit.replaceChildren();
    const spinner = document.createElement("span");
    spinner.className = "spinner-border spinner-border-sm me-2";
    spinner.setAttribute("role", "status");
    spinner.setAttribute("aria-hidden", "true");
    submit.append(spinner, `${window.NB.t("template.login.signing_in")}...`);
  }, options);

  return () => controller.abort();
}, { name: "login-page" });
