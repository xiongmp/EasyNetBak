window.NB = window.NB || {};

(function () {
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

    if (titleEl) titleEl.textContent = title || '确认操作';
    if (confirmText) confirmText.textContent = message || '确定要执行此操作吗？';
    if (confirmBtn) {
        confirmBtn.textContent = confirmBtnText || '确定';
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
    window.NB.confirm({
      title: '确认删除',
      message: message || '确定要删除吗？此操作不可恢复。',
      onConfirm: onConfirm,
      confirmBtnText: '确认删除',
      confirmBtnClass: 'btn-danger'
    });
  };

  window.NB.showToast = function(message, type = 'success') {
    const toastEl = document.getElementById('nb-toast');
    if (!toastEl) return;

    const messageEl = document.getElementById('nb-toast-message');
    const iconEl = document.getElementById('nb-toast-icon');

    if (messageEl) messageEl.textContent = message;
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
