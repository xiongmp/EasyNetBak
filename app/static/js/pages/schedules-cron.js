(function () {
      const isEnglish = Boolean(window.NB && window.NB.i18n && window.NB.i18n.isEnglish);
      // cronstrue 默认按 Linux 周定义解析（0=周日），这里把 APScheduler 周数字（0=周一, 6=周日）
      // 转成 Linux 语义，仅用于前端文案展示，不影响后端实际调度。
      function normalizeApschedulerCronForCronstrue(expr) {
        if (!expr || typeof expr !== "string") return expr;
        const parts = expr.trim().split(/\s+/);
        if (parts.length !== 5) return expr;

        const apsToLinuxDow = (n) => (n === 6 ? 0 : n + 1);

        const mapDowSegment = (segment) => {
          const s = (segment || "").trim();
          if (!s || s === "*" || s.includes("/") || /[A-Za-z]/.test(s)) return s;

          if (/^\d+$/.test(s)) {
            const n = Number(s);
            if (Number.isInteger(n) && n >= 0 && n <= 6) return String(apsToLinuxDow(n));
            return s;
          }

          const m = s.match(/^(\d+)-(\d+)$/);
          if (!m) return s;
          const start = Number(m[1]);
          const end = Number(m[2]);
          if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || start > 6 || end < 0 || end > 6) {
            return s;
          }

          const apsDays = [];
          let cur = start;
          while (true) {
            apsDays.push(cur);
            if (cur === end) break;
            cur = (cur + 1) % 7;
          }
          const linuxDays = apsDays.map(apsToLinuxDow);
          const uniq = Array.from(new Set(linuxDays));
          if (uniq.length === 7) return "*";
          return uniq.join(",");
        };

        const dowField = parts[4];
        parts[4] = dowField
          .split(",")
          .map(mapDowSegment)
          .join(",");
        return parts.join(" ");
      }

      function updateCronMeaning(inputEl, targetEl) {
        if (!inputEl || !targetEl) return;
        const val = inputEl.value || inputEl.getAttribute("data-cron");
        if (!val) {
          targetEl.textContent = "";
          return;
        }
        try {
          const normalizedCron = normalizeApschedulerCronForCronstrue(val);
          let meaning = cronstrue.toString(normalizedCron, {
            locale: isEnglish ? "en" : "zh_CN",
            use24HourTimeFormat: true 
          });
          
          if (!isEnglish && meaning.startsWith(NB.t("js.schedule_stats_cron.at"))) {
            const timeMatch = meaning.match(/^在\s?(\d{2}:\d{2})/);
            if (timeMatch) {
              const timeStr = timeMatch[1];
              let rest = meaning.replace(timeMatch[0], "").replace(/^[,\s，]+/, "");
              
              if (!rest) {
                meaning = NB.t("js.schedule_stats_cron.daily") + timeStr;
              } else if (rest.startsWith(NB.t("js.schedule_stats_cron.only_on"))) {
                meaning = rest.replace(NB.t("js.schedule_stats_cron.only_on"), NB.t("js.schedule_stats_cron.weekly")) + " " + timeStr;
              } else if (rest.includes(NB.t("js.schedule_stats_cron.day"))) {
                meaning = rest.replace(/在每月的第\s?(\d+)\s?天/, NB.t("js.schedule_stats_cron.day_1_of_every_month")) + " " + timeStr;
              } else {
                // 其他复杂情况，至少把时间挪到后面，去掉开头的“在”
                meaning = rest + " " + timeStr;
              }
            }
          }

          targetEl.textContent = meaning;
          targetEl.classList.remove("text-danger");
          targetEl.classList.add("text-primary");
        } catch (e) {
          targetEl.textContent = isEnglish ? "Invalid cron expression" : NB.t("js.schedule_stats_cron.invalid_cron_expression");
          targetEl.classList.remove("text-primary");
          targetEl.classList.add("text-danger");
        }
      }

      // 列表中的表达式识别
      document.querySelectorAll(".js-cron-meaning").forEach(el => updateCronMeaning(el, el));

      // 如果是管理员，添加输入框实时识别
      const cronInput = document.getElementById("crontab-input");
      const cronMeaning = document.getElementById("crontab-meaning");
      if (cronInput && cronMeaning) {
        cronInput.addEventListener("input", () => updateCronMeaning(cronInput, cronMeaning));
        updateCronMeaning(cronInput, cronMeaning);
      }

      // 弹窗自动唤起与 URL 清理
      const scheduleModalEl = document.getElementById('scheduleModal');
      if (scheduleModalEl) {
        const scheduleModal = new bootstrap.Modal(scheduleModalEl);
        
        // 如果 URL 中有 edit 参数，自动打开弹窗
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.has('edit')) {
          scheduleModal.show();
        }

        // 弹窗关闭后，如果 URL 中有 edit 参数，则清理并刷新（或直接修改 URL）
        scheduleModalEl.addEventListener('hidden.bs.modal', function () {
          if (urlParams.has('edit')) {
            window.location.href = '/schedules';
          }
        });

        // 如果没有打开弹窗（即不是通过 edit 参数进来的），则显示返回列表按钮（如果存在）
        if (!urlParams.has('edit')) {
          const backBtn = document.getElementById('back-to-list-btn');
          if (backBtn) backBtn.classList.remove('d-none');
        }
      }
    })();
