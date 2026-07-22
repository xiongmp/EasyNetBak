window.NB.ready(function initApiKeysPage() {
  const controller = new AbortController();
  const options = { signal: controller.signal };
  let pendingRevokeFormId = "";
  let pendingDeleteFormId = "";
  let resetCopyTimer = null;
  const modals = [];

  function modalFor(id) {
    const element = document.getElementById(id);
    if (!element || !window.bootstrap?.Modal) return null;
    const modal = window.bootstrap.Modal.getOrCreateInstance(element);
    modals.push(modal);
    return modal;
  }

  document.querySelectorAll(".js-api-key-revoke").forEach((button) => {
    button.addEventListener("click", () => {
      pendingRevokeFormId = button.dataset.formId || "";
      document.getElementById("revokeKeyName").textContent = button.dataset.keyName || "";
      modalFor("revokeApiKeyModal")?.show();
    }, options);
  });
  document.querySelectorAll(".js-api-key-delete").forEach((button) => {
    button.addEventListener("click", () => {
      pendingDeleteFormId = button.dataset.formId || "";
      document.getElementById("deleteKeyName").textContent = button.dataset.keyName || "";
      modalFor("deleteApiKeyModal")?.show();
    }, options);
  });

  document.getElementById("confirmRevokeBtn")?.addEventListener("click", () => {
    document.getElementById(pendingRevokeFormId)?.requestSubmit();
  }, options);
  document.getElementById("confirmDeleteBtn")?.addEventListener("click", () => {
    document.getElementById(pendingDeleteFormId)?.requestSubmit();
  }, options);

  document.getElementById("copyNewApiKey")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const input = document.getElementById("newApiKey");
    const value = input?.value || "";
    let copied = false;
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(value);
        copied = true;
      } catch (error) {
        console.warn("Clipboard API copy failed", error);
      }
    }
    if (!copied && input) {
      input.focus();
      input.select();
      input.setSelectionRange(0, value.length);
      copied = Boolean(document.execCommand?.("copy"));
    }
    if (!copied) {
      window.alert(`${window.NB.t("template.api_keys.copy_failed_in")} Ctrl+C ${window.NB.t("template.api_keys.copy")}.`);
      return;
    }

    const originalHtml = button.innerHTML;
    button.replaceChildren();
    const icon = document.createElement("i");
    icon.className = "bi bi-check2 me-1";
    button.append(icon, ` ${window.NB.t("template.api_keys.copied")}`);
    button.classList.replace("btn-primary", "btn-success");
    resetCopyTimer = window.setTimeout(() => {
      button.innerHTML = originalHtml;
      button.classList.replace("btn-success", "btn-primary");
    }, 2000);
  }, options);

  modalFor("showNewKeyModal")?.show();
  return () => {
    controller.abort();
    if (resetCopyTimer) window.clearTimeout(resetCopyTimer);
    Array.from(new Set(modals)).forEach((modal) => modal.dispose());
  };
}, { name: "api-keys-page" });
