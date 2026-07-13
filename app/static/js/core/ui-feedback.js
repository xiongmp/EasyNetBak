window.NB = window.NB || {};

(function () {
  function normalizeConfirmText(value) {
    const tr = window.NB && typeof window.NB.tr === 'function' ? window.NB.tr : function(text) { return text; };
    let text = tr(value == null ? '' : String(value));
    const replacements = [
      [/Confirm要Delete此Group\?\?/gi, 'Delete this group?'],
      [/Confirm要Delete此Template\?\?/gi, 'Delete this template?'],
      [/Confirm要Delete此Role\?\?/gi, 'Delete this role?'],
      [/Confirm要Delete此Schedules?\?\?/gi, 'Delete this schedule?'],
      [/Confirm要Delete此(?:用户|User)\?\?/gi, 'Delete this user?'],
      [/Confirm要Delete此Device\?\?/gi, 'Delete this device?'],
      [/Confirm要Delete此Credential\?\?/gi, 'Delete this credential?'],
      [/Confirm要Delete此Backup record\?\?/gi, 'Delete this backup record?'],
      [/Confirm要Permanent移除这itemsIgnore rules\?\?/gi, 'Permanently remove these ignore rules?']
    ];
    replacements.forEach(function(entry) {
      text = text.replace(entry[0], entry[1]);
    });
    return text;
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

    const tr = window.NB && typeof window.NB.tr === 'function' ? window.NB.tr : function(value) { return value; };

    if (titleEl) titleEl.textContent = normalizeConfirmText(title || '确认操作');
    if (confirmText) confirmText.textContent = normalizeConfirmText(message || '确定要执行此操作吗？');
    if (confirmBtn) {
        confirmBtn.textContent = normalizeConfirmText(confirmBtnText || '确定');
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

    const tr = window.NB && typeof window.NB.tr === 'function' ? window.NB.tr : function(value) { return value; };
    if (messageEl) messageEl.textContent = tr(message);
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
