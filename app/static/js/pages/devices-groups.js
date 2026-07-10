const groupItems = (window.DEVICES_PAGE_CONFIG && window.DEVICES_PAGE_CONFIG.groupItems) || [];

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

        document.addEventListener('DOMContentLoaded', function() {
          class SearchableSelect {
            constructor(selectElement) {
              if (!selectElement) return;
              this.select = selectElement;
              this.container = document.createElement('div');
              this.container.className = 'dropdown w-100';
              this.select.parentNode.insertBefore(this.container, this.select);
              this.select.style.display = 'none';

              this.buildDropdown();
              this.attachEvents();
            }

            buildDropdown() {
              const selectedOption = this.select.options[this.select.selectedIndex];
              const label = selectedOption ? selectedOption.text : 'Select...';

              this.btn = document.createElement('button');
              this.btn.className = 'btn btn-sm btn-outline-secondary dropdown-toggle w-100 text-start d-flex justify-content-between align-items-center bg-body text-truncate filter-control-sm';
              this.btn.type = 'button';
              this.btn.dataset.bsToggle = 'dropdown';
              this.btn.dataset.bsAutoClose = 'outside';
              this.btn.style.fontSize = '13px'; // 调整为 13px 以匹配视觉大小
              this.btn.innerHTML = `<span class="text-truncate flex-grow-1">${label}</span>`;
              this.container.appendChild(this.btn);

              this.menu = document.createElement('ul');
              this.menu.className = 'dropdown-menu w-100 p-0 shadow'; // 去掉 p-2，因为搜索框需要贴边，或者在 searchContainer 里处理
              this.menu.style.maxHeight = '300px';
              this.menu.style.overflowY = 'auto';
              this.menu.style.fontSize = '13px'; // 菜单字体也设为 13px

              // Search Input
              const searchContainer = document.createElement('div'); // 改为 div 以便做 sticky
              searchContainer.className = 'p-2 bg-body sticky-top border-bottom'; // sticky-top 加上白色背景
              searchContainer.style.top = '0';
              searchContainer.style.zIndex = '1';

              this.searchInput = document.createElement('input');
              this.searchInput.type = 'text';
              this.searchInput.className = 'form-control form-control-sm'; // 去掉 mb-2，由容器 padding 控制
              this.searchInput.style.fontSize = '13px'; // 搜索框字体
              this.searchInput.placeholder = '搜索...';
              this.searchInput.addEventListener('click', (e) => e.stopPropagation());
              searchContainer.appendChild(this.searchInput);
              this.menu.appendChild(searchContainer);

              // Options Container
              this.optionsList = document.createElement('div');
              this.optionsList.className = 'p-1'; // 把 padding 移到这里
              this.menu.appendChild(this.optionsList);

              this.container.appendChild(this.menu);
              this.renderOptions();
            }

            renderOptions() {
              this.optionsList.innerHTML = '';
              const filter = this.searchInput.value.toLowerCase();
              let hasVisibleOption = false;

              Array.from(this.select.options).forEach(opt => {
                if (opt.hidden || opt.disabled || opt.style.display === 'none') return;

                const text = opt.text;
                if (filter && !text.toLowerCase().includes(filter)) return;

                hasVisibleOption = true;
                const li = document.createElement('li');
                const a = document.createElement('a');
                a.className = `dropdown-item ${opt.selected ? 'active' : ''} text-truncate`;
                a.style.fontSize = '13px'; // 选项字体
                a.href = '#';
                a.textContent = text;
                a.dataset.value = opt.value;
                a.onclick = (e) => {
                  e.preventDefault();
                  this.select.value = opt.value;
                  this.select.dispatchEvent(new Event('change'));
                  this.updateLabel();
                  const dropdownInstance = bootstrap.Dropdown.getInstance(this.btn);
                  if (dropdownInstance) dropdownInstance.hide();
                };
                li.appendChild(a);
                this.optionsList.appendChild(li);
              });

              if (!hasVisibleOption) {
                  const li = document.createElement('li');
                  li.className = 'dropdown-item disabled text-muted text-center small';
                  li.textContent = '无匹配项';
                  this.optionsList.appendChild(li);
              }
            }

            updateLabel() {
              const selectedOption = this.select.options[this.select.selectedIndex];
              const span = this.btn.querySelector('span');
              if (span) span.textContent = selectedOption ? selectedOption.text : 'Select...';
            }

            attachEvents() {
              this.searchInput.addEventListener('input', () => {
                this.renderOptions();
              });

              this.select.addEventListener('change', () => {
                 this.updateLabel();
              });

              this.btn.addEventListener('show.bs.dropdown', () => {
                  this.renderOptions();
                  setTimeout(() => this.searchInput.focus(), 0);
              });
            }

            refresh() {
              this.renderOptions();
              this.updateLabel();
            }
          }

          const loginSelect = document.getElementById("device-filter-login-method");
          const platformSelect = document.getElementById("device-filter-platform");
          const filterGroupIdInput = document.getElementById('filterGroupId');

          let platformDropdown;
          if (platformSelect) {
             platformDropdown = new SearchableSelect(platformSelect);
          }

          if (filterGroupIdInput) {
              const currentGroupId = parseInt(filterGroupIdInput.value || '0', 10);

              const updateFilterGroupSelection = (nodeId) => {
                  filterGroupIdInput.value = nodeId;
                  const selectedText = document.getElementById('filterGroupSelectedText');
                  if (nodeId == 0) {
                      selectedText.textContent = '所属分组: 全部';
                  } else {
                      const selectedGroup = groupItems.find(g => g.id == nodeId);
                      selectedText.textContent = selectedGroup ? selectedGroup.name : '所属分组: 全部';
                  }

                  const treeData = [
                      { id: 0, name: '所属分组: 全部', depth: 0, children: [] },
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
        });
