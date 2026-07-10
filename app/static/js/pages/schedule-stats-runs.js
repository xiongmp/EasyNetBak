(function () {
        const btn = document.querySelector(".js-schedule-run");
        const scheduleStatsConfig = window.SCHEDULE_STATS_CONFIG || {};
        const scheduleId = scheduleStatsConfig.scheduleId == null ? null : scheduleStatsConfig.scheduleId;
        const runsTbody = document.getElementById("schedule-runs-tbody");
        const canTerminateRuns = scheduleStatsConfig.canTerminateRuns === true;
        let pollTimer = null;
        const terminatingRunIds = new Set();

        function stopPolling() {
          if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
          }
        }

        function escapeText(text) {
          const span = document.createElement("span");
          span.textContent = text == null ? "" : String(text);
          return span.innerHTML;
        }

        function renderRuns(items) {
          if (!runsTbody) return;
          const rows = Array.isArray(items) ? items : [];
          if (!rows.length) {
            runsTbody.innerHTML = `
              <tr>
                <td colspan="8" class="text-center text-secondary py-5">
                  <div class="my-3">
                    <i class="bi bi-inbox fs-1 opacity-25"></i>
                    <p class="mt-2 text-xs">暂无运行记录</p>
                  </div>
                </td>
              </tr>`;
            return;
          }

          function canTerminateRun(status) {
            return ["planned", "dispatching", "running", "finalizing"].includes(String(status || "").trim());
          }

          runsTbody.innerHTML = rows.map((r) => {
            const statusHtml = window.NB && typeof window.NB.renderTaskStatusBadge === "function"
              ? window.NB.renderTaskStatusBadge("schedule_run", { status: r.status, success: null })
              : escapeText(r.status_label || r.status || "");
            const statusMeta = window.NB && typeof window.NB.scheduleRunStatusMeta === "function"
              ? window.NB.scheduleRunStatusMeta(r.status)
              : null;
            const successCount = r.finished_at ? Number(r.success_count || 0) : "—";
            const failCount = r.finished_at ? Number(r.fail_count || 0) : "—";
            const isTerminating = terminatingRunIds.has(String(r.id || ""));
            const finishedAtHtml = r.finished_at
              ? `<div class="fw-medium text-dark">${escapeText(r.finished_at)}</div>`
              : '<span class="text-secondary opacity-50">—</span>';
            let durationHtml = escapeText(r.duration_text || "—");
            if (!r.finished_at && statusMeta) {
              if (r.status === "running") {
                durationHtml = `<span class="text-primary"><i class="bi bi-hourglass-split me-1 spin"></i>${escapeText(statusMeta.label || r.duration_text || "运行中")}</span>`;
              } else if (r.status === "finalizing") {
                durationHtml = `<span class="text-primary"><i class="bi bi-hourglass-bottom me-1 spin"></i>${escapeText(statusMeta.label || r.duration_text || "收尾中")}</span>`;
              } else if (r.status === "cancelling") {
                durationHtml = `<span class="text-warning"><i class="bi bi-slash-circle me-1"></i>${escapeText(statusMeta.label || r.duration_text || "终止中")}</span>`;
              } else if (window.NB && typeof window.NB.isActiveScheduleRunStatus === "function" && window.NB.isActiveScheduleRunStatus(r.status)) {
                durationHtml = `<span class="text-secondary"><i class="bi bi-hourglass-split me-1"></i>${escapeText(statusMeta.label || r.duration_text || r.status || "")}</span>`;
              }
            }
            const actionHtml = canTerminateRuns && canTerminateRun(r.status)
              ? `<button class="btn btn-outline-danger btn-sm js-run-terminate" type="button" data-run-id="${escapeText(r.id || "")}" ${isTerminating ? "disabled" : ""}>${isTerminating ? "处理中..." : "终止未运行任务"}</button>`
              : '<span class="text-secondary opacity-50 text-xs">—</span>';

            return `
              <tr>
                <td class="text-xs text-nowrap">
                  <div class="fw-medium text-dark">${escapeText(r.started_at || "")}</div>
                </td>
                <td class="text-xs text-nowrap">${finishedAtHtml}</td>
                <td class="text-nowrap">
                  <div class="backup-status backup-status-light">${escapeText(r.trigger || "")}</div>
                </td>
                <td class="text-nowrap">
                  <span class="text-xs text-secondary">${durationHtml}</span>
                </td>
                <td class="text-center text-nowrap">
                  <div class="d-inline-flex align-items-center gap-1 px-2 py-1 rounded bg-light border border-light-subtle">
                    <span class="text-success fw-bold text-xs">${successCount}</span>
                    <span class="text-secondary mx-1">/</span>
                    <span class="text-danger fw-bold text-xs">${failCount}</span>
                    <span class="text-secondary mx-1">/</span>
                    <span class="text-dark fw-bold text-xs">${Number(r.total_devices || 0)}</span>
                  </div>
                </td>
                <td class="text-nowrap">${statusHtml}</td>
                <td class="text-nowrap">${actionHtml}</td>
                <td class="text-truncate text-secondary text-xs error-summary-cell" title="${escapeText(r.error_message || "")}">${escapeText(r.error_summary || r.error_message || "")}</td>
              </tr>`;
          }).join("");
        }

        async function refreshRuns() {
          if (!scheduleId) return;
          try {
            const result = await window.NB.api.request(`/api/schedules/${encodeURIComponent(scheduleId)}/stats/runs`);
            const data = result.data || {};
            if (!result.ok) {
              const detail = await window.NB.api.extractErrorDetail(result.response, "");
              if (result.response && result.response.status === 404) {
                stopPolling();
                if (window.NB && typeof window.NB.showToast === "function") {
                  window.NB.showToast(detail || "定时任务不存在", "warning");
                }
              }
              return;
            }
            renderRuns(data.items || []);
            if (!data.has_active_runs) {
              stopPolling();
            }
          } catch (e) {
            console.error(e);
          }
        }

        function ensurePolling() {
          if (pollTimer || !scheduleId) return;
          pollTimer = setInterval(refreshRuns, 2000);
        }

        async function terminatePendingTasks(runId) {
          const rid = String(runId || "").trim();
          if (!rid || terminatingRunIds.has(rid)) return;
          terminatingRunIds.add(rid);
          await refreshRuns();
          try {
            const result = await window.NB.api.request(`/api/schedules/runs/${encodeURIComponent(rid)}/terminate`, { method: "POST" });
            const data = result.data || null;
            if (!result.ok) {
              const msg = await window.NB.api.extractErrorDetail(result.response, "终止失败");
              throw new Error(msg);
            }
            if (data && data.message && window.NB && typeof window.NB.showToast === "function") {
              window.NB.showToast(data.message, "info");
            }
            ensurePolling();
            await refreshRuns();
          } catch (e) {
            console.error(e);
            if (window.NB && typeof window.NB.showToast === "function") {
              window.NB.showToast("终止失败: " + e.message, "error");
            }
          } finally {
            terminatingRunIds.delete(rid);
            await refreshRuns();
          }
        }

        async function runSchedule(scheduleId) {
          if (!btn) return;
          btn.disabled = true;
          const old = btn.innerHTML;
          btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>执行中';
          try {
            const result = await window.NB.api.request(`/api/schedules/${encodeURIComponent(scheduleId)}/run`, { method: "POST" });
            const data = result.data || {};
            if (!result.ok) {
              const detail = await window.NB.api.extractErrorDetail(result.response, "");
              throw new Error(detail || "执行失败");
            }
            const records = (data && data.records) || [];
            const enqueueStatus = (data && data.enqueue_status) ? String(data.enqueue_status) : "none";
            const enqueueWarning = (data && data.enqueue_warning_message) ? String(data.enqueue_warning_message) : "";
            if (records.length) {
              if (window.NB && typeof window.NB.trackBackups === "function") {
                window.NB.trackBackups({
                  run_id: data && data.run_id ? data.run_id : "",
                  warning_message: enqueueWarning,
                });
              }
              if (window.NB && typeof window.NB.showToast === "function") {
                if (enqueueWarning) {
                  window.NB.showToast(enqueueWarning, "warning");
                } else if (enqueueStatus === "partial") {
                  window.NB.showToast("已启动 " + records.length + " 个备份任务，部分任务入队失败", "warning");
                } else {
                  window.NB.showToast("已启动 " + records.length + " 个备份任务", "info");
                }
              }
              ensurePolling();
              await refreshRuns();
            } else if (window.NB && typeof window.NB.showToast === "function") {
              window.NB.showToast("没有找到需要备份的设备", "warning");
            }
          } catch (e) {
            console.error(e);
            if (window.NB && typeof window.NB.showToast === "function") {
              window.NB.showToast("手动执行失败: " + e.message, "error");
            }
          } finally {
            btn.disabled = false;
            btn.innerHTML = old;
          }
        }        if (btn) {
          btn.addEventListener("click", () => {
            const sid = btn.getAttribute("data-schedule-id");
            runSchedule(sid);
          });
        }

        if (runsTbody) {
          runsTbody.addEventListener("click", (event) => {
            const terminateBtn = event.target.closest(".js-run-terminate");
            if (!terminateBtn) return;
            const runId = terminateBtn.getAttribute("data-run-id");
            if (!runId) return;
            if (window.NB && typeof window.NB.confirm === "function") {
              window.NB.confirm({
                title: "确认终止",
                message: "确认终止本次运行中尚未开始的任务吗？已在执行中的任务将继续完成。",
                confirmBtnText: "确认终止",
                confirmBtnClass: "btn-danger",
                onConfirm: () => {
                  terminatePendingTasks(runId);
                },
              });
              return;
            }
            if (window.confirm("确认终止本次运行中尚未开始的任务吗？已在执行中的任务将继续完成。")) {
              terminatePendingTasks(runId);
            }
          });
        }

        const hasActiveRuns = scheduleStatsConfig.hasActiveRuns === true;
        if (hasActiveRuns) {
          ensurePolling();
        }
      })();
