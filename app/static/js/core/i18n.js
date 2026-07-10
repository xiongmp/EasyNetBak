(function (global) {
  "use strict";
  const NB = global.NB = global.NB || {};
  const messages = global.NB_MESSAGES || {};
  const legacyMessages = global.NB_LEGACY_MESSAGES || {};
  const legacyEntries = Object.entries(legacyMessages).sort((a, b) => b[0].length - a[0].length);

  function translateLegacy(value) {
    let text = String(value == null ? "" : value);
    legacyEntries.forEach(([source, target]) => {
      text = text.split(source).join(target);
    });
    text = text
      .replace(/\b(Last \d+ (?:hours?|days?))(Backup tasks|Success rate|Duration distribution)\b/gi, "$1 $2")
      .replace(/\b(Last \d+ days),\s*按天\b/gi, "$1, by day")
      .replace(/，\s*/g, ", ")
      .replace(/\bConfiguration change frequency\s*\((Last \d+ days?)\)/gi, "Configuration change frequency ($1)")
      .replace(/\bWhy\s*Configuration\s*Ignore rules[?？]?/gi, "Why configure ignore rules?")
      .replace(/\bRule\s*Configuration\s*(?:notes|说明)[:：]?/gi, "Rule configuration notes:")
      .replace(/\bBy\s*Add\s*Ignore rules\b/gi, "By adding ignore rules")
      .replace(/\bSearch\s*Device\s*name\s*or\s*(?:IP)?\.{0,3}/gi, "Search device name or IP...")
      .replace(/([A-Za-z0-9)])：/g, "$1:")
      .replace(/\bTotal\s{2,}/g, "Total ")
      .replace(/\s{2,}/g, " ");
    return text;
  }

  NB.i18n = {
    locale: global.NB_LOCALE || "zh-CN",
    isEnglish: /^en\b/i.test(global.NB_LOCALE || ""),
    messages: messages,
    t(key, params, fallback) {
      let text = Object.prototype.hasOwnProperty.call(messages, key)
        ? messages[key]
        : (fallback == null ? key : fallback);
      Object.entries(params || {}).forEach(([name, value]) => {
        text = text.split("{" + name + "}").join(String(value));
      });
      return translateLegacy(text);
    }
  };
  NB.t = NB.i18n.t.bind(NB.i18n);
  NB.translateLegacy = translateLegacy;

  function localizeNode(root) {
    if (!legacyEntries.length || !root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      const translated = translateLegacy(root.nodeValue);
      if (translated !== root.nodeValue) root.nodeValue = translated;
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE && ["SCRIPT", "STYLE", "CODE", "PRE"].includes(root.tagName)) return;
    if (root.nodeType === Node.ELEMENT_NODE) {
      ["title", "placeholder", "aria-label", "data-confirm-message"].forEach((name) => {
        if (!root.hasAttribute(name)) return;
        const current = root.getAttribute(name);
        const translated = translateLegacy(current);
        if (translated !== current) root.setAttribute(name, translated);
      });
    }
    Array.from(root.childNodes || []).forEach(localizeNode);
  }

  const dynamicUiSelector = [
    "button", "option", "label", "th", "td",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "[role='status']", "[role='alert']",
    ".backup-status", ".status-pill", ".badge",
    ".breadcrumb-item", ".breadcrumb-subtitle",
    ".modal-title", ".toast", ".form-text",
    ".nav-link", ".dropdown-item",
    ".card-title", ".section-label", ".empty-state",
    ".rule-card", ".fw-bold", ".small", ".text-secondary", "p"
  ].join(",");

  function localizeDynamicUi(root) {
    if (!legacyEntries.length || !root) return;
    if (root.nodeType === Node.TEXT_NODE) {
      if (root.parentElement && root.parentElement.closest(dynamicUiSelector)) localizeNode(root);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE && root.matches(dynamicUiSelector)) localizeNode(root);
    if (typeof root.querySelectorAll === "function") {
      root.querySelectorAll(dynamicUiSelector).forEach(localizeNode);
    }
  }

  NB.localizeDynamicUi = localizeDynamicUi;

  function localizeObject(value, seen) {
    if (typeof value === "string") return translateLegacy(value);
    if (!value || typeof value !== "object") return value;
    seen = seen || new WeakSet();
    if (seen.has(value)) return value;
    seen.add(value);
    Object.keys(value).forEach((key) => {
      if (typeof value[key] === "string") value[key] = translateLegacy(value[key]);
      else if (value[key] && typeof value[key] === "object") localizeObject(value[key], seen);
    });
    return value;
  }

  function wrapEcharts(library) {
    if (!library || library.__nbI18nWrapped || typeof library.init !== "function") return;
    const nativeInit = library.init.bind(library);
    library.init = function () {
      const chart = nativeInit.apply(library, arguments);
      const nativeSetOption = chart.setOption.bind(chart);
      chart.setOption = function (option) {
        arguments[0] = localizeObject(option);
        return nativeSetOption.apply(chart, arguments);
      };
      return chart;
    };
    library.__nbI18nWrapped = true;
  }

  function installFunctionTranslator(name, mapArguments) {
    let wrapped;
    Object.defineProperty(NB, name, {
      configurable: true,
      get() { return wrapped; },
      set(fn) {
        if (typeof fn !== "function") { wrapped = fn; return; }
        wrapped = function () {
          const args = Array.from(arguments);
          mapArguments(args);
          return fn.apply(NB, args);
        };
      }
    });
  }

  if (legacyEntries.length) {
    const nativeAlert = global.alert.bind(global);
    const nativeConfirm = global.confirm.bind(global);
    global.alert = (message) => nativeAlert(translateLegacy(message));
    global.confirm = (message) => nativeConfirm(translateLegacy(message));
    installFunctionTranslator("showToast", (args) => { args[0] = translateLegacy(args[0]); });
    installFunctionTranslator("confirmDelete", (args) => { args[0] = translateLegacy(args[0]); });
    installFunctionTranslator("confirm", (args) => {
      const options = Object.assign({}, args[0] || {});
      ["title", "message", "confirmBtnText"].forEach((key) => {
        if (options[key]) options[key] = translateLegacy(options[key]);
      });
      args[0] = options;
    });
    if (global.echarts) {
      wrapEcharts(global.echarts);
    } else {
      Object.defineProperty(global, "echarts", {
        configurable: true,
        set(value) {
          Object.defineProperty(global, "echarts", {value: value, writable: true, configurable: true});
          wrapEcharts(value);
        }
      });
    }
    document.addEventListener("DOMContentLoaded", () => {
      document.documentElement.lang = NB.i18n.locale;
      if (NB.i18n.isEnglish) document.documentElement.lang = "en";
      localizeDynamicUi(document.body);
      document.querySelectorAll("input, select, textarea").forEach((field) => {
        field.addEventListener("invalid", () => {
          if (!NB.i18n.isEnglish) return;
          if (field.validity && field.validity.valueMissing) {
            field.setCustomValidity("Please fill out this field.");
          }
        });
        field.addEventListener("input", () => field.setCustomValidity(""));
        field.addEventListener("change", () => field.setCustomValidity(""));
      });
      const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
          if (mutation.type === "characterData") {
            localizeDynamicUi(mutation.target);
            return;
          }
          if (mutation.type === "attributes") {
            localizeNode(mutation.target);
            return;
          }
          mutation.addedNodes.forEach(localizeDynamicUi);
        });
      });
      observer.observe(document.body, {
        subtree: true,
        childList: true,
        characterData: true,
        attributes: true,
        attributeFilter: ["title", "placeholder", "aria-label", "data-confirm-message"]
      });
    });
  }
})(window);
