import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createMetricsPlugin, METRICS_ID} from "./metricsPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));

function fakeClock() {
  let at = 0, next = 1;
  const timers = new Map();
  return {
    setTimeout: (fn, ms) => { const id = next++; timers.set(id, {at: at + ms, fn, every: 0}); return id; },
    setInterval: (fn, ms) => { const id = next++; timers.set(id, {at: at + ms, fn, every: ms}); return id; },
    clearTimeout: (id) => timers.delete(id),
    clearInterval: (id) => timers.delete(id),
    now: () => 1_000_000 + at,
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

function fakeWindow() {
  const handlers = new Map();
  return {
    handlers,
    addEventListener: (event, fn) => { handlers.set(event, [...(handlers.get(event) || []), fn]); },
    removeEventListener: (event, fn) => { handlers.set(event, (handlers.get(event) || []).filter((f) => f !== fn)); },
    fire: (event) => { for (const fn of handlers.get(event) || []) fn(); },
    count: (event) => (handlers.get(event) || []).length,
  };
}

function setup({gauges = () => [], http} = {}) {
  const clock = fakeClock();
  const win = fakeWindow();
  const requests = [], seenFlags = [];
  const ws = createWorkspace({log: quiet, timers: clock});
  ws.provide("httpFetch", http || (async (url, init) => { requests.push({url, init, body: init?.body ? JSON.parse(init.body) : null}); return {ok: true, json: async () => ({url})}; }));
  ws.provide("metricsProbe", {
    unloadTarget: win,
    environment: () => ({userAgent: "UA", screenWidth: 1200, screenHeight: 800}),
    gauges: (flags) => { seenFlags.push(flags); return gauges(); },
  });
  const {tool, controls} = createMetricsPlugin({log: quiet, now: clock.now, random: () => 0.123456789});
  ws.register(tool);
  const posted = (path) => requests.filter((r) => r.url === path);
  return {ws, clock, win, controls, requests, posted, seenFlags};
}

test("the session id is built from the clock and the random source", () => {
  const {controls} = setup();
  assert.match(controls.sessionId, /^session_1000000_[a-z0-9]+$/);
  assert.equal(controls.sessionId, `session_1000000_${(0.123456789).toString(36).slice(2, 11)}`);
});

test("activating logs session_start with the page environment and the session id", async () => {
  const {ws, posted, controls} = setup();
  await ws.activate(METRICS_ID);
  const [request] = posted("/api/metrics/interaction");
  assert.equal(request.init.method, "POST");
  assert.deepEqual(request.body.session_id, controls.sessionId);
  assert.equal(request.body.action, "session_start");
  assert.equal(request.body.target, "app");
  assert.equal(request.body.details.userAgent, "UA");
  assert.equal(request.body.details.screenWidth, 1200);
  assert.equal(request.body.details.timestamp, new Date(1_000_000).toISOString());
});

test("metrics are buffered, remembered for the explorer, and flushed when the batch is full", async () => {
  const {ws, controls, posted} = setup();
  await ws.activate(METRICS_ID);
  for (let i = 0; i < 49; i++) controls.log("m", `n${i}`, i);
  assert.equal(posted("/api/metrics/log").length, 0);
  assert.equal(controls._lastMetrics.n48, 48);
  controls.log("m", "n49", 49);
  await settle();
  const [request] = posted("/api/metrics/log");
  assert.equal(request.body.batch.length, 50);
  assert.deepEqual(request.body.batch[0], {module: "m", metric_name: "n0", value: 0, metadata: {}, timestamp: 1_000_000});
  assert.equal(request.body.session_id, controls.sessionId);
});

test("the collector runs every logInterval, logs the gauges and flushes", async () => {
  const {ws, clock, posted, seenFlags} = setup({gauges: () => [{module: "ais", name: "vessel_count", value: 7}]});
  await ws.activate(METRICS_ID);
  await clock.tick(29999);
  assert.equal(posted("/api/metrics/log").length, 0);
  await clock.tick(1);
  const [request] = posted("/api/metrics/log");
  assert.deepEqual(request.body.batch.map((m) => [m.module, m.metric_name, m.value]), [["ais", "vessel_count", 7]]);
  assert.deepEqual(seenFlags[0], {performance: true, hypergraph: true});
});

test("the gauge groups follow the config switches", async () => {
  const {ws, clock, controls, seenFlags} = setup();
  controls.config.logPerformance = false;
  await ws.activate(METRICS_ID);
  await clock.tick(30000);
  assert.deepEqual(seenFlags[0], {performance: false, hypergraph: true});
});

test("PINNED: a gauge that throws part way keeps the ones before it and skips the flush", async () => {
  function* gauges() { yield {module: "recon", name: "update_count", value: 1}; throw new Error("viewer gone"); }
  const {ws, clock, posted, controls} = setup({gauges});
  await ws.activate(METRICS_ID);
  await clock.tick(30000);
  assert.equal(posted("/api/metrics/log").length, 0);
  assert.equal(controls._lastMetrics.update_count, 1);
  await ws.deactivate(METRICS_ID);   // the kept gauge goes out with the final flush
  assert.deepEqual(posted("/api/metrics/log")[0].body.batch.map((m) => m.metric_name), ["update_count"]);
});

test("a flush that throws puts the batch back in front, in order", async () => {
  let fail = true;
  const sent = [];
  const http = async (url, init) => {
    if (url === "/api/metrics/log") { if (fail) throw new Error("offline"); sent.push(JSON.parse(init.body).batch.map((m) => m.metric_name)); }
    return {ok: true, json: async () => ({})};
  };
  const {ws, controls} = setup({http});
  await ws.activate(METRICS_ID);
  controls.log("m", "a", 1);
  await controls.flush();
  controls.log("m", "b", 2);
  fail = false;
  await controls.flush();
  assert.deepEqual(sent, [["a", "b"]]);
});

test("PINNED: a flush does not look at the HTTP status, so a 500 drops the batch", async () => {
  const sent = [];
  const http = async (url, init) => {
    if (url === "/api/metrics/log") { sent.push(JSON.parse(init.body).batch.length); return {ok: false, status: 500}; }
    return {ok: true, json: async () => ({})};
  };
  const {ws, controls} = setup({http});
  await ws.activate(METRICS_ID);
  controls.log("m", "a", 1);
  await controls.flush();
  await controls.flush();
  assert.deepEqual(sent, [1]);   // nothing left to send the second time
});

test("a failed interaction post is only a warning", async () => {
  const warned = [];
  const clock = fakeClock();
  const ws = createWorkspace({log: quiet, timers: clock});
  ws.provide("httpFetch", async () => { throw new Error("offline"); });
  ws.provide("metricsProbe", {unloadTarget: fakeWindow(), environment: () => ({}), gauges: () => []});
  const {tool} = createMetricsPlugin({log: {...quiet, warn: (...a) => warned.push(a[0])}, now: clock.now});
  ws.register(tool);
  await ws.activate(METRICS_ID);
  await settle();
  assert.equal(ws.state(METRICS_ID), "active");
  assert.deepEqual(warned, ["[MetricsLogger] Interaction log failed:"]);
});

test("the in-memory interaction log keeps the last 100, and the controls see the trimmed list", async () => {
  const {ws, controls} = setup();
  await ws.activate(METRICS_ID);
  for (let i = 0; i < 150; i++) controls.logInteraction("click", `t${i}`);
  assert.equal(controls._interactionLog.length, 100);
  assert.equal(controls._interactionLog.at(-1).target, "t149");
  assert.equal(controls._interactionLog[0].target, "t50");   // 151 logged counting session_start: the first 51 (it and t0-t49) are gone
});

test("switches: disabled logs nothing, and interactions can be turned off on their own", async () => {
  const {ws, controls, posted} = setup();
  await ws.activate(METRICS_ID);
  controls.config.logUserInteractions = false;
  controls.logInteraction("click", "x");
  assert.equal(posted("/api/metrics/interaction").length, 1);   // only session_start
  controls.config.enabled = false;
  controls.log("m", "n", 1);
  await controls.flush();
  assert.equal(posted("/api/metrics/log").length, 0);
  assert.equal(controls._lastMetrics.n, undefined);
});

test("PINNED: beforeunload flushes, then reports session_end with seconds since the logger was created", async () => {
  const {ws, clock, win, controls, posted} = setup();
  await ws.activate(METRICS_ID);
  await clock.tick(40_000);
  controls.log("m", "n", 1);
  win.fire("beforeunload");
  await settle();
  assert.equal(posted("/api/metrics/log").length, 1);
  const end = posted("/api/metrics/interaction").at(-1).body;
  assert.equal(end.action, "session_end");
  assert.equal(end.details.duration_seconds, 40);
});

test("deactivating stops the collector and the unload hook, then flushes what is left", async () => {
  const order = [];
  const {ws, clock, win, controls} = setup({http: async (url) => {
    order.push(`${url} timers=${clock.pending()} hooks=${win.count("beforeunload")}`);
    return {ok: true, json: async () => ({})};
  }});
  await ws.activate(METRICS_ID);
  assert.equal(clock.pending(), 1);
  assert.equal(win.count("beforeunload"), 1);
  controls.log("m", "n", 1);
  order.length = 0;
  const errors = await ws.deactivate(METRICS_ID);
  assert.deepEqual(errors, []);
  assert.deepEqual(order, ["/api/metrics/log timers=0 hooks=0"]);
  win.fire("beforeunload");
  await clock.tick(60_000);
  assert.deepEqual(order, ["/api/metrics/log timers=0 hooks=0"]);   // nothing else went out
});

test("while inactive the controls do nothing, and the data survives a reactivation", async () => {
  const {ws, controls, posted, clock} = setup();
  await ws.activate(METRICS_ID);
  controls.logInteraction("click", "kept");
  await ws.deactivate(METRICS_ID);
  const before = posted("/api/metrics/interaction").length;
  controls.logInteraction("click", "dropped");
  controls.log("m", "dropped", 1);
  assert.equal(await controls.flush(), undefined);
  assert.equal(await controls.collectAndLogMetrics(), undefined);
  assert.equal(await controls.getSessionSummary(), null);
  assert.equal(await controls.queryMetrics({module: "x"}), null);
  assert.equal(posted("/api/metrics/interaction").length, before);
  assert.equal(controls._lastMetrics.dropped, undefined);
  const id = controls.sessionId;
  await ws.activate(METRICS_ID);
  assert.equal(controls.sessionId, id);
  assert.deepEqual(controls._interactionLog.map((i) => i.target), ["app", "kept", "app"]);
  assert.equal(clock.pending(), 1);   // one collector, not two
});

test("session summary and queries go to the server and return its JSON", async () => {
  const {ws, controls, requests} = setup();
  await ws.activate(METRICS_ID);
  assert.deepEqual(await controls.getSessionSummary(), {url: "/api/metrics/session"});
  await controls.queryMetrics({module: "ais", metric_name: "vessel_count", limit: 5, start_time: 0});
  assert.equal(requests.at(-1).url, "/api/metrics/query?module=ais&metric_name=vessel_count&limit=5");   // a falsy 0 is skipped, as before
});

test("summary and query return null when the request fails", async () => {
  const {ws, controls} = setup({http: async (url) => { if (url.startsWith("/api/metrics/s") || url.startsWith("/api/metrics/q")) throw new Error("down"); return {ok: true, json: async () => ({})}; }});
  await ws.activate(METRICS_ID);
  assert.equal(await controls.getSessionSummary(), null);
  assert.equal(await controls.queryMetrics(), null);
});
