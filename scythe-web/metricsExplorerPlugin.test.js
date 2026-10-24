import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {
  createMetricsExplorerPlugin, METRICS_EXPLORER_ID, escapeHtml, formatDuration, summarize, metricNames,
  renderModuleBars, renderMetricsRows, renderInteractions,
} from "./metricsExplorerPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const clockOf = (ms) => new Date(ms).toLocaleTimeString("en-US", {hour12: false});

function fakeClock() {
  let at = 0, next = 1;
  const timers = new Map();
  return {
    setTimeout: (fn, ms) => { const id = next++; timers.set(id, {at: at + ms, fn, every: 0}); return id; },
    setInterval: (fn, ms) => { const id = next++; timers.set(id, {at: at + ms, fn, every: ms}); return id; },
    clearTimeout: (id) => timers.delete(id),
    clearInterval: (id) => timers.delete(id),
    async tick(ms) {
      const target = at + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, t] = due;
        at = t.at;
        if (t.every) t.at += t.every; else timers.delete(id);
        t.fn();
        await settle();
      }
      at = target;
    },
    pending: () => timers.size,
  };
}

// --- pure rendering ---

test("escapeHtml escapes the five characters and stringifies numbers", () => {
  assert.equal(escapeHtml(`<a href="x" onclick='y'>&</a>`), "&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;");
  assert.equal(escapeHtml(3.5), "3.5");
});

test("formatDuration: seconds, minutes, hours; the boundaries are exclusive; missing is 0s", () => {
  assert.equal(formatDuration(undefined), "0s");
  assert.equal(formatDuration(59.9), "59s");
  assert.equal(formatDuration(60), "60s");
  assert.equal(formatDuration(61.5), "1m 1s");
  assert.equal(formatDuration(3600), "60m 0s");
  assert.equal(formatDuration(3661), "1h 1m");
});

test("summarize totals the counts and groups them by the part before the first dot", () => {
  const {total, moduleCounts} = summarize({"ais.vessel_count": {count: 3}, "ais.x.y": {count: 2}, "recon.entity_count": {}, "browser.heap": {count: 5}});
  assert.equal(total, 10);
  assert.deepEqual(moduleCounts, {ais: 5, recon: 0, browser: 5});
  assert.deepEqual(summarize(undefined), {total: 0, moduleCounts: {}});
});

test("metricNames keeps everything after the module (names may contain dots), sorted and unique", () => {
  assert.deepEqual(metricNames({"b.z": 1, "a.y.x": 1, "c.z": 1, nodot: 1}), ["y.x", "z"]);
});

