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

        function tr(text) { return text; }

        function trHtml(html) { return html; }

        function renderRuns(items) {
          if (!runsTbody) return;
          const rows = Array.isArray(items) ? items : [];
          if (!rows.length) {
            runsTbody.innerHTML = trHtml(NB.t("js.schedule_stats_runs.tr_td_colspan_8_class_text_center_text"));
            return;
          }

          function canTerminateRun(status) {
            return ["planned", "dispatching", "running", "finalizing"].includes(String(status || "").trim());
          }

          runsTbody.innerHTML = trHtml(rows.map((r) => {
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
                durationHtml = NB.t("js.schedule_stats_runs.span_class_text_primary_i_class_bi_bi", {value0: escapeText(statusMeta.label || r.duration_text || NB.t("status.schedule_run.running"))});
              } else if (r.status === "finalizing") {
                durationHtml = NB.t("js.schedule_stats_runs.span_class_text_primary_i_class_bi_bi_357be361", {value0: escapeText(statusMeta.label || r.duration_text || NB.t("status.schedule_run.finalizing"))});
              } else if (r.status === "cancelling") {
                durationHtml = NB.t("js.schedule_stats_runs.span_class_text_warning_i_class_bi_bi", {value0: escapeText(statusMeta.label || r.duration_text || NB.t("status.schedule_run.cancelling"))});
              } else if (window.NB && typeof window.NB.isActiveScheduleRunStatus === "function" && window.NB.isActiveScheduleRunStatus(r.status)) {
                durationHtml = `<span class="text-secondary"><i class="bi bi-hourglass-split me-1"></i>${escapeText(statusMeta.label || r.duration_text || r.status || "")}</span>`;
              }
            }
            const actionHtml = canTerminateRuns && canTerminateRun(r.status)
              ? NB.t("js.schedule_stats_runs.button_class_btn_btn_outline_danger_btn_sm", {value0: escapeText(r.id || ""), value1: isTerminating ? "disabled" : "", value2: isTerminating ? NB.t("js.nb_common.processing") : NB.t("template.schedule_stats.cancel_pending_tasks")})
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
                <td class="text-truncate text-secondary text-xs error-summary-cell" title="${escapeText(r.error_summary || r.error_message || "")}">${escapeText(r.error_summary || r.error_message || "")}</td>
              </tr>`;
          }).join(""));
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
                  window.NB.showToast(detail || NB.t("js.schedule_stats_runs.schedule_not_found"), "warning");
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
              const msg = await window.NB.api.extractErrorDetail(result.response, NB.t("js.schedule_stats_runs.cancellation_failed"));
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
              window.NB.showToast(NB.t("js.nb_common.cancellation_failed") + e.message, "error");
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
          btn.innerHTML = trHtml(NB.t("js.schedule_stats_runs.span_class_spinner_border_spinner_border_sm_me"));
          try {
            const result = await window.NB.api.request(`/api/schedules/${encodeURIComponent(scheduleId)}/run`, { method: "POST" });
            const data = result.data || {};
            if (!result.ok) {
              const detail = await window.NB.api.extractErrorDetail(result.response, "");
              throw new Error(detail || NB.t("js.schedule_stats_runs.execution_failed"));
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
                  window.NB.showToast(NB.t("js.devices_bulk.started") + records.length + NB.t("js.devices_bulk.backup_tasks_some_tasks_failed_to_queue"), "warning");
                } else {
                  window.NB.showToast(NB.t("js.devices_bulk.started") + records.length + NB.t("js.devices_bulk.backup_tasks"), "info");
                }
              }
              ensurePolling();
              await refreshRuns();
            } else if (window.NB && typeof window.NB.showToast === "function") {
              window.NB.showToast(NB.t("js.schedule_stats_runs.no_devices_found_for_backup"), "warning");
            }
          } catch (e) {
            console.error(e);
            if (window.NB && typeof window.NB.showToast === "function") {
              window.NB.showToast(NB.t("js.schedule_stats_runs.manual_execution_failed") + e.message, "error");
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
                title: NB.t("js.nb_common.confirm_cancellation"),
                message: NB.t("js.nb_common.cancel_tasks_in_this_run_that_have_not"),
                confirmBtnText: NB.t("js.nb_common.confirm_cancellation"),
                confirmBtnClass: "btn-danger",
                onConfirm: () => {
                  terminatePendingTasks(runId);
                },
              });
              return;
            }
            if (window.confirm(NB.t("js.nb_common.cancel_tasks_in_this_run_that_have_not"))) {
              terminatePendingTasks(runId);
            }
          });
        }

        const hasActiveRuns = scheduleStatsConfig.hasActiveRuns === true;
        if (hasActiveRuns) {
          ensurePolling();
        }
      })();
