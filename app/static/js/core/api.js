window.NB = window.NB || {};

(function () {
  async function parseJson(response) {
    if (!response) return null;
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

  async function request(url, options) {
    const response = await fetch(url, options || {});
    const data = await parseJson(response);
    return { response, data, ok: !!(response && response.ok) };
  }

  window.NB.api = {
    parseJson,
    extractErrorDetail,
    extractErrorCode,
    request,
  };
})();
