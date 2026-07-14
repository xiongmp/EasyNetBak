document.addEventListener('DOMContentLoaded', function() {
      const DEVICE_REACHABILITY = (window.DEVICES_PAGE_CONFIG && window.DEVICES_PAGE_CONFIG.reachability) || {};

      const selectAll = document.getElementById('select-all');
      const checks = Array.from(document.querySelectorAll('.device-check'));
      const idsInput = document.getElementById('bulk-device-ids');
      const backupModeInput = document.getElementById('bulk-backup-mode');
      const countEl = document.getElementById('bulk-selected-count');
      const btnSelected = document.getElementById('bulk-backup-selected-btn');
      const btnAll = document.getElementById('bulk-backup-all-btn');

      const idsInput3 = document.getElementById('bulk-device-ids-3');
      const bulkDeleteBtn = document.getElementById('bulk-delete-btn');
      const bulkDeleteForm = document.getElementById('bulk-delete-form');
      const bulkBackupForm = document.getElementById('bulk-backup-form');
      const reachBtn = document.getElementById('bulk-reachability-btn');
      const reachPanel = document.getElementById('reachabilityModal');
      const reachSummary = document.getElementById('bulk-reachability-summary');
      const reachTbody = document.getElementById('bulk-reachability-tbody');
      const reachFilterGroup = document.getElementById('reach-filter-group');
      const filterQ = document.querySelector('input[name="q"]');
      const filterLogin = document.getElementById('device-filter-login-method');
      const filterPlatform = document.getElementById('device-filter-platform');
      const filterGroup = document.querySelector('select[name="group_id"]');
      const filterStatus = document.querySelector('select[name="status"]');

      const reachProgressContainer = document.getElementById('reach-progress-container');
      const reachProgressBar = document.getElementById('reach-progress-bar');
      const reachProgressText = document.getElementById('reach-progress-text');
      const reachProgressPercent = document.getElementById('reach-progress-percent');

      let reachBtnText = reachBtn ? reachBtn.innerHTML : "";

      // Tooltip for reachability button
      const reachWrapper = document.getElementById('reach-btn-wrapper');
      let reachTooltip = null;
      if (reachWrapper) {
          reachTooltip = new bootstrap.Tooltip(reachWrapper);
      }

      let lastReachItems = [];
      let currentReachFilter = 'all';
      let reachModalInstance = null;

      // Initialize modal with current DB state when opened
      if (reachBtn) {
          reachBtn.addEventListener('click', () => {
              if (reachPanel) {
                  // Show Modal
                  if (!reachModalInstance) {
                      reachModalInstance = new bootstrap.Modal(reachPanel);
                  }
                  reachModalInstance.show();

                  const selectedIds = checks.filter(c => c.checked).map(c => c.value);
                  let targetItems = [];

                  if (selectedIds.length > 0) {
                      // Show selected
                      targetItems = selectedIds.map(id => DEVICE_REACHABILITY[id]).filter(x => x);
                  } else {
                      // Show all on current page if nothing selected
                      targetItems = Object.values(DEVICE_REACHABILITY);
                  }

                  lastReachItems = targetItems;
                  renderReachabilityTable();

                  const total = lastReachItems.length;
                  const checkedItems = lastReachItems.filter(i => i.success !== null);
                  const ok = checkedItems.filter(i => i.success === true).length;
                  const bad = checkedItems.filter(i => i.success === false).length;

                  if (checkedItems.length > 0) {
                      updateReachSummary({total, success: ok, failed: bad});
                  } else {
                      updateReachSummary(null);
                  }
              }
          });
      }

      function updateBulkAll() {
        const ids = checks.filter(c => c.checked).map(c => c.value);
        const idsValue = ids.join(',');
        if (idsInput) idsInput.value = idsValue;
        countEl.textContent = String(ids.length);
        if (btnSelected) btnSelected.disabled = ids.length === 0;
        if (idsInput3) idsInput3.value = idsValue;
        const has = ids.length > 0;
        if (bulkDeleteBtn) bulkDeleteBtn.disabled = !has;

        const bulkUpdateBtn = document.getElementById('bulk-update-btn');
        if (bulkUpdateBtn) bulkUpdateBtn.disabled = !has;
        if (reachBtn) {
           reachBtn.disabled = !has;
           reachBtn.style.pointerEvents = has ? 'auto' : 'none';
           if (reachTooltip) {
               if (has) {
                   reachTooltip.disable();
               } else {
                   reachTooltip.enable();
               }
           }
        }

        if (selectAll) {
          selectAll.checked = ids.length > 0 && ids.length === checks.length;
          selectAll.indeterminate = ids.length > 0 && ids.length < checks.length;
        }
      }

      if (selectAll) {
        selectAll.addEventListener('change', () => {
          checks.forEach(c => { c.checked = selectAll.checked; });
          updateBulkAll();
        });
      }
      checks.forEach(c => c.addEventListener('change', updateBulkAll));
      updateBulkAll();

      if (bulkDeleteForm) {
        bulkDeleteForm.addEventListener('submit', (e) => {
          e.preventDefault();
          const cnt = Number(countEl.textContent || '0');
          if (window.NB && typeof window.NB.confirmDelete === 'function') {
            window.NB.confirmDelete(NB.t("js.devices_bulk.delete_selected_value0_devices", {value0: cnt}), () => {
              HTMLFormElement.prototype.submit.call(bulkDeleteForm);
            });
          } else if (confirm(NB.t("js.devices_bulk.delete_selected_value0_devices", {value0: cnt}))) {
            HTMLFormElement.prototype.submit.call(bulkDeleteForm);
          }
        });
      }

      if (bulkBackupForm) {
        const doBulkBackup = async (mode) => {
          if (btnSelected) btnSelected.disabled = true;
          if (btnAll) btnAll.disabled = true;
          if (backupModeInput) backupModeInput.value = mode;
          if (window.NB && typeof window.NB.beginBackupTracking === 'function') {
            window.NB.beginBackupTracking();
          }

          const fd = new FormData(bulkBackupForm);
          try {
            const result = await window.NB.api.request('/api/devices/bulk_backup', { method: 'POST', body: fd });
            const data = result.data || {};
            if (!result.ok) {
              if (window.NB && typeof window.NB.cancelPendingBackupTracking === 'function') {
                window.NB.cancelPendingBackupTracking();
              }
              const detail = await window.NB.api.extractErrorDetail(result.response, '');
              if (window.NB && typeof window.NB.showToast === 'function') {
                window.NB.showToast(detail || '??????', 'error');
              }
              updateBulkAll();
              if (btnAll) btnAll.disabled = false;
              return;
            }
            const records = (data && data.records) ? data.records : [];
            const enqueueStatus = (data && data.enqueue_status) ? String(data.enqueue_status) : 'none';
            const enqueueWarning = (data && data.enqueue_warning_message) ? String(data.enqueue_warning_message) : '';
            if (window.NB && typeof window.NB.trackBackups === 'function' && Array.isArray(records) && records.length) {
              window.NB.trackBackups({
                run_id: data && data.run_id ? data.run_id : '',
                warning_message: enqueueWarning,
              });
              if (window.NB && typeof window.NB.showToast === 'function') {
                if (enqueueWarning) {
                  window.NB.showToast(enqueueWarning, 'warning');
                } else if (enqueueStatus === 'partial') {
                  window.NB.showToast(NB.t("js.devices_bulk.started") + records.length + NB.t("js.devices_bulk.backup_tasks_some_tasks_failed_to_queue"), 'warning');
                } else {
                  window.NB.showToast(NB.t("js.devices_bulk.started") + records.length + NB.t("js.devices_bulk.backup_tasks"), 'info');
                }
              }
            } else {
              if (window.NB && typeof window.NB.cancelPendingBackupTracking === 'function') {
                window.NB.cancelPendingBackupTracking();
              }
              if (window.NB && typeof window.NB.showToast === 'function') {
                window.NB.showToast(NB.t("js.devices_bulk.no_devices_available_for_backup"), 'warning');
              }
            }
            updateBulkAll();
            if (btnAll) btnAll.disabled = false;
          } catch (err) {
            if (window.NB && typeof window.NB.cancelPendingBackupTracking === 'function') {
              window.NB.cancelPendingBackupTracking();
            }
            if (window.NB && typeof window.NB.showToast === 'function') {
              window.NB.showToast(NB.t("js.devices_bulk.request_failed") + err.message, 'error');
            }
            updateBulkAll();
            if (btnAll) btnAll.disabled = false;
          }
        };

        if (btnSelected) {
          btnSelected.addEventListener('click', () => {
            const cnt = Number(countEl.textContent || '0');
            if (window.NB && typeof window.NB.confirm === 'function') {
              window.NB.confirm({
                title: NB.t("js.devices_bulk.back_up_selected_devices"),
                message: NB.t("js.devices_bulk.run_backup_for_selected_value0_devicesrun_backup_now", {value0: cnt}),
                onConfirm: () => doBulkBackup('selected'),
                confirmBtnText: NB.t("template.backups.start_backup"),
                confirmBtnClass: 'btn-primary'
              });
            } else if (confirm(NB.t("js.devices_bulk.run_backup_for_selected_value0_devicesrun_backup_now", {value0: cnt}))) {
              doBulkBackup('selected');
            }
          });
        }

        if (btnAll) {
          btnAll.addEventListener('click', () => {
            if (window.NB && typeof window.NB.confirm === 'function') {
              window.NB.confirm({
                title: NB.t("js.devices_bulk.back_up_all_devices"),
                message: NB.t("js.devices_bulk.run_backup_for_all_devices_in_the_system"),
                onConfirm: () => doBulkBackup('all'),
                confirmBtnText: NB.t("template.backups.start_backup"),
                confirmBtnClass: 'btn-primary'
              });
            } else if (confirm(NB.t("js.devices_bulk.run_backup_for_all_devices_in_the_system"))) {
              doBulkBackup('all');
            }
          });
        }
      }

      function escapeText(text) {
        const span = document.createElement('span');
        span.textContent = text == null ? '' : String(text);
        return span.innerHTML;
      }

      function tr(text) { return text; }

      function trHtml(html) { return html; }

      function renderReachBadge(item) {
        if (item && item.success === true) {
          return `<div class="status-pill status-online"><span class="status-dot"></span><span>${window.NB.t('status.device.reachable')}</span></div>`;
        }
        if (item && item.success === null) {
          return `<div class="status-pill status-unknown"><span class="status-dot"></span><span>${window.NB.t('status.device.not_tested')}</span></div>`;
        }
        const err = (item && item.error_message) ? item.error_message : '';
        let label = window.NB.t('status.device.unreachable');
        let title = '';
        if (err.includes(NB.t("status.device.connection_timeout"))) { label = window.NB.t('status.device.timeout'); title = window.NB.t('status.device.connection_timeout'); }
        else if (err.includes(NB.t("status.device.authentication_failed"))) { label = window.NB.t('status.device.authentication_failed'); title = label; }
        else if (err.includes(NB.t("status.device.connection_refused"))) { label = window.NB.t('status.device.refused'); title = window.NB.t('status.device.connection_refused'); }
        else if (err.includes(NB.t("js.devices_bulk.privilege_mode_failed"))) { label = window.NB.t('status.device.privileged_failed'); title = window.NB.t('status.device.enable_password_error'); }
        else if (err.includes(NB.t("status.device.read_timeout"))) { label = window.NB.t('status.device.read_timeout'); title = window.NB.t('status.device.slow_response'); }
        else if (err.includes(NB.t("status.device.disconnected"))) { label = window.NB.t('status.device.disconnected'); title = window.NB.t('status.device.unstable_connection'); }
        else if (err.includes(NB.t("status.device.key_error"))) { label = window.NB.t('status.device.key_error'); title = window.NB.t('status.device.invalid_ssh_key'); }
        else if (err.includes(NB.t("status.device.credentials_not_configured"))) { label = window.NB.t('status.device.no_credentials'); title = window.NB.t('status.device.credentials_not_configured'); }

        return `<div class="status-pill status-offline" title="${title}"><span class="status-dot"></span><span>${label}</span></div>`;
      }

      function renderReachabilityTable() {
        if (!reachTbody) return;

        let items = lastReachItems;
        if (currentReachFilter === 'success') {
          items = items.filter(i => i.success);
        } else if (currentReachFilter === 'failed') {
          items = items.filter(i => !i.success);
        }

        if (!items || !items.length) {
           if (lastReachItems.length > 0) {
             reachTbody.innerHTML = trHtml(NB.t("js.devices_bulk.no_matching_records_html"));
           } else {
             reachTbody.innerHTML = trHtml(NB.t("js.devices_bulk.empty_test_results_html"));
           }
           return;
        }

        reachTbody.innerHTML = items.map((item) => {
          const host = `${escapeText(item.host || '')}`;
          const name = escapeText(item.name || '');
          const lm = (item.login_method || '').toLowerCase() === 'telnet' ? 'Telnet' : 'SSH';
          const err = escapeText(item.error_message || '');
          const duration = item.duration_ms != null ? `${item.duration_ms} ms` : '-';
          const lastChecked = escapeText(item.last_checked || '-');
          return `
            <tr>
              <td class="text-secondary small ps-3 text-start">${escapeText(item.id || '')}</td>
              <td class="text-start">${name}</td>
              <td class="text-secondary small text-start">${host}</td>
              <td class="text-start">
                <div class="backup-status" style="background: var(--bs-secondary-bg-subtle); color: var(--bs-secondary-text-emphasis); border-color: var(--bs-border-color); font-size: 10px;">${lm}</div>
              </td>
              <td class="text-start">${renderReachBadge(item)}</td>
              <td class="text-secondary small text-start text-nowrap">${lastChecked}</td>
              <td class="text-secondary small text-start text-nowrap">${duration}</td>
              <td class="text-danger small text-truncate text-start" style="max-width: 360px;" title="${err}">${err}</td>
            </tr>
          `;
        }).join('');
      }

      function updateReachSummary(summary) {
         if (!reachSummary) return;
         if (!summary) {
             reachSummary.innerHTML = trHtml(NB.t("js.devices_bulk.ready_status_html"));
             return;
         }
         const total = summary.total || 0;
         const ok = summary.success || 0;
         const bad = summary.failed || 0;

         reachSummary.innerHTML = trHtml(NB.t("js.devices_bulk.reachability_summary_html", {value0: total, value1: window.NB.t('status.device.reachable'), value2: ok, value3: window.NB.t('status.device.unreachable'), value4: bad}));
      }

      if (reachFilterGroup) {
        const btns = reachFilterGroup.querySelectorAll('button');
        btns.forEach(btn => {
          btn.addEventListener('click', () => {
            btns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentReachFilter = btn.getAttribute('data-filter') || 'all';
            renderReachabilityTable();
          });
        });
      }

      const startReachTestBtn = document.getElementById('start-reach-test-btn');

      function updateMainTableStatus(item) {
        const cell = document.getElementById(`device-status-${item.id}`);
        if (!cell) return;

        let html = '';
        if (item.success === true) {
          html = `
            <div class="status-pill status-online" title="${window.NB.t('label.test_time')}: ${escapeText(item.last_checked)}">
              <span class="status-dot"></span>
              <span>${window.NB.t('status.device.online')}</span>
            </div>`;
        } else if (item.success === false) {
          html = `
            <div class="status-pill status-offline" title="${window.NB.t('label.test_time')}: ${escapeText(item.last_checked)}\n${window.NB.t('label.error')}: ${escapeText(item.error_message)}">
              <span class="status-dot"></span>
              <span>${window.NB.t('status.device.offline')}</span>
            </div>`;
        } else {
          html = `
            <div class="status-pill status-unknown">
              <span class="status-dot"></span>
              <span>${window.NB.t('status.unknown')}</span>
            </div>`;
        }
        cell.innerHTML = html;
      }

      // 执行检测的逻辑提取为单独函数 (异步轮询版)
      async function performReachabilityTest() {
          if (!startReachTestBtn) return;

          const originalBtnText = startReachTestBtn.innerHTML;
          startReachTestBtn.disabled = true;
          startReachTestBtn.classList.add('disabled');

          // Show Progress UI
          if (reachProgressContainer) reachProgressContainer.style.display = 'block';
          if (reachProgressBar) {
              reachProgressBar.style.width = '0%';
              reachProgressBar.classList.add('progress-bar-animated');
              reachProgressBar.classList.remove('bg-success');
          }
          if (reachProgressPercent) reachProgressPercent.textContent = '0%';
          if (reachProgressText) reachProgressText.textContent = tr(NB.t("js.devices_bulk.ready_to_start"));

          if (reachSummary) {
            reachSummary.innerHTML = trHtml(NB.t("js.devices_bulk.starting_status_html"));
          }

          // Reset filter
          if (reachFilterGroup) {
             const allBtn = reachFilterGroup.querySelector('[data-filter="all"]');
             if (allBtn) {
                 allBtn.click();
                 const btns = reachFilterGroup.querySelectorAll('button');
                 btns.forEach(b => b.classList.remove('active'));
                 allBtn.classList.add('active');
                 currentReachFilter = 'all';
             }
          }

          // Clear table initially
          if (reachTbody) reachTbody.innerHTML = '';
          lastReachItems = [];

          const formData = new FormData();
          const qVal = filterQ ? filterQ.value || '' : "";
          const lVal = filterLogin ? filterLogin.value || '' : "";
          const pVal = filterPlatform ? filterPlatform.value || '' : "";
          const gVal = filterGroup ? filterGroup.value || '' : "0";
          const sVal = filterStatus ? filterStatus.value || '' : "";

          const selectedIds = checks.filter(c => c.checked).map(c => c.value);
          if (selectedIds.length > 0) {
             formData.append("device_ids", selectedIds.join(","));
          } else {
             formData.append("q", qVal);
             formData.append("login_method", lVal);
             formData.append("platform", pVal);
             formData.append("group_id", gVal);
             formData.append("status", sVal);
          }

          try {
            // 1. Start Task
            const result = await window.NB.api.request("/api/devices/bulk_reachability", {
                method: "POST",
                body: formData,
            });

            if (!result.ok) {
                throw new Error("Failed to start task");
            }

            const data = result.data || {};
            const taskId = data.task_id;

            if (!taskId) {
                if (reachSummary) reachSummary.innerHTML = trHtml(NB.t("js.devices_bulk.no_testable_devices_html"));
                if (reachProgressContainer) reachProgressContainer.style.display = 'none';
                startReachTestBtn.disabled = false;
                startReachTestBtn.classList.remove('disabled');
                startReachTestBtn.innerHTML = originalBtnText;
                return;
            }

            // 2. Poll Task Status
            const pollInterval = setInterval(async () => {
                try {
                    const result = await window.NB.api.request(`/api/devices/reachability_tasks/${taskId}`);
                    if (!result.ok) return;

                    const task = result.data || {};

                    // Update Progress
                    const total = task.total || 1;
                    const processed = task.processed || 0;
                    const percent = Math.round((processed / total) * 100);

                    if (reachProgressBar) reachProgressBar.style.width = `${percent}%`;
                    if (reachProgressPercent) reachProgressPercent.textContent = `${percent}%`;
                    if (reachProgressText) reachProgressText.textContent = tr(NB.t("js.devices_bulk.testing_value0_value1", {value0: processed, value1: total}));

                    // Update Summary
                    updateReachSummary({
                        total: task.total,
                        success: task.success,
                        failed: task.failed
                    });

                    // Update Table & Main List
                    lastReachItems = task.items || [];

                    // Update Main Table Badges
                    (task.items || []).forEach(item => {
                         if (DEVICE_REACHABILITY[item.id]) {
                            Object.assign(DEVICE_REACHABILITY[item.id], item);
                        }
                        updateMainTableStatus(item);
                    });

                    renderReachabilityTable();

                    if (task.status === 'finished') {
                        clearInterval(pollInterval);
                        if (reachProgressBar) {
                            reachProgressBar.classList.remove('progress-bar-animated');
                            reachProgressBar.classList.add('bg-success');
                        }
                        if (reachProgressText) reachProgressText.textContent = tr(NB.t("js.devices_bulk.test_complete"));

                        startReachTestBtn.disabled = false;
                        startReachTestBtn.classList.remove('disabled');
                        startReachTestBtn.innerHTML = originalBtnText;
                    }

                } catch (e) {
                    console.error("Polling error", e);
                    clearInterval(pollInterval);
                    startReachTestBtn.disabled = false;
                    startReachTestBtn.classList.remove('disabled');
                    startReachTestBtn.innerHTML = originalBtnText;
                }
            }, 1000);

          } catch (e) {
            if (reachSummary) reachSummary.innerHTML = trHtml(NB.t("js.devices_bulk.request_failed_html"));
            if (reachProgressContainer) reachProgressContainer.style.display = 'none';
            console.error(e);
            startReachTestBtn.disabled = false;
            startReachTestBtn.classList.remove('disabled');
            startReachTestBtn.innerHTML = originalBtnText;
          }
      }

      if (startReachTestBtn) {
          startReachTestBtn.addEventListener('click', performReachabilityTest);
      }

      async function refreshDeviceStatus() {
        const ids = Object.keys(DEVICE_REACHABILITY);
        if (ids.length === 0) return;

        try {
            const result = await window.NB.api.request(`/api/devices/status?ids=${ids.join(',')}`);
            if (!result.ok) return;
            const data = result.data || {};

            if (data.items && Array.isArray(data.items)) {
                data.items.forEach(item => {
                    if (DEVICE_REACHABILITY[item.id]) {
                        // Update local cache
                        Object.assign(DEVICE_REACHABILITY[item.id], item);
                        // Update Main Table
                        updateMainTableStatus(item);
                    }
                });

                // If modal is open, update it too
                if (reachModalInstance && reachPanel && reachPanel.classList.contains('show')) {
                     renderReachabilityTable();
                }
            }
        } catch (e) {
            console.error("Failed to refresh status", e);
        }
      }

      refreshDeviceStatus();
      let refreshCount = 0;
      const maxRefreshCount = 12;
      const refreshInterval = setInterval(() => {
        refreshCount += 1;
        refreshDeviceStatus();
        if (refreshCount >= maxRefreshCount) {
          clearInterval(refreshInterval);
        }
      }, 5000);

      // Bulk Update Logic
      const bulkUpdateBtn = document.getElementById('bulk-update-btn');
      const bulkUpdateModalEl = document.getElementById('bulkUpdateModal');
      const bulkUpdateModal = bulkUpdateModalEl ? new bootstrap.Modal(bulkUpdateModalEl) : null;
      const bulkUpdateIds = document.getElementById('bulk-update-ids');
      const bulkUpdateCount = document.getElementById('bulk-update-count');
      const bulkUpdateField = document.getElementById('bulk-update-field');

      const bulkGroupSelect = document.getElementById('bulk-update-group-select');
      const bulkPlatformSelect = document.getElementById('bulk-update-platform-select');
      const bulkLoginSelect = document.getElementById('bulk-update-login-select');
      const bulkCredentialSelect = document.getElementById('bulk-update-credential-select');
      const bulkEncodingSelect = document.getElementById('bulk-update-encoding-select');

      function updateBulkInputVisibility() {
        if (!bulkUpdateField) return;
        const field = bulkUpdateField.value;

        // Hide all first
        [bulkGroupSelect, bulkPlatformSelect, bulkLoginSelect, bulkCredentialSelect, bulkEncodingSelect].forEach(el => {
          if (el) {
            el.classList.add('d-none');
            el.disabled = true;
          }
        });

        // Show selected
        let activeEl = null;
        if (field === 'group_id') activeEl = bulkGroupSelect;
        else if (field === 'platform') activeEl = bulkPlatformSelect;
        else if (field === 'login_method') activeEl = bulkLoginSelect;
        else if (field === 'credential_id') activeEl = bulkCredentialSelect;
        else if (field === 'encoding') activeEl = bulkEncodingSelect;

        if (activeEl) {
          activeEl.classList.remove('d-none');
          activeEl.disabled = false;
        }
      }

      if (bulkUpdateBtn) {
        bulkUpdateBtn.addEventListener('click', function() {
           const selectedIds = checks.filter(c => c.checked).map(c => c.value);
           if (selectedIds.length === 0) return;

           if (bulkUpdateIds) bulkUpdateIds.value = selectedIds.join(',');
           if (bulkUpdateCount) bulkUpdateCount.textContent = selectedIds.length;

           // Reset form
           if (bulkUpdateField) bulkUpdateField.value = 'group_id';
           updateBulkInputVisibility();

           if (bulkUpdateModal) bulkUpdateModal.show();
        });
      }

      if (bulkUpdateField) {
        bulkUpdateField.addEventListener('change', updateBulkInputVisibility);
      }

      if (reachBtn) {
         // Moved the click listener to earlier in the file to handle logic properly
      }
      });