test("module bars: largest first, scaled to the largest, coloured by module, labels escaped; empty is empty", () => {
  const html = renderModuleBars({ais: 50, recon: 100, "<b>": 25});
  assert.ok(html.indexOf("recon") < html.indexOf("ais") && html.indexOf("ais") < html.indexOf("&lt;b&gt;"));
  assert.match(html, /width: 100%; background: #4a9eff/);
  assert.match(html, /width: 50%; background: #ff9800/);
  assert.match(html, /width: 25%; background: #888/);
  assert.equal(renderModuleBars({}), "");
  assert.match(renderModuleBars({ais: 0}), /width: 0%/);   // the largest is floored at 1
});

test("metric rows: time from epoch seconds, integers as they are, other numbers to two places, text as text, all escaped", () => {
  const html = renderMetricsRows([
    {timestamp: 100, module: "m<", metric_name: "n&", value: 7},
    {timestamp: 100, module: "m", metric_name: "n", value: 1.23456},
    {timestamp: 100, module: "m", metric_name: "n", value: "<i>"},
  ]);
  assert.ok(html.includes(`<td class="metric-time-cell">${clockOf(100_000)}</td>`));
  assert.ok(html.includes('<td class="metric-module-cell">m&lt;</td>'));
  assert.ok(html.includes("<td>n&amp;</td>"));
  assert.ok(html.includes('<td class="metric-value-cell">7</td>'));
  assert.ok(html.includes('<td class="metric-value-cell">1.23</td>'));
  assert.ok(html.includes('<td class="metric-value-cell">&lt;i&gt;</td>'));
  assert.match(renderMetricsRows([]), /No metrics found/);
  assert.match(renderMetricsRows(null), /No metrics found/);
});

test("interactions render newest first, with the target only when there is one, escaped", () => {
  const html = renderInteractions([
    {timestamp: 1000, action: "first", target: ""},
    {timestamp: 2000, action: "<b>second</b>", target: "<img onerror=x>"},
  ]);
  assert.ok(html.includes("<strong>&lt;b&gt;second&lt;/b&gt;</strong>"));
  assert.ok(html.indexOf("&lt;b&gt;second") < html.indexOf("first"));
  assert.ok(html.includes("→ &lt;img onerror=x&gt;"));
  assert.equal((html.match(/interaction-target/g) || []).length, 1);
  assert.ok(!html.includes("<img"));
});

// --- the tool ---

function setup({responses = {}, interactions = null, last = null, recon = null, heap = null, active = true, noElements = []} = {}) {
  const clock = fakeClock();
  const calls = [];
  const dom = {text: {}, html: {}};
  const view = {
    bound: 0, unbound: 0, handlers: null, names: [], activeCalls: 0,
    filters: {module: "", name: "", limit: "50"},
    setText: (id, text) => { dom.text[id] = text; },
    setHtml: (id, html) => { if (noElements.includes(id)) return false; dom.html[id] = html; return true; },
    setMetricNames(names) { view.names.push(names); },
    panelActive: () => { view.activeCalls += 1; return active; },
  };
  view.bind = (handlers) => { view.bound += 1; view.handlers = handlers; return () => { view.unbound += 1; view.handlers = null; }; };
  const viewService = {...view, filters: () => ({...view.filters})};
  const ws = createWorkspace({log: quiet, timers: clock});
  const pending = [];
  ws.provide("httpFetch", async (url) => {
    calls.push(url);
    const r = responses[url.split("?")[0] + (url.includes("module=interaction") ? "?interaction" : "")] ?? responses[url];
    if (typeof r === "function") return r(url);
    if (r) return r;
    return {ok: true, json: async () => (url.startsWith("/api/metrics/query") ? {metrics: []} : {})};
  });
  ws.provide("explorerView", viewService);
  ws.provide("liveSources", {interactionLog: () => interactions, lastMetrics: () => last, recon: () => recon, heapBytes: () => heap});
  const errors = [];
  const {tool, controls} = createMetricsExplorerPlugin({log: {...quiet, error: (m) => errors.push(m)}});
  ws.register(tool);
  const ok = (body) => ({ok: true, json: async () => body});
  return {ws, clock, calls, dom, view, controls, errors, ok, pending};
}

const SESSION = {session_duration_seconds: 125, metrics: {"ais.vessel_count": {count: 3}, "recon.entity_count": {count: 9}}};

test("activating refreshes at once: session, then the filtered query, then the interactions request; the panel is filled", async () => {
  const t = setup({responses: {
    "/api/metrics/session": {ok: true, json: async () => SESSION},
    "/api/metrics/query?limit=50": {ok: true, json: async () => ({metrics: [{timestamp: 1, module: "ais", metric_name: "vessel_count", value: 2}], total_count: 1234})},
  }});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.deepEqual(t.calls, ["/api/metrics/session", "/api/metrics/query?limit=50", "/api/metrics/query?module=interaction&limit=20"]);
  assert.equal(t.dom.text["session-duration"], "2m 5s");
  assert.equal(t.dom.text["total-metrics-count"], (1234).toLocaleString());   // the query's total replaces the session's 12
  assert.match(t.dom.html["module-bars"], /recon/);
  assert.deepEqual(t.view.names[0], ["entity_count", "vessel_count"]);
  assert.match(t.dom.html["metrics-table-body"], /vessel_count/);
  assert.equal(t.dom.text["total-interactions-count"], "--");
});

test("filters become query parameters, encoded; an empty module or name is left out", async () => {
  const t = setup({responses: {"/api/metrics/session": {ok: true, json: async () => ({})}}});
  t.view.filters = {module: "a b", name: "x&y", limit: "200"};
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(t.calls[1], "/api/metrics/query?limit=200&module=a%20b&metric_name=x%26y");
  t.calls.length = 0;
  t.view.filters = {module: "", name: "", limit: "50"};
  await t.view.handlers.filtersChanged();
  assert.equal(t.calls[0], "/api/metrics/query?limit=50");
});

test("a non-OK session response only skips the summary", async () => {
  const t = setup({responses: {"/api/metrics/session": {ok: false, status: 500}, "/api/metrics/query?limit=50": undefined}});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(t.dom.text["session-duration"], undefined);
  assert.ok(t.calls.includes("/api/metrics/query?limit=50"));
});

test("PINNED: a session request that throws, or a body that is not JSON, ends the refresh: no table, no live stats", async () => {
  for (const session of [() => { throw new Error("down"); }, {ok: true, json: async () => { throw new Error("html"); }}]) {
    const t = setup({responses: {"/api/metrics/session": session}, last: {heap_used_mb: 5}});
    await t.ws.activate(METRICS_EXPLORER_ID);
    await settle();
    assert.deepEqual(t.calls, ["/api/metrics/session"]);
    assert.equal(t.dom.text["live-heap-used"], undefined);
    assert.deepEqual(t.errors, ["[MetricsExplorer] Error fetching metrics:"]);
  }
});

test("a query that is not OK leaves the table alone but still refreshes the interactions; one that throws does not", async () => {
  const notOk = setup({responses: {"/api/metrics/query?limit=50": {ok: false}}});
  await notOk.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(notOk.dom.html["metrics-table-body"], undefined);
  assert.equal(notOk.dom.text["total-interactions-count"], "--");
  const throws = setup({responses: {"/api/metrics/query?limit=50": () => { throw new Error("down"); }}});
  await throws.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(throws.dom.text["total-interactions-count"], undefined);
  assert.deepEqual(throws.errors, ["[MetricsExplorer] Error fetching filtered metrics:"]);
});

test("a plain list is shown as the table without touching the total; an object with no list is an error", async () => {
  const list = setup({responses: {"/api/metrics/query?limit=50": {ok: true, json: async () => [{timestamp: 1, module: "m", metric_name: "n", value: 1}]}}});
  await list.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.match(list.dom.html["metrics-table-body"], /<td>n<\/td>/);
  assert.equal(list.dom.text["total-metrics-count"], (0).toLocaleString());   // from the session: nothing counted
  const bad = setup({responses: {"/api/metrics/query?limit=50": {ok: true, json: async () => ({error: "x"})}}});
  await bad.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.deepEqual(bad.errors, ["[MetricsExplorer] Error fetching filtered metrics:"]);
});

test("the total falls back to the list length when the server sends no total_count", async () => {
  const t = setup({responses: {"/api/metrics/query?limit=50": {ok: true, json: async () => ({metrics: [{timestamp: 1, module: "m", metric_name: "n", value: 1}, {timestamp: 1, module: "m", metric_name: "n", value: 1}]})}}});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(t.dom.text["total-metrics-count"], "2");
});

test("PINNED: the interactions request is made and ignored, and if it throws the list is not updated", async () => {
  const t = setup({interactions: [{timestamp: 1, action: "a", target: ""}], responses: {
    "/api/metrics/query?interaction": () => { throw new Error("down"); },
  }});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.ok(t.calls.includes("/api/metrics/query?module=interaction&limit=20"));
  assert.equal(t.dom.html["interactions-list"], undefined);
  assert.deepEqual(t.errors, ["[MetricsExplorer] Error fetching interactions:"]);
});

test("interactions come from the in-memory log: the last ten, newest first, with the full count", async () => {
  const log = Array.from({length: 25}, (_, i) => ({timestamp: i * 1000, action: `act${i}`, target: ""}));
  const t = setup({interactions: log});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  const html = t.dom.html["interactions-list"];
  assert.ok(html.includes("act24") && html.includes("act15") && !html.includes("act14"));
  assert.ok(html.indexOf("act24") < html.indexOf("act15"));
  assert.equal(t.dom.text["total-interactions-count"], "25");
});

test("no interactions logged shows the placeholder; a missing list element skips the count", async () => {
  for (const interactions of [null, []]) {
    const t = setup({interactions});
    await t.ws.activate(METRICS_EXPLORER_ID);
    await settle();
    assert.match(t.dom.html["interactions-list"], /Interactions logged to SQLite/);
    assert.equal(t.dom.text["total-interactions-count"], "--");
  }
  const missing = setup({interactions: [{timestamp: 1, action: "a", target: ""}], noElements: ["interactions-list"]});
  await missing.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(missing.dom.text["total-interactions-count"], undefined);
});

test("live stats come from the metrics tool's last values; a zero shows as '--'", async () => {
  const t = setup({last: {heap_used_mb: 12.345, entity_count: 0, avg_update_ms: 0}});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(t.dom.text["live-heap-used"], "12.3 MB");
  assert.equal(t.dom.text["live-entity-count"], "0");   // `??`: zero is a value here
  assert.equal(t.dom.text["live-vessel-count"], "--");
  assert.equal(t.dom.text["live-update-rate"], "--");   // truthiness: zero is not shown
  const noHeap = setup({last: {heap_used_mb: 0}});
  await noHeap.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(noHeap.dom.text["live-heap-used"], "--");
  const missing = setup({last: {}});
  await missing.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(missing.dom.text["live-heap-used"], "--");
});

test("without those values it falls back to the recon counters and the browser heap", async () => {
  const t = setup({recon: {perfMetrics: {entityUpdates: 41, avgUpdateMs: 3.14159}}, heap: 5 * 1024 * 1024});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(t.dom.text["live-entity-count"], "41");
  assert.equal(t.dom.text["live-update-rate"], "3.1 ms");
  assert.equal(t.dom.text["live-heap-used"], "5.0 MB");
  const bare = setup({recon: {}, heap: 0});
  await bare.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(bare.dom.text["live-entity-count"], "--");
  assert.equal(bare.dom.text["live-update-rate"], "--");
  assert.equal(bare.dom.text["live-heap-used"], "0.0 MB");   // a heap object is enough, as before
  const none = setup();
  await none.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.deepEqual(none.dom.text["live-heap-used"], undefined);
});

test("it refreshes every 30 s, but only while the panel is showing", async () => {
  const t = setup({active: false});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  t.calls.length = 0;
  await t.clock.tick(30000);
  assert.equal(t.view.activeCalls, 1);
  assert.deepEqual(t.calls, []);
  t.calls.length = 0;
  const shown = setup({active: true});
  await shown.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  shown.calls.length = 0;
  await shown.clock.tick(29999);
  assert.deepEqual(shown.calls, []);
  await shown.clock.tick(1);
  assert.equal(shown.calls[0], "/api/metrics/session");
});

test("the Refresh button refreshes everything, a filter change only the table", async () => {
  const t = setup();
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  t.calls.length = 0;
  await t.view.handlers.refresh();
  assert.equal(t.calls[0], "/api/metrics/session");
  t.calls.length = 0;
  await t.view.handlers.filtersChanged();
  assert.equal(t.calls[0], "/api/metrics/query?limit=50");
});

test("showPanel's control refreshes; while inactive it, and the filters control, do nothing", async () => {
  const t = setup();
  assert.equal(t.controls.fetchAllMetrics(), undefined);
  assert.deepEqual(t.calls, []);
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  t.calls.length = 0;
  await t.controls.fetchAllMetrics();
  assert.equal(t.calls[0], "/api/metrics/session");
  await t.ws.deactivate(METRICS_EXPLORER_ID);
  t.calls.length = 0;
  assert.equal(t.controls.fetchAllMetrics(), undefined);
  assert.equal(t.controls.fetchMetrics(), undefined);
  assert.deepEqual(t.calls, []);
});

test("deactivating unbinds the view, stops the interval, and re-activating does not stack either", async () => {
  const t = setup();
  await t.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  assert.equal(t.clock.pending(), 1);
  assert.deepEqual(await t.ws.deactivate(METRICS_EXPLORER_ID), []);
  assert.equal(t.view.unbound, 1);
  assert.equal(t.clock.pending(), 0);
  await t.ws.activate(METRICS_EXPLORER_ID);
  assert.equal(t.view.bound, 2);
  assert.equal(t.clock.pending(), 1);
});

test("a response that lands after deactivation is dropped, at each step", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const t = setup({responses: {"/api/metrics/session": async () => { await gate; return {ok: true, json: async () => SESSION}; }}});
  await t.ws.activate(METRICS_EXPLORER_ID);
  await t.ws.deactivate(METRICS_EXPLORER_ID);
  release();
  await settle();
  assert.deepEqual(t.dom.text, {});
  assert.deepEqual(t.calls, ["/api/metrics/session"]);   // and it did not go on to the next request

  let releaseLate;
  const lateGate = new Promise((resolve) => { releaseLate = resolve; });
  const late = setup({responses: {"/api/metrics/query?limit=50": async () => { await lateGate; return {ok: true, json: async () => ({metrics: []})}; }}});
  await late.ws.activate(METRICS_EXPLORER_ID);
  await settle();
  const before = {...late.dom.html};
  await late.ws.deactivate(METRICS_EXPLORER_ID);
  releaseLate();
  await settle();
  assert.deepEqual(late.dom.html, before);
  assert.equal(late.dom.html["metrics-table-body"], undefined);
});

test("a response that lands after deactivation never reaches the interactions or the live stats", async () => {
  for (const [url, expectText, expectHtml] of [
    ["/api/metrics/query?interaction", "live-heap-used", "interactions-list"],
    ["/api/metrics/query?limit=50", "live-heap-used", "metrics-table-body"],
  ]) {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const t = setup({last: {heap_used_mb: 5}, interactions: [{timestamp: 1, action: "a", target: ""}], responses: {
      [url]: async () => { await gate; return {ok: true, json: async () => ({metrics: []})}; },
    }});
    await t.ws.activate(METRICS_EXPLORER_ID);
    await settle();
    await t.ws.deactivate(METRICS_EXPLORER_ID);
    release();
    await settle();
    assert.equal(t.dom.text[expectText], undefined, url);   // live stats are written after the query
    assert.equal(t.dom.html[expectHtml], undefined, url);
    assert.equal(t.dom.text["total-interactions-count"], undefined, url);
  }
});
