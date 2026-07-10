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
              
              if (!isEnglish && meaning.startsWith("在")) {
                const timeMatch = meaning.match(/^在\s?(\d{2}:\d{2})/);
                if (timeMatch) {
                  const timeStr = timeMatch[1];
                  let rest = meaning.replace(timeMatch[0], "").replace(/^[,\s，]+/, "");
                  
                  if (!rest) {
                    meaning = "每天 " + timeStr;
                  } else if (rest.startsWith("仅星期")) {
                    meaning = rest.replace("仅星期", "每周") + " " + timeStr;
                  } else if (rest.includes("每月的第")) {
                    meaning = rest.replace(/在每月的第\s?(\d+)\s?天/, "每月$1号") + " " + timeStr;
                  } else {
                    meaning = rest + " " + timeStr;
                  }
                }
              }
              cronEl.textContent = meaning;
            } catch (e) {
              cronEl.textContent = isEnglish ? "Invalid cron expression" : "无效的 Cron 表达式";
              cronEl.classList.replace("text-primary", "text-danger");
            }
          }
        }
      })();
