(function () {
        const tr = (text) => window.NB && typeof window.NB.tr === 'function' ? window.NB.tr(text) : text;
        const loginSelect = document.getElementById("device-create-login-method");
        const platformSelect = document.getElementById("device-create-platform");
        const portInput = document.getElementById("device-create-port");

        function escapeText(text) {
            const span = document.createElement('span');
            span.textContent = text == null ? '' : String(text);
            return span.innerHTML;
        }
        
        // 分组树选择逻辑
        const createGroupIdInput = document.getElementById('createGroupId');
        if (createGroupIdInput) {
            const updateCreateGroupSelection = (nodeId) => {
                createGroupIdInput.value = nodeId;
                const selectedText = document.getElementById('createGroupSelectedText');
                if (nodeId == 0) {
                    selectedText.textContent = tr('未分组');
                } else {
                    const selectedGroup = groupItems.find(g => g.id == nodeId);
                    selectedText.innerHTML = selectedGroup ? '<span data-i18n-preserve>' + escapeText(selectedGroup.name) + '</span>' : tr('未分组');
                }
                
                const treeData = [
                    { id: 0, name: tr('未分组'), depth: 0, children: [] },
                    ...buildTree(groupItems)
                ];
                
                renderTree(treeData, document.getElementById('createGroupTreeContainer'), {
                    selectedId: nodeId,
                    onSelect: (node) => {
                        updateCreateGroupSelection(node.id);
                        const dropdownBtn = document.getElementById('createGroupDropdownBtn');
                        const bsDropdown = bootstrap.Dropdown.getInstance(dropdownBtn) || new bootstrap.Dropdown(dropdownBtn);
                        bsDropdown.hide();
                    }
                });
            };
            
            // 初始化
            const modal = document.getElementById('deviceCreateModal');
            if (modal) {
                modal.addEventListener('show.bs.modal', function() {
                    updateCreateGroupSelection(0);
                });
            }
        }

        if (!loginSelect || !platformSelect) return;

        function syncPlatformOptions() {
          const loginMethod = (loginSelect.value || "ssh").toLowerCase();
          const wantKind = loginMethod === "telnet" ? "telnet" : "ssh";
          const options = Array.from(platformSelect.options);
          let firstVisibleValue = null;
          options.forEach((opt) => {
            const kind = opt.getAttribute("data-kind") || "ssh";
            const visible = kind === wantKind;
            opt.hidden = !visible;
            opt.disabled = !visible;
            if (visible && !firstVisibleValue) firstVisibleValue = opt.value;
          });
          const current = platformSelect.value;
          const currentOpt = options.find((o) => o.value === current);
          if (!currentOpt || currentOpt.hidden || currentOpt.disabled) {
            platformSelect.value = firstVisibleValue || "";
          }

          if (portInput) {
            const v = String(portInput.value || "").trim();
            if (!v || v === "22" || v === "23") {
              portInput.value = loginMethod === "telnet" ? "23" : "22";
            }
          }
        }

        loginSelect.addEventListener("change", syncPlatformOptions);
        syncPlatformOptions();
      })();
