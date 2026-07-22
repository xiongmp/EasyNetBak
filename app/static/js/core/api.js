window.NB = window.NB || {};

(function () {
  async function parseJson(response) {
    if (!response || response.status === 204) return null;
    try {
      return await response.clone().json();
    } catch (e) {
      return null;
    }
  }

  async function extractErrorDetail(response, fallback) {
    const payload = await parseJson(response);
    if (payload && payload.error && typeof payload.error.message === "string" && payload.error.message) {
      return payload.error.message;
    }
    if (payload && typeof payload.detail === "string" && payload.detail) {
      return payload.detail;
    }
    if (payload && typeof payload.message === "string" && payload.message) {
      return payload.message;
    }
    return fallback || "";
  }

  async function extractErrorCode(response, fallback) {
    const payload = await parseJson(response);
    if (payload && payload.error && typeof payload.error.code === "string" && payload.error.code) {
      return payload.error.code;
    }
    if (payload && typeof payload.code === "string" && payload.code) {
      return payload.code;
    }
    return fallback || "";
  }

  function buildOptions(options) {
    const source = options || {};
    const headers = new Headers(source.headers || {});
    if (!headers.has("Accept")) headers.set("Accept", "application/json");
    if (!headers.has("X-Requested-With")) headers.set("X-Requested-With", "XMLHttpRequest");
    return { ...source, credentials: source.credentials || "same-origin", headers };
  }

  async function request(url, options) {
    const response = await fetch(url, buildOptions(options));
    const data = await parseJson(response);
    const requestId = response.headers.get("X-Request-ID") || "";
    if (response.status === 401) {
      document.dispatchEvent(new CustomEvent("nb:authentication-required", {
        detail: { url: String(url), requestId },
      }));
    }
    return { response, data, ok: response.ok, requestId };
  }

  window.NB.api = {
    parseJson,
    extractErrorDetail,
    extractErrorCode,
    buildOptions,
    request,
  };
})();
