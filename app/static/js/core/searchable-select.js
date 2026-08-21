(function (global) {
  "use strict";

  const NB = global.NB = global.NB || {};

  function optionLabel(option) {
    if (!option) return "";
    const explicitLabel = option.dataset ? option.dataset.nbLabel : "";
    if (explicitLabel) return explicitLabel;
    return String(option.textContent || "")
      .replace(/\r/g, "")
      .replace(/\n[ \t]*/g, "")
      .replace(/^[ \t\n]+|[ \t\n]+$/g, "");
  }

  function setPreserveI18n(element, preserve) {
    if (!element) return;
    if (preserve) element.setAttribute("data-i18n-preserve", "");
    else element.removeAttribute("data-i18n-preserve");
  }

  class SearchableSelect {
    constructor(selectElement) {
      if (!selectElement || selectElement.dataset.nbSearchableSelect === "true") return;

      this.select = selectElement;
      this.select.dataset.nbSearchableSelect = "true";
      // Prevent the generic select enhancer from wrapping this custom dropdown again.
      this.select.dataset.nbSelectEnhanced = "true";

      this.container = document.createElement("div");
      this.container.className = "dropdown w-100 nb-searchable-select-wrap";
      this.select.parentNode.insertBefore(this.container, this.select);
      this.select.style.display = "none";

      this.buildDropdown();
      this.attachEvents();
    }

    buildDropdown() {
      this.btn = document.createElement("button");
      this.btn.type = "button";
      this.btn.className = `${this.select.className} nb-searchable-select-button text-start d-flex justify-content-between align-items-center`;
      this.btn.dataset.bsToggle = "dropdown";
      this.btn.dataset.bsAutoClose = "outside";
      this.btn.setAttribute("aria-expanded", "false");
      this.btn.setAttribute("aria-haspopup", "listbox");
      this.btn.innerHTML = '<span class="nb-select-text text-truncate flex-grow-1"></span>';
      this.container.appendChild(this.btn);

      this.menu = document.createElement("div");
      this.menu.className = "dropdown-menu nb-select-menu nb-searchable-select-menu";
      this.menu.setAttribute("role", "listbox");

      const searchContainer = document.createElement("div");
      searchContainer.className = "nb-searchable-select-search p-2 bg-body sticky-top border-bottom";

      this.searchInput = document.createElement("input");
      this.searchInput.type = "search";
      this.searchInput.className = "form-control form-control-sm";
      this.searchInput.placeholder = NB.t("js.select_enhance.search", {}, "Search...");
      this.searchInput.setAttribute("aria-label", this.searchInput.placeholder);
      this.searchInput.addEventListener("click", (event) => event.stopPropagation());
      this.searchInput.addEventListener("keydown", (event) => event.stopPropagation());
      searchContainer.appendChild(this.searchInput);
      this.menu.appendChild(searchContainer);

      this.optionsList = document.createElement("div");
      this.optionsList.className = "nb-searchable-select-options p-1";
      this.menu.appendChild(this.optionsList);
      this.container.appendChild(this.menu);

      this.updateLabel();
      this.renderOptions();
    }

    renderOptions() {
      this.optionsList.innerHTML = "";
      const filter = String(this.searchInput.value || "").trim().toLocaleLowerCase();
      let hasVisibleOption = false;

      Array.from(this.select.options).forEach((option) => {
        if (option.hidden || option.disabled) return;

        const label = optionLabel(option);
        if (filter && !label.toLocaleLowerCase().includes(filter)) return;

        hasVisibleOption = true;
        const item = document.createElement("button");
        item.type = "button";
        item.className = "nb-select-option";
        item.textContent = label;
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", option.selected ? "true" : "false");
        item.classList.toggle("selected", option.selected);
        setPreserveI18n(item, option.hasAttribute("data-i18n-preserve"));
        item.addEventListener("click", () => {
          this.select.value = option.value;
          this.select.dispatchEvent(new Event("change", { bubbles: true }));
          this.updateLabel();
          const dropdown = bootstrap.Dropdown.getInstance(this.btn) || new bootstrap.Dropdown(this.btn);
          dropdown.hide();
        });
        this.optionsList.appendChild(item);
      });

      if (!hasVisibleOption) {
        const empty = document.createElement("div");
        empty.className = "nb-searchable-select-empty text-muted text-center small px-2 py-2";
        empty.setAttribute("role", "status");
        empty.textContent = NB.t("js.select_enhance.no_matches", {}, "No matches");
        this.optionsList.appendChild(empty);
      }
    }

    updateLabel() {
      const selectedOption = this.select.options[this.select.selectedIndex];
      const text = this.btn.querySelector(".nb-select-text");
      setPreserveI18n(text, !!(selectedOption && selectedOption.hasAttribute("data-i18n-preserve")));
      if (text) text.textContent = optionLabel(selectedOption);
      this.btn.disabled = this.select.disabled;
      this.btn.classList.toggle("disabled", this.select.disabled);
      this.btn.setAttribute("aria-disabled", this.select.disabled ? "true" : "false");
    }

    attachEvents() {
      this.searchInput.addEventListener("input", () => this.renderOptions());
      this.select.addEventListener("change", () => {
        this.updateLabel();
        this.renderOptions();
      });
      this.btn.addEventListener("show.bs.dropdown", () => {
        this.renderOptions();
        setTimeout(() => this.searchInput.focus(), 0);
      });
    }

    refresh() {
      this.updateLabel();
      this.renderOptions();
    }
  }

  NB.SearchableSelect = SearchableSelect;
})(window);
