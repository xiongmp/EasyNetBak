window.NB.ready(function initCredentialsPage() {
  const config = window.NB.readJson("credentials-page-config", {});
  const modalElement = document.getElementById("credentialModal");
  if (!modalElement || !window.bootstrap?.Modal) return undefined;

  const controller = new AbortController();
  const modal = window.bootstrap.Modal.getOrCreateInstance(modalElement);
  if (config.openModal === true) modal.show();

  modalElement.addEventListener("hidden.bs.modal", () => {
    if (new URLSearchParams(window.location.search).has("edit")) {
      window.location.assign("/credentials");
    }
  }, { signal: controller.signal });

  return () => {
    controller.abort();
    modal.dispose();
  };
}, { name: "credentials-page" });
