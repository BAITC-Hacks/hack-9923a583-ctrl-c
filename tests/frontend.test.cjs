const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const apiSource = fs.readFileSync(path.join(root, "js/api.js"), "utf8");
const appSource = fs.readFileSync(path.join(root, "js/app.js"), "utf8");

// Minimal DOM doubles exercise rendering and request handling without dependencies.
// These tests inspect generated markup; they do not replace browser layout checks.
function createHarness() {
  const elements = new Map();
  const timers = new Map();
  const cleared = [];
  const loggedErrors = [];
  let nextTimerId = 0;

  function element(selector) {
    if (!elements.has(selector)) {
      const classes = new Set();
      const listeners = new Map();
      elements.set(selector, {
        textContent: "",
        innerHTML: "",
        attributes: {},
        listeners,
        addEventListener(type, callback) { listeners.set(type, callback); },
        classList: {
          toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
          remove(name) { classes.delete(name); },
          contains(name) { return classes.has(name); }
        },
        setAttribute(name, value) { this.attributes[name] = value; },
        querySelector(child) { return element(`${selector} ${child}`); },
        querySelectorAll() { return []; },
        scrollIntoView() {}
      });
    }
    return elements.get(selector);
  }

  const context = vm.createContext({
    console: { error: (...args) => loggedErrors.push(args) },
    AbortController,
    Intl,
    document: { querySelector: element },
    setTimeout(callback, duration) {
      const id = ++nextTimerId;
      timers.set(id, { callback, duration });
      return id;
    },
    clearTimeout(id) {
      cleared.push(id);
      timers.delete(id);
    }
  });
  vm.runInContext(apiSource, context, { filename: "js/api.js" });
  vm.runInContext(appSource, context, { filename: "js/app.js" });
  return { context, element, timers, cleared, loggedErrors };
}

function vendor(overrides = {}) {
  return {
    id: "HK-1",
    name: "Пример подрядчика",
    city: "Алматы",
    categories: ["Ведущий"],
    explanation: "Свободен 14.11.2026; проводит свадьбы на русском языке.",
    score: 19,
    max_score: 27,
    synthetic: false,
    price: 200000,
    matches: ["Город", "Свободен на дату"],
    event_formats: ["свадьба"],
    languages: ["русский"],
    description: "Проводит камерные церемонии.",
    ...overrides
  };
}

function success(results = [vendor()]) {
  return { status: "success", message: "Подобрали подрядчиков.", results };
}

function assertTimerCleared(harness) {
  assert.equal(harness.timers.size, 0, "request timer must not remain active");
  assert.equal(harness.cleared.length, 1, "timer must be cleared once");
}

test("missing prices are explicit; a genuine zero remains zero", () => {
  const { context } = createHarness();
  for (const value of [null, undefined, "", "  ", NaN, false, -1]) {
    assert.equal(context.formatPrice(value), "Цена уточняется");
  }
  assert.equal(context.formatPrice(0), "0 ₸");
  assert.equal(context.formatPrice(200000).replace(/\s/g, ""), "200000₸");
});

test("cards prioritize explanation, retain matches, and collapse description", () => {
  const { context } = createHarness();
  const html = context.renderContractorCard(vendor({ price: null }), 0);
  assert(html.indexOf('class="recommendation"') < html.indexOf('class="profile-description"'));
  assert(html.includes('<details class="profile-description">'));
  assert(!html.includes('class="profile-description" open'));
  assert(html.includes('class="match-chip">Город</span>'));
  assert(html.includes("Цена уточняется"));
  assert(!html.includes("<small>от </small>"));
});

test("profile text, explanation, and matches cannot inject HTML", () => {
  const { context } = createHarness();
  const html = context.renderContractorCard(vendor({
    name: "<script>name()</script>",
    description: "<script>description()</script>",
    explanation: "<img src=x onerror=alert(1)>",
    matches: ["Город <script>"]
  }), 0);
  assert(!html.includes("<script>"));
  assert(!html.includes("<img"));
  assert(html.includes("&lt;script&gt;description()&lt;/script&gt;"));
  assert(html.includes("Город &lt;script&gt;"));
});

test("all three valid result outcomes are rendered distinctly", () => {
  const { context, element } = createHarness();
  context.renderResults(success([vendor(), vendor({ id: "HK-2" }), vendor({ id: "HK-3" })]));
  assert.equal((element("#results-content").innerHTML.match(/class="contractor-card"/g) || []).length, 3);
  const titles = new Set([element("#results-title").textContent]);
  for (const status of ["category_not_found", "no_matches"]) {
    context.renderResults({ status, message: "Причина пустой выдачи.", results: [] });
    assert(element("#results-content").innerHTML.includes("Причина пустой выдачи."));
    assert(!element("#results-content").innerHTML.includes('class="contractor-card"'));
    titles.add(element("#results-title").textContent);
  }
  assert.equal(titles.size, 3);
});

