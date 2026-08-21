const groupItems = (window.DEVICES_PAGE_CONFIG && window.DEVICES_PAGE_CONFIG.groupItems) || [];
        const tr = (text) => text;

        function escapeText(text) {
            const span = document.createElement('span');
            span.textContent = text == null ? '' : String(text);
            return span.innerHTML;
        }

        function buildTree(flatList) {
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

        function renderTree(nodes, container, options = {}) {
            const ul = document.createElement('ul');
            ul.className = 'tree-view';

            nodes.forEach(node => {
                const li = document.createElement('li');
                li.className = 'tree-node';

                const content = document.createElement('div');
                content.className = 'tree-node-content' + (options.selectedId == node.id ? ' selected' : '');
                content.style.setProperty('--tree-depth', String(Math.max(0, Number(node.depth || 0))));

                const isRoot = node.depth === 0 || node.id === 0;
                const hasChildren = node.children && node.children.length > 0;

                const toggle = document.createElement('div');
                toggle.className = 'tree-toggle' + (hasChildren ? '' : ' empty');
                toggle.innerHTML = '<i class="bi bi-caret-down-fill"></i>';

                let iconClass = 'bi me-2 ';
                let textClass = '';
                if (node.depth === 0 || node.id === 0) {
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
                if (node.id !== 0) {
                    text.setAttribute('data-i18n-preserve', '');
                }
                text.textContent = node.name;

                content.appendChild(toggle);
                content.appendChild(icon);
                content.appendChild(text);
                li.appendChild(content);

                let childrenUl = null;
                if (hasChildren) {
                    childrenUl = renderTree(node.children, null, options);
                    childrenUl.className = 'tree-view tree-children';
                    li.appendChild(childrenUl);

                    toggle.addEventListener('click', (e) => {
                        e.stopPropagation();
                        toggle.classList.toggle('collapsed');
                        childrenUl.classList.toggle('collapsed');
                    });
                }

                content.addEventListener('click', (e) => {
                    if (e.target.closest('.tree-toggle')) return;
                    if (options.onSelect) options.onSelect(node);
                });

                ul.appendChild(li);
            });

            if (container) {
                container.innerHTML = '';
                container.appendChild(ul);
            }
            return ul;
        }

        window.NB.ready(function initDeviceGroupControls() {
          const loginSelect = document.getElementById("device-filter-login-method");
          const platformSelect = document.getElementById("device-filter-platform");
          const filterGroupIdInput = document.getElementById('filterGroupId');

          const platformDropdown = platformSelect ? new NB.SearchableSelect(platformSelect) : null;

          if (filterGroupIdInput) {
              const currentGroupId = parseInt(filterGroupIdInput.value || '0', 10);

              const updateFilterGroupSelection = (nodeId) => {
                  filterGroupIdInput.value = nodeId;
                  const selectedText = document.getElementById('filterGroupSelectedText');
                  if (nodeId == 0) {
                      selectedText.textContent = tr(NB.t("js.devices_groups.group_all"));
                  } else {
                      const selectedGroup = groupItems.find(g => g.id == nodeId);
                      selectedText.innerHTML = selectedGroup ? '<span data-i18n-preserve>' + escapeText(selectedGroup.name) + '</span>' : tr(NB.t("js.devices_groups.group_all"));
                  }

                  const treeData = [
                      { id: 0, name: tr(NB.t("js.devices_groups.group_all")), depth: 0, children: [] },
                      ...buildTree(groupItems)
                  ];

                  renderTree(treeData, document.getElementById('filterGroupTreeContainer'), {
                      selectedId: nodeId,
                      onSelect: (node) => {
                          updateFilterGroupSelection(node.id);
                          const dropdownBtn = document.getElementById('filterGroupDropdownBtn');
                          const bsDropdown = bootstrap.Dropdown.getInstance(dropdownBtn) || new bootstrap.Dropdown(dropdownBtn);
                          bsDropdown.hide();
                          // 自动触发表单提交
                          dropdownBtn.closest('form').submit();
                      }
                  });
              };

              updateFilterGroupSelection(currentGroupId);
          }

          if (!loginSelect || !platformSelect) return;

          function syncPlatformOptions() {
            const loginMethod = (loginSelect.value || "").toLowerCase();
            const options = Array.from(platformSelect.options);
            let firstVisibleValue = "";

            options.forEach((opt, idx) => {
              if (idx === 0) return;
              const kind = opt.getAttribute("data-kind") || "ssh";
              const visible = !loginMethod || kind === loginMethod;
              opt.hidden = !visible;
              opt.disabled = !visible;
              if (visible && !firstVisibleValue) firstVisibleValue = opt.value;
            });

            const current = platformSelect.value;
            if (current) {
              const currentOpt = options.find((o) => o.value === current);
              if (currentOpt && (currentOpt.hidden || currentOpt.disabled)) {
                platformSelect.value = "";
                platformSelect.dispatchEvent(new Event('change'));
              }
            }

            if (platformDropdown) platformDropdown.refresh();
          }

          loginSelect.addEventListener("change", syncPlatformOptions);
          syncPlatformOptions();
        }, { name: "devices-groups" });
