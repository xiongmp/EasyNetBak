window.NB.ready(function initStorageSettingsPage() {
  const controller = new AbortController();
  const signal = controller.signal;
  const csrfToken = document.querySelector('input[name="csrf_token"]')?.value || "";

  function bindEnabledState(switchId, cardId) {
    const toggle = document.getElementById(switchId);
    const card = document.getElementById(cardId);
    if (!toggle || !card) return;
    const update = () => card.classList.toggle("s3-inactive-mode", !toggle.checked);
    toggle.addEventListener("change", update, { signal });
    update();
  }

  function fieldValue(selector) {
    return document.querySelector(selector)?.value || "";
  }

  function setTesting(button, testing) {
    button.disabled = testing;
    if (!testing) return;
    button.replaceChildren();
    const spinner = document.createElement("span");
    spinner.className = "spinner-border spinner-border-sm me-1";
    button.append(spinner, `${window.NB.t("template.storage_settings.testing")}...`);
  }

  function bindConnectionTest({ buttonId, url, prefix, fields }) {
    const button = document.getElementById(buttonId);
    if (!button) return;
    button.addEventListener("click", async () => {
      const originalHtml = button.innerHTML;
      setTesting(button, true);
      const body = new FormData();
      Object.entries(fields).forEach(([name, selector]) => body.append(name, fieldValue(selector)));

      try {
        const result = await window.NB.api.request(url, {
          method: "POST",
          body,
          headers: { "X-CSRF-Token": csrfToken },
        });
        const data = result.data || {};
        const fallbackKey = data.success
          ? "template.storage_settings.connection_test_succeeded"
          : "template.storage_settings.connection_test_failed";
        const detail = data.message || await window.NB.api.extractErrorDetail(result.response, "");
        window.NB.showToast(
          `${data.success ? "✅" : "❌"} ${detail || `${prefix} ${window.NB.t(fallbackKey)}`}`,
          data.success ? "success" : "error",
        );
      } catch (error) {
        window.NB.showToast(`❌ ${window.NB.t("template.backups.request_failed")}: ${error.message}`, "error");
      } finally {
        button.disabled = false;
        button.innerHTML = originalHtml;
      }
    }, { signal });
  }

  bindEnabledState("s3_enabled", "s3-config-card");
  bindEnabledState("ftp_enabled", "ftp-config-card");
  bindConnectionTest({
    buttonId: "btn-test-s3",
    url: "/settings/test-s3",
    prefix: "S3",
    fields: {
      s3_endpoint: "#s3_endpoint",
      s3_region: "#s3_region",
      s3_access_key: "#s3_access_key",
      s3_secret_key: "#s3_secret_key",
      s3_bucket: "#s3_bucket",
    },
  });
  bindConnectionTest({
    buttonId: "btn-test-ftp",
    url: "/settings/test-ftp",
    prefix: "FTP",
    fields: {
      ftp_host: "#ftp_host",
      ftp_port: "#ftp_port",
      ftp_username: "#ftp_username",
      ftp_password: "#ftp_password",
      ftp_base_dir: "#ftp_base_dir",
      ftp_passive: '[name="ftp_passive"]',
      ftp_timeout: '[name="ftp_timeout"]',
      ftp_encoding: "#ftp_encoding",
    },
  });

  return () => controller.abort();
}, { name: "storage-settings-page" });
