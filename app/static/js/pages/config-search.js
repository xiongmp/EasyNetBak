window.NB.ready(function initConfigSearchPage() {
    function escapeText(text) {
        const span = document.createElement('span');
        span.textContent = text == null ? '' : String(text);
        return span.innerHTML;
    }

    // Search form enhancements
    const searchForm = document.getElementById('search-form');
    const searchBtn = document.getElementById('search-btn');
    const clearBtn = document.getElementById('clear-search');
    const searchInput = document.getElementById('q');

    if (searchForm) {
        searchForm.addEventListener('submit', function() {
            searchBtn.disabled = true;
            searchBtn.querySelector('.search-btn-content').classList.add('d-none');
            searchBtn.querySelector('.spinner-border').classList.remove('d-none');
        });
    }

    if (clearBtn && searchInput) {
        searchInput.addEventListener('input', function() {
            if (this.value) {
                clearBtn.classList.remove('d-none');
            } else {
                clearBtn.classList.add('d-none');
            }
        });

        clearBtn.addEventListener('click', function() {
            searchInput.value = '';
            clearBtn.classList.add('d-none');
            searchInput.focus();
        });
    }

    // Back to top button
    const backToTop = document.getElementById('back-to-top');
    if (backToTop) {
        window.addEventListener('scroll', function() {
            if (window.pageYOffset > 300) {
                backToTop.classList.remove('d-none');
            } else {
                backToTop.classList.add('d-none');
            }
        });

        backToTop.addEventListener('click', function() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }

    // Auto scroll to results if paging
    if (window.location.search.includes('page=') || window.location.search.includes('q=')) {
        const resultsEl = document.querySelector('.config-search-results');
        if (resultsEl && window.location.search.includes('q=')) {
            setTimeout(() => {
                resultsEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 100);
        }
    }

    // Copy snippet logic
    document.querySelectorAll('.copy-snippet-btn').forEach(btn => {
        btn.addEventListener('click', function() {
            const wrapper = this.closest('.config-snippet-wrapper');
            const code = wrapper.querySelector('code');
            
            // For snippets that have been processed into lines with line numbers
            // we want to extract only the text content, excluding line numbers
            let textToCopy = '';
            const lines = code.querySelectorAll('.d-flex');
            if (lines.length > 0) {
                textToCopy = Array.from(lines).map(line => {
                    const contentSpan = line.querySelector('span:last-child');
                    return contentSpan ? contentSpan.textContent : '';
                }).join('\n');
            } else {
                textToCopy = code.textContent;
            }

            navigator.clipboard.writeText(textToCopy).then(() => {
                const icon = this.querySelector('i');
                icon.className = 'bi bi-check-lg text-success';
                setTimeout(() => {
                    icon.className = 'bi bi-clipboard';
                }, 2000);
            });
        });
    });

    // Expand/Collapse all
    document.getElementById('expand-all')?.addEventListener('click', function() {
        document.querySelectorAll('.config-device-group .collapse').forEach(el => {
            const bs = window.bootstrap;
            if (bs && bs.Collapse) {
                const col = bs.Collapse.getOrCreateInstance(el);
                col.show();
            }
        });
    });

    document.getElementById('collapse-all')?.addEventListener('click', function() {
        document.querySelectorAll('.config-device-group .collapse').forEach(el => {
            const bs = window.bootstrap;
            if (bs && bs.Collapse) {
                const col = bs.Collapse.getOrCreateInstance(el);
                col.hide();
            }
        });
    });

    // View config modal (reusing the one from base.html)
    document.querySelectorAll('.view-config-btn').forEach(btn => {
        btn.addEventListener('click', async function() {
            const backupId = this.getAttribute('data-backup-id');
            const deviceName = this.getAttribute('data-device-name');
            const timeStr = this.getAttribute('data-time');
            
            const modalEl = document.getElementById('backup-view-modal');
            const modal = new bootstrap.Modal(modalEl);
            
            document.getElementById('backup-view-title').innerHTML = window.NB.t("template.config_search.view_configuration") + ' - <span data-i18n-preserve>' + escapeText(deviceName) + '</span>';
            document.getElementById('backup-view-meta').textContent = window.NB.t("email.field.backup_time") + ": " + timeStr;
            
            const renderEl = document.getElementById('backup-view-render');
            const loadingEl = document.getElementById('backup-view-loading');
            const errorEl = document.getElementById('backup-view-error');
            const downloadBtn = document.getElementById('backup-view-download');
            const oldFullscreenBtn = document.getElementById('backup-view-fullscreen');
            
            // Clone the button to remove any existing event listeners from base.html
            const fullscreenBtn = oldFullscreenBtn.cloneNode(true);
            oldFullscreenBtn.parentNode.replaceChild(fullscreenBtn, oldFullscreenBtn);
            
            renderEl.innerHTML = '';
            loadingEl.classList.remove('d-none');
            errorEl.classList.add('d-none');
            downloadBtn.classList.add('d-none');
            fullscreenBtn.classList.remove('d-none');
            
            // Handle fullscreen button
             fullscreenBtn.addEventListener('click', function() {
                 const dialog = modalEl.querySelector('.modal-dialog');
                 const isFullscreen = dialog.classList.contains('modal-fullscreen');
                 const icon = this.querySelector('i');
                 const pre = renderEl.querySelector('pre');
                 
                 if (isFullscreen) {
                     dialog.classList.remove('modal-fullscreen');
                     dialog.classList.add('modal-xl');
                     icon.className = 'bi bi-arrows-fullscreen';
                     this.setAttribute('aria-label', window.NB.t("template.base.enter_full_screen"));
                     if (pre) {
                         pre.classList.remove('config-view-pre-full');
                         pre.classList.add('config-view-pre-normal');
                     }
                 } else {
                     dialog.classList.add('modal-fullscreen');
                     dialog.classList.remove('modal-xl');
                     icon.className = 'bi bi-fullscreen-exit';
                     this.setAttribute('aria-label', window.NB.t("template.config_search.exit_full_screen"));
                     if (pre) {
                         pre.classList.remove('config-view-pre-normal');
                         pre.classList.add('config-view-pre-full');
                     }
                 }
             });
            
            // Reset fullscreen state when opening
            const dialog = modalEl.querySelector('.modal-dialog');
            dialog.classList.remove('modal-fullscreen');
            dialog.classList.add('modal-xl');
            fullscreenBtn.querySelector('i').className = 'bi bi-arrows-fullscreen';
            
            modal.show();
            
            try {
                const result = await window.NB.api.request(`/api/backups/${backupId}`);
                const resp = result.response;
                if (!result.ok) {
                    const detail = await window.NB.api.extractErrorDetail(resp, "");
                    if (resp.status === 403) throw new Error(window.NB.t("template.config_search.this_account_cannot_view_backup_content"));
                    if (resp.status === 404) throw new Error(detail || window.NB.t("template.config_search.backup_record_not_found"));
                    throw new Error(detail || window.NB.t("template.config_search.failed_to_load_backup_content"));
                }
                const data = result.data || {};
                
                loadingEl.classList.add('d-none');
                
                const configText = data.record ? data.record.config_text : null;
                
                if (configText) {
                    const pre = document.createElement('pre');
                    pre.className = 'mb-0 p-3 bg-dark text-light border-0 rounded font-monospace small config-view-pre config-view-pre-normal';
                    
                    // Highlight keyword if present
                    if (q) {
                        const regex = new RegExp('(' + q.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&') + ')', 'gi');
                        if (configText.length < 100000) { // Limit highlight for large configs
                            pre.innerHTML = escapeText(configText).replace(regex, '<mark class="p-0 bg-warning text-dark">$1</mark>');
                        } else {
                            pre.textContent = configText;
                        }
                    } else {
                        pre.textContent = configText;
                    }
                    
                    renderEl.appendChild(pre);
                    
                    downloadBtn.href = `/api/backups/${backupId}/download`;
                    downloadBtn.classList.remove('d-none');
                } else {
                    errorEl.textContent = window.NB.t("template.config_search.no_configuration_content");
                    errorEl.classList.remove('d-none');
                }
            } catch (err) {
                loadingEl.classList.add('d-none');
                errorEl.textContent = err.message;
                errorEl.classList.remove('d-none');
            }
        });
    });

    // Highlight and extract snippets
    const q = String(window.NB.readJson("config-search-config", {}).query || "");
    if (q) {
        const regex = new RegExp('(' + q.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&') + ')', 'gi');
        document.querySelectorAll('.config-snippet code').forEach(code => {
            const original = code.textContent;
            const lines = original.split('\n');
            const matchingIndices = [];
            
            lines.forEach((line, index) => {
                if (regex.test(line)) {
                    matchingIndices.push(index);
                }
            });

            if (matchingIndices.length > 0) {
                const contextLines = 2;
                const resultGroups = [];
                let currentGroup = [];
                let lastIndex = -1;

                matchingIndices.forEach(idx => {
                    const start = Math.max(0, idx - contextLines);
                    const end = Math.min(lines.length - 1, idx + contextLines);

                    if (lastIndex !== -1 && start <= lastIndex + 1) {
                        // Merge with last group
                        for (let i = lastIndex + 1; i <= end; i++) {
                            currentGroup.push({ln: i + 1, text: lines[i]});
                        }
                    } else {
                        // New group
                        if (currentGroup.length > 0) resultGroups.push(currentGroup);
                        currentGroup = [];
                        for (let i = start; i <= end; i++) {
                            currentGroup.push({ln: i + 1, text: lines[i]});
                        }
                    }
                    lastIndex = end;
                });
                if (currentGroup.length > 0) resultGroups.push(currentGroup);

                // Build HTML
                let html = '';
                resultGroups.forEach((group, groupIdx) => {
                    if (groupIdx > 0) {
                        html += '<div class="text-secondary opacity-50 my-1">...</div>';
                    }
                    group.forEach(item => {
                        const escaped = item.text.replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
                        const highlighted = escaped.replace(regex, '<mark class="p-0 bg-warning text-dark">$1</mark>');
                        html += `<div class="d-flex"><span class="text-secondary me-3 line-number">${item.ln}</span><span>${highlighted}</span></div>`;
                    });
                });
                code.parentElement.classList.add('config-snippet-normal-wrap'); // Allow our custom layout
                code.innerHTML = html;
            }
        });
    }
}, { name: "config-search-page" });
