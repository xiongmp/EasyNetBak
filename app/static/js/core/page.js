window.NB = window.NB || {};

(function (global) {
  "use strict";

  const NB = global.NB;
  const initialized = new Set();
  const cleanupCallbacks = new Map();
  let anonymousInitializerId = 0;

  function reportInitializationError(name, error) {
    console.error(`[NB] Failed to initialize ${name}`, error);
    document.dispatchEvent(new CustomEvent("nb:page-error", {
      detail: { name, error },
    }));
  }

  function ready(callback, options) {
    if (typeof callback !== "function") {
      throw new TypeError("NB.ready requires a callback");
    }

    const settings = options || {};
    const name = String(settings.name || callback.name || `anonymous-${++anonymousInitializerId}`);
    const once = settings.once !== false;
    const run = function () {
      if (once && initialized.has(name)) return;
      if (once) initialized.add(name);

      try {
        const result = callback();
        if (typeof result === "function") {
          cleanupCallbacks.set(name, result);
        }
        if (result && typeof result.catch === "function") {
          result
            .then((cleanup) => {
              if (typeof cleanup === "function") cleanupCallbacks.set(name, cleanup);
            })
            .catch((error) => reportInitializationError(name, error));
        }
      } catch (error) {
        reportInitializationError(name, error);
      }
    };

    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", run, { once: true });
    } else {
      queueMicrotask(run);
    }
  }

  function destroy(name) {
    const key = String(name || "");
    const cleanup = cleanupCallbacks.get(key);
    cleanupCallbacks.delete(key);
    initialized.delete(key);
    if (typeof cleanup !== "function") return;
    try {
      cleanup();
    } catch (error) {
      reportInitializationError(`destroy:${key}`, error);
    }
  }

  function destroyAll() {
    Array.from(cleanupCallbacks.keys()).reverse().forEach(destroy);
  }

  function readJson(elementOrId, fallback) {
    const element = typeof elementOrId === "string"
      ? document.getElementById(elementOrId)
      : elementOrId;
    if (!element) return fallback;

    try {
      return JSON.parse(element.textContent || "null") ?? fallback;
    } catch (error) {
      reportInitializationError(`config:${element.id || "anonymous"}`, error);
      return fallback;
    }
  }

  function delegate(root, eventName, selector, handler, options) {
    const target = root || document;
    const listener = function (event) {
      const matched = event.target instanceof Element ? event.target.closest(selector) : null;
      if (!matched || (target !== document && !target.contains(matched))) return;
      handler.call(matched, event, matched);
    };

    target.addEventListener(eventName, listener, options);
    return function removeDelegatedListener() {
      target.removeEventListener(eventName, listener, options);
    };
  }

  NB.ready = ready;
  NB.destroy = destroy;
  NB.destroyAll = destroyAll;
  NB.readJson = readJson;
  NB.delegate = delegate;
  if (typeof global.addEventListener === "function") {
    global.addEventListener("pagehide", destroyAll);
  }
})(window);
