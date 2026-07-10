window.NB = window.NB || {};
        (function () {
          // Auto-expand active menu group
          const activeLink = document.querySelector('.sidebar .nav-link.active');
          if (activeLink) {
            const collapseEl = activeLink.closest('.collapse');
            if (collapseEl) {
              const bs = window.bootstrap;
              if (bs && bs.Collapse) {
                const col = new bs.Collapse(collapseEl, { toggle: false });
                col.show();
                
                // Also update the label state (remove .collapsed)
                const label = document.querySelector(`[data-bs-target="#${collapseEl.id}"]`);
                if (label) {
                  label.classList.remove('collapsed');
                }
              } else {
                // Fallback if BS is not fully ready
                collapseEl.classList.add('show');
                const label = document.querySelector(`[data-bs-target="#${collapseEl.id}"]`);
                if (label) label.classList.remove('collapsed');
              }
            }
          }

          // Check for msg/err in URL
          const urlParams = new URLSearchParams(window.location.search);
          const msg = urlParams.get('msg');
          const err = urlParams.get('err');
          if (msg) window.NB.showToast(msg, 'success');
          if (err) window.NB.showToast(err, 'error');

          document.addEventListener('click', function(e) {
            const btn = e.target.closest('.btn-delete-ask');
            if (!btn) return;
            
            e.preventDefault();
            const targetForm = btn.closest('form');
            const msg = btn.getAttribute('data-confirm-msg');
            
            window.NB.confirmDelete(msg, function() {
                if (targetForm) {
                    HTMLFormElement.prototype.submit.call(targetForm);
                }
            });
          });

          const taskConfig = window.NB_TASK_CONFIG || {};
          const CAN_TRACK_BACKUPS = taskConfig.canTrackBackups === true;
          const CAN_TERMINATE_TASK_RUNS = taskConfig.canTerminateTaskRuns === true;
          const CAN_RETRY_TASK_RUNS = taskConfig.canRetryTaskRuns === true;
          const panel = document.getElementById("nb-job-panel");
          const trigger = document.getElementById("nb-job-trigger");
          const triggerBadge = document.getElementById("nb-job-badge");
          const summary = document.getElementById("nb-job-summary");
          const tbody = document.getElementById("nb-job-tbody");
          const headerMeta = document.getElementById("nb-job-header-meta");
          const closeBtn = document.getElementById("nb-job-close");
          const bulkRetryBtn = document.getElementById("nb-job-bulk-retry");
          const bulkTerminateBtn = document.getElementById("nb-job-bulk-terminate");
          const retryBtn = document.getElementById("nb-job-retry");
          const terminateBtn = document.getElementById("nb-job-terminate");
          const logsToggleBtn = document.getElementById("nb-job-logs-toggle");
          const selectAllCheckbox = document.getElementById("nb-job-select-all");
          const logSection = document.getElementById("nb-job-log-section");
          const logTitle = document.getElementById("nb-job-log-title");
          const logList = document.getElementById("nb-job-log-list");
          const logStatus = document.getElementById("nb-job-log-status");
          const logCloseBtn = document.getElementById("nb-job-log-close");

          const STORAGE_KEY = "nb_backup_jobs";
          const TASK_SOCKET_RECONNECT_DELAY_MS = 1500;

          function saveJobs() {
            try {
              localStorage.setItem(STORAGE_KEY, JSON.stringify({
                version: 2,
                panel_visible: !!state.panelVisible,
              }));
            } catch (e) {
              console.error("Failed to save jobs", e);
            }
          }

          function loadJobs() {
            try {
              const saved = localStorage.getItem(STORAGE_KEY);
              if (saved) {
                const parsed = JSON.parse(saved);
                state.panelVisible = Array.isArray(parsed)
                  ? false
                  : !!(parsed && parsed.panel_visible);
              }
            } catch (e) {
              console.error("Failed to load jobs", e);
            }
            restoreRecentTask({ force: true, openPanel: state.panelVisible }).then((restored) => {
              if (!restored) {
                state.jobs = [];
                state.panelVisible = false;
                try {
                  localStorage.removeItem(STORAGE_KEY);
                } catch (e) {
                  console.error("Failed to clear backup jobs", e);
                }
                render();
                setPanelVisible(false, false);
              }
            });
          }

          async function restoreRecentTask({ force = false, openPanel = false } = {}) {
            if (!CAN_TRACK_BACKUPS || (!force && state.jobs.length)) return false;
            let data = null;
            try {
              const result = await window.NB.api.request("/api/tasks/backups/recent");
              if (result.ok) {
                data = result.data;
              }
            } catch (e) {
              data = null;
            }
            if (!data || data.found === false || !data.track) return false;
            const track = data.track || {};
            const kind = String(track.kind || "").trim();
            const id = String(track.id || "").trim();
            if (!id) return false;
            const job = {
              id,
              run_id: kind === "run" ? id : "",
              backup_id: kind === "backup" ? id : "",
              requested_at: data.requested_at || nowStr(),
              devices: (data.items || []).map((it) => ({
                id: it && it.id ? it.id : "",
                name: it && it.device ? it.device.name : "",
                host: it && it.device ? it.device.host : "",
                started_at: it ? it.started_at || "" : "",
                finished_at: it ? it.finished_at || null : null,
                status: it ? it.status || "" : "",
                success: it ? it.success : false,
                error_message: it ? it.error_message || "" : "",
              })),
              run_status: data.run_status || "",
            };
            state.jobs = [job];
            state.panelVisible = !!openPanel;
            state.selectedBackupIds = [];
            state.taskLogTarget = getDefaultTaskLogTarget();
            state.taskLogsVisible = !!openPanel;
            setTaskChannelMode(typeof window.WebSocket === "undefined" ? "http_fallback" : "connecting");
            saveJobs();
            render();
            ensureTaskSocket();
            syncTaskLogSubscription();
            ensureTimer();
            refreshJobs();
            if (openPanel) {
              setPanelVisible(true);
            }
            return true;
          }

          function setPanelVisible(visible, persist = true) {
            if (!panel) return;
            state.panelVisible = !!visible;
            
            const bs = window.bootstrap;
            if (bs && bs.Offcanvas) {
              let offcanvasInstance = bs.Offcanvas.getInstance(panel);
              if (!offcanvasInstance) {
                offcanvasInstance = new bs.Offcanvas(panel);
                // Bind hide event to update state
                panel.addEventListener('hidden.bs.offcanvas', function () {
                  state.panelVisible = false;
                  saveJobs();
                });
              }
              const isShown = panel.classList.contains('show');
              const isTransitioning = panel.classList.contains('showing') || panel.classList.contains('hiding');
              if (visible) {
                if (!isShown && !isTransitioning) {
                  offcanvasInstance.show();
                }
              } else {
                if (isShown || isTransitioning) {
                  offcanvasInstance.hide();
                }
              }
            } else {
              // Fallback
              panel.style.display = visible ? "block" : "none";
            }
            
            if (persist) {
              saveJobs();
            }
          }

          function togglePanel() {
            if (!state.jobs.length) {
              setPanelVisible(false);
              return;
            }
            const isVisible = panel.classList.contains('show');
            setPanelVisible(!isVisible);
          }

          let latestTaskPanelOpening = false;

          async function openLatestTaskPanel() {
            if (latestTaskPanelOpening) return;
            latestTaskPanelOpening = true;
            try {
              const restored = await restoreRecentTask({ force: true, openPanel: true });
              if (!restored) {
                state.jobs = [];
                state.panelVisible = false;
                try {
                  localStorage.removeItem(STORAGE_KEY);
                } catch (e) {
                  console.error("Failed to clear backup jobs", e);
                }
                render();
                setPanelVisible(false);
                if (window.NB && typeof window.NB.showToast === "function") {
                  window.NB.showToast("\u672a\u83b7\u53d6\u5230\u6700\u8fd1\u5907\u4efd\u4efb\u52a1", "warning");
                }
              }
            } finally {
              latestTaskPanelOpening = false;
            }
          }

          const backupViewModalEl = document.getElementById("backup-view-modal");
          const backupViewTitle = document.getElementById("backup-view-title");
          const backupViewMeta = document.getElementById("backup-view-meta");
          const backupViewLoading = document.getElementById("backup-view-loading");
          const backupViewError = document.getElementById("backup-view-error");
          const backupViewRender = document.getElementById("backup-view-render");
          const backupViewDownload = document.getElementById("backup-view-download");
          const backupViewFullscreen = document.getElementById("backup-view-fullscreen");
          const backupViewFullscreenIcon = document.getElementById("backup-view-fullscreen-icon");
          const backupLogModalEl = document.getElementById("backup-log-modal");
          const backupLogTitle = document.getElementById("backup-log-title");
          const backupLogMeta = document.getElementById("backup-log-meta");
          const backupLogLoading = document.getElementById("backup-log-loading");
          const backupLogError = document.getElementById("backup-log-error");
          const backupLogList = document.getElementById("backup-log-list");
          let backupViewModal = null;
          let backupViewIsFullscreen = false;
          let backupLogModal = null;

          if (!panel || !tbody || !summary) return;

          const state = {
            jobs: [],
            timer: null,
            panelVisible: false,
            socket: null,
            socketConnected: false,
            socketRetryTimer: null,
            taskChannelMode: "idle",
            retryingRunId: "",
            terminatingRunId: "",
            bulkRetryingRunId: "",
            bulkTerminatingRunId: "",
            selectedBackupIds: [],
            taskLogs: [],
            taskLogsCursor: 0,
            taskLogSequence: 0,
            taskLogsVisible: false,
            taskLogTarget: null,
            summaryFilter: "all",
            warningMessage: "",
          };

          function setTaskChannelMode(mode) {
            state.taskChannelMode = String(mode || "idle");
            renderTaskLogs();
          }

          function getTaskChannelModeMeta() {
            switch (String(state.taskChannelMode || "idle")) {
              case "connecting":
                return { text: "实时通道连接中", badgeClass: "text-secondary" };
              case "websocket_event_bus":
                return { text: "事件实时推送", badgeClass: "text-success" };
              case "websocket_snapshot":
                return { text: "WebSocket 轮询同步", badgeClass: "text-info" };
              case "http_fallback":
                return { text: "已降级为 HTTP 轮询", badgeClass: "text-warning" };
              default:
                return { text: "等待建立同步通道", badgeClass: "text-secondary" };
            }
          }

          function getCurrentTrackedJob() {
            return state.jobs.length ? state.jobs[0] : null;
          }

          function getCurrentTrackPayload(action = "subscribe") {
            const job = getCurrentTrackedJob();
            if (!job) return null;
            const runId = job.run_id ? String(job.run_id).trim() : "";
            const backupId = job.backup_id ? String(job.backup_id).trim() : "";
            if (runId) return { action, run_id: runId };
            if (backupId) return { action, backup_id: backupId };
            return null;
          }

          function normalizeSelectedBackupIds() {
            const job = getCurrentTrackedJob();
            const validIds = new Set(
              (job && job.run_id ? (job.devices || []) : [])
                .map((device) => String(device && device.id ? device.id : "").trim())
                .filter(Boolean)
            );
            state.selectedBackupIds = (state.selectedBackupIds || []).filter((backupId) => validIds.has(String(backupId || "").trim()));
          }

          function getSelectedBackupIds() {
            normalizeSelectedBackupIds();
            return Array.from(new Set((state.selectedBackupIds || []).map((backupId) => String(backupId || "").trim()).filter(Boolean)));
          }

          function getSelectedDevices(job = getCurrentTrackedJob()) {
            if (!job || !job.run_id) return [];
            const selectedIds = new Set(getSelectedBackupIds());
            return (job.devices || []).filter((device) => selectedIds.has(String(device && device.id ? device.id : "").trim()));
          }

          function isBackupSelected(backupId) {
            const wanted = String(backupId || "").trim();
            if (!wanted) return false;
            return getSelectedBackupIds().includes(wanted);
          }

          function setSelectedBackupId(backupId, selected) {
            const wanted = String(backupId || "").trim();
            if (!wanted) return;
            const next = new Set(getSelectedBackupIds());
            if (selected) {
              next.add(wanted);
            } else {
              next.delete(wanted);
            }
            state.selectedBackupIds = Array.from(next);
            render();
          }

          function setAllSelectedBackups(selected) {
            const job = getCurrentTrackedJob();
            if (!job || !job.run_id) {
              state.selectedBackupIds = [];
              render();
              return;
            }
            if (selected) {
              state.selectedBackupIds = (job.devices || [])
                .map((device) => String(device && device.id ? device.id : "").trim())
                .filter(Boolean);
            } else {
              state.selectedBackupIds = [];
            }
            render();
          }

          function buildLogTargetPayload(target, action = "subscribe_logs") {
            if (!target || typeof target !== "object") return null;
            const kind = String(target.kind || "").trim();
            const id = String(target.id || "").trim();
            if (!kind || !id) return null;
            if (kind === "run") return { action, run_id: id };
            if (kind === "backup") return { action, backup_id: id };
            return null;
          }

          function getDefaultTaskLogTarget() {
            const payload = getCurrentTrackPayload("subscribe_logs");
            if (!payload) return null;
            if (payload.run_id) {
              return { kind: "run", id: String(payload.run_id), label: "批次实时日志" };
            }
            if (payload.backup_id) {
              return { kind: "backup", id: String(payload.backup_id), label: "任务实时日志" };
            }
            return null;
          }

          function findDeviceByBackupId(backupId) {
            const wanted = String(backupId || "").trim();
            if (!wanted) return null;
            for (const job of state.jobs) {
              for (const device of (job.devices || [])) {
                if (String(device && device.id ? device.id : "").trim() === wanted) {
                  return device;
                }
              }
            }
            return null;
          }

          function syncTaskLogTargetWithJobs() {
            if (!state.taskLogTarget) {
              state.taskLogTarget = getDefaultTaskLogTarget();
              return;
            }
            if (state.taskLogTarget.kind === "run") {
              const current = getCurrentTrackedJob();
              const runId = current && current.run_id ? String(current.run_id).trim() : "";
              if (!runId || runId !== String(state.taskLogTarget.id || "").trim()) {
                state.taskLogTarget = getDefaultTaskLogTarget();
                resetTaskLogs();
              }
              return;
            }
            const device = findDeviceByBackupId(state.taskLogTarget.id);
            if (!device) {
              state.taskLogTarget = getDefaultTaskLogTarget();
              resetTaskLogs();
              return;
            }
            const name = String(device.name || "").trim();
            const host = String(device.host || "").trim();
            const suffix = name || host
              ? `${name || host}${host && name && host !== name ? ` (${host})` : ""}`
              : "";
            state.taskLogTarget = {
              kind: "backup",
              id: String(device.id || ""),
              label: suffix ? `设备实时日志: ${suffix}` : "设备实时日志",
            };
          }

          function setTaskLogTarget(target, { reset = true, visible = true } = {}) {
            state.taskLogTarget = target || getDefaultTaskLogTarget();
            if (visible) {
              state.taskLogsVisible = true;
            }
            if (reset) {
              resetTaskLogs();
            } else {
              renderTaskLogs();
            }
            syncTaskLogSubscription();
            render();
          }

          function taskSocketUrl() {
            const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
            return `${protocol}//${window.location.host}/ws/tasks/backups`;
          }

          function clearTaskSocketRetry() {
            if (!state.socketRetryTimer) return;
            clearTimeout(state.socketRetryTimer);
            state.socketRetryTimer = null;
          }

          function sendTaskSocketMessage(payload) {
            if (!state.socket || state.socket.readyState !== window.WebSocket.OPEN) return false;
            try {
              state.socket.send(JSON.stringify(payload));
              return true;
            } catch (e) {
              return false;
            }
          }

          function getTaskLogDetailLabels(item) {
            const targetKind = state.taskLogTarget && state.taskLogTarget.kind ? String(state.taskLogTarget.kind) : "";
            if (targetKind !== "run") return [];
            const details = item && item.details && typeof item.details === "object" ? item.details : {};
            const labels = [];
            const labelMap = {
              schedule_id: "计划",
              trigger: "触发",
              status: "状态",
              planned_count: "计划",
              total_devices: "设备",
              job_count: "任务",
              backup_count: "跟踪",
              enqueued_count: "入队",
              failed_count: "失败",
              success_count: "成功",
              fail_count: "失败",
              cancelled_count: "已终止",
              unfinished_count: "未完成",
              terminated_records: "终止",
              skipped_records: "跳过",
              running_records: "运行中",
              selected_records: "已选",
              retried_records: "重试",
              enqueue_status: "入队状态",
              poll_seconds: "检查间隔",
              time_limit_seconds: "超时",
              failure_type: "失败类型",
              reason: "原因",
              source_run_id: "来源批次",
            };
            const orderedKeys = [
              "schedule_id",
              "trigger",
              "status",
              "planned_count",
              "total_devices",
              "job_count",
              "backup_count",
              "enqueued_count",
              "success_count",
              "fail_count",
              "failed_count",
              "cancelled_count",
              "unfinished_count",
              "terminated_records",
              "skipped_records",
              "running_records",
              "selected_records",
              "retried_records",
              "enqueue_status",
              "poll_seconds",
              "time_limit_seconds",
              "failure_type",
              "reason",
              "source_run_id",
            ];
            orderedKeys.forEach((key) => {
              const value = details[key] ?? item[key];
              if (value === undefined || value === null || value === "") return;
              const valueText = key.endsWith("_id") && String(value).length > 18
                ? `${String(value).slice(0, 8)}...`
                : String(value);
              labels.push(`${labelMap[key] || key}: ${valueText}`);
            });
            return labels;
          }

          function renderTaskLogDetailLabels(item) {
            return "";
          }

          function shouldCollapseRunLogEvent(item) {
            if (!item || !state.taskLogTarget || state.taskLogTarget.kind !== "run") return false;
            const eventName = String(item.event || "").trim();
            return eventName === "finalize_schedule_run_started";
          }

          function renderTaskLogs() {
            if (!logSection || !logList || !logStatus || !logTitle) return;
            logSection.classList.toggle("d-none", !state.taskLogsVisible || !state.jobs.length);
            if (!state.jobs.length) {
              logTitle.textContent = "任务实时日志";
              logStatus.textContent = "未跟踪任务";
              logList.innerHTML = '<div class="text-secondary opacity-75">暂无实时日志</div>';
              return;
            }
            if (!state.taskLogsVisible) {
              return;
            }
            syncTaskLogTargetWithJobs();
            logTitle.textContent = (state.taskLogTarget && state.taskLogTarget.label) || "任务实时日志";
            logStatus.textContent = getTaskChannelModeMeta().text;
            if (!state.taskLogs.length) {
              logList.innerHTML = '<div class="text-secondary opacity-75">暂无日志</div>';
              return;
            }
            const toneMap = {
              success: "text-success",
              warning: "text-warning",
              error: "text-danger",
              info: "text-info",
            };
            logList.innerHTML = state.taskLogs.map((item) => {
              const toneClass = toneMap[String(item.tone || "")] || "text-light";
              const timeText = escapeText(item.created_at || "");
              const messageText = escapeText(item.message || "");
              const eventText = escapeText(item.event || "");
              return `<div class="py-1 border-bottom border-secondary-subtle">
                <div class="d-flex align-items-start gap-2">
                  <span class="text-secondary opacity-75 text-nowrap">${timeText}</span>
                  <span class="${toneClass} flex-grow-1">${messageText}</span>
                </div>
                <div class="x-small text-secondary opacity-75 mt-1">事件: ${eventText}</div>
              </div>`;
            }).join("");
            logList.scrollTop = logList.scrollHeight;
          }

          function resetTaskLogs() {
            state.taskLogs = [];
            state.taskLogsCursor = 0;
            state.taskLogSequence = 0;
            renderTaskLogs();
          }

          function stopFallbackPolling() {
            stopTimer();
            if (state.taskChannelMode === "http_fallback") {
              setTaskChannelMode(state.socketConnected ? "connecting" : "idle");
            }
          }

          function enableFallbackPolling() {
            if (!state.jobs.length) {
              stopTimer();
              setTaskChannelMode("idle");
              return;
            }
            setTaskChannelMode("http_fallback");
            ensureTimer();
            refreshJobs();
          }

          function isTaskLogPayloadForCurrentTarget(data) {
            const payloadTrack = data && data.track && typeof data.track === "object" ? data.track : null;
            const currentTarget = state.taskLogTarget || getDefaultTaskLogTarget();
            if (!payloadTrack || !currentTarget) return true;
            const payloadKind = String(payloadTrack.kind || "").trim();
            const payloadId = String(payloadTrack.id || "").trim();
            const targetKind = String(currentTarget.kind || "").trim();
            const targetId = String(currentTarget.id || "").trim();
            return payloadKind === targetKind && payloadId === targetId;
          }

          function taskLogSortValue(item) {
            const id = Number(item && item.id ? item.id : 0);
            if (id > 0) return { bucket: 0, value: id };
            const timeValue = Date.parse(String(item && item.created_at ? item.created_at : ""));
            if (Number.isFinite(timeValue)) return { bucket: 1, value: timeValue };
            return { bucket: 2, value: Number(item && item._received_seq ? item._received_seq : 0) };
          }

          function taskLogFinalPhase(item) {
            const eventName = String(item && item.event ? item.event : "").trim();
            if (eventName === "backup_record_task_succeeded" || eventName === "backup_record_task_failed") return 1;
            if (eventName === "backup_record_alert_check_started") return 2;
            if (eventName === "backup_record_alert_check_completed") return 3;
            return 0;
          }

          function shouldUseFinalPhaseOrder(a, b) {
            const leftPhase = taskLogFinalPhase(a);
            const rightPhase = taskLogFinalPhase(b);
            if (!leftPhase || !rightPhase) return false;
            const leftRecordId = String(a && a.record_id ? a.record_id : "");
            const rightRecordId = String(b && b.record_id ? b.record_id : "");
            return !!leftRecordId && leftRecordId === rightRecordId;
          }

          function compareTaskLogItems(a, b) {
            if (shouldUseFinalPhaseOrder(a, b)) {
              const phaseDelta = taskLogFinalPhase(a) - taskLogFinalPhase(b);
              if (phaseDelta !== 0) return phaseDelta;
            }
            const left = taskLogSortValue(a);
            const right = taskLogSortValue(b);
            if (left.bucket !== right.bucket) return left.bucket - right.bucket;
            if (left.value !== right.value) return left.value - right.value;
            return Number(a && a._received_seq ? a._received_seq : 0) - Number(b && b._received_seq ? b._received_seq : 0);
          }

          function applyTaskLogs(data) {
            if (!data || typeof data !== "object") return;
            if (!isTaskLogPayloadForCurrentTarget(data)) return;
            if (data.found === false) {
              resetTaskLogs();
              return;
            }
            if (data.reset) {
              state.taskLogs = [];
            }
            const seen = new Set(state.taskLogs.map((item) => Number(item.id || 0)));
            const collapsedRunEvents = new Set(
              state.taskLogs
                .filter((item) => shouldCollapseRunLogEvent(item))
                .map((item) => String(item.event || "").trim())
                .filter(Boolean)
            );
            (data.items || []).forEach((item) => {
              const logId = Number(item && item.id ? item.id : 0);
              if (logId && seen.has(logId)) {
                return;
              }
              if (shouldCollapseRunLogEvent(item)) {
                const eventName = String(item.event || "").trim();
                if (collapsedRunEvents.has(eventName)) {
                  return;
                }
                collapsedRunEvents.add(eventName);
              }
              if (logId) {
                seen.add(logId);
              }
              item._received_seq = ++state.taskLogSequence;
              state.taskLogs.push(item);
            });
            state.taskLogs.sort(compareTaskLogItems);
            if (state.taskLogs.length > 200) {
              state.taskLogs = state.taskLogs.slice(-200);
            }
            state.taskLogsCursor = Number(data.next_after_id || state.taskLogsCursor || 0);
            renderTaskLogs();
          }

          function closeTaskSocket({ reconnect = false } = {}) {
            clearTaskSocketRetry();
            const socket = state.socket;
            state.socket = null;
            state.socketConnected = false;
            if (!reconnect) {
              setTaskChannelMode("idle");
            }
            if (socket) {
              try {
                socket.onopen = null;
                socket.onmessage = null;
                socket.onclose = null;
                socket.onerror = null;
                socket.close();
              } catch (e) {
                console.error("Failed to close task socket", e);
              }
            }
            if (reconnect && state.jobs.length) {
              enableFallbackPolling();
              state.socketRetryTimer = setTimeout(() => {
                state.socketRetryTimer = null;
                ensureTaskSocket();
              }, TASK_SOCKET_RECONNECT_DELAY_MS);
            }
          }

          function scheduleTaskSocketReconnect() {
            if (!state.jobs.length || state.socketRetryTimer) return;
            enableFallbackPolling();
            state.socketRetryTimer = setTimeout(() => {
              state.socketRetryTimer = null;
              ensureTaskSocket();
            }, TASK_SOCKET_RECONNECT_DELAY_MS);
          }

          function applyTaskSnapshot(data) {
            if (!data || typeof data !== "object") return;
            if (data.found === false) {
              state.jobs = [];
              resetTaskLogs();
              saveJobs();
              stopTimer();
              closeTaskSocket();
              render();
              return;
            }
            const devices = (data.items || []).map((it) => ({
              id: it && it.id ? it.id : "",
              name: it && it.device ? it.device.name : "",
              host: it && it.device ? it.device.host : "",
              started_at: it ? it.started_at || "" : "",
              finished_at: it ? it.finished_at || null : null,
              status: it ? it.status || "" : "",
              success: it ? it.success : false,
              error_message: it ? it.error_message || "" : "",
            }));
            state.jobs = state.jobs.map((job) => ({
              ...job,
              devices,
              run_status: data && data.run_status ? data.run_status : "",
            }));
            normalizeSelectedBackupIds();
            syncTaskLogTargetWithJobs();
            if (state.socketConnected) {
              stopFallbackPolling();
            }
            render();
            const running = state.jobs.some((j) => j.devices && j.devices.some((d) => isActiveBackupStatus(d.status)));
            if (!running) {
              stopTimer();
            }
          }

          function handleTaskSocketMessage(raw) {
            let data = null;
            try {
              data = JSON.parse(raw);
            } catch (e) {
              return;
            }
            if (!data || typeof data !== "object") return;
            if (data.type === "hello") {
              const deliveryMode = String(data.delivery_mode || "").trim();
              if (deliveryMode === "event_bus") {
                setTaskChannelMode("websocket_event_bus");
              } else {
                setTaskChannelMode("websocket_snapshot");
              }
              return;
            }
            if (data.type === "task_snapshot") {
              stopTimer();
              applyTaskSnapshot(data);
              return;
            }
            if (data.type === "task_logs") {
              applyTaskLogs(data);
              return;
            }
            if (data.type === "task_command_result") {
              if (data.action === "retry_run") {
                state.retryingRunId = "";
                if (data.ok && data.new_run_id && window.NB && typeof window.NB.trackBackups === "function") {
                  window.NB.trackBackups({
                    run_id: data.new_run_id,
                  });
                } else {
                  render();
                }
              }
              if (data.action === "retry_selected") {
                state.bulkRetryingRunId = "";
                state.selectedBackupIds = [];
                if (data.ok && data.new_run_id && window.NB && typeof window.NB.trackBackups === "function") {
                  window.NB.trackBackups({
                    run_id: data.new_run_id,
                  });
                } else {
                  render();
                }
              }
              if (data.action === "terminate_run") {
                state.terminatingRunId = "";
                render();
              }
              if (data.action === "terminate_selected") {
                state.bulkTerminatingRunId = "";
                state.selectedBackupIds = [];
                render();
              }
              if (data.ok) {
                window.NB.showToast(data.message || "操作已提交", "success");
                refreshJobs();
              } else {
                window.NB.showToast(data.message || "操作失败", "error");
              }
              return;
            }
            if (data.type === "task_error") {
              state.retryingRunId = "";
              state.terminatingRunId = "";
              state.bulkRetryingRunId = "";
              state.bulkTerminatingRunId = "";
              render();
              window.NB.showToast(data.message || "任务通道异常", "warning");
            }
          }

          function subscribeTaskSocket() {
            const payload = getCurrentTrackPayload("subscribe");
            if (!payload) return false;
            const subscribed = sendTaskSocketMessage(payload);
            if (subscribed && state.taskLogsVisible) {
              syncTaskLogTargetWithJobs();
              const logPayload = buildLogTargetPayload(state.taskLogTarget || getDefaultTaskLogTarget(), "subscribe_logs");
              if (logPayload) {
                if (state.taskLogsCursor > 0) {
                  logPayload.after_id = state.taskLogsCursor;
                }
                sendTaskSocketMessage(logPayload);
              }
            }
            return subscribed;
          }

          function syncTaskLogSubscription() {
            if (!state.socketConnected) {
              renderTaskLogs();
              return false;
            }
            if (state.taskLogsVisible) {
              syncTaskLogTargetWithJobs();
              const payload = buildLogTargetPayload(state.taskLogTarget || getDefaultTaskLogTarget(), "subscribe_logs");
              if (!payload) return false;
              if (state.taskLogsCursor > 0) {
                payload.after_id = state.taskLogsCursor;
              }
              return sendTaskSocketMessage(payload);
            }
            return sendTaskSocketMessage({ action: "unsubscribe_logs" });
          }

          function ensureTaskSocket() {
            if (!CAN_TRACK_BACKUPS || !state.jobs.length || typeof window.WebSocket === "undefined") {
              if (state.jobs.length) {
                enableFallbackPolling();
              }
              return;
            }
            if (state.socket) {
              const readyState = state.socket.readyState;
              if (readyState === window.WebSocket.OPEN) {
                subscribeTaskSocket();
                return;
              }
              if (readyState === window.WebSocket.CONNECTING) {
                return;
              }
            }
            clearTaskSocketRetry();
            setTaskChannelMode("connecting");
            const socket = new window.WebSocket(taskSocketUrl());
            state.socket = socket;
            socket.onopen = function() {
              if (state.socket !== socket) return;
              state.socketConnected = true;
              stopFallbackPolling();
              subscribeTaskSocket();
            };
            socket.onmessage = function(event) {
              if (typeof event.data === "string") {
                handleTaskSocketMessage(event.data);
              }
            };
            socket.onerror = function() {
              // Let close handler deal with fallback/reconnect.
            };
            socket.onclose = function() {
              if (state.socket === socket) {
                state.socket = null;
              }
              state.socketConnected = false;
              if (state.jobs.length) {
                scheduleTaskSocketReconnect();
              } else {
                setTaskChannelMode("idle");
              }
            };
          }

          function canTerminateTrackedRun(job) {
            if (!CAN_TERMINATE_TASK_RUNS || !job) return false;
            const runId = job.run_id ? String(job.run_id).trim() : "";
            if (!runId) return false;
            const runStatus = String(job.run_status || "").trim();
            if (runStatus) {
              return isActiveScheduleRunStatus(runStatus);
            }
            return !!(job.devices || []).some((d) => isActiveBackupStatus(d.status));
          }

          function canBulkTerminateTrackedRun(job) {
            if (!CAN_TERMINATE_TASK_RUNS || !job) return false;
            const runId = job.run_id ? String(job.run_id).trim() : "";
            if (!runId) return false;
            return getSelectedDevices(job).some((d) => ["planned", "queued"].includes(String(d && d.status ? d.status : "").trim()));
          }

          function canRetryTrackedRun(job) {
            if (!CAN_RETRY_TASK_RUNS || !job) return false;
            const runId = job.run_id ? String(job.run_id).trim() : "";
            if (!runId) return false;
            if ((job.devices || []).some((d) => isActiveBackupStatus(d.status))) {
              return false;
            }
            return !!(job.devices || []).some((d) => {
              const status = String(d && d.status ? d.status : "").trim();
              return status === "failed" || status === "cancelled";
            });
          }

          function canBulkRetryTrackedRun(job) {
            if (!CAN_RETRY_TASK_RUNS || !job) return false;
            const runId = job.run_id ? String(job.run_id).trim() : "";
            if (!runId) return false;
            return getSelectedDevices(job).some((d) => {
              const status = String(d && d.status ? d.status : "").trim();
              return status === "failed" || status === "cancelled";
            });
          }

          async function retryTrackedRun() {
            const job = getCurrentTrackedJob();
            if (!canRetryTrackedRun(job)) return;
            const runId = String(job.run_id || "").trim();
            if (!runId || state.retryingRunId === runId) return;
            state.retryingRunId = runId;
            render();
            try {
              const sentBySocket = state.socketConnected && sendTaskSocketMessage({
                action: "retry_run",
                run_id: runId,
              });
              if (sentBySocket) {
                return;
              }
              const result = await window.NB.api.request(`/api/schedules/runs/${encodeURIComponent(runId)}/retry`, { method: "POST" });
              const data = result.data || null;
              if (!result.ok) {
                const msg = await window.NB.api.extractErrorDetail(result.response, "????");
                throw new Error(msg);
              }
              if (data && data.new_run_id && window.NB && typeof window.NB.trackBackups === "function") {
                window.NB.trackBackups({
                  run_id: data.new_run_id,
                });
              }
              if (data && data.message && window.NB && typeof window.NB.showToast === "function") {
                window.NB.showToast(data.message, data.enqueue_status === "partial" ? "warning" : "success");
              }
            } catch (e) {
              console.error(e);
              if (window.NB && typeof window.NB.showToast === "function") {
                window.NB.showToast("重试失败: " + e.message, "error");
              }
            } finally {
              state.retryingRunId = "";
              render();
            }
          }

          async function retrySelectedTrackedRun() {
            const job = getCurrentTrackedJob();
            if (!canBulkRetryTrackedRun(job)) return;
            const runId = String(job.run_id || "").trim();
            const backupIds = getSelectedBackupIds();
            if (!runId || !backupIds.length || state.bulkRetryingRunId === runId) return;
            state.bulkRetryingRunId = runId;
            render();
            try {
              const sentBySocket = state.socketConnected && sendTaskSocketMessage({
                action: "retry_selected",
                run_id: runId,
                backup_ids: backupIds,
              });
              if (sentBySocket) {
                return;
              }
              const result = await window.NB.api.request(`/api/schedules/runs/${encodeURIComponent(runId)}/retry-selected`, {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({ backup_ids: backupIds }),
              });
              const data = result.data || null;
              if (!result.ok) {
                const msg = await window.NB.api.extractErrorDetail(result.response, "??????");
                throw new Error(msg);
              }
              state.selectedBackupIds = [];
              if (data && data.new_run_id && window.NB && typeof window.NB.trackBackups === "function") {
                window.NB.trackBackups({
                  run_id: data.new_run_id,
                });
              }
              if (data && data.message && window.NB && typeof window.NB.showToast === "function") {
                window.NB.showToast(data.message, data.enqueue_status === "partial" ? "warning" : "success");
              }
            } catch (e) {
              console.error(e);
              if (window.NB && typeof window.NB.showToast === "function") {
                window.NB.showToast("批量重试失败: " + e.message, "error");
              }
            } finally {
              state.bulkRetryingRunId = "";
              render();
            }
          }

          async function terminateTrackedRun() {
            const job = getCurrentTrackedJob();
            if (!canTerminateTrackedRun(job)) return;
            const runId = String(job.run_id || "").trim();
            if (!runId || state.terminatingRunId === runId) return;
            state.terminatingRunId = runId;
            render();
            try {
              const sentBySocket = state.socketConnected && sendTaskSocketMessage({
                action: "terminate_run",
                run_id: runId,
              });
              if (sentBySocket) {
                return;
              }
              const result = await window.NB.api.request(`/api/schedules/runs/${encodeURIComponent(runId)}/terminate`, { method: "POST" });
              const data = result.data || null;
              if (!result.ok) {
                const msg = await window.NB.api.extractErrorDetail(result.response, "????");
                throw new Error(msg);
              }
              if (data && data.message && window.NB && typeof window.NB.showToast === "function") {
                window.NB.showToast(data.message, "info");
              }
              refreshJobs();
            } catch (e) {
              console.error(e);
              if (window.NB && typeof window.NB.showToast === "function") {
                window.NB.showToast("终止失败: " + e.message, "error");
              }
            } finally {
              state.terminatingRunId = "";
              render();
            }
          }

          async function terminateSelectedTrackedRun() {
            const job = getCurrentTrackedJob();
            if (!canBulkTerminateTrackedRun(job)) return;
            const runId = String(job.run_id || "").trim();
            const backupIds = getSelectedBackupIds();
            if (!runId || !backupIds.length || state.bulkTerminatingRunId === runId) return;
            state.bulkTerminatingRunId = runId;
            render();
            try {
              const sentBySocket = state.socketConnected && sendTaskSocketMessage({
                action: "terminate_selected",
                run_id: runId,
                backup_ids: backupIds,
              });
              if (sentBySocket) {
                return;
              }
              const result = await window.NB.api.request(`/api/schedules/runs/${encodeURIComponent(runId)}/terminate-selected`, {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                },
                body: JSON.stringify({ backup_ids: backupIds }),
              });
              const data = result.data || null;
              if (!result.ok) {
                const msg = await window.NB.api.extractErrorDetail(result.response, "??????");
                throw new Error(msg);
              }
              state.selectedBackupIds = [];
              if (data && data.message && window.NB && typeof window.NB.showToast === "function") {
                window.NB.showToast(data.message, "info");
              }
              refreshJobs();
            } catch (e) {
              console.error(e);
              if (window.NB && typeof window.NB.showToast === "function") {
                window.NB.showToast("批量终止失败: " + e.message, "error");
              }
            } finally {
              state.bulkTerminatingRunId = "";
              render();
            }
          }

          function escapeText(text) {
            const span = document.createElement("span");
            span.textContent = text == null ? "" : String(text);
            return span.innerHTML;
          }

          function countLines(text) {
            if (!text) return 0;
            let count = 1;
            for (let i = 0; i < text.length; i++) {
              if (text.charCodeAt(i) === 10) count++;
            }
            return count;
          }

          function renderBackupText(text) {
            if (!backupViewRender) return;
            const normalized = (text || "").replace(/\r\n/g, "\n");
            backupViewRender.innerHTML = "";

            const lineCount = countLines(normalized);
            const MAX_TABLE_LINES = 1500;
            const MAX_LINENUM_LINES = 8000;

            if (lineCount <= MAX_TABLE_LINES) {
              const table = document.createElement("table");
              table.className = "diff-table";
              const tbodyEl = document.createElement("tbody");
              const lines = normalized.split("\n");
              const frag = document.createDocumentFragment();
              lines.forEach((line, idx) => {
                const tr = document.createElement("tr");
                tr.className = "diff-row diff-context";
                const tdLn = document.createElement("td");
                tdLn.className = "diff-ln";
                tdLn.textContent = String(idx + 1);
                const tdTxt = document.createElement("td");
                tdTxt.className = "diff-code";
                tdTxt.textContent = line;
                tr.appendChild(tdLn);
                tr.appendChild(tdTxt);
                frag.appendChild(tr);
              });
              tbodyEl.appendChild(frag);
              table.appendChild(tbodyEl);
              backupViewRender.appendChild(table);
              return;
            }

            if (lineCount <= MAX_LINENUM_LINES) {
              const pre = document.createElement("pre");
              pre.className = "nb-backup-pre nb-backup-pre-linenums";
              const code = document.createElement("code");
              const lines = normalized.split("\n");
              const frag = document.createDocumentFragment();
              lines.forEach((line) => {
                const span = document.createElement("span");
                span.textContent = line;
                frag.appendChild(span);
              });
              code.appendChild(frag);
              pre.appendChild(code);
              backupViewRender.appendChild(pre);
              return;
            }

            const pre = document.createElement("pre");
            pre.className = "nb-backup-pre nb-backup-pre-plain";
            pre.textContent = normalized;
            backupViewRender.appendChild(pre);
          }

          function setBackupViewFullscreen(next) {
            if (!backupViewModalEl) return;
            const dialog = backupViewModalEl.querySelector(".modal-dialog");
            if (!dialog) return;
            const want = !!next;
            backupViewIsFullscreen = want;
            dialog.classList.toggle("modal-fullscreen", want);
            dialog.classList.toggle("modal-xl", !want);
            if (backupViewFullscreenIcon) {
              backupViewFullscreenIcon.className = want ? "bi bi-fullscreen-exit" : "bi bi-arrows-fullscreen";
            }
            if (backupViewFullscreen) {
              backupViewFullscreen.setAttribute("aria-label", want ? "退出全屏" : "全屏");
            }
          }

          async function resolveBackupViewErrorMessage(resp) {
            if (!resp) return "备份内容加载失败";

            const detail = window.NB.api
              ? await window.NB.api.extractErrorDetail(resp, "")
              : "";

            if (resp.status === 403) return "当前账号无权限查看备份内容";
            if (resp.status === 404) return detail || "备份记录不存在";
            return detail || "备份内容加载失败";
          }

          async function openBackupView(backupId) {
            if (!backupId || !backupViewModalEl) return;
            if (!backupViewModal) {
              const bs = window.bootstrap;
              if (!bs || !bs.Modal) return;
              backupViewModal = new bs.Modal(backupViewModalEl);
            }
            if (backupViewTitle) backupViewTitle.textContent = "备份详情";
            if (backupViewMeta) backupViewMeta.textContent = "";
            if (backupViewError) {
              backupViewError.classList.add("d-none");
              backupViewError.textContent = "";
            }
            if (backupViewRender) backupViewRender.innerHTML = "";
            if (backupViewLoading) backupViewLoading.classList.remove("d-none");
            if (backupViewDownload) {
              backupViewDownload.classList.add("d-none");
              backupViewDownload.href = "#";
            }
            if (backupViewFullscreen) {
              backupViewFullscreen.classList.remove("d-none");
            }
            backupViewModal.show();

            let result;
            try {
              result = await window.NB.api.request(`/api/backups/${encodeURIComponent(backupId)}`);
            } catch (e) {
              result = null;
            }
            if (backupViewLoading) backupViewLoading.classList.add("d-none");
            if (!result || !result.ok) {
              if (backupViewError) {
                backupViewError.textContent = await resolveBackupViewErrorMessage(result ? result.response : null);
                backupViewError.classList.remove("d-none");
              }
              return;
            }
            const data = result.data || {};
            const device = data && data.device ? data.device : {};
            const record = data && data.record ? data.record : {};

            if (backupViewMeta) {
              backupViewMeta.textContent = `${device.name || ""} · ${device.host || ""} · ${record.started_at || ""}`;
            }

            const err = record.error_message || "";
            if (err && backupViewError) {
              backupViewError.textContent = err;
              backupViewError.classList.remove("d-none");
            }

            if (backupViewDownload) {
              backupViewDownload.href = `/backups/${encodeURIComponent(backupId)}/download`;
              backupViewDownload.classList.remove("d-none");
            }

            renderBackupText(record.config_text || "");
          }

          function renderBackupLogItems(items) {
            if (!backupLogList) return;
            if (!items || !items.length) {
              backupLogList.innerHTML = '<div class="text-secondary opacity-75 p-2">暂无执行日志</div>';
              return;
            }
            backupLogList.innerHTML = items.map((item) => {
              const toneMap = {
                success: "text-success",
                warning: "text-warning",
                error: "text-danger",
                info: "text-info",
              };
              const toneClass = toneMap[String(item.tone || "")] || "text-info";
              const timeText = escapeText(item.created_at || "");
              const messageText = escapeText(item.message || item.event || "");
              const eventText = escapeText(item.event || "");
              return `<div class="backup-log-item py-2 border-bottom border-secondary-subtle">
                <div class="d-flex align-items-start gap-2">
                  <span class="text-secondary opacity-75 text-nowrap">${timeText}</span>
                  <span class="${toneClass} flex-grow-1">${messageText}</span>
                </div>
                <div class="x-small text-secondary opacity-75 mt-1">事件: ${eventText}</div>
              </div>`;
            }).join("");
            backupLogList.scrollTop = backupLogList.scrollHeight;
          }

          async function openBackupLogView(backupId) {
            if (!backupId || !backupLogModalEl) return;
            const bs = window.bootstrap;
            if (!backupLogModal) {
              if (!bs || !bs.Modal) return;
              backupLogModal = new bs.Modal(backupLogModalEl);
            }
            if (backupLogTitle) backupLogTitle.textContent = "执行日志";
            if (backupLogMeta) backupLogMeta.textContent = "";
            if (backupLogError) {
              backupLogError.classList.add("d-none");
              backupLogError.textContent = "";
            }
            if (backupLogList) backupLogList.innerHTML = "";
            if (backupLogLoading) backupLogLoading.classList.remove("d-none");
            backupLogModal.show();

            let result;
            try {
              result = await window.NB.api.request(`/api/backups/${encodeURIComponent(backupId)}/logs`);
            } catch (e) {
              result = null;
            }
            if (backupLogLoading) backupLogLoading.classList.add("d-none");
            if (!result || !result.ok) {
              if (backupLogError) {
                backupLogError.textContent = await resolveBackupViewErrorMessage(result ? result.response : null);
                backupLogError.classList.remove("d-none");
              }
              return;
            }
            const data = result.data || {};
            const device = data && data.device ? data.device : {};
            const record = data && data.record ? data.record : {};
            if (backupLogMeta) {
              backupLogMeta.textContent = `${device.name || ""} · ${device.host || ""} · ${record.started_at || ""}`;
            }
            renderBackupLogItems(data.items || []);
          }

          function isActiveBackupStatus(status) {
            return ["planned", "queued", "running"].includes(String(status || "").trim());
          }

          function isActiveScheduleRunStatus(status) {
            return ["planned", "dispatching", "running", "finalizing", "cancelling"].includes(String(status || "").trim());
          }

          function applySummaryFilter() {
            const filter = state.summaryFilter || "all";
            const rows = tbody.querySelectorAll(".nb-job-row");
            let visibleCount = 0;
            rows.forEach(row => {
              const status = (row.getAttribute("data-status") || "").trim();
              let show = true;
              if (filter === "active") {
                show = isActiveBackupStatus(status);
              } else if (filter !== "all") {
                show = status === filter;
              }
              row.style.display = show ? "" : "none";
              if (show) visibleCount++;
            });
            // 隐藏空的批次组头
            const groups = tbody.querySelectorAll(".nb-job-group-header");
            groups.forEach((header, idx) => {
              const nextHeader = groups[idx + 1] || null;
              let sibling = header.nextElementSibling;
              let hasVisible = false;
              while (sibling && sibling !== nextHeader) {
                if (sibling.classList.contains("nb-job-row") && sibling.style.display !== "none") {
                  hasVisible = true;
                  break;
                }
                sibling = sibling.nextElementSibling;
              }
              header.style.display = hasVisible ? "" : "none";
            });
          }

          function bindSummaryCardClicks() {
            const cards = summary.querySelectorAll(".nb-job-summary-item");
            cards.forEach(card => {
              card.addEventListener("click", () => {
                cards.forEach(c => c.classList.remove("active"));
                card.classList.add("active");
                state.summaryFilter = card.getAttribute("data-filter") || "all";
                applySummaryFilter();
              });
            });
          }

          function syncSummaryCardActiveState() {
            const filter = state.summaryFilter || "all";
            summary.querySelectorAll(".nb-job-summary-item").forEach((card) => {
              card.classList.toggle("active", (card.getAttribute("data-filter") || "all") === filter);
            });
          }

          function backupStatusMeta(status, fallbackSuccess) {
            const normalized = String(status || "").trim();
            const metaMap = {
              planned: { label: "待计划", tone: "info", icon: "bi-hourglass-split" },
              queued: { label: "已入队", tone: "info", icon: "bi-list-task" },
              running: { label: "运行中", tone: "running", icon: "bi-arrow-repeat" },
              cancelled: { label: "已终止", tone: "warning", icon: "bi-stop-circle" },
              succeeded: { label: "成功", tone: "success", icon: "bi-check-circle" },
              failed: { label: "失败", tone: "failed", icon: "bi-x-circle" },
            };
            if (metaMap[normalized]) {
              return { ...metaMap[normalized], status: normalized };
            }
            if (fallbackSuccess === true) {
              return { label: "成功", tone: "success", icon: "bi-check-circle", status: "succeeded" };
            }
            if (fallbackSuccess === false) {
              return { label: "失败", tone: "failed", icon: "bi-x-circle", status: "failed" };
            }
            return { label: "未知", tone: "info", icon: "bi-question-circle", status: normalized || "unknown" };
          }

          function scheduleRunStatusMeta(status) {
            const normalized = String(status || "").trim();
            const metaMap = {
              planned: { label: "待计划", tone: "info", icon: "bi-hourglass-split" },
              dispatching: { label: "派发中", tone: "info", icon: "bi-send" },
              running: { label: "运行中", tone: "running", icon: "bi-arrow-repeat" },
              finalizing: { label: "收尾中", tone: "running", icon: "bi-hourglass-bottom" },
              cancelling: { label: "终止中", tone: "warning", icon: "bi-slash-circle" },
              cancelled: { label: "已终止", tone: "warning", icon: "bi-stop-circle" },
              partial_cancelled: { label: "部分终止", tone: "warning", icon: "bi-exclamation-octagon" },
              succeeded: { label: "全部成功", tone: "success", icon: "bi-check-all" },
              partial_failed: { label: "部分失败", tone: "failed", icon: "bi-exclamation-triangle" },
              failed: { label: "全部失败", tone: "failed", icon: "bi-x-circle" },
            };
            if (metaMap[normalized]) {
              return { ...metaMap[normalized], status: normalized };
            }
            return { label: "未知", tone: "info", icon: "bi-question-circle", status: normalized || "unknown" };
          }

          function taskStatusMeta(kind, status, fallbackSuccess) {
            if (String(kind || "") === "schedule_run") {
              return scheduleRunStatusMeta(status);
            }
            return backupStatusMeta(status, fallbackSuccess);
          }

          function renderTaskStatusBadge(kind, record) {
            const meta = taskStatusMeta(kind, record ? record.status : "", record ? record.success : null);
            return `<div class="backup-status backup-status-${meta.tone}"><i class="bi ${meta.icon}"></i>${escapeText(meta.label)}</div>`;
          }

          function renderBackupStatusBadge(record) {
            return renderTaskStatusBadge("backup_record", record);
          }

          window.NB.backupStatusMeta = backupStatusMeta;
          window.NB.scheduleRunStatusMeta = scheduleRunStatusMeta;
          window.NB.taskStatusMeta = taskStatusMeta;
          window.NB.renderTaskStatusBadge = renderTaskStatusBadge;
          window.NB.renderBackupStatusBadge = renderBackupStatusBadge;
          window.NB.isActiveBackupStatus = isActiveBackupStatus;
          window.NB.isActiveScheduleRunStatus = isActiveScheduleRunStatus;

          function render() {
            const jobs = state.jobs;
            normalizeSelectedBackupIds();
            const hasActive = jobs.some(j => j.devices && j.devices.some(d => isActiveBackupStatus(d.status)));
            
            // 更新触发器状态
            if (trigger) {
              trigger.classList.toggle("active", hasActive);
            }
            if (triggerBadge) {
              triggerBadge.classList.toggle("d-none", !hasActive);
            }

            if (!jobs.length) {
              state.panelVisible = false;
              state.warningMessage = "";
              stopTimer();
              closeTaskSocket();
              resetTaskLogs();
              setPanelVisible(false, false);
              return;
            }
            setPanelVisible(state.panelVisible, false);
            const allDevices = jobs.reduce((acc, j) => acc + (j.devices ? j.devices.length : 0), 0);
            const runningDevices = jobs.reduce(
              (acc, j) => acc + (j.devices ? j.devices.filter((d) => isActiveBackupStatus(d.status)).length : 0),
              0,
            );
            const successDevices = jobs.reduce(
              (acc, j) => acc + (j.devices ? j.devices.filter((d) => String(d.status || "") === "succeeded").length : 0),
              0,
            );
            const failedDevices = jobs.reduce(
              (acc, j) => acc + (j.devices ? j.devices.filter((d) => String(d.status || "") === "failed").length : 0),
              0,
            );
            const cancelledDevices = jobs.reduce(
              (acc, j) => acc + (j.devices ? j.devices.filter((d) => String(d.status || "") === "cancelled").length : 0),
              0,
            );
            
            summary.innerHTML = `
              <div class="nb-job-summary-item" data-filter="all">
                <span class="nb-job-summary-label">设备总数</span>
                <span class="nb-job-summary-value text-primary">${allDevices}</span>
              </div>
              <div class="nb-job-summary-item" data-filter="active">
                <span class="nb-job-summary-label">进行中</span>
                <span class="nb-job-summary-value text-warning">${runningDevices}</span>
              </div>
              <div class="nb-job-summary-item" data-filter="succeeded">
                <span class="nb-job-summary-label">成功</span>
                <span class="nb-job-summary-value text-success">${successDevices}</span>
              </div>
              <div class="nb-job-summary-item" data-filter="failed">
                <span class="nb-job-summary-label">失败</span>
                <span class="nb-job-summary-value text-danger">${failedDevices}</span>
              </div>
              <div class="nb-job-summary-item cancelled" data-filter="cancelled">
                <span class="nb-job-summary-label">已终止</span>
                <span class="nb-job-summary-value">${cancelledDevices}</span>
              </div>
            `;
            syncSummaryCardActiveState();
            bindSummaryCardClicks();

            // 更新标题栏元数据（显示最新一次请求的信息）
            if (jobs.length > 0) {
              const latest = jobs[0];
              const selectedDevices = getSelectedDevices(latest);
              const channelMeta = getTaskChannelModeMeta();
              const batchStartedAt = latest.requested_at ? escapeText(latest.requested_at) : "";
              headerMeta.innerHTML = `
                <span class="nb-header-badge ${channelMeta.badgeClass}">
                  <i class="bi bi-broadcast"></i>${channelMeta.text}
                </span>
                ${batchStartedAt ? `<span class="nb-header-badge"><i class="bi bi-clock"></i>任务开始时间: ${batchStartedAt}</span>` : ""}
                ${selectedDevices.length ? `<span class="nb-header-badge"><i class="bi bi-check2-square"></i>已选 ${selectedDevices.length} 台</span>` : ""}
              `;
              if (bulkTerminateBtn) {
                const canBulkTerminate = canBulkTerminateTrackedRun(latest);
                const bulkTerminating = state.bulkTerminatingRunId && latest.run_id && state.bulkTerminatingRunId === latest.run_id;
                bulkTerminateBtn.classList.toggle("d-none", !selectedDevices.length);
                bulkTerminateBtn.disabled = !canBulkTerminate || !!bulkTerminating;
                bulkTerminateBtn.textContent = bulkTerminating ? "处理中..." : "终止所选";
              }
              if (bulkRetryBtn) {
                const canBulkRetry = canBulkRetryTrackedRun(latest);
                const bulkRetrying = state.bulkRetryingRunId && latest.run_id && state.bulkRetryingRunId === latest.run_id;
                bulkRetryBtn.classList.toggle("d-none", !selectedDevices.length);
                bulkRetryBtn.disabled = !canBulkRetry || !!bulkRetrying;
                bulkRetryBtn.textContent = bulkRetrying ? "处理中..." : "重试所选";
              }
              if (terminateBtn) {
                const canTerminate = canTerminateTrackedRun(latest);
                const terminating = state.terminatingRunId && latest.run_id && state.terminatingRunId === latest.run_id;
                terminateBtn.classList.toggle("d-none", !canTerminate);
                terminateBtn.disabled = !!terminating;
                terminateBtn.textContent = terminating ? "处理中..." : "终止未运行任务";
              }
              if (retryBtn) {
                const canRetry = canRetryTrackedRun(latest);
                const retrying = state.retryingRunId && latest.run_id && state.retryingRunId === latest.run_id;
                retryBtn.classList.toggle("d-none", !canRetry);
                retryBtn.disabled = !!retrying;
                retryBtn.textContent = retrying ? "处理中..." : "重试失败项";
              }
              if (logsToggleBtn) {
                logsToggleBtn.classList.remove("d-none");
                logsToggleBtn.textContent = "批次日志";
                const isShowingBatchLogs = !!(
                  state.taskLogsVisible &&
                  state.taskLogTarget &&
                  state.taskLogTarget.kind === "run"
                );
                logsToggleBtn.classList.toggle("btn-outline-primary", isShowingBatchLogs);
                logsToggleBtn.classList.toggle("btn-outline-secondary", !isShowingBatchLogs);
              }
            } else {
              headerMeta.innerHTML = '';
              if (bulkTerminateBtn) {
                bulkTerminateBtn.classList.add("d-none");
                bulkTerminateBtn.disabled = false;
                bulkTerminateBtn.textContent = "终止所选";
              }
              if (bulkRetryBtn) {
                bulkRetryBtn.classList.add("d-none");
                bulkRetryBtn.disabled = false;
                bulkRetryBtn.textContent = "重试所选";
              }
              if (terminateBtn) {
                terminateBtn.classList.add("d-none");
                terminateBtn.disabled = false;
                terminateBtn.textContent = "终止未运行任务";
              }
              if (retryBtn) {
                retryBtn.classList.add("d-none");
                retryBtn.disabled = false;
                retryBtn.textContent = "重试失败项";
              }
              if (logsToggleBtn) {
                logsToggleBtn.classList.add("d-none");
                logsToggleBtn.classList.remove("btn-outline-primary");
                logsToggleBtn.classList.add("btn-outline-secondary");
                logsToggleBtn.textContent = "批次日志";
              }
            }
            renderTaskLogs();

            const rows = [];
            const warningMessage = String(state.warningMessage || "").trim();
            if (warningMessage) {
              rows.push(
                `<tr class="nb-job-warning-row">
                  <td colspan="5">
                    <div class="alert alert-warning py-2 px-3 small mb-2 d-flex align-items-start gap-2">
                      <i class="bi bi-exclamation-triangle-fill flex-shrink-0"></i>
                      <span>${escapeText(warningMessage)}</span>
                    </div>
                  </td>
                </tr>`,
              );
            }
            jobs.forEach((job, idx) => {
              const req = job.requested_at || "";
              const cnt = job.devices ? job.devices.length : 0;
              
              // 如果有多个批次，显示一个简单的分割线或更紧凑的标识
              if (jobs.length > 1) {
                rows.push(
                  `<tr class="nb-job-group-header">
                    <td colspan="5" class="py-1">
                      <div class="d-flex justify-content-between x-small opacity-75">
                        <span>批次: ${escapeText(req)}</span>
                        <span>设备: ${cnt}</span>
                      </div>
                    </td>
                  </tr>`,
                );
              }

              (job.devices || []).forEach((d) => {
                const status = renderBackupStatusBadge(d);
                const name = escapeText(d.name || "");
                const host = escapeText(d.host || "");
                const backupId = d.id || "";
                const isCurrentDeviceLog = !!(
                  state.taskLogsVisible &&
                  state.taskLogTarget &&
                  state.taskLogTarget.kind === "backup" &&
                  String(state.taskLogTarget.id || "").trim() === String(backupId).trim()
                );
                const canSelect = !!(job.run_id && backupId);
                const selected = canSelect && isBackupSelected(backupId);
                const backupAttr = backupId ? `data-backup-id="${backupId}"` : "";
                const linkOpen = backupId ? `<a class="nb-backup-link text-decoration-none" href="#" ${backupAttr}>` : "";
                const linkClose = backupId ? `</a>` : "";
                const logNameAttr = escapeText(d.name || "");
                const logHostAttr = escapeText(d.host || "");
                rows.push(
                  `<tr class="nb-job-row" data-status="${escapeText(d.status || '')}">
                    <td class="align-middle text-center">
                      ${canSelect ? `<input class="form-check-input nb-job-select-item" type="checkbox" data-backup-id="${backupId}" ${selected ? "checked" : ""} aria-label="选择任务">` : ""}
                    </td>
                    <td>
                      <div class="d-flex flex-column">
                        ${linkOpen}<div class="fw-bold text-truncate small" style="max-width: 320px;" title="${name}">${name}</div>${linkClose}
                      </div>
                    </td>
                    <td class="text-secondary small text-nowrap opacity-75 align-middle">${host}</td>
                    <td>${status}</td>
                    <td class="align-middle text-center">
                      ${backupId ? `<button type="button" class="btn ${isCurrentDeviceLog ? "btn-secondary" : "btn-outline-secondary"} btn-sm py-0 px-2 nb-device-log-btn text-nowrap" data-device-log-id="${backupId}" data-device-log-name="${logNameAttr}" data-device-log-host="${logHostAttr}">${isCurrentDeviceLog ? "查看中" : "设备日志"}</button>` : ""}
                    </td>
                  </tr>`,
                );
              });
              if (idx < jobs.length - 1) {
                rows.push(`<tr><td colspan="5" style="height: 4px; border:none;"></td></tr>`);
              }
            });
            if (!rows.length) {
              rows.push(
                `<tr><td colspan="5" class="text-center text-secondary py-4 small">任务状态加载中...</td></tr>`
              );
            }
            tbody.innerHTML = rows.join("");
            applySummaryFilter();
            if (selectAllCheckbox) {
              const latest = jobs[0] || null;
              const selectableCount = latest && latest.run_id ? (latest.devices || []).filter((d) => d && d.id).length : 0;
              const selectedCount = getSelectedBackupIds().length;
              selectAllCheckbox.classList.toggle("d-none", selectableCount <= 0);
              selectAllCheckbox.checked = selectableCount > 0 && selectedCount === selectableCount;
              selectAllCheckbox.indeterminate = selectedCount > 0 && selectedCount < selectableCount;
              selectAllCheckbox.disabled = selectableCount <= 0;
            }

            tbody.querySelectorAll(".nb-backup-link").forEach((a) => {
              a.addEventListener("click", (ev) => {
                ev.preventDefault();
                const bid = a.getAttribute("data-backup-id");
                if (!bid) return;
                if (window.NB && typeof window.NB.openBackupView === "function") {
                  window.NB.openBackupView(bid);
                }
              });
            });
            tbody.querySelectorAll(".nb-device-log-btn").forEach((btn) => {
              btn.addEventListener("click", () => {
                const backupId = btn.getAttribute("data-device-log-id");
                if (!backupId) return;
                const rawName = btn.getAttribute("data-device-log-name") || "";
                const rawHost = btn.getAttribute("data-device-log-host") || "";
                const suffix = rawName || rawHost
                  ? `${rawName || rawHost}${rawHost && rawName && rawHost !== rawName ? ` (${rawHost})` : ""}`
                  : "";
                setTaskLogTarget(
                  {
                    kind: "backup",
                    id: backupId,
                    label: suffix ? `设备实时日志: ${suffix}` : "设备实时日志",
                  },
                  { reset: true, visible: true },
                );
              });
            });
            tbody.querySelectorAll(".nb-job-select-item").forEach((input) => {
              input.addEventListener("change", () => {
                const backupId = input.getAttribute("data-backup-id");
                if (!backupId) return;
                setSelectedBackupId(backupId, !!input.checked);
              });
            });
          }

          function stopTimer() {
            if (state.timer) {
              clearInterval(state.timer);
              state.timer = null;
            }
          }

          async function refreshJobs() {
            if (state.socketConnected || state.taskChannelMode !== "http_fallback") {
              return;
            }
            if (!state.jobs.length) {
              stopTimer();
              render();
              return;
            }
            const currentJob = state.jobs[0];
            const runId = currentJob && currentJob.run_id ? String(currentJob.run_id).trim() : "";
            const backupId = currentJob && currentJob.backup_id ? String(currentJob.backup_id).trim() : "";
            if (!runId && !backupId) {
              state.jobs = [];
              saveJobs();
              stopTimer();
              render();
              return;
            }
            let data = null;
            try {
              const result = await window.NB.api.request(`/api/tasks/backups/query`, {
                method: "POST",
                headers: {
                  "Content-Type": "application/json",
                },
                body: JSON.stringify(runId ? { run_id: runId } : { backup_id: backupId }),
              });
              if (result.ok) {
                data = result.data;
              }
            } catch (e) {
              data = null;
            }
            if (!data) {
              render();
              return;
            }
            if (data.found === false) {
              state.jobs = [];
              saveJobs();
              stopTimer();
              resetTaskLogs();
              render();
              return;
            }
            const devices = (data.items || []).map((it) => ({
              id: it && it.id ? it.id : "",
              name: it && it.device ? it.device.name : "",
              host: it && it.device ? it.device.host : "",
              started_at: it ? it.started_at || "" : "",
              finished_at: it ? it.finished_at || null : null,
              status: it ? it.status || "" : "",
              success: it ? it.success : false,
              error_message: it ? it.error_message || "" : "",
            }));
            state.jobs = state.jobs.map((job) => ({
              ...job,
              devices,
            }));
            normalizeSelectedBackupIds();
            render();

            const running = state.jobs.some((j) => j.devices && j.devices.some((d) => isActiveBackupStatus(d.status)));
            if (!running) {
              stopTimer();
            }
          }

          function ensureTimer() {
            if (state.timer) return;
            state.timer = setInterval(refreshJobs, 1500);
          }

          function nowStr() {
            const d = new Date();
            const pad = (n) => String(n).padStart(2, "0");
            return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
          }

          window.NB.trackBackups = function (payload) {
            if (!CAN_TRACK_BACKUPS) return;
            let runId = "";
            let backupId = "";
            let requestedAt = nowStr();
            let warningMessage = "";
            if (payload && typeof payload === "object" && !Array.isArray(payload)) {
              runId = payload.run_id == null ? "" : String(payload.run_id).trim();
              backupId = payload.backup_id == null ? "" : String(payload.backup_id).trim();
              requestedAt = payload.requested_at ? String(payload.requested_at) : requestedAt;
              warningMessage = payload.warning_message == null ? "" : String(payload.warning_message).trim();
            }
            if (!runId && !backupId) return;
            const job = {
              id: runId || backupId,
              run_id: runId,
              backup_id: backupId,
              requested_at: requestedAt,
              devices: [],
              run_status: "",
            };
            // New backup execution replaces the previously tracked batch to avoid unbounded accumulation.
            state.jobs = [job];
            state.selectedBackupIds = [];
            state.warningMessage = warningMessage;
            state.taskLogTarget = getDefaultTaskLogTarget();
            state.taskLogsVisible = true;
            setTaskChannelMode(typeof window.WebSocket === "undefined" ? "http_fallback" : "connecting");
            resetTaskLogs();
            setPanelVisible(true);
            render();
            ensureTaskSocket();
            syncTaskLogSubscription();
            if (typeof window.WebSocket === "undefined") {
              enableFallbackPolling();
              refreshJobs();
            }
          };

          window.NB.openBackupView = function (backupId) {
            if (!backupId) return;
            openBackupView(backupId);
          };
          window.NB.openBackupLogView = function (backupId) {
            if (!backupId) return;
            openBackupLogView(backupId);
          };

          if (backupViewFullscreen) {
            backupViewFullscreen.addEventListener("click", () => {
              setBackupViewFullscreen(!backupViewIsFullscreen);
            });
          }

          if (backupViewModalEl) {
            backupViewModalEl.addEventListener("hidden.bs.modal", () => {
              setBackupViewFullscreen(false);
            });
          }

          if (closeBtn) {
            closeBtn.addEventListener("click", () => {
              setPanelVisible(false);
            });
          }

          if (selectAllCheckbox) {
            selectAllCheckbox.addEventListener("change", () => {
              setAllSelectedBackups(!!selectAllCheckbox.checked);
            });
          }

          if (bulkTerminateBtn) {
            bulkTerminateBtn.addEventListener("click", () => {
              const job = getCurrentTrackedJob();
              const selectedCount = getSelectedBackupIds().length;
              if (!canBulkTerminateTrackedRun(job) || !selectedCount) return;
              const message = `确认终止当前选中的 ${selectedCount} 个未运行任务吗？已在执行中的任务不会被停止。`;
              if (window.NB && typeof window.NB.confirm === "function") {
                window.NB.confirm({
                  title: "确认批量终止",
                  message,
                  confirmBtnText: "确认终止",
                  confirmBtnClass: "btn-danger",
                  onConfirm: () => {
                    terminateSelectedTrackedRun();
                  },
                });
                return;
              }
              if (window.confirm(message)) {
                terminateSelectedTrackedRun();
              }
            });
          }

          if (bulkRetryBtn) {
            bulkRetryBtn.addEventListener("click", () => {
              const job = getCurrentTrackedJob();
              const selectedCount = getSelectedBackupIds().length;
              if (!canBulkRetryTrackedRun(job) || !selectedCount) return;
              const message = `确认重试当前选中的 ${selectedCount} 个失败或已终止任务吗？`;
              if (window.NB && typeof window.NB.confirm === "function") {
                window.NB.confirm({
                  title: "确认批量重试",
                  message,
                  confirmBtnText: "确认重试",
                  confirmBtnClass: "btn-warning",
                  onConfirm: () => {
                    retrySelectedTrackedRun();
                  },
                });
                return;
              }
              if (window.confirm(message)) {
                retrySelectedTrackedRun();
              }
            });
          }

          if (terminateBtn) {
            terminateBtn.addEventListener("click", () => {
              const job = getCurrentTrackedJob();
              if (!canTerminateTrackedRun(job)) return;
              const runId = job && job.run_id ? String(job.run_id).trim() : "";
              if (!runId) return;
              if (window.NB && typeof window.NB.confirm === "function") {
                window.NB.confirm({
                  title: "确认终止",
                  message: "确认终止本次运行中尚未开始的任务吗？已在执行中的任务将继续完成。",
                  confirmBtnText: "确认终止",
                  confirmBtnClass: "btn-danger",
                  onConfirm: () => {
                    terminateTrackedRun();
                  },
                });
                return;
              }
              if (window.confirm("确认终止本次运行中尚未开始的任务吗？已在执行中的任务将继续完成。")) {
                terminateTrackedRun();
              }
            });
          }

          if (retryBtn) {
            retryBtn.addEventListener("click", () => {
              const job = getCurrentTrackedJob();
              if (!canRetryTrackedRun(job)) return;
              const runId = job && job.run_id ? String(job.run_id).trim() : "";
              if (!runId) return;
              if (window.NB && typeof window.NB.confirm === "function") {
                window.NB.confirm({
                  title: "确认重试",
                  message: "确认重试本次运行中失败或已终止的任务吗？成功项不会重复执行。",
                  confirmBtnText: "确认重试",
                  confirmBtnClass: "btn-warning",
                  onConfirm: () => {
                    retryTrackedRun();
                  },
                });
                return;
              }
              if (window.confirm("确认重试本次运行中失败或已终止的任务吗？成功项不会重复执行。")) {
                retryTrackedRun();
              }
            });
          }

          if (logsToggleBtn) {
            logsToggleBtn.addEventListener("click", () => {
              const wasShowingBatchLogs = !!(
                state.taskLogsVisible &&
                state.taskLogTarget &&
                state.taskLogTarget.kind === "run"
              );
              state.taskLogsVisible = !wasShowingBatchLogs;
              state.taskLogTarget = getDefaultTaskLogTarget();
              resetTaskLogs();
              render();
              syncTaskLogSubscription();
            });
          }

          if (logCloseBtn) {
            logCloseBtn.addEventListener("click", () => {
              state.taskLogsVisible = false;
              state.taskLogTarget = getDefaultTaskLogTarget();
              render();
              syncTaskLogSubscription();
            });
          }

          if (trigger) {
            trigger.addEventListener("click", (e) => {
              e.stopPropagation();
              if (panel && panel.classList.contains("show")) {
                setPanelVisible(false);
                return;
              }
              openLatestTaskPanel();
            });
          }

          // 点击面板外部关闭面板
          document.addEventListener("click", (e) => {
            if (panel && panel.classList.contains('show')) {
              if (!panel.contains(e.target) && !trigger.contains(e.target)) {
                // 如果是点击了全局的某些按钮（例如开始备份），不应该关闭
                if (!e.target.closest('button, a, .form-check-input')) {
                  setPanelVisible(false);
                }
              }
            }
          });

          // 初始化加载
          loadJobs();
        })();
