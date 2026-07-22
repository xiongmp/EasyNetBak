window.NB = window.NB || {};

(function () {
  "use strict";

  window.NB.ready(function initAppShell() {
    const cleanups = [];
    const activeLink = document.querySelector(".sidebar .nav-link.active");
    const collapseElement = activeLink?.closest(".collapse");
    if (collapseElement) {
      const collapse = window.bootstrap?.Collapse
        ? window.bootstrap.Collapse.getOrCreateInstance(collapseElement, { toggle: false })
        : null;
      if (collapse) collapse.show();
      else collapseElement.classList.add("show");
      document.querySelector(`[data-bs-target="#${collapseElement.id}"]`)?.classList.remove("collapsed");
    }

    const flash = window.NB_FLASH || {};
    if (flash.message) window.NB.showToast(flash.message, "success");
    if (flash.error) window.NB.showToast(flash.error, "error");

    cleanups.push(window.NB.delegate(document, "click", ".btn-delete-ask", function (event, button) {
      event.preventDefault();
      const targetForm = button.closest("form");
      const messageKey = button.dataset.confirmKey;
      const message = messageKey && typeof window.NB.t === "function"
        ? window.NB.t(messageKey)
        : button.dataset.confirmMsg;

      window.NB.confirmDelete(message, function () {
        if (targetForm) HTMLFormElement.prototype.submit.call(targetForm);
      });
    }));

    cleanups.push(window.NB.delegate(document, "click", "[data-forward-click]", function (_event, element) {
      document.querySelector(element.dataset.forwardClick)?.click();
    }));
    cleanups.push(window.NB.delegate(document, "click", "[data-select-on-click]", function (_event, element) {
      if (typeof element.select === "function") element.select();
    }));
    cleanups.push(window.NB.delegate(document, "click", "[data-window-close]", function () {
      window.close();
    }));
    cleanups.push(window.NB.delegate(document, "click", "[data-toggle-password-target]", function (_event, button) {
      const input = document.getElementById(button.dataset.togglePasswordTarget);
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.querySelector("i")?.classList.toggle("bi-eye", !show);
      button.querySelector("i")?.classList.toggle("bi-eye-slash", show);
    }));
    cleanups.push(window.NB.delegate(document, "change", "[data-pagination-base]", function (_event, select) {
      window.location.href = `${select.dataset.paginationBase}1&limit=${encodeURIComponent(select.value)}`;
    }));
    cleanups.push(window.NB.delegate(document, "keydown", "[data-pagination-jump]", function (event, input) {
      if (event.key !== "Enter") return;
      const page = Number.parseInt(input.value, 10);
      const total = Number.parseInt(input.dataset.paginationTotal || "0", 10);
      if (page >= 1 && page <= total) window.location.href = `${input.dataset.paginationJump}${page}`;
    }));

    return () => cleanups.reverse().forEach((cleanup) => cleanup());
  }, { name: "app-shell" });
})();
