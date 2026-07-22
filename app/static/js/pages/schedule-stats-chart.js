window.NB.ready(function initScheduleStatsChart() {
      const scheduleStatsConfig = window.SCHEDULE_STATS_CONFIG || {};
      const tr = (text) => text;
      const TREND = Array.isArray(scheduleStatsConfig.trend) ? scheduleStatsConfig.trend : [];
      const chartDom = document.getElementById("trend-chart");
      if (!chartDom) return;

      const isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
      const textColor = isDark ? '#94a3b8' : '#64748b';
      const splitLineColor = isDark ? 'rgba(255, 255, 255, 0.08)' : '#f1f5f9';
      const tooltipBg = isDark ? 'rgba(30, 41, 59, 0.95)' : 'rgba(255, 255, 255, 0.95)';
      const tooltipBorder = isDark ? '#475569' : '#e2e8f0';
      const tooltipText = isDark ? '#f1f5f9' : '#334155';

      const chart = echarts.init(chartDom);

      if (!TREND || !TREND.length) {
        chart.setOption({
          title: {
            text: NB.t("js.schedule_stats_chart.no_trend_data"),
            left: 'center',
            top: 'center',
            textStyle: { color: '#94a3b8', fontSize: 12, fontWeight: 'normal' }
          }
        });
        return;
      }

      const labels = TREND.map(function (p) {
        var s = p.started_at || '';
        return s.length > 10 ? s.substr(5) : s;
      });
      const rateData = TREND.map(function (p) { return Math.round((p.rate || 0) * 100); });

      chart.setOption({
        animation: true,
        animationDuration: 800,
        animationEasing: 'cubicOut',
        tooltip: {
          trigger: 'axis',
          backgroundColor: tooltipBg,
          borderColor: tooltipBorder,
          borderWidth: 1,
          padding: [10, 14],
          textStyle: { color: tooltipText, fontSize: 12 },
          axisPointer: {
            type: 'shadow',
            shadowStyle: { color: isDark ? 'rgba(59, 130, 246, 0.08)' : 'rgba(59, 130, 246, 0.06)' }
          },
          formatter: function (params) {
            var p = params[0];
            var idx = p.dataIndex;
            var item = TREND[idx] || {};
            var rate = p.value;
            var rateColor = rate >= 90 ? '#22c55e' : (rate >= 70 ? '#f59e0b' : '#ef4444');
            return '<div style="font-weight:600;margin-bottom:6px;color:' + (isDark ? '#e2e8f0' : '#1e293b') + '">' + (item.started_at || '') + '</div>'
              + '<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px;">'
              + '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#3b82f6;"></span>'
              + '<span>' + tr(NB.t("template.dashboard.success_rate")) + ': <b style="color:' + rateColor + '">' + rate + '%</b></span>'
              + '</div>'
              + '<div style="font-size:11px;color:' + (isDark ? '#94a3b8' : '#64748b') + ';margin-top:4px;">'
              + '<span style="color:#22c55e;">' + tr(NB.t("status.backup.succeeded")) + ' ' + (item.success || 0) + '</span>'
              + ' / <span style="color:#ef4444;">' + tr(NB.t("status.backup.failed")) + ' ' + (item.fail || 0) + '</span>'
              + ' / ' + tr(NB.t("js.schedule_stats_chart.total")) + ' ' + (item.total || 0)
              + '</div>';
          }
        },
        grid: {
          left: '0%',
          right: '3%',
          top: 8,
          bottom: 4,
          containLabel: true
        },
        xAxis: {
          type: 'category',
          data: labels,
          boundaryGap: false,
          axisLine: { show: false },
          axisTick: { show: false },
          axisLabel: { color: textColor, fontSize: 10 }
        },
        yAxis: {
          type: 'value',
          min: 0,
          max: 100,
          axisLabel: { color: textColor, fontSize: 10, formatter: '{value}%' },
          splitLine: { lineStyle: { color: splitLineColor, type: 'dashed' } }
        },
        series: [{
          type: 'line',
          data: rateData,
          smooth: true,
          symbol: 'emptyCircle',
          symbolSize: 6,
          lineStyle: {
            color: '#3b82f6',
            width: 2.5,
            shadowBlur: 6,
            shadowColor: 'rgba(59, 130, 246, 0.15)'
          },
          itemStyle: {
            color: '#3b82f6',
            borderColor: '#3b82f6',
            borderWidth: 2
          },
          emphasis: {
            focus: 'series',
            itemStyle: { borderWidth: 4, symbolSize: 10 }
          },

        }]
      });
    }, { name: "schedule-stats-chart" });