test("unknown statuses and malformed results never become empty search outcomes", () => {
  const { context, element } = createHarness();
  const malformed = [
    null,
    [],
    {},
    { status: "unknown", message: "Ответ", results: [] },
    { status: "success", message: "Ответ", results: [] },
    { status: "success", message: "Ответ" },
    success([null]),
    success([vendor({ name: undefined })]),
    success([vendor({ categories: [] })]),
    success([vendor({ price: -1 })]),
    success([vendor({ matches: "Город" })]),
    success([vendor({ explanation: "" })]),
    success([vendor({ score: "10" })]),
    success([vendor(), vendor(), vendor(), vendor()]),
    { ...success(), status: "no_matches" }
  ];
  for (const response of malformed) {
    element("#results-content").innerHTML = "previous view";
    assert.throws(() => context.renderResults(response), (error) => error.code === "invalid_response");
    assert.equal(element("#results-content").innerHTML, "previous view");
  }
});

test("technical error details remain outside the user interface", () => {
  const { context, element } = createHarness();
  for (const code of ["server", "validation", "catalog_unavailable", "timeout", "network", "invalid_response"]) {
    context.renderError({ code, message: "Traceback secret database path" });
    const html = element("#results-content").innerHTML;
    assert(!html.includes("Traceback"));
    assert(!html.includes("secret"));
    assert(html.includes('id="retry-button"'));
  }
});

test("successful requests send form data with a 10 second abort timer and clear it", async () => {
  const harness = createHarness();
  const payload = { city: "Алматы", budget: 200000 };
  harness.context.fetch = async (url, options) => {
    assert.equal(new URL(url).pathname, "/api/recommend");
    assert.equal(options.method, "POST");
    assert.equal(options.headers["Content-Type"], "application/json");
    assert.equal(options.body, JSON.stringify(payload));
    assert(options.signal instanceof AbortSignal);
    assert.equal([...harness.timers.values()][0].duration, 10000);
    return { ok: true, json: async () => success() };
  };
  assert.equal((await harness.context.getRecommendations(payload)).status, "success");
  assertTimerCleared(harness);
});

for (const [status, expectedCode] of [[422, "validation"], [503, "catalog_unavailable"], [500, "server"]]) {
  test(`HTTP ${status} yields ${expectedCode} and clears the request timer`, async () => {
    const harness = createHarness();
    harness.context.fetch = async () => ({ ok: false, status, text: async () => "Internal technical details" });
    await assert.rejects(() => harness.context.getRecommendations({}), (error) => error.code === expectedCode);
    assertTimerCleared(harness);
  });
}

test("invalid JSON is treated as an invalid response and clears the timer", async () => {
  const harness = createHarness();
  harness.context.fetch = async () => ({ ok: true, json: async () => { throw new SyntaxError("HTML instead of JSON"); } });
  await assert.rejects(() => harness.context.getRecommendations({}), (error) => error.code === "invalid_response");
  assertTimerCleared(harness);
});

test("network failure produces a network error and clears the timer", async () => {
  const harness = createHarness();
  harness.context.fetch = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(() => harness.context.getRecommendations({}), (error) => error.code === "network");
  assertTimerCleared(harness);
});

test("a stalled fetch is aborted when the timer fires", async () => {
  const harness = createHarness();
  harness.context.fetch = (url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new DOMException("Abort", "AbortError")));
  });
  const pending = harness.context.getRecommendations({});
  [...harness.timers.values()][0].callback();
  await assert.rejects(() => pending, (error) => error.code === "timeout");
  assertTimerCleared(harness);
});

test("the timeout also covers an unfinished response body", async () => {
  const harness = createHarness();
  let notifyBodyStarted;
  const bodyStarted = new Promise((resolve) => { notifyBodyStarted = resolve; });
  harness.context.fetch = async (url, options) => ({
    ok: true,
    json: () => new Promise((resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new DOMException("Abort", "AbortError")));
      notifyBodyStarted();
    })
  });
  const pending = harness.context.getRecommendations({});
  await bodyStarted;
  [...harness.timers.values()][0].callback();
  await assert.rejects(() => pending, (error) => error.code === "timeout");
  assertTimerCleared(harness);
});

test("a malformed HTTP 200 reply shows an error, logs details, and unlocks the form", async () => {
  const harness = createHarness();
  harness.context.fetch = async () => ({ ok: true, json: async () => ({ status: "unknown", results: [] }) });
  await harness.context.runSearch({ city: "Алматы", budget: 200000 });
  assert.equal(harness.element("#results-title").textContent, "Не удалось получить рекомендации");
  assert(harness.element("#results-content").innerHTML.includes("Сервис вернул неполный ответ"));
  assert(!harness.element("#results-content").innerHTML.includes("Совпадений не нашлось"));
  assert.equal(harness.element("#submit-button").disabled, false);
  assert.equal(harness.element("#results").attributes["aria-busy"], "false");
  assert.equal(harness.loggedErrors.length, 1);
  assertTimerCleared(harness);
});
