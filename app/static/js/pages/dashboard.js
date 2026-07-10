document.addEventListener('DOMContentLoaded', function() {
    const isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
    const chartTextColor = isDark ? '#94a3b8' : '#64748b';
    const chartTitleColor = isDark ? '#e2e8f0' : '#334155';
    const chartSplitLineColor = isDark ? 'rgba(255, 255, 255, 0.05)' : '#f1f5f9';
    const chartTooltipBg = isDark ? 'rgba(30, 41, 59, 0.95)' : 'rgba(255, 255, 255, 0.95)';
    const chartTooltipBorder = isDark ? '#475569' : '#e2e8f0';
    const chartTooltipText = isDark ? '#f1f5f9' : '#334155';
    const chartBorderColor = isDark ? '#1e293b' : '#fff';
    const chartEmptyColor = isDark ? '#334155' : '#f1f5f9';
    const heatmapColorList = isDark 
        ? ['#1e293b', '#172554', '#1e40af', '#2563eb', '#3b82f6', '#60a5fa'] 
        : ['#f1f5f9', '#dbeafe', '#93c5fd', '#3b82f6', '#1d4ed8', '#1e3a8a'];
    const dashboardLayout = document.querySelector('.dashboard-layout');
    const dashboardSummaryRow = document.querySelector('.dashboard-summary-row');
    const dashboardAnalyticsRow = document.querySelector('.dashboard-analytics-row');
    const dashboardDetailRow = document.querySelector('.dashboard-detail-row');

    function applyDashboardLayoutState(isFullscreen) {
        if (!dashboardLayout || !dashboardSummaryRow || !dashboardAnalyticsRow || !dashboardDetailRow) return;

        if (isFullscreen) {
            dashboardLayout.classList.add('dashboard-layout-fullscreen');
            dashboardSummaryRow.style.setProperty('flex', '0 0 auto');
            dashboardAnalyticsRow.style.setProperty('flex', '0 0 42%');
            dashboardDetailRow.style.setProperty('flex', '1 1 58%');
            dashboardSummaryRow.style.setProperty('margin-bottom', '0');
            dashboardAnalyticsRow.style.setProperty('margin-bottom', '0');
            dashboardDetailRow.style.setProperty('margin-bottom', '0');
            return;
        }

        dashboardLayout.classList.remove('dashboard-layout-fullscreen');
        dashboardSummaryRow.style.removeProperty('flex');
        dashboardAnalyticsRow.style.removeProperty('flex');
        dashboardDetailRow.style.removeProperty('flex');
        dashboardSummaryRow.style.removeProperty('margin-bottom');
        dashboardAnalyticsRow.style.removeProperty('margin-bottom');
        dashboardDetailRow.style.removeProperty('margin-bottom');

        // 退出全屏后显式恢复两行内容区的默认分配，避免浏览器保留旧的 flex 计算结果
        dashboardAnalyticsRow.style.setProperty('flex-grow', '1');
        dashboardAnalyticsRow.style.setProperty('flex-basis', '0');
        dashboardDetailRow.style.setProperty('flex-grow', '1');
        dashboardDetailRow.style.setProperty('flex-basis', '0');

        window.requestAnimationFrame(() => {
            dashboardAnalyticsRow.style.removeProperty('flex-grow');
            dashboardAnalyticsRow.style.removeProperty('flex-basis');
            dashboardDetailRow.style.removeProperty('flex-grow');
            dashboardDetailRow.style.removeProperty('flex-basis');
        });
    }

    // 备份趋势图
    const trendChart = echarts.init(document.getElementById('backup-trend-chart'));
    const dashboardConfig = window.DASHBOARD_CONFIG || {};
    const trendData = dashboardConfig.trendStats || {};
    const isHourlyTrend = trendData.granularity === 'hour';

    function setTrendChartOption(textColor, splitLineColor, tooltipBg, tooltipBorder, tooltipText, borderColor) {
        trendChart.setOption({
            animation: true,
            animationDuration: 1000,
            animationEasing: 'cubicOut',
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'shadow',
                    shadowStyle: {
                        color: 'rgba(59, 130, 246, 0.1)'
                    }
                },
                backgroundColor: tooltipBg,
                borderColor: tooltipBorder,
                borderWidth: 1,
                textStyle: {
                    color: tooltipText
                }
            },
            legend: {
                data: ['成功', '失败'],
                bottom: 0,
                itemWidth: 12,
                itemHeight: 12,
                textStyle: { fontSize: 12, color: textColor }
            },
            grid: {
                left: '2%',
                right: '3%',
                bottom: isHourlyTrend ? 60 : 40,
                top: '5%',
                containLabel: true
            },
            xAxis: {
                type: 'category',
                data: trendData.labels,
                axisLine: { lineStyle: { color: borderColor } },
                axisLabel: {
                    color: textColor,
                    fontSize: 11,
                    rotate: isHourlyTrend ? 45 : 0,
                    hideOverlap: !isHourlyTrend
                },
                axisTick: { show: false }
            },
            yAxis: {
                type: 'value',
                splitLine: {
                    lineStyle: {
                        color: splitLineColor,
                        type: 'dashed'
                    }
                },
                axisLabel: { color: textColor, fontSize: 11 },
                axisLine: { show: false }
            },
            series: [
                {
                    name: '成功',
                    type: 'bar',
                    stack: 'total',
                    itemStyle: {
                        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                            { offset: 0, color: '#22c55e' },
                            { offset: 1, color: '#16a34a' }
                        ]),
                        borderRadius: [4, 4, 0, 0]
                    },
                    data: trendData.success,
                    barWidth: isHourlyTrend ? '70%' : '60%',
                    emphasis: {
                        itemStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: '#4ade80' },
                                { offset: 1, color: '#22c55e' }
                            ])
                        }
                    }
                },
                {
                    name: '失败',
                    type: 'bar',
                    stack: 'total',
                    itemStyle: {
                        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                            { offset: 0, color: '#ef4444' },
                            { offset: 1, color: '#dc2626' }
                        ]),
                        borderRadius: [4, 4, 0, 0]
                    },
                    data: trendData.fail,
                    barWidth: isHourlyTrend ? '70%' : '60%',
                    emphasis: {
                        itemStyle: {
                            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                                { offset: 0, color: '#f87171' },
                                { offset: 1, color: '#ef4444' }
                            ])
                        }
                    }
                }
            ]
        });
    }

    setTrendChartOption(
        chartTextColor,
        chartSplitLineColor,
        chartTooltipBg,
        chartTooltipBorder,
        chartTooltipText,
        chartBorderColor
    );
    
    // 移除趋势图加载状态
    document.querySelector('#backup-trend-chart .chart-loading')?.remove();

    // 平台分布饼图
    const pieChartEl = document.getElementById('platform-pie-chart');
    const pieChart = echarts.init(pieChartEl);
    const platformData = Array.isArray(dashboardConfig.platformStats) ? dashboardConfig.platformStats : [];

    function getPlatformPieLayout() {
        const width = Math.max(pieChartEl?.clientWidth || 0, 240);
        const height = Math.max(pieChartEl?.clientHeight || 0, 180);
        const availableLegendWidth = Math.max(width - 24, 160);
        let currentRowWidth = 0;
        let legendRows = 1;

        platformData.forEach(item => {
            const name = String(item.name || '');
            const itemWidth = Math.min(150, Math.max(56, name.length * 7 + 40));
            if (currentRowWidth > 0 && currentRowWidth + itemWidth > availableLegendWidth) {
                legendRows += 1;
                currentRowWidth = itemWidth;
                return;
            }
            currentRowWidth += itemWidth;
        });

        const legendHeight = Math.max(32, legendRows * 24 + 8);
        const chartAreaHeight = Math.max(height - legendHeight - 10, 96);
        const outerRadius = Math.floor(Math.max(60, Math.min(width * 0.31, chartAreaHeight * 0.5, 132)));
        const centerY = Math.floor(Math.max(outerRadius + 4, Math.min(chartAreaHeight * 0.5, height - legendHeight - outerRadius - 8)));

        return {
            legendHeight,
            innerRadius: Math.floor(outerRadius * 0.62),
            outerRadius,
            centerY
        };
    }

    function setPlatformPieOption(textColor, tooltipBg, tooltipBorder, tooltipText, borderColor) {
        const layout = getPlatformPieLayout();
    
        pieChart.setOption({
        animation: true,
        animationDuration: 1000,
        animationEasing: 'cubicOut',
        tooltip: {
            trigger: 'item',
            formatter: '{b}: {c} ({d}%)',
            backgroundColor: tooltipBg,
            borderColor: tooltipBorder,
            borderWidth: 1,
            textStyle: {
                color: tooltipText
            }
        },
        legend: {
            type: 'plain',
            bottom: 5,
            left: 'center',
            width: '94%',
            itemWidth: 12,
            itemHeight: 12,
            itemGap: 12,
            padding: [0, 5],
            textStyle: {
                fontSize: 12,
                color: textColor
            }
        },
        series: [
            {
                name: '平台',
                type: 'pie',
                radius: [layout.innerRadius, layout.outerRadius],
                center: ['50%', layout.centerY],
                avoidLabelOverlap: false,
                itemStyle: {
                    borderRadius: 5,
                    borderColor: borderColor,
                    borderWidth: 2
                },
                label: {
                    show: false,
                    position: 'center'
                },
                emphasis: {
                    label: {
                        show: true,
                        fontSize: '14',
                        fontWeight: 'bold'
                    },
                    scale: true,
                    scaleSize: 8
                },
                labelLine: {
                    show: false
                },
                data: platformData,
                animationType: 'scale',
                animationEasing: 'elasticOut'
            }
        ]
        }, true);
    }

    let platformPieTheme = {
        textColor: chartTextColor,
        tooltipBg: chartTooltipBg,
        tooltipBorder: chartTooltipBorder,
        tooltipText: chartTooltipText,
        borderColor: chartBorderColor
    };

    function refreshPlatformPieLayout() {
        setPlatformPieOption(
            platformPieTheme.textColor,
            platformPieTheme.tooltipBg,
            platformPieTheme.tooltipBorder,
            platformPieTheme.tooltipText,
            platformPieTheme.borderColor
        );
    }

    refreshPlatformPieLayout();
    
    // 移除饼图加载状态
    document.querySelector('#platform-pie-chart .chart-loading')?.remove();

    // 配置变更频率热力图
    const heatmapChart = echarts.init(document.getElementById('change-heatmap-chart'));
    const heatmapData = dashboardConfig.changeHeatmap || {};
    const heatmapValues = Array.isArray(heatmapData.data) ? heatmapData.data : [];
    const heatmapMax = heatmapData.max || 0;
    const isMatrixHeatmap = heatmapData.mode === 'matrix';

    function setHeatmapChartOption(textColor, tooltipBg, tooltipBorder, tooltipText, borderColor, emptyColor, colorList) {
        const baseOption = {
            animation: true,
            animationDuration: 1000,
            animationEasing: 'cubicOut',
            visualMap: {
                type: 'continuous',
                min: 0,
                max: Math.max(5, heatmapMax),
                calculable: false,
                orient: 'horizontal',
                left: 'center',
                bottom: 0,
                inRange: {
                    color: colorList
                },
                text: ['多', '少'],
                textStyle: { fontSize: 12, color: textColor },
                itemWidth: 16,
                itemHeight: 200
            }
        };

        if (isMatrixHeatmap) {
            heatmapChart.setOption({
                ...baseOption,
                tooltip: {
                    position: 'top',
                    formatter: function (params) {
                        const xIndex = params.value && params.value.length > 2 ? params.value[0] : 0;
                        const yIndex = params.value && params.value.length > 2 ? params.value[1] : 0;
                        const val = params.value && params.value.length > 2 ? params.value[2] : 0;
                        const xLabel = (heatmapData.x_labels || [])[xIndex] || '';
                        const yLabel = (heatmapData.y_labels || [])[yIndex] || '';
                        return `<div style="text-align: center; font-weight: bold; margin-bottom: 4px;">${yLabel}</div>${xLabel}<br>变更次数：<span style="font-weight: bold; color: #3b82f6;">${val}</span>`;
                    },
                    backgroundColor: tooltipBg,
                    borderColor: tooltipBorder,
                    borderWidth: 1,
                    padding: [8, 12],
                    textStyle: {
                        color: tooltipText,
                        fontSize: 12
                    },
                    extraCssText: 'box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06); border-radius: 6px;'
                },
                grid: {
                    left: '4%',
                    right: '3%',
                    top: '10%',
                    bottom: 40,
                    containLabel: true
                },
                xAxis: {
                    type: 'category',
                    data: heatmapData.x_labels || [],
                    splitArea: { show: true },
                    splitLine: { show: false },
                    axisLine: { lineStyle: { color: borderColor } },
                    axisLabel: {
                        color: textColor,
                        fontSize: 12,
                        rotate: (heatmapData.y_labels || []).length === 1 ? 45 : 0,
                        hideOverlap: false
                    },
                    axisTick: { show: false }
                },
                yAxis: {
                    type: 'category',
                    data: heatmapData.y_labels || [],
                    splitArea: { show: true },
                    splitLine: { show: false },
                    axisLine: { lineStyle: { color: borderColor } },
                    axisLabel: { color: textColor, fontSize: 12 },
                    axisTick: { show: false }
                },
                series: [
                    {
                        type: 'heatmap',
                        data: heatmapValues,
                        label: { show: false },
                        itemStyle: {
                            borderRadius: 4,
                            borderColor: borderColor,
                            borderWidth: 1
                        },
                        emphasis: {
                            itemStyle: {
                                shadowBlur: 6,
                                shadowColor: 'rgba(0, 0, 0, 0.12)',
                                borderColor: '#3b82f6',
                                borderWidth: 1
                            }
                        }
                    }
                ]
            }, true);
            return;
        }

        heatmapChart.setOption({
            ...baseOption,
            tooltip: {
                position: 'top',
                formatter: function (params) {
                    const label = params.value && params.value.length > 1 ? params.value[0] : '';
                    const val = params.value && params.value.length > 1 ? params.value[1] : 0;
                    return `<div style="text-align: center; font-weight: bold; margin-bottom: 4px;">${label}</div>变更次数：<span style="font-weight: bold; color: #3b82f6;">${val}</span>`;
                },
                backgroundColor: tooltipBg,
                borderColor: tooltipBorder,
                borderWidth: 1,
                padding: [8, 12],
                textStyle: {
                    color: tooltipText,
                    fontSize: 12
                },
                extraCssText: 'box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06); border-radius: 6px;'
            },
            calendar: {
                top: 25,
                left: 30,
                right: 10,
                bottom: 40,
                cellSize: ['auto', 16],
                range: heatmapData.range,
                itemStyle: {
                    borderWidth: 2,
                    borderColor: borderColor,
                    color: emptyColor
                },
                splitLine: {
                    show: false
                },
                dayLabel: {
                    nameMap: ['日', '一', '二', '三', '四', '五', '六'],
                    firstDay: 1,
                    color: textColor,
                    fontSize: 10
                },
                monthLabel: {
                    nameMap: 'cn',
                    color: textColor,
                    fontSize: 11,
                    margin: 6
                },
                yearLabel: { show: false }
            },
            series: [
                {
                    type: 'heatmap',
                    coordinateSystem: 'calendar',
                    data: heatmapValues,
                    itemStyle: {
                        borderRadius: 3,
                    },
                    emphasis: {
                        itemStyle: {
                            shadowBlur: 4,
                            shadowColor: 'rgba(0, 0, 0, 0.1)',
                            borderColor: '#3b82f6',
                            borderWidth: 1
                        }
                    }
                }
            ]
        }, true);
    }

    setHeatmapChartOption(
        chartTextColor,
        chartTooltipBg,
        chartTooltipBorder,
        chartTooltipText,
        chartBorderColor,
        chartEmptyColor,
        heatmapColorList
    );

    document.querySelector('#change-heatmap-chart .chart-loading')?.remove();

    // 分组健康度柱状图
    const healthChart = echarts.init(document.getElementById('group-health-chart'));
    const healthData = Array.isArray(dashboardConfig.healthStats) ? dashboardConfig.healthStats : [];
    
    // 动态计算是否需要滚动条
    const showZoom = healthData.length > 5;
    
    healthChart.setOption({
        animation: true,
        animationDuration: 1000,
        animationEasing: 'cubicOut',
        tooltip: {
            trigger: 'axis',
            axisPointer: { type: 'shadow' },
            formatter: '{b}: {c}%',
            backgroundColor: chartTooltipBg,
            borderColor: chartTooltipBorder,
            borderWidth: 1,
            textStyle: {
                color: chartTooltipText
            }
        },
        grid: {
            left: '1%',
            right: showZoom ? '10%' : '8%',
            bottom: 20,
            top: '2%',
            containLabel: true
        },
        dataZoom: showZoom ? [
            {
                type: 'slider',
                yAxisIndex: 0,
                width: 6,
                right: 2,
                showDataShadow: false,
                startValue: 0,
                endValue: 4,
                borderColor: 'transparent',
                backgroundColor: 'transparent',
                fillerColor: isDark ? 'rgba(255, 255, 255, 0.25)' : 'rgba(0, 0, 0, 0.2)',
                handleSize: 0,
                showDetail: false
            },
            {
                type: 'inside',
                yAxisIndex: 0,
                zoomOnMouseWheel: false,
                moveOnMouseWheel: true,
                moveOnMouseMove: true
            }
        ] : [],
        xAxis: {
            type: 'value',
            max: 100,
            splitLine: { 
                lineStyle: { 
                    color: chartSplitLineColor,
                    type: 'dashed'
                } 
            },
            axisLabel: { formatter: '{value}%', color: chartTextColor, fontSize: 12 },
            axisLine: { show: false }
        },
        yAxis: {
            type: 'category',
            inverse: true, // 设置为反向，让第一个数据在上方
            data: healthData.map(item => item.name),
            axisLine: { lineStyle: { color: chartBorderColor } },
            axisLabel: { 
                color: chartTextColor, 
                fontSize: 12,
                width: 120, // 为层级分组名称预留更多显示空间
                overflow: 'truncate' // 文本过长时截断
            },
            axisTick: { show: false }
        },
        series: [
            {
                name: '成功率',
                type: 'bar',
                data: healthData.map(item => item.value),
                itemStyle: {
                    color: function(params) {
                        const val = params.value;
                        if (val >= 90) return new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                            { offset: 0, color: '#22c55e' },
                            { offset: 1, color: '#16a34a' }
                        ]);
                        if (val >= 70) return new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                            { offset: 0, color: '#fbbf24' },
                            { offset: 1, color: '#f59e0b' }
                        ]);
                        return new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                            { offset: 0, color: '#f87171' },
                            { offset: 1, color: '#ef4444' }
                        ]);
                    },
                    borderRadius: [0, 4, 4, 0]
                },
                label: {
                    show: true,
                    position: 'right',
                    formatter: '{c}%',
                    fontSize: 12,
                    fontWeight: 500,
                    color: chartTitleColor
                },
                barMaxWidth: 20,
                emphasis: {
                    itemStyle: {
                        shadowBlur: 10,
                        shadowColor: 'rgba(0, 0, 0, 0.1)'
                    }
                },
                animationDelay: function (idx) {
                    return idx * 100;
                }
            }
        ]
    });
    
    // 移除健康度图加载状态
    document.querySelector('#group-health-chart .chart-loading')?.remove();

    // 监听主题切换事件
    window.addEventListener('themeChanged', function(e) {
        const newTheme = e.detail.theme;
        const isDark = newTheme === 'dark';
        
        // 重新定义颜色变量
        const textColor = isDark ? '#94a3b8' : '#64748b';
        const titleColor = isDark ? '#e2e8f0' : '#334155';
        const splitLineColor = isDark ? 'rgba(255, 255, 255, 0.05)' : '#f1f5f9';
        const tooltipBg = isDark ? 'rgba(30, 41, 59, 0.95)' : 'rgba(255, 255, 255, 0.95)';
        const tooltipBorder = isDark ? '#475569' : '#e2e8f0';
        const tooltipText = isDark ? '#f1f5f9' : '#334155';
        const borderColor = isDark ? '#1e293b' : '#fff';
        const emptyColor = isDark ? '#334155' : '#f1f5f9';
        const heatmapColors = isDark 
            ? ['#1e293b', '#172554', '#1e40af', '#2563eb', '#3b82f6', '#60a5fa'] 
            : ['#f1f5f9', '#dbeafe', '#93c5fd', '#3b82f6', '#1d4ed8', '#1e3a8a'];

        // 更新趋势图
        setTrendChartOption(
            textColor,
            splitLineColor,
            tooltipBg,
            tooltipBorder,
            tooltipText,
            borderColor
        );

        // 更新饼图
        platformPieTheme = {
            textColor,
            tooltipBg,
            tooltipBorder,
            tooltipText,
            borderColor
        };
        refreshPlatformPieLayout();

        // 更新热力图
        setHeatmapChartOption(
            textColor,
            tooltipBg,
            tooltipBorder,
            tooltipText,
            borderColor,
            emptyColor,
            heatmapColors
        );

        // 更新健康度图
        healthChart.setOption({
            tooltip: {
                backgroundColor: tooltipBg,
                borderColor: tooltipBorder,
                textStyle: { color: tooltipText }
            },
            dataZoom: showZoom ? [{
                backgroundColor: 'transparent',
                fillerColor: isDark ? 'rgba(255, 255, 255, 0.25)' : 'rgba(0, 0, 0, 0.2)'
            }, {
                type: 'inside'
            }] : [],
            xAxis: {
                axisLabel: { color: textColor },
                splitLine: { lineStyle: { color: splitLineColor } }
            },
            yAxis: {
                axisLine: { lineStyle: { color: borderColor } },
                axisLabel: { color: textColor }
            },
            series: [{
                label: { color: titleColor }
            }]
        });
    });

    // 响应式优化：使用 ResizeObserver 监听容器大小变化
    const chartMap = new Map();
    if (document.getElementById('backup-trend-chart')) chartMap.set(document.getElementById('backup-trend-chart'), trendChart);
    if (document.getElementById('platform-pie-chart')) chartMap.set(document.getElementById('platform-pie-chart'), pieChart);
    if (document.getElementById('change-heatmap-chart')) chartMap.set(document.getElementById('change-heatmap-chart'), heatmapChart);
    if (document.getElementById('group-health-chart')) chartMap.set(document.getElementById('group-health-chart'), healthChart);

    function resizeAllCharts() {
        chartMap.forEach(chart => {
            if (chart) chart.resize();
        });
        refreshPlatformPieLayout();
    }

    const resizeObserver = new ResizeObserver(entries => {
        // 使用 requestAnimationFrame 优化，避免 "ResizeObserver loop limit exceeded" 错误
        window.requestAnimationFrame(() => {
            entries.forEach(entry => {
                const chart = chartMap.get(entry.target);
                if (chart) chart.resize();
                if (entry.target === pieChartEl) refreshPlatformPieLayout();
            });
        });
    });

    chartMap.forEach((chart, container) => {
        resizeObserver.observe(container);
    });

    window.addEventListener('appFullscreenChanged', function() {
        applyDashboardLayoutState(Boolean(document.fullscreenElement));

        // 全屏切换时布局会经历多次重排，这里连续触发几次 resize 以确保图表恢复到正确尺寸
        window.requestAnimationFrame(resizeAllCharts);
        setTimeout(resizeAllCharts, 120);
        setTimeout(resizeAllCharts, 320);
    });

    applyDashboardLayoutState(Boolean(document.fullscreenElement));

    // 备份活动表格过滤
    const filterButtons = document.querySelectorAll('[data-filter]');
    const backupRows = document.querySelectorAll('.backup-row');
    const emptyStateRow = document.getElementById('empty-state-row');
    
    filterButtons.forEach(button => {
        button.addEventListener('click', function() {
            const filter = this.getAttribute('data-filter');
            
            // 更新按钮状态
            filterButtons.forEach(btn => {
                btn.classList.remove('btn-primary', 'active');
                btn.classList.add('btn-outline-secondary');
            });
            this.classList.remove('btn-outline-secondary');
            this.classList.add('btn-primary', 'active');
            
            let visibleCount = 0;
            
            // 过滤表格行
            backupRows.forEach(row => {
                const status = row.getAttribute('data-status');
                if (filter === 'all' || status === filter) {
                    row.style.display = '';
                    visibleCount++;
                } else {
                    row.style.display = 'none';
                }
            });
            
            // 处理空状态显示
            if (visibleCount === 0 && emptyStateRow) {
                emptyStateRow.style.display = '';
                const emptyText = emptyStateRow.querySelector('p');
                if (filter === 'success') {
                    emptyText.textContent = '暂无成功的备份记录';
                } else if (filter === 'failed') {
                    emptyText.textContent = '暂无失败的备份记录';
                } else {
                    emptyText.textContent = '暂无备份记录';
                }
            } else if (emptyStateRow) {
                emptyStateRow.style.display = 'none';
            }
        });
    });

    // 备份详情查看按钮
    const backupLinks = document.querySelectorAll('.nb-backup-link');
    backupLinks.forEach(link => {
        link.addEventListener('click', function(ev) {
            ev.preventDefault();
            const backupId = this.getAttribute('data-backup-id');
            if (!backupId) return;
            if (window.NB && typeof window.NB.openBackupView === 'function') {
                window.NB.openBackupView(backupId);
            }
        });
    });
});
