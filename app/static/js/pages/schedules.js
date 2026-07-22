window.NB.ready(function initSchedulesPage() {
        const btns = Array.from(document.querySelectorAll(".js-schedule-run"));
        const toggles = Array.from(document.querySelectorAll(".js-schedule-toggle"));

        function updateNextRunDisplay(checkbox, nextRun) {
          if (!checkbox) return;
          const row = checkbox.closest("tr");
          const nextRunEl = row ? row.querySelector(".js-next-run") : null;
          if (!nextRunEl || !nextRun) return;

          nextRunEl.classList.remove("text-danger", "text-primary", "text-secondary");
          if (nextRun.tone === "danger") {
            nextRunEl.classList.add("text-danger");
          } else if (nextRun.tone === "primary") {
            nextRunEl.classList.add("text-primary");
          } else {
            nextRunEl.classList.add("text-secondary");
          }
          nextRunEl.innerHTML = `<i class="bi bi-clock me-1"></i>${escapeText(tr(nextRun.text || ""))}`;
        }

        // 处理开关切换
        async function toggleSchedule(scheduleId, checkbox) {
          const originalStatus = !checkbox.checked;
          const label = checkbox.parentElement.querySelector(".js-toggle-label");
          const originalLabel = label ? label.textContent.trim() : "";

          checkbox.disabled = true;
          if (label) {
            label.classList.add("text-muted");
            label.textContent = tr(NB.t("js.schedules.syncing"));
          }

          try {
            const result = await window.NB.api.request(`/api/schedules/${encodeURIComponent(scheduleId)}/toggle`, { method: "POST" });
            const data = result.data || {};
            if (!result.ok) {
              const detail = await window.NB.api.extractErrorDetail(result.response, "");
              throw new Error(detail || NB.t("js.schedules.switch_failed"));
            }

            checkbox.checked = data.enabled;
            if (label) {
              label.textContent = tr(data.enabled ? NB.t("template.schedules.enabled") : NB.t("template.notifications.disabled"));
              label.classList.remove("text-muted");
            }
            updateNextRunDisplay(checkbox, data.next_run);

            if (window.NB && typeof window.NB.showToast === "function") {
              window.NB.showToast(data.enabled ? NB.t("js.schedules.schedulesenabled") : NB.t("js.schedules.schedulesdisabled"), "success");
            }
          } catch (e) {
            console.error(e);
            checkbox.checked = originalStatus;
            if (label) {
              label.textContent = originalLabel;
              label.classList.remove("text-muted");
            }
            if (window.NB && typeof window.NB.showToast === "function") {
              window.NB.showToast(NB.t("js.schedule_stats_runs.manual_execution_failed") + e.message, "error");
            }
          } finally {
            checkbox.disabled = false;
          }
        }        toggles.forEach((toggle) => {
          toggle.addEventListener("change", () => {
            const sid = toggle.getAttribute("data-schedule-id");
            toggleSchedule(sid, toggle);
          });
          
          // 点击 Label 也能触发开关
          const label = toggle.parentElement.querySelector(".js-toggle-label");
          if (label) {
            label.addEventListener("click", () => {
              if (!toggle.disabled) {
                toggle.checked = !toggle.checked;
                toggle.dispatchEvent(new Event('change'));
              }
            });
          }
        });

        const targetsEl = document.getElementById("schedule-targets");
        const previewSummary = document.getElementById("schedule-preview-summary");
        const previewCounts = document.getElementById("schedule-preview-counts");
        const previewDevices = document.getElementById("schedule-preview-devices");
        const previewLoading = document.getElementById("schedule-preview-loading");
        const previewError = document.getElementById("schedule-preview-error");
        
        let lastPreviewData = null;
        let previewVisibleLimit = 30;
        
        // 目标选择器相关元素
        const targetAllCheckbox = document.getElementById("target-all");
        const platformSelection = document.getElementById("platform-selection");
        const groupSelection = document.getElementById("group-selection");
        const deviceSelection = document.getElementById("device-selection");
        const deviceSearch = document.getElementById("device-search");
        
        // Drawer相关元素
        const openTargetDrawerBtn = document.getElementById("open-target-drawer");
        const targetDrawer = document.getElementById("target-drawer");
        const applyTargetsBtn = document.getElementById("apply-targets");
        const targetsSummaryText = document.getElementById("targets-summary-text");
        
        // Drawer预览相关元素
        const drawerPreviewContainer = document.getElementById("drawer-preview-container");
        const drawerPreviewSummary = document.getElementById("drawer-preview-summary");
        const drawerPreviewCounts = document.getElementById("drawer-preview-counts");
        const drawerPreviewDevices = document.getElementById("drawer-preview-devices");
        const drawerPreviewLoading = document.getElementById("drawer-preview-loading");
        const drawerPreviewError = document.getElementById("drawer-preview-error");
        
        let lastDrawerPreviewData = null;
        let drawerPreviewVisibleLimit = 20;
        
        // 存储加载的数据
        let platformsData = [];
        let groupsData = [];
        let devicesData = [];
        
        // Drawer实例
        let drawerInstance = null;
        let scheduleModalFocusTrap = null;
        let scheduleModalBodyState = null;

        function escapeText(text) {
          const span = document.createElement("span");
          span.textContent = text == null ? "" : String(text);
          return span.innerHTML;
        }

        function tr(text) { return text; }

        function trHtml(html) { return html; }

        function isNearScrollBottom(el) {
          if (!el) return false;
          return el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
        }

        // 加载平台数据
        async function loadPlatforms() {
          try {
            const result = await window.NB.api.request("/api/schedules/targets/platforms");
            if (!result.ok) throw new Error("Failed to load platforms");
            platformsData = result.data || [];
            renderPlatforms();
          } catch (e) {
            platformSelection.innerHTML = `<div class="text-danger small">${escapeText(NB.t("js.schedules.load_failed"))}</div>`;
          }
        }

        // 加载分组数据
        async function loadGroups() {
          try {
            const result = await window.NB.api.request("/api/schedules/targets/groups");
            if (!result.ok) throw new Error("Failed to load groups");
            groupsData = result.data || [];
            renderGroups();
          } catch (e) {
            groupSelection.innerHTML = `<div class="text-danger small">${escapeText(NB.t("js.schedules.load_failed"))}</div>`;
          }
        }

        // 加载设备数据
        async function loadDevices(search = "") {
          try {
            const url = new URL("/api/schedules/targets/devices", window.location.origin);
            if (search) url.searchParams.set("q", search);
            const result = await window.NB.api.request(url);
            if (!result.ok) throw new Error("Failed to load devices");
            const data = result.data || {};
            devicesData = data.devices || [];
            renderDevices();
          } catch (e) {
            deviceSelection.innerHTML = `<div class="text-danger small">${escapeText(NB.t("js.schedules.load_failed"))}</div>`;
          }
        }

        // 渲染平台选择器
        function renderPlatforms() {
          if (!platformsData.length) {
            platformSelection.innerHTML = `<div class="text-secondary small">${escapeText(NB.t("js.schedules.no_platform_data"))}</div>`;
            return;
          }
          
          platformSelection.innerHTML = platformsData.map(p => `
            <div class="form-check form-check-inline me-0">
              <input class="form-check-input d-none" type="checkbox" id="platform-${escapeText(p.name)}" data-type="platform" data-value="${escapeText(p.name)}">
              <label class="btn btn-sm btn-outline-secondary border fw-normal px-2 py-1 cursor-pointer transition-all" for="platform-${escapeText(p.name)}" data-i18n-preserve>
                ${escapeText(p.name)} <span class="badge bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle fw-normal ms-1">${p.count}</span>
              </label>
            </div>
          `).join('');
          
          // 添加事件监听
          platformSelection.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            cb.addEventListener('change', (e) => {
              const label = platformSelection.querySelector(`label[for="${e.target.id}"]`);
              if (e.target.checked) {
                label.classList.remove('btn-outline-secondary');
                label.classList.add('btn-primary', 'border-primary');
              } else {
                label.classList.remove('btn-primary', 'border-primary');
                label.classList.add('btn-outline-secondary');
              }
              handleOtherSelectionChange();
            });
          });
        }

        function buildGroupTree(flatList) {
          const map = new Map();
          const roots = [];
          
          flatList.forEach(item => {
              map.set(item.id, { ...item, children: [] });
          });
          
          flatList.forEach(item => {
              if (item.parent_id && map.has(item.parent_id)) {
                  map.get(item.parent_id).children.push(map.get(item.id));
              } else {
                  roots.push(map.get(item.id));
              }
          });
          
          return roots;
        }

        function renderGroupTreeNodes(nodes, container) {
          const ul = document.createElement('ul');
          ul.className = 'tree-view';
          
          nodes.forEach(node => {
              const li = document.createElement('li');
              li.className = 'tree-node';
              
              const content = document.createElement('div');
              content.className = 'tree-node-content';
              content.style.setProperty('--tree-depth', String(Math.max(0, Number(node.depth || 0))));
              
              const hasChildren = node.children && node.children.length > 0;
              
              const toggle = document.createElement('div');
              toggle.className = 'tree-toggle' + (hasChildren ? ' collapsed' : ' empty');
              toggle.innerHTML = '<i class="bi bi-caret-down-fill"></i>';
              
              // 复选框
              const checkbox = document.createElement('input');
              checkbox.type = 'checkbox';
              checkbox.className = 'form-check-input tree-checkbox mt-0';
              checkbox.id = `group-${node.id}`;
              checkbox.dataset.type = 'group';
              checkbox.dataset.value = node.id;
              checkbox.dataset.name = node.full_path || node.name;
              
              // 按照记忆：层级图标与配色
              let iconClass = 'bi me-2 fs-5 ';
              let textClass = '';
              if (node.depth === 0 || node.id === 0 || node.id === -1) {
                  iconClass += 'bi-house-door-fill text-secondary';
                  textClass = 'fw-medium text-body';
              } else if (node.depth === 1) {
                  iconClass += 'bi-folder-fill text-secondary';
                  textClass = 'fw-medium text-body';
              } else {
                  iconClass += 'bi-folder text-secondary opacity-75';
                  textClass = 'text-secondary';
              }
              
              const icon = document.createElement('i');
              icon.className = iconClass;
              
              const text = document.createElement('span');
              text.className = textClass;
              text.setAttribute('data-i18n-preserve', '');
              text.textContent = node.name;
              
              const countBadge = document.createElement('span');
              countBadge.className = 'badge bg-primary-subtle text-primary-emphasis border border-primary-subtle fw-normal ms-2';
              countBadge.textContent = node.count || 0;
              
              content.appendChild(toggle);
              content.appendChild(checkbox);
              content.appendChild(icon);
              content.appendChild(text);
              content.appendChild(countBadge);
              li.appendChild(content);
              
              let childrenUl = null;
              if (hasChildren) {
                  childrenUl = renderGroupTreeNodes(node.children, null);
                  childrenUl.className = 'tree-view tree-children collapsed';
                  li.appendChild(childrenUl);
                  
                  toggle.addEventListener('click', (e) => {
                      e.stopPropagation();
                      toggle.classList.toggle('collapsed');
                      childrenUl.classList.toggle('collapsed');
                  });
              }
              
              checkbox.addEventListener('click', (e) => {
                  e.stopPropagation();
              });
              
              checkbox.addEventListener('change', (e) => {
                  handleOtherSelectionChange();
              });
              
              content.addEventListener('click', (e) => {
                  if (e.target.closest('.tree-toggle') || e.target === checkbox) return;
                  checkbox.checked = !checkbox.checked;
                  checkbox.dispatchEvent(new Event('change'));
              });
              
              ul.appendChild(li);
          });
          
          if (container) {
              container.innerHTML = '';
              container.appendChild(ul);
          }
          return ul;
        }

        // 渲染分组选择器
        function renderGroups(searchText = "") {
          if (!groupsData.length) {
            groupSelection.innerHTML = `<div class="text-secondary small">${escapeText(NB.t("js.schedules.no_group_data"))}</div>`;
            return;
          }
          
          let filteredGroups = groupsData;
          if (searchText) {
            const query = searchText.toLowerCase();
            // 找到所有匹配的节点
            const matchedIds = new Set();
            groupsData.forEach(g => {
              if (g.name.toLowerCase().includes(query) || (g.full_path && g.full_path.toLowerCase().includes(query))) {
                matchedIds.add(g.id);
              }
            });

            // 确保匹配节点的父节点也显示出来，以便维持树形结构
            const finalIds = new Set();
            const addWithParents = (id) => {
              if (finalIds.has(id)) return;
              finalIds.add(id);
              const item = groupsData.find(g => g.id === id);
              if (item && item.parent_id) {
                addWithParents(item.parent_id);
              }
            };
            matchedIds.forEach(id => addWithParents(id));
            
            filteredGroups = groupsData.filter(g => finalIds.has(g.id));
          }

          if (searchText && !filteredGroups.length) {
            groupSelection.innerHTML = `<div class="p-3 text-secondary small text-center">${escapeText(NB.t("js.schedules.no_matching_groups"))}</div>`;
            return;
          }

          const treeData = buildGroupTree(filteredGroups);
          renderGroupTreeNodes(treeData, groupSelection);

          // 如果是搜索状态，自动展开所有节点
          if (searchText) {
            groupSelection.querySelectorAll('.tree-children').forEach(ul => {
              ul.classList.remove('collapsed');
            });
            groupSelection.querySelectorAll('.tree-toggle').forEach(toggle => {
              toggle.classList.remove('collapsed');
            });
          }
        }

        // 渲染设备选择器
        function renderDevices() {
          const container = document.getElementById('device-selection');
          const countEl = document.getElementById('device-selection-count');
          
          if (!devicesData.length) {
            container.innerHTML = `<div class="p-3 text-secondary small text-center">${escapeText(NB.t("js.schedules.no_matching_devices"))}</div>`;
            if (countEl) countEl.textContent = tr(NB.t("js.schedules.total_0_devices"));
            return;
          }
          
          if (countEl) countEl.textContent = tr(NB.t("js.schedules.total_value0_devices", {value0: devicesData.length}));
          
          container.innerHTML = trHtml(`
            <table class="table table-sm table-hover align-middle mb-0 small">
              <thead class="bg-body-tertiary sticky-top">
                <tr>
                  <th class="ps-3" style="width: 40px;">
                    <input class="form-check-input" type="checkbox" id="device-select-all-toggle">
                  </th>
                  <th>${NB.t("email.field.device_name")}</th>
                  <th style="width: 140px;">${NB.t("audit.csv.ip_address")}</th>
                  <th style="width: 120px;">${NB.t("template.backups.platform")}</th>
                  <th style="width: 120px;">${NB.t("audit.resource.group")}</th>
                </tr>
              </thead>
              <tbody>
                ${devicesData.map(d => `
                  <tr class="cursor-pointer js-device-row" data-device-id="${d.id}">
                    <td class="ps-3">
                      <input class="form-check-input js-device-cb" type="checkbox" id="device-${d.id}" data-type="device" data-value="${d.id}" onclick="event.stopPropagation()">
                    </td>
                    <td><div class="text-body text-truncate" style="max-width: 180px;" title="${escapeText(d.name || d.host)}" data-i18n-preserve>${escapeText(d.name || d.host)}</div></td>
                    <td><code class="text-secondary small" data-i18n-preserve>${escapeText(d.host)}</code></td>
                    <td><span class="badge bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle fw-normal px-2" data-i18n-preserve>${escapeText(d.platform)}</span></td>
                    <td><span class="badge bg-primary-subtle text-primary-emphasis border border-primary-subtle fw-normal px-2" data-i18n-preserve>${escapeText(d.group)}</span></td>
                  </tr>
                `).join('')}
              </tbody>
            </table>
          `);
          
          // 行点击事件
          container.querySelectorAll('.js-device-row').forEach(row => {
            row.addEventListener('click', () => {
              const cb = row.querySelector('.js-device-cb');
              cb.checked = !cb.checked;
              cb.dispatchEvent(new Event('change', { bubbles: true }));
            });
          });
          
          // 复选框变化事件
          container.querySelectorAll('.js-device-cb').forEach(cb => {
            cb.addEventListener('change', (e) => {
              const row = e.target.closest('tr');
              if (e.target.checked) {
                row.classList.add('is-selected');
              } else {
                row.classList.remove('is-selected');
              }
              handleOtherSelectionChange();
            });
          });
          
          // 全选/取消全选设备
          const selectAllToggle = document.getElementById('device-select-all-toggle');
          if (selectAllToggle) {
            selectAllToggle.addEventListener('change', (e) => {
              container.querySelectorAll('.js-device-cb').forEach(cb => {
                if (cb.checked !== e.target.checked) {
                  cb.checked = e.target.checked;
                  cb.dispatchEvent(new Event('change', { bubbles: true }));
                }
              });
            });
          }
        }

        // 更新目标文本框
        function updateTargets() {
          const targets = [];
          
          // 如果选择了所有设备
          if (targetAllCheckbox && targetAllCheckbox.checked) {
            targets.push('all');
          } else {
            // 收集选中的平台
            platformSelection.querySelectorAll('input[type="checkbox"]:checked').forEach(cb => {
              targets.push(`platform:${cb.dataset.value}`);
            });
            
            // 收集选中的分组
            groupSelection.querySelectorAll('input[type="checkbox"]:checked').forEach(cb => {
              targets.push(`group:${cb.dataset.value}`);
            });
            
            // 收集选中的设备
            deviceSelection.querySelectorAll('input[type="checkbox"]:checked').forEach(cb => {
              targets.push(`device:${cb.dataset.value}`);
            });
          }
          
          // 更新文本框
          if (targetsEl) {
            const newValue = targets.length ? targets.join('\n') : (targetAllCheckbox && targetAllCheckbox.checked ? 'all' : '');
            if (targetsEl.value !== newValue) {
              targetsEl.value = newValue;
              // 手动触发 input 事件以更新预览
              targetsEl.dispatchEvent(new Event('input', { bubbles: true }));
            }
          }
          
          // 更新Drawer预览
          updateDrawerPreview();
        }

        // 更新Drawer预览
        let previewDebounceTimer;
        function updateDrawerPreview() {
          if (!targetsEl || !drawerPreviewSummary || !drawerPreviewCounts || !drawerPreviewDevices) return;
          
          clearTimeout(previewDebounceTimer);
          previewDebounceTimer = setTimeout(() => {
            const targets = targetsEl.value.split('\n').map(t => t.trim()).filter(t => t);
            
            // 如果没有任何目标且没有勾选"所有设备"，则直接清空预览并返回
            if (targets.length === 0 && (!targetAllCheckbox || !targetAllCheckbox.checked)) {
              drawerPreviewSummary.textContent = tr(NB.t("js.schedules.no_devices_selected"));
              drawerPreviewCounts.innerHTML = '';
              drawerPreviewDevices.innerHTML = '';
              drawerPreviewLoading.classList.add('d-none');
              drawerPreviewError.classList.add('d-none');
              return;
            }

            // 检查是否有现有内容
            const hasContent = drawerPreviewDevices.children.length > 0;
            
            if (hasContent) {
              // 如果有内容，通过透明度和顶部加载提示来避免布局跳动
              drawerPreviewCounts.classList.add('opacity-50');
              drawerPreviewDevices.classList.add('opacity-50');
              drawerPreviewSummary.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${escapeText(NB.t("js.schedules.calculating"))}`;
              drawerPreviewLoading.classList.add('d-none');
            } else {
              // 如果没内容，显示加载占位符
              drawerPreviewLoading.classList.remove('d-none');
              drawerPreviewCounts.innerHTML = '';
              drawerPreviewDevices.innerHTML = '';
            }
            
            drawerPreviewError.classList.add('d-none');
            
            // 发送预览请求
            const formData = new FormData();
            formData.append('targets', targets.join('\n'));
            
            window.NB.api.request('/api/schedules/preview', {
              method: 'POST',
              body: formData
            })
            .then(result => {
              if (!result.ok) throw new Error("Preview failed");
              renderDrawerPreview(result.data || {});
            })
            .catch(error => {
              drawerPreviewLoading.classList.add('d-none');
              drawerPreviewCounts.classList.remove('opacity-50');
              drawerPreviewDevices.classList.remove('opacity-50');
              drawerPreviewSummary.textContent = tr(NB.t("js.schedules.calculation_failed"));
              drawerPreviewError.classList.remove('d-none');
            });
          }, 300);
        }

        // 抽屉预览渲染函数
        function renderDrawerPreview(data) {
          if (data !== lastDrawerPreviewData) {
            drawerPreviewVisibleLimit = 20;
          }
          lastDrawerPreviewData = data;
          drawerPreviewLoading.classList.add('d-none');
          drawerPreviewCounts.classList.remove('opacity-50');
          drawerPreviewDevices.classList.remove('opacity-50');
          
          const counts = data.counts || {};
          const devices = data.devices || [];
          const total = counts.total || 0;
          
          // 更新摘要
          drawerPreviewSummary.textContent = tr(NB.t("js.schedules.total_devices", {value0: total}));
          
          // 更新计数
          let countHtml = '';
          if (counts.platforms && Object.keys(counts.platforms).length > 0) {
            const list = Object.entries(counts.platforms).sort().map(([p, c]) => 
              `<span class="badge bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle fw-normal px-2 mb-1" data-i18n-preserve>${escapeText(p)} · ${c}</span>`
            ).join(' ');
            countHtml += `<div class="mb-1 w-100"><div class="text-secondary x-small opacity-75 mb-0">${escapeText(NB.t("js.schedules.matched_platforms"))}</div><div class="d-flex flex-wrap gap-1">${list}</div></div>`;
          }
          if (counts.groups && Object.keys(counts.groups).length > 0) {
            const list = Object.entries(counts.groups).sort().map(([g, c]) => 
              `<span class="badge bg-primary-subtle text-primary-emphasis border border-primary-subtle fw-normal px-2 mb-1" data-i18n-preserve>${escapeText(g)} · ${c}</span>`
            ).join(' ');
            countHtml += `<div class="mb-1 w-100"><div class="text-secondary x-small opacity-75 mb-0">${escapeText(NB.t("js.schedules.matched_groups"))}</div><div class="d-flex flex-wrap gap-1">${list}</div></div>`;
          }
          drawerPreviewCounts.innerHTML = trHtml(countHtml);
          
          // 更新设备列表
          const displayLimit = Math.min(drawerPreviewVisibleLimit, devices.length || 0);
          const displayDevices = devices.slice(0, displayLimit);

          const deviceBadges = displayDevices.map(device => 
            `<span class="badge rounded-pill bg-warning-subtle text-warning-emphasis border border-warning-subtle px-3 py-2 fw-normal mb-1 me-1 device-badge" data-i18n-preserve>${escapeText(device.name || device.host)} <small class="opacity-75">(${escapeText(device.host)})</small></span>`
          ).join('');
          
          if (deviceBadges) {
            drawerPreviewDevices.innerHTML = `<div class="w-100"><div class="text-secondary x-small opacity-75 mb-0">${escapeText(NB.t("js.schedules.matched_devices"))}</div><div class="d-flex flex-wrap gap-1" data-i18n-preserve>${deviceBadges}</div></div>`;
          } else {
            drawerPreviewDevices.innerHTML = '';
          }

          if (devices.length > displayLimit) {
            const more = document.createElement("div");
            more.className = "w-100 text-center text-muted small mt-2";
            more.textContent = tr(NB.t("js.schedules.scroll_down_to_load_more_value0_devices", {value0: devices.length - displayLimit}));
            drawerPreviewDevices.appendChild(more);
          }
        }

        // 处理"所有设备"复选框变化
        function handleTargetAllChange() {
          if (targetAllCheckbox.checked) {
            // 取消其他所有选择
            platformSelection.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
            groupSelection.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
            deviceSelection.querySelectorAll('input[type="checkbox"]').forEach(cb => cb.checked = false);
            // 还需要更新 UI 样式（比如平台的按钮颜色）
            platformSelection.querySelectorAll('label').forEach(label => {
              label.classList.remove('btn-primary', 'border-primary');
              label.classList.add('btn-outline-secondary');
            });
            // 移除设备的选中样式
            deviceSelection.querySelectorAll('tr.is-selected').forEach(row => row.classList.remove('is-selected'));
          }
          updateTargets();
        }

        // 处理其他选择变化
        function handleOtherSelectionChange() {
          if (targetAllCheckbox.checked) {
            targetAllCheckbox.checked = false;
          }
          updateTargets();
        }

        // 初始化目标选择器
        function initTargetSelector() {
          if (!targetAllCheckbox || !platformSelection || !groupSelection || !deviceSelection) return;
          
          // 加载数据
          loadPlatforms();
          loadGroups();
          loadDevices();
          
          // 添加事件监听
          targetAllCheckbox.addEventListener('change', handleTargetAllChange);
          
          // 分组搜索
          const groupSearch = document.getElementById("group-search");
          if (groupSearch) {
            let groupSearchTimer;
            groupSearch.addEventListener('input', () => {
              clearTimeout(groupSearchTimer);
              groupSearchTimer = setTimeout(() => {
                renderGroups(groupSearch.value);
              }, 200);
            });
          }

          // 快捷选择：全选/清空
          document.querySelectorAll('.js-select-all').forEach(link => {
            link.addEventListener('click', (e) => {
              e.preventDefault();
              const targetId = e.target.dataset.target;
              const container = document.getElementById(targetId);
              if (container) {
                container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                  if (!cb.checked) {
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                  }
                });
              }
            });
          });
          
          document.querySelectorAll('.js-clear-all').forEach(link => {
            link.addEventListener('click', (e) => {
              e.preventDefault();
              const targetId = e.target.dataset.target;
              const container = document.getElementById(targetId);
              if (container) {
                container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                  if (cb.checked) {
                    cb.checked = false;
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                  }
                });
              }
            });
          });
          
          // 设备搜索
          if (deviceSearch) {
            let searchTimer;
            deviceSearch.addEventListener('input', () => {
              clearTimeout(searchTimer);
              searchTimer = setTimeout(() => {
                loadDevices(deviceSearch.value);
              }, 300);
            });
          }
          
          // 为选择器添加变化监听（用于在动态加载后绑定事件）
          const observer = new MutationObserver(() => {
            // 解析现有目标到动态加载的复选框
            parseTargetsToQuickMode();
          });
          
          observer.observe(platformSelection, { childList: true });
          observer.observe(groupSelection, { childList: true });
          observer.observe(deviceSelection, { childList: true });
          
          // 初始化时解析现有目标
          parseTargetsToQuickMode();
          
          // 更新目标摘要
          updateTargetsSummary();
          
          // 初始化Drawer
          initDrawer();
        }

        // 初始化Drawer
        function getScheduleModalElement() {
          return document.getElementById('scheduleModal');
        }

        function isScheduleModalOpen() {
          const scheduleModal = getScheduleModalElement();
          return !!(scheduleModal && scheduleModal.classList.contains('show'));
        }

        function suspendScheduleModalFocusTrap() {
          if (typeof bootstrap === 'undefined' || !bootstrap.Modal) return;
          const scheduleModal = getScheduleModalElement();
          const modalInstance = scheduleModal ? bootstrap.Modal.getInstance(scheduleModal) : null;
          const focusTrap = modalInstance && modalInstance._focustrap;
          if (focusTrap && typeof focusTrap.deactivate === 'function') {
            focusTrap.deactivate();
            scheduleModalFocusTrap = focusTrap;
          }
        }

        function restoreScheduleModalFocusTrap() {
          if (!scheduleModalFocusTrap || !isScheduleModalOpen()) {
            scheduleModalFocusTrap = null;
            return;
          }
          if (typeof scheduleModalFocusTrap.activate === 'function') {
            scheduleModalFocusTrap.activate();
          }
          scheduleModalFocusTrap = null;
        }

        function captureScheduleModalBodyState() {
          if (!isScheduleModalOpen()) return;
          scheduleModalBodyState = {
            paddingRight: document.body.style.paddingRight,
            overflow: document.body.style.overflow,
          };
        }

        function restoreScheduleModalBodyState() {
          if (!scheduleModalBodyState || !isScheduleModalOpen()) {
            scheduleModalBodyState = null;
            return;
          }
          document.body.classList.add('modal-open');
          document.body.style.paddingRight = scheduleModalBodyState.paddingRight;
          document.body.style.overflow = scheduleModalBodyState.overflow;
          scheduleModalBodyState = null;
        }

        function placeTargetDrawerOnTop() {
          if (!targetDrawer) return;
          if (targetDrawer.parentElement !== document.body) {
            document.body.appendChild(targetDrawer);
          }
          targetDrawer.style.setProperty('z-index', '2085', 'important');

          const backdrops = document.querySelectorAll('.offcanvas-backdrop');
          const backdrop = backdrops[backdrops.length - 1];
          if (backdrop) {
            document.body.appendChild(backdrop);
            backdrop.classList.add('schedule-target-drawer-backdrop');
            backdrop.style.setProperty('z-index', '2080', 'important');
          }
        }

        function clearTargetDrawerLayering() {
          if (targetDrawer) {
            targetDrawer.style.removeProperty('z-index');
          }
          document.querySelectorAll('.schedule-target-drawer-backdrop').forEach((backdrop) => {
            backdrop.classList.remove('schedule-target-drawer-backdrop');
            backdrop.style.removeProperty('z-index');
          });
        }

        function initDrawer() {
          // 创建Drawer实例
          if (typeof bootstrap !== 'undefined' && bootstrap.Offcanvas) {
            drawerInstance = new bootstrap.Offcanvas(targetDrawer, {
              backdrop: true,
              scroll: true,
            });
          }
          
          // 打开Drawer按钮事件
          if (openTargetDrawerBtn) {
            openTargetDrawerBtn.addEventListener('click', () => {
              if (drawerInstance) {
                placeTargetDrawerOnTop();
                drawerInstance.show();
                
                // 强制将 Offcanvas 及其 backdrop 移动到 body 末尾，确保不受 Modal DOM 结构影响
                requestAnimationFrame(placeTargetDrawerOnTop);
                setTimeout(placeTargetDrawerOnTop, 50);
              }
            });
          }
          
          // 应用选择按钮事件
          if (applyTargetsBtn) {
            applyTargetsBtn.addEventListener('click', () => {
              applyTargets();
            });
          }
          
          // 监听Drawer显示事件，加载数据
          if (targetDrawer) {
            targetDrawer.addEventListener('show.bs.offcanvas', () => {
              captureScheduleModalBodyState();
              suspendScheduleModalFocusTrap();
              placeTargetDrawerOnTop();
              loadDrawerData();
              // 确保打开时立即触发一次预览更新
              updateDrawerPreview();
            });
            targetDrawer.addEventListener('shown.bs.offcanvas', placeTargetDrawerOnTop);
            targetDrawer.addEventListener('hidden.bs.offcanvas', () => {
              clearTargetDrawerLayering();
              restoreScheduleModalBodyState();
              restoreScheduleModalFocusTrap();
            });
          }
        }

        // 加载Drawer数据
        function loadDrawerData() {
          loadPlatforms();
          loadGroups();
          loadDevices();
        }

        // 应用选择
        function applyTargets() {
          updateTargets();
          updateTargetsSummary();
          if (drawerInstance) {
            drawerInstance.hide();
          }
        }

        // 更新目标摘要
        function updateTargetsSummary() {
          if (!targetsEl || !targetsSummaryText) return;
          const val = targetsEl.value.trim();
          if (!val) {
            targetsSummaryText.innerHTML = `<span class="badge rounded-pill bg-info-subtle text-info border border-info-subtle px-3 py-2 fw-normal">${escapeText(NB.t("js.schedules.no_device_selected"))}</span>`;
            return;
          }
          const lines = val.split('\n').filter(l => l.trim());
          if (lines.length === 1 && lines[0] === 'all') {
            targetsSummaryText.innerHTML = `<span class="badge rounded-pill bg-warning-subtle text-warning-emphasis border border-warning-subtle px-3 py-2 fw-normal">${escapeText(NB.t("js.schedules.all_devices"))}</span>`;
          } else {
            targetsSummaryText.innerHTML = `<span class="badge rounded-pill bg-warning-subtle text-warning-emphasis border border-warning-subtle px-3 py-2 fw-normal">${escapeText(NB.t("js.schedules.selected_config_items", {value0: lines.length}))}</span>`;
          }
        }

        // 解析目标文本到快速选择模式
        function parseTargetsToQuickMode() {
          if (!targetsEl) return;
          
          const targets = targetsEl.value.split('\n').map(t => t.trim()).filter(t => t);
          
          // 如果包含 all，则勾选所有设备
          if (targets.includes('all')) {
            if (targetAllCheckbox && !targetAllCheckbox.checked) {
              targetAllCheckbox.checked = true;
              // 触发 change 事件以同步 UI
              targetAllCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
            }
            return;
          }

          // 分别处理其他目标
          targets.forEach(target => {
            if (target.startsWith('platform:')) {
              const platform = target.substring(9);
              const cb = platformSelection.querySelector(`input[data-value="${platform}"]`);
              if (cb && !cb.checked) {
                cb.checked = true;
                // 展开父节点以便看到选中的项
                let parentUl = cb.closest('.tree-children');
                while (parentUl) {
                  parentUl.classList.remove('collapsed');
                  const toggle = parentUl.parentElement.querySelector('.tree-toggle');
                  if (toggle) toggle.classList.remove('collapsed');
                  parentUl = parentUl.parentElement.closest('.tree-children');
                }
                cb.dispatchEvent(new Event('change', { bubbles: true }));
              }
            } else if (target.startsWith('group:')) {
              const groupValue = target.substring(6);
              // 根据名称或者ID匹配（优先匹配name）
              let cb = groupSelection.querySelector(`input[data-name="${groupValue}"]`);
              if (!cb) {
                cb = groupSelection.querySelector(`input[data-value="${groupValue}"]`);
              }
              if (cb && !cb.checked) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', { bubbles: true }));
              }
            } else if (target.startsWith('device:')) {
              const deviceId = target.substring(7);
              const cb = deviceSelection.querySelector(`input[data-value="${deviceId}"]`);
              if (cb && !cb.checked) {
                cb.checked = true;
                cb.dispatchEvent(new Event('change', { bubbles: true }));
              }
            }
          });
        }

        // 初始化
        initTargetSelector();

        async function runSchedule(scheduleId, btn) {
          if (!scheduleId) return;
          btn.disabled = true;
          const old = btn.innerHTML;
          btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>${escapeText(NB.t("js.schedule_stats_runs.running_button"))}`;
          try {
            const result = await window.NB.api.request(`/api/schedules/${encodeURIComponent(scheduleId)}/run`, { method: "POST" });
            const data = result.data || {};
            if (!result.ok) {
              const detail = await window.NB.api.extractErrorDetail(result.response, "");
              throw new Error(detail || NB.t("js.schedules.switch_failed"));
            }
            const records = (data && data.records) || [];
            const enqueueStatus = (data && data.enqueue_status) ? String(data.enqueue_status) : "none";
            const enqueueWarning = (data && data.enqueue_warning_message) ? String(data.enqueue_warning_message) : "";
            if (records.length && window.NB && typeof window.NB.trackBackups === "function") {
              window.NB.trackBackups({
                run_id: data && data.run_id ? data.run_id : "",
                warning_message: enqueueWarning,
              });
              if (enqueueWarning) {
                window.NB.showToast(enqueueWarning, "warning");
              } else if (enqueueStatus === "partial") {
                window.NB.showToast(NB.t("js.devices_bulk.started") + records.length + NB.t("js.devices_bulk.backup_tasks_some_tasks_failed_to_queue"), "warning");
              } else {
                window.NB.showToast(NB.t("js.devices_bulk.started") + records.length + NB.t("js.devices_bulk.backup_tasks"), "info");
              }
            } else if (records.length === 0) {
              window.NB.showToast(NB.t("js.schedule_stats_runs.no_devices_found_for_backup"), "warning");
            }
          } catch (e) {
            console.error(e);
            window.NB.showToast(NB.t("js.schedule_stats_runs.manual_execution_failed") + e.message, "error");
          } finally {
            btn.disabled = false;
            btn.innerHTML = old;
          }
        }        btns.forEach((btn) => {
          btn.addEventListener("click", () => {
            const sid = btn.getAttribute("data-schedule-id");
            runSchedule(sid, btn);
          });
        });

        if (targetsEl && previewSummary && previewCounts && previewDevices && previewLoading && previewError) {
          let timer = null;
          let inflight = 0;

          function setLoading(loading) {
            previewLoading.classList.toggle("d-none", !loading);
          }

          function setError(hasError, message) {
            previewError.classList.toggle("d-none", !hasError);
            const msgEl = previewError.querySelector(".small");
            if (msgEl) {
              msgEl.textContent = message || tr(NB.t("template.schedules.preview_failed_to_load_please_try_again"));
            }
          }

          function renderPreview(data) {
            if (data !== lastPreviewData) {
              previewVisibleLimit = 30;
            }
            lastPreviewData = data;
            const counts = (data && data.counts) || {};
            const total = Number(counts.total || 0);
            previewSummary.textContent = tr(total ? NB.t("js.schedules.matched_value0_devices", {value0: total}) : NB.t("js.schedules.no_matched_devices"));

            const platforms = counts.platforms || {};
            const groups = counts.groups || {};

            previewCounts.innerHTML = "";
            let countHtml = '';
             if (Object.keys(platforms).length > 0) {
               const list = Object.keys(platforms).sort().map(k => `<span class="badge bg-secondary-subtle text-secondary-emphasis border border-secondary-subtle fw-normal px-2 mb-1" data-i18n-preserve>${escapeText(k || "unknown")} · ${platforms[k]}</span>`).join(' ');
               countHtml += `<div class="mb-1 w-100"><div class="text-secondary x-small opacity-75 mb-0">${escapeText(NB.t("js.schedules.matched_platforms"))}</div><div class="d-flex flex-wrap gap-1">${list}</div></div>`;
             }
             if (Object.keys(groups).length > 0) {
               const list = Object.keys(groups).sort().map(k => `<span class="badge bg-primary-subtle text-primary-emphasis border border-primary-subtle fw-normal px-2 mb-1" data-i18n-preserve>${escapeText(k)} · ${groups[k]}</span>`).join(' ');
               countHtml += `<div class="mb-1 w-100"><div class="text-secondary x-small opacity-75 mb-0">${escapeText(NB.t("js.schedules.matched_groups"))}</div><div class="d-flex flex-wrap gap-1">${list}</div></div>`;
             }
            previewCounts.innerHTML = trHtml(countHtml);

            const devices = (data && data.devices) || [];
            
            // 按需渲染：先显示一批，滚动接近底部时继续追加。
            const displayLimit = Math.min(previewVisibleLimit, devices.length || 0);
            const displayDevices = devices.slice(0, displayLimit);

            const deviceBadges = displayDevices
                  .map((d) => {
                    const name = escapeText(d.name || "");
                    const host = escapeText(d.host || "");
                    const platform = escapeText(d.platform || "");
                    const group = escapeText(d.group || "");
                    return `<span class="badge rounded-pill bg-warning-subtle text-warning-emphasis border border-warning-subtle px-3 py-2 fw-normal mb-1 me-1 device-badge" title="${name} · ${host} · ${platform} · ${group}" data-i18n-preserve>${name || host} <small class="opacity-75">(${host})</small></span>`;
                  })
                  .join("");
            
            if (deviceBadges) {
              previewDevices.innerHTML = `<div class="w-100"><div class="text-secondary x-small opacity-75 mb-0">${escapeText(NB.t("js.schedules.matched_devices"))}</div><div class="d-flex flex-wrap gap-1" data-i18n-preserve>${deviceBadges}</div></div>`;
            } else {
              previewDevices.innerHTML = "";
            }

            if (devices.length > displayLimit) {
              const more = document.createElement("div");
              more.className = "w-100 text-center text-muted small mt-2";
              more.textContent = tr(NB.t("js.schedules.scroll_down_to_load_more_value0_devices", {value0: devices.length - displayLimit}));
              previewDevices.appendChild(more);
            }
          }

          async function loadPreview() {
            const targets = (targetsEl.value || "").trim();
            if (!targets) {
              renderPreview({ devices: [], counts: { total: 0, platforms: {}, groups: {} } });
              setLoading(false);
              return;
            }
            const reqId = ++inflight;
            setError(false);
            setLoading(true);
            try {
              const fd = new FormData();
              fd.set("targets", targets);
              const result = await window.NB.api.request("/api/schedules/preview", { method: "POST", body: fd });
              const data = result.data || {};
              if (!result.ok) {
                const detail = await window.NB.api.extractErrorDetail(result.response, "");
                throw new Error(detail || NB.t("template.schedules.preview_failed_to_load_please_try_again"));
              }
              if (reqId !== inflight) return;
              renderPreview(data);
            } catch (e) {
              if (reqId !== inflight) return;
              previewSummary.textContent = "";
              previewCounts.innerHTML = "";
              previewDevices.innerHTML = "";
              setError(true, e && e.message ? String(e.message) : tr(NB.t("template.schedules.preview_failed_to_load_please_try_again")));
            } finally {
              if (reqId === inflight) setLoading(false);
            }
          }

          window.scheduleLoad = function scheduleLoad() {
            if (timer) clearTimeout(timer);
            timer = setTimeout(loadPreview, 250);
          };

          // 监听文本框变化
          targetsEl.addEventListener("input", window.scheduleLoad);
          
          // 监听文本框内容变化（包括程序化修改）
          const observer = new MutationObserver(() => {
            window.scheduleLoad();
          });
          observer.observe(targetsEl, { 
            attributes: true, 
            attributeFilter: ['value'],
            childList: false, 
            subtree: false 
          });
          
          // 初始加载
          loadPreview();

          const previewScroll = previewDevices.closest(".preview-scroll");
          if (previewScroll) {
            previewScroll.addEventListener("scroll", () => {
              if (!lastPreviewData || !isNearScrollBottom(previewScroll)) return;
              const total = ((lastPreviewData.devices || [])).length;
              if (previewVisibleLimit >= total) return;
              previewVisibleLimit = Math.min(previewVisibleLimit + 30, total);
              renderPreview(lastPreviewData);
            });
          }
        }

        if (drawerPreviewContainer) {
          drawerPreviewContainer.addEventListener("scroll", () => {
            if (!lastDrawerPreviewData || !isNearScrollBottom(drawerPreviewContainer)) return;
            const total = ((lastDrawerPreviewData.devices || [])).length;
            if (drawerPreviewVisibleLimit >= total) return;
            drawerPreviewVisibleLimit = Math.min(drawerPreviewVisibleLimit + 20, total);
            renderDrawerPreview(lastDrawerPreviewData);
          });
        }
      }, { name: "schedules-page" });
