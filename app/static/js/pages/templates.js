window.NB.ready(function initTemplatesPage() {
  const config = window.NB.readJson("templates-page-config", {});
  const modalElement = document.getElementById("templateModal");
  const loginSelect = document.getElementById("template-login-method");
  const platformSelect = document.getElementById("template-platform");
  if (!modalElement || !loginSelect || !platformSelect) return undefined;

  const controller = new AbortController();
  const options = { signal: controller.signal };
  const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
  const idInput = modalElement.querySelector('[name="template_id"]');
  const nameInput = modalElement.querySelector('[name="name"]');
  const commandsInput = modalElement.querySelector('[name="commands"]');
  const title = modalElement.querySelector(".modal-title");

  function syncPlatformOptions() {
    const wanted = loginSelect.value === "telnet" ? "telnet" : "ssh";
    let firstVisible = "";
    Array.from(platformSelect.options).forEach((option) => {
      const visible = (option.dataset.kind || "ssh") === wanted;
      option.hidden = !visible;
      option.disabled = !visible;
      if (visible && !firstVisible) firstVisible = option.value;
    });
    if (platformSelect.selectedOptions[0]?.disabled) platformSelect.value = firstVisible;
  }

  function populate(values, editing) {
    idInput.value = String(values?.id || 0);
    nameInput.value = values?.name || "";
    commandsInput.value = values?.commands || "";
    const platform = values?.platform || "";
    loginSelect.value = platform.endsWith("_telnet") ? "telnet" : "ssh";
    syncPlatformOptions();
    if (platform) platformSelect.value = platform;
    title.textContent = window.NB.t(editing
      ? "template.templates.edit_custom_template"
      : "template.templates.add_custom_template");
  }

  loginSelect.addEventListener("change", syncPlatformOptions, options);
  document.getElementById("resetTemplateModal")?.addEventListener("click", () => populate(null, false), options);
  document.querySelectorAll(".btn-edit-template").forEach((button) => {
    button.addEventListener("click", () => {
      populate({
        id: button.dataset.id,
        name: button.dataset.name,
        commands: button.dataset.commands,
        platform: button.dataset.platform,
      }, true);
      modal.show();
    }, options);
  });

  syncPlatformOptions();
  if (config.current) {
    populate(config.current, true);
    modal.show();
  }
  return () => {
    controller.abort();
    modal.dispose();
  };
}, { name: "templates-page" });
