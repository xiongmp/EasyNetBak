window.NB.ready(function initDeviceColumns() {
      const table = document.querySelector('.device-list-table table');
      if (!table) return;

      const thead = table.querySelector('thead tr');
      if (!thead) return;
      
      const ths = Array.from(thead.querySelectorAll('th'));
      const menu = document.getElementById('column-toggle-menu');
      if (!menu) return;

      const columns = ths.map((th, index) => {
          const text = th.textContent.trim();
          const isCheckbox = th.querySelector('input[type="checkbox"]') !== null;
          const isAction = text === NB.t("audit.csv.action");
          const isName = text === NB.t("email.field.device_name"); // Force name to be always visible to avoid empty table look
          
          return {
              index,
              text: text || `Column ${index}`,
              toggleable: !isCheckbox && !isAction && !isName,
              visible: true
          };
      });

      const STORAGE_KEY = 'deviceListColumnPrefs';
      let savedPrefs = {};
      try {
          const stored = localStorage.getItem(STORAGE_KEY);
          if (stored) savedPrefs = JSON.parse(stored);
      } catch (e) {}

      columns.forEach(col => {
          if (!col.toggleable) return;

          if (savedPrefs[col.text] !== undefined) {
              col.visible = savedPrefs[col.text];
          }

          const li = document.createElement('li');
          li.className = 'px-2 py-1';
          li.innerHTML = `
              <div class="form-check m-0 d-flex align-items-center">
                  <input class="form-check-input me-2 mt-0" type="checkbox" id="col-toggle-${col.index}" ${col.visible ? 'checked' : ''} style="cursor: pointer;">
                  <label class="form-check-label flex-grow-1 user-select-none" for="col-toggle-${col.index}" style="cursor: pointer;">
                      ${col.text}
                  </label>
              </div>
          `;
          
          const checkbox = li.querySelector('input');
          checkbox.addEventListener('change', (e) => {
              col.visible = e.target.checked;
              savedPrefs[col.text] = col.visible;
              localStorage.setItem(STORAGE_KEY, JSON.stringify(savedPrefs));
              applyColumnVisibility();
          });

          li.addEventListener('click', (e) => {
              e.stopPropagation();
          });

          menu.appendChild(li);
      });

      function applyColumnVisibility() {
          let visibleCount = 0;
          columns.forEach(col => {
              if (col.visible || !col.toggleable) visibleCount++;
          });

          const rows = table.querySelectorAll('tr');
          rows.forEach(row => {
              const cells = row.children;
              if (cells.length === 1 && cells[0].hasAttribute('colspan')) {
                 cells[0].setAttribute('colspan', visibleCount);
                 return;
              }
              
              columns.forEach(col => {
                  if (col.toggleable && cells[col.index]) {
                      if (col.visible) {
                          cells[col.index].style.display = '';
                      } else {
                          cells[col.index].style.display = 'none';
                      }
                  }
              });
          });
      }

      applyColumnVisibility();
    }, { name: "devices-columns" });
