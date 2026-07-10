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
    return text;
  }

  NB.i18n = {
    locale: global.NB_LOCALE || "zh-CN",
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
        if (root.hasAttribute(name)) root.setAttribute(name, translateLegacy(root.getAttribute(name)));
      });
    }
    Array.from(root.childNodes || []).forEach(localizeNode);
  }

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
    });
  }
})(window);
