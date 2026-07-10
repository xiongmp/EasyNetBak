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
            window.NB.confirmDelete(`确认删除选中的 ${cnt} 台设备？`, () => {
              HTMLFormElement.prototype.submit.call(bulkDeleteForm);
            });
          } else if (confirm(`确认删除选中的 ${cnt} 台设备？`)) {
            HTMLFormElement.prototype.submit.call(bulkDeleteForm);
          }
        });
      }

      if (bulkBackupForm) {
        const doBulkBackup = async (mode) => {
          if (btnSelected) btnSelected.disabled = true;
          if (btnAll) btnAll.disabled = true;
          if (backupModeInput) backupModeInput.value = mode;

          const fd = new FormData(bulkBackupForm);
          try {
            const result = await window.NB.api.request('/api/devices/bulk_backup', { method: 'POST', body: fd });
            const data = result.data || {};
            if (!result.ok) {
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
                  window.NB.showToast('已启动 ' + records.length + ' 个备份任务，部分任务入队失败', 'warning');
                } else {
                  window.NB.showToast('已启动 ' + records.length + ' 个备份任务', 'info');
                }
              }
            } else {
              if (window.NB && typeof window.NB.showToast === 'function') {
                window.NB.showToast('未找到可备份设备', 'warning');
              }
            }
            updateBulkAll();
            if (btnAll) btnAll.disabled = false;
          } catch (err) {
            if (window.NB && typeof window.NB.showToast === 'function') {
              window.NB.showToast('请求出错: ' + err.message, 'error');
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
                title: '备份所选设备',
                message: `确定要对选中的 ${cnt} 台设备立即执行备份吗？`,
                onConfirm: () => doBulkBackup('selected'),
                confirmBtnText: '开始备份',
                confirmBtnClass: 'btn-primary'
              });
            } else if (confirm(`确定要对选中的 ${cnt} 台设备立即执行备份吗？`)) {
              doBulkBackup('selected');
            }
          });
        }

        if (btnAll) {
          btnAll.addEventListener('click', () => {
            if (window.NB && typeof window.NB.confirm === 'function') {
              window.NB.confirm({
                title: '备份所有设备',
                message: `确定要对系统中的所有设备立即执行备份吗？`,
                onConfirm: () => doBulkBackup('all'),
                confirmBtnText: '开始备份',
                confirmBtnClass: 'btn-primary'
              });
            } else if (confirm(`确定要对系统中的所有设备立即执行备份吗？`)) {
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
        if (err.includes('连接超时')) { label = window.NB.t('status.device.timeout'); title = window.NB.t('status.device.connection_timeout'); }
        else if (err.includes('认证失败')) { label = window.NB.t('status.device.authentication_failed'); title = label; }
        else if (err.includes('连接被拒绝')) { label = window.NB.t('status.device.refused'); title = window.NB.t('status.device.connection_refused'); }
        else if (err.includes('特权模式失败')) { label = window.NB.t('status.device.privileged_failed'); title = window.NB.t('status.device.enable_password_error'); }
        else if (err.includes('读取超时')) { label = window.NB.t('status.device.read_timeout'); title = window.NB.t('status.device.slow_response'); }
        else if (err.includes('连接断开')) { label = window.NB.t('status.device.disconnected'); title = window.NB.t('status.device.unstable_connection'); }
        else if (err.includes('密钥错误')) { label = window.NB.t('status.device.key_error'); title = window.NB.t('status.device.invalid_ssh_key'); }
        else if (err.includes('未配置凭据')) { label = window.NB.t('status.device.no_credentials'); title = window.NB.t('status.device.credentials_not_configured'); }

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
             reachTbody.innerHTML = '<tr><td colspan="7" class="text-center text-secondary py-3">没有符合条件的记录</td></tr>';
           } else {
             reachTbody.innerHTML = '<tr><td colspan="7" class="text-center text-secondary py-3">暂无检测结果</td></tr>';
           }
           return;
        }

        reachTbody.innerHTML = items.map((item) => {
          const host = `${escapeText(item.host || '')}`;
          const name = escapeText(item.name || '');
          const lm = (item.login_method || '').toLowerCase() === 'telnet' ? 'Telnet' : 'SSH';
          const err = escapeText(window.NB.translateLegacy(item.error_message || ''));
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
             reachSummary.innerHTML = '<span class="text-secondary">准备就绪</span>';
             return;
         }
         const total = summary.total || 0;
         const ok = summary.success || 0;
         const bad = summary.failed || 0;

         reachSummary.innerHTML = `
           <div class="backup-status" style="background: var(--bs-secondary-bg); color: var(--bs-secondary-color); border-color: var(--bs-border-color);">共 ${total}</div>
           <div class="backup-status backup-status-success">${window.NB.t('status.device.reachable')} ${ok}</div>
           <div class="backup-status backup-status-failed">${window.NB.t('status.device.unreachable')} ${bad}</div>
         `;
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
            <div class="status-pill status-offline" title="${window.NB.t('label.test_time')}: ${escapeText(item.last_checked)}\n${window.NB.t('label.error')}: ${escapeText(window.NB.translateLegacy(item.error_message))}">
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
          if (reachProgressText) reachProgressText.textContent = '准备开始...';

          if (reachSummary) {
            reachSummary.innerHTML = '<span class="text-secondary">正在启动任务...</span>';
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
                if (reachSummary) reachSummary.innerHTML = '<span class="text-warning">未找到可检测的设备</span>';
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
                    if (reachProgressText) reachProgressText.textContent = `正在检测 ${processed}/${total}`;

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
                        if (reachProgressText) reachProgressText.textContent = '检测完成';

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
            if (reachSummary) reachSummary.innerHTML = '<span class="text-danger">请求失败</span>';
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
