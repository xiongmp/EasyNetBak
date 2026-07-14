(function() {
        const isEnglish = Boolean(window.NB && window.NB.i18n && window.NB.i18n.isEnglish);
        const cronEl = document.getElementById("cron-meaning");
        if (cronEl) {
          const cron = cronEl.getAttribute("data-cron");
          if (cron) {
            try {
              let meaning = cronstrue.toString(cron, { 
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
                    meaning = rest + " " + timeStr;
                  }
                }
              }
              cronEl.textContent = meaning;
            } catch (e) {
              cronEl.textContent = isEnglish ? "Invalid cron expression" : NB.t("js.schedule_stats_cron.invalid_cron_expression");
              cronEl.classList.replace("text-primary", "text-danger");
            }
          }
        }
      })();
