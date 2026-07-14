(function (global) {
  "use strict";

  const NB = global.NB = global.NB || {};
  const messages = global.NB_MESSAGES || {};

  function interpolate(message, params) {
    return String(message).replace(/\{([A-Za-z0-9_.-]+)\}/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(params || {}, name) ? String(params[name]) : match
    ));
  }

  NB.i18n = {
    locale: global.NB_LOCALE || "zh-CN",
    isEnglish: /^en(?:-|$)/i.test(global.NB_LOCALE || ""),
    messages,
    t(key, params, fallback) {
      const message = Object.prototype.hasOwnProperty.call(messages, key)
        ? messages[key]
        : (fallback == null ? key : fallback);
      return interpolate(message, params);
    },
    formatNumber(value, options) {
      return new Intl.NumberFormat(this.locale, options || {}).format(value);
    },
    formatDateTime(value, options) {
      const date = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(date.getTime())) return String(value == null ? "" : value);
      return new Intl.DateTimeFormat(this.locale, options || {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit"
      }).format(date);
    },
    formatList(values, options) {
      if (typeof Intl.ListFormat !== "function") return Array.from(values || []).join(", ");
      return new Intl.ListFormat(this.locale, options || {}).format(Array.from(values || []));
    }
  };

  NB.t = NB.i18n.t.bind(NB.i18n);
  document.documentElement.lang = NB.i18n.locale;
})(window);
