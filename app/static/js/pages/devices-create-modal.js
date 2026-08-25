window.NB.ready(function initDeviceCreateModal() {
        const tr = (text) => text;
        const loginSelect = document.getElementById("device-create-login-method");
        const platformSelect = document.getElementById("device-create-platform");
        const portInput = document.getElementById("device-create-port");
        const modal = document.getElementById('deviceCreateModal');
        const form = modal ? modal.querySelector('form') : null;
        const titleEl = document.getElementById('device-create-modal-title');
        const iconEl = document.getElementById('device-create-modal-icon');
        const copyNotice = document.getElementById('device-copy-notice');
        let copySource = null;
        let updateCreateGroupSelection = null;

        function escapeText(text) {
            const span = document.createElement('span');
            span.textContent = text == null ? '' : String(text);
            return span.innerHTML;
        }
        
        // 分组树选择逻辑
        const createGroupIdInput = document.getElementById('createGroupId');
        if (createGroupIdInput) {
            updateCreateGroupSelection = (nodeId) => {
                createGroupIdInput.value = nodeId;
                const selectedText = document.getElementById('createGroupSelectedText');
                if (nodeId == 0) {
                    selectedText.textContent = tr(NB.t("template.device_detail.ungrouped"));
                } else {
                    const selectedGroup = groupItems.find(g => g.id == nodeId);
                    selectedText.innerHTML = selectedGroup ? '<span data-i18n-preserve>' + escapeText(selectedGroup.name) + '</span>' : tr(NB.t("template.device_detail.ungrouped"));
                }
                
                const treeData = [
                    { id: 0, name: tr(NB.t("template.device_detail.ungrouped")), depth: 0, children: [] },
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
            if (modal) {
                modal.addEventListener('show.bs.modal', function(event) {
                    if (copySource || !event.relatedTarget) return;
                    if (form) form.reset();
                    updateCreateGroupSelection(0);
                    if (titleEl) titleEl.textContent = NB.t("js.devices_copy.add_title");
                    if (iconEl) iconEl.className = 'bi bi-plus-circle-fill text-primary me-2';
                    if (copyNotice) copyNotice.classList.add('d-none');
                });
            }
        }

        if (!loginSelect || !platformSelect) return;

        const platformDropdown = new NB.SearchableSelect(platformSelect);

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

          platformDropdown.refresh();
        }

        loginSelect.addEventListener("change", syncPlatformOptions);
        syncPlatformOptions();

        function setSelectValue(select, value, fallback) {
          if (!select) return;
          const normalized = String(value == null ? fallback : value);
          const option = Array.from(select.options).find((item) => item.value === normalized && !item.disabled);
          select.value = option ? normalized : String(fallback == null ? '' : fallback);
        }

        async function openCopyModal(deviceId) {
          if (!modal || !form) return;
          try {
            const result = await window.NB.api.request(`/api/devices/${encodeURIComponent(deviceId)}/copy`);
            if (!result.ok) {
              const detail = await window.NB.api.extractErrorDetail(result.response, NB.t("js.devices_copy.load_failed"));
              throw new Error(detail || NB.t("js.devices_copy.load_failed"));
            }
            const source = result.data && result.data.device;
            if (!source) throw new Error(NB.t("js.devices_copy.load_failed"));

            copySource = source;
            form.reset();
            form.elements.name.value = source.name || '';
            form.elements.host.value = source.host || '';
            loginSelect.value = (source.login_method || 'ssh').toLowerCase();
            syncPlatformOptions();
            setSelectValue(platformSelect, source.platform, '');
            platformDropdown.refresh();
            portInput.value = String(source.port || 22);
            setSelectValue(form.elements.encoding, source.encoding, 'utf-8');
            setSelectValue(form.elements.credential_id, source.credential_id, '');
            setSelectValue(form.elements.default_template_id, source.default_template_id, '0');
            if (updateCreateGroupSelection) updateCreateGroupSelection(source.group_id || 0);

            if (titleEl) titleEl.textContent = NB.t("js.devices_copy.copy_title");
            if (iconEl) iconEl.className = 'bi bi-copy text-primary me-2';
            if (copyNotice) {
              copyNotice.textContent = NB.t("js.devices_copy.notice", { name: source.name || '' });
              copyNotice.classList.remove('d-none');
            }
            bootstrap.Modal.getOrCreateInstance(modal).show();
            form.elements.name.focus();
            form.elements.name.select();
          } catch (error) {
            if (window.NB && typeof window.NB.showToast === 'function') {
              window.NB.showToast(error.message || NB.t("js.devices_copy.load_failed"), 'error');
            }
          }
        }

        if (form) {
          form.addEventListener('submit', (event) => {
            if (!copySource) return;
            const nameInput = form.elements.name;
            const hostInput = form.elements.host;
            const currentName = String(nameInput.value || '').trim();
            const currentHost = String(hostInput.value || '').trim();
            const currentPort = Number(portInput.value || 0);
            const sameName = currentName === String(copySource.name || '').trim();
            const sameHostPort = currentHost === String(copySource.host || '').trim() && currentPort === Number(copySource.port || 0);
            nameInput.classList.toggle('is-invalid', sameName);
            hostInput.classList.toggle('is-invalid', sameHostPort);
            portInput.classList.toggle('is-invalid', sameHostPort);
            if (sameName || sameHostPort) {
              event.preventDefault();
              if (window.NB && typeof window.NB.showToast === 'function') {
                window.NB.showToast(NB.t("js.devices_copy.unique_fields_required"), 'warning');
              }
              (sameName ? nameInput : hostInput).focus();
            }
          });
          ['name', 'host', 'port'].forEach((fieldName) => {
            const field = form.elements[fieldName];
            if (field) field.addEventListener('input', () => field.classList.remove('is-invalid'));
          });
        }

        if (modal) {
          modal.addEventListener('hidden.bs.modal', () => {
            copySource = null;
            if (form) form.querySelectorAll('.is-invalid').forEach((item) => item.classList.remove('is-invalid'));
          });
        }

        window.NB.devices = window.NB.devices || {};
        window.NB.devices.openCopyModal = openCopyModal;
      }, { name: "devices-create-modal" });
