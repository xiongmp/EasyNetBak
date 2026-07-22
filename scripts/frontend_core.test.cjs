const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

class TestCustomEvent {
  constructor(type, options) {
    this.type = type;
    this.detail = options?.detail;
  }
}

test("page lifecycle runs named initializers once after DOM readiness", () => {
  const listeners = [];
  const document = {
    readyState: "loading",
    addEventListener(name, callback) { if (name === "DOMContentLoaded") listeners.push(callback); },
    dispatchEvent() {},
    getElementById() { return null; },
  };
  const context = {
    window: { NB: {} }, document, CustomEvent: TestCustomEvent,
    Element: class {}, queueMicrotask, console, Set,
  };

  vm.runInNewContext(source("app/static/js/core/page.js"), context);
  let calls = 0;
  context.window.NB.ready(() => { calls += 1; }, { name: "sample" });
  context.window.NB.ready(() => { calls += 1; }, { name: "sample" });
  listeners.forEach((callback) => callback());
  assert.equal(calls, 1);
});

test("page config reader parses JSON and safely falls back", () => {
  const events = [];
  const elements = new Map([
    ["valid", { id: "valid", textContent: '{"enabled":true}' }],
    ["invalid", { id: "invalid", textContent: "{" }],
  ]);
  const context = {
    window: { NB: {} },
    document: {
      readyState: "complete", addEventListener() {},
      dispatchEvent(event) { events.push(event); },
      getElementById(id) { return elements.get(id) || null; },
    },
    CustomEvent: TestCustomEvent, Element: class {}, queueMicrotask,
    console: { error() {} }, Set,
  };

  vm.runInNewContext(source("app/static/js/core/page.js"), context);
  assert.equal(context.window.NB.readJson("valid", {}).enabled, true);
  assert.equal(context.window.NB.readJson("missing", "fallback"), "fallback");
  assert.equal(context.window.NB.readJson("invalid", "fallback"), "fallback");
  assert.equal(events[0].type, "nb:page-error");
});

test("page lifecycle runs registered cleanup on pagehide", async () => {
  const windowListeners = new Map();
  let cleaned = 0;
  const context = {
    window: {
      NB: {},
      addEventListener(name, callback) { windowListeners.set(name, callback); },
    },
    document: {
      readyState: "complete", addEventListener() {}, dispatchEvent() {}, getElementById() { return null; },
    },
    CustomEvent: TestCustomEvent, Element: class {}, queueMicrotask, console, Set,
  };

  vm.runInNewContext(source("app/static/js/core/page.js"), context);
  context.window.NB.ready(() => () => { cleaned += 1; }, { name: "cleanup-sample" });
  await new Promise((resolve) => setImmediate(resolve));
  windowListeners.get("pagehide")();
  assert.equal(cleaned, 1);
});

test("API helper applies same-origin defaults and exposes request IDs", async () => {
  let capturedOptions;
  const context = {
    window: { NB: {} }, document: { dispatchEvent() {} },
    CustomEvent: TestCustomEvent, Headers, Response, String,
    fetch: async (_url, options) => {
      capturedOptions = options;
      return new Response('{"ok":true}', {
        status: 200,
        headers: { "Content-Type": "application/json", "X-Request-ID": "req-42" },
      });
    },
  };

  vm.runInNewContext(source("app/static/js/core/api.js"), context);
  const result = await context.window.NB.api.request("/api/example");
  assert.equal(capturedOptions.credentials, "same-origin");
  assert.equal(capturedOptions.headers.get("Accept"), "application/json");
  assert.equal(capturedOptions.headers.get("X-Requested-With"), "XMLHttpRequest");
  assert.equal(result.data.ok, true);
  assert.equal(result.requestId, "req-42");
});

test("API helper publishes an authentication event for 401 responses", async () => {
  const events = [];
  const context = {
    window: { NB: {} },
    document: { dispatchEvent(event) { events.push(event); } },
    CustomEvent: TestCustomEvent, Headers, Response, String,
    fetch: async () => new Response('{"detail":"expired"}', {
      status: 401, headers: { "Content-Type": "application/json" },
    }),
  };

  vm.runInNewContext(source("app/static/js/core/api.js"), context);
  await context.window.NB.api.request("/api/private");
  assert.equal(events[0].type, "nb:authentication-required");
  assert.equal(events[0].detail.url, "/api/private");
});
