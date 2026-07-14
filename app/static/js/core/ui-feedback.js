window.NB = window.NB || {};

(function () {
  function normalizeConfirmText(value) {
    return value == null ? '' : String(value);
  }

  window.NB.confirm = function(options) {
    const { title, message, onConfirm, confirmBtnText, confirmBtnClass } = options || {};
    const modalEl = document.getElementById('deleteConfirmModal');
    if (!modalEl) return;
    const bs = window.bootstrap;
    if (!bs) return;

    let modal = bs.Modal.getInstance(modalEl);
    if (!modal) {
        modal = new bs.Modal(modalEl);
    }

    const titleEl = modalEl.querySelector('.modal-title');
    const confirmBtn = document.getElementById('deleteConfirmBtn');
    const confirmText = document.getElementById('deleteConfirmText');

    if (titleEl) titleEl.textContent = normalizeConfirmText(title || NB.t("js.ui_feedback.confirm_action"));
    if (confirmText) confirmText.textContent = normalizeConfirmText(message || NB.t("js.ui_feedback.perform_this_action"));
    if (confirmBtn) {
        confirmBtn.textContent = normalizeConfirmText(confirmBtnText || NB.t("js.ui_feedback.confirm"));
        confirmBtn.className = 'btn btn-sm px-4 ' + (confirmBtnClass || 'btn-primary');
    }

    const newBtn = confirmBtn.cloneNode(true);
    confirmBtn.parentNode.replaceChild(newBtn, confirmBtn);

    newBtn.addEventListener('click', function() {
        if (typeof onConfirm === 'function') onConfirm();
        modal.hide();
    });

    modal.show();
  };

  window.NB.confirmDelete = function(message, onConfirm) {
    const t = window.NB && typeof window.NB.t === 'function'
      ? window.NB.t
      : function(key, params, fallback) { return fallback || key; };
    window.NB.confirm({
      title: t('dialog.delete.title'),
      message: message || t('dialog.delete.default_message'),
      onConfirm: onConfirm,
      confirmBtnText: t('dialog.delete.confirm'),
      confirmBtnClass: 'btn-danger'
    });
  };

  window.NB.showToast = function(message, type = 'success') {
    const toastEl = document.getElementById('nb-toast');
    if (!toastEl) return;

    const messageEl = document.getElementById('nb-toast-message');
    const iconEl = document.getElementById('nb-toast-icon');

    if (messageEl) messageEl.textContent = String(message == null ? '' : message);
    if (!iconEl) return;

    toastEl.classList.remove('text-bg-success', 'text-bg-danger', 'text-bg-warning', 'text-bg-info');
    iconEl.classList.remove('bi-check-circle-fill', 'bi-exclamation-triangle-fill', 'bi-info-circle-fill');

    if (type === 'success') {
      toastEl.classList.add('text-bg-success');
      iconEl.classList.add('bi-check-circle-fill');
    } else if (type === 'error' || type === 'danger') {
      toastEl.classList.add('text-bg-danger');
      iconEl.classList.add('bi-exclamation-triangle-fill');
    } else if (type === 'warning') {
      toastEl.classList.add('text-bg-warning');
      iconEl.classList.add('bi-exclamation-triangle-fill');
    } else {
      toastEl.classList.add('text-bg-info');
      iconEl.classList.add('bi-info-circle-fill');
    }

    const bs = window.bootstrap;
    if (bs && bs.Toast) {
      const toast = new bs.Toast(toastEl, { delay: 3000 });
      toast.show();
    }
  };
})();
