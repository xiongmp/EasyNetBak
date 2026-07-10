(function () {
        function shouldEnhance(select) {
          if (!select || select.dataset.nbSelectEnhanced === "true") return false;
          if (!select.classList.contains("form-select")) return false;
          if (select.multiple) return false;
          const size = Number(select.getAttribute("size") || "1");
          if (size > 1) return false;
          return true;
        }

        function optionLabel(option) {
          if (!option) return "";
          const explicitLabel = option.dataset ? option.dataset.nbLabel : "";
          if (explicitLabel) return explicitLabel;
          const rawText = option.textContent == null ? "" : String(option.textContent);
          return rawText
            .replace(/\r/g, "")
            .replace(/\n[ \t]*/g, "")
            .replace(/^[ \t\n]+|[ \t\n]+$/g, "");
        }

        function selectedLabel(select) {
          const option = select.selectedOptions && select.selectedOptions[0];
          return optionLabel(option);
        }

        function syncWrapperState(select, wrapper, button) {
          wrapper.classList.toggle("d-none", select.classList.contains("d-none"));
          button.disabled = select.disabled;
          button.classList.toggle("disabled", select.disabled);
          button.setAttribute("aria-disabled", select.disabled ? "true" : "false");
          button.querySelector(".nb-select-text").textContent = selectedLabel(select);
        }

        function buildOptions(select, menu, button) {
          menu.innerHTML = "";
          Array.from(select.options).forEach((option) => {
            if (option.hidden) return;
            const item = document.createElement("button");
            item.type = "button";
            item.className = "nb-select-option";
            item.textContent = optionLabel(option);
            const depth = Math.max(0, Number(option.dataset && option.dataset.depth ? option.dataset.depth : "0"));
            item.style.setProperty("--nb-option-depth", String(depth));
            item.disabled = option.disabled;
            item.classList.toggle("selected", option.selected);
            item.setAttribute("role", "option");
            item.setAttribute("aria-selected", option.selected ? "true" : "false");
            item.addEventListener("click", () => {
              if (option.disabled) return;
              select.value = option.value;
              select.dispatchEvent(new Event("change", { bubbles: true }));
              button.querySelector(".nb-select-text").textContent = selectedLabel(select);
              const dropdown = bootstrap.Dropdown.getInstance(button) || new bootstrap.Dropdown(button);
              dropdown.hide();
              setTimeout(refreshAllSelects, 0);
            });
            menu.appendChild(item);
          });
        }

        function enhanceSelect(select) {
          if (!shouldEnhance(select)) return;

          select.dataset.nbSelectEnhanced = "true";
          const wrapper = document.createElement("div");
          wrapper.className = "dropdown nb-select-wrap";
          wrapper.style.cssText = select.style.cssText;
          select.style.cssText = "";

          const button = document.createElement("button");
          button.type = "button";
          button.className = select.className
            .replace(/\bd-none\b/g, "")
            .trim() + " nb-select-button text-start d-flex justify-content-between align-items-center";
          button.setAttribute("data-bs-toggle", "dropdown");
          button.setAttribute("data-bs-auto-close", "outside");
          button.setAttribute("aria-expanded", "false");
          button.innerHTML = '<span class="nb-select-text text-truncate"></span>';

          const menu = document.createElement("div");
          menu.className = "dropdown-menu nb-select-menu";
          menu.setAttribute("role", "listbox");

          select.parentNode.insertBefore(wrapper, select);
          wrapper.appendChild(select);
          wrapper.appendChild(button);
          wrapper.appendChild(menu);
          select.classList.add("nb-select-native");

          button.addEventListener("show.bs.dropdown", () => {
            buildOptions(select, menu, button);
          });
          select.addEventListener("change", () => {
            syncWrapperState(select, wrapper, button);
            buildOptions(select, menu, button);
          });

          const observer = new MutationObserver(() => {
            syncWrapperState(select, wrapper, button);
            buildOptions(select, menu, button);
          });
          observer.observe(select, {
            attributes: true,
            childList: true,
            subtree: true,
            attributeFilter: ["class", "disabled", "hidden", "selected", "style"],
          });

          syncWrapperState(select, wrapper, button);
          buildOptions(select, menu, button);
        }

        function refreshAllSelects() {
          document.querySelectorAll("select.form-select[data-nb-select-enhanced='true']").forEach((select) => {
            const wrapper = select.closest(".nb-select-wrap");
            const button = wrapper ? wrapper.querySelector(".nb-select-button") : null;
            const menu = wrapper ? wrapper.querySelector(".nb-select-menu") : null;
            if (wrapper && button && menu) {
              syncWrapperState(select, wrapper, button);
              buildOptions(select, menu, button);
            }
          });
        }

    window.NB = window.NB || {};
    window.NB.refreshSelectDropdowns = refreshAllSelects;

    document.addEventListener("DOMContentLoaded", () => {
      document.querySelectorAll("select.form-select").forEach(enhanceSelect);
    });
  })();
