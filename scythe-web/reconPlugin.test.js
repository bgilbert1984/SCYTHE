import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createReconStore} from "./reconStore.js";
import {alertListHtml, describeEntity} from "./reconLogic.js";
import {createReconPlugin, RECON_ID} from "./reconPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };
const WALL = 1700000000000;

/** Timers the test drives by hand. */
function fakeTimers() {
  const timers = [];
  const add = (kind) => (fn, ms) => { const t = {kind, fn, ms, cleared: false}; timers.push(t); return t; };
  return {
    timers,
    setInterval: add("interval"), setTimeout: add("timeout"),
    clearInterval: (t) => { t.cleared = true; }, clearTimeout: (t) => { t.cleared = true; },
    live: (kind) => timers.filter((t) => t.kind === kind && !t.cleared),
  };
}

class FakeSource {
  constructor(url, sources) { this.url = url; this.closed = false; sources.push(this); }
  close() { this.closed = true; }
  send(raw) { this.onmessage({data: typeof raw === "string" ? raw : JSON.stringify(raw)}); }
}

/**
 * Everything the tool touches, as fakes. `replies` maps "METHOD url" (GET by default) to a function returning the JSON body or a Promise of it;
 * a body that is an Error makes `json()` throw, and a function that throws makes the request fail.
 */
function setup({replies = {}, drawn = true, token = null, answer = null, durations = [10], openEventSource} = {}) {
  const timers = fakeTimers();
  const requests = [], sources = [], messages = [], notes = [], globeCalls = [], viewCalls = [];
  const store = createReconStore();
  const handlers = {};
  let perfCalls = 0, perfClock = 1000;
  const view = {
    boundWith: null, unbound: 0, binds: 0,
    bind(h) { view.binds++; view.boundWith = h; return () => { view.unbound++; }; },
    setFiltersShown: (v) => viewCalls.push(["filters", v]),
    setEntityCount: (n) => viewCalls.push(["entityCount", n]),
    setAlertCount: (n) => viewCalls.push(["alertCount", n]),
    setAlertList: (html) => viewCalls.push(["alertList", html()]),
    refreshList: () => viewCalls.push(["list"]),
    askDisposition: (message, current) => { viewCalls.push(["ask", message, current]); return answer; },
  };
  const globe = {
    viewer: null, drawn,
    attach(v) { globe.viewer = v; globeCalls.push(["attach", v]); },
    available: () => Boolean(globe.viewer),
    render() { globeCalls.push(["render"]); return globe.drawn; },
    addMarker: (e) => globeCalls.push(["add", e.entity_id]),
    removeMarker: (id) => globeCalls.push(["remove", id]),
    updatePosition: (e) => globeCalls.push(["position", e.entity_id]),
    clear: () => globeCalls.push(["clear"]),
    detach: () => globeCalls.push(["detach"]),
    flyTo: (id) => globeCalls.push(["flyTo", id]),
    addTaskLine: (t) => globeCalls.push(["taskLine", t.task_id]),
    createEntityIcon: (...a) => { globeCalls.push(["icon", ...a]); return "icon-data"; },
    setVisible: (v) => globeCalls.push(["visible", v]),
    setShowRings: (v) => globeCalls.push(["rings", v]),
    setShowLabels: (v) => globeCalls.push(["labels", v]),
    setReferencePoint: (p) => globeCalls.push(["reference", p]),
    cameraPosition: () => ({lat: 36.123456, lon: -86.654321}),
  };
  const workspace = createWorkspace({log: quiet, timers});
  workspace.provide("httpFetch", async (url, init = {}) => {
    const key = `${init.method || "GET"} ${url}`;
    requests.push({key, init});
    const reply = replies[key];
    if (!reply) throw new Error(`no reply for ${key}`);
    const body = await reply(init);
    return {json: async () => { if (body instanceof Error) throw body; return body; }};
  });
  workspace.provide("openEventSource", openEventSource || ((url) => new FakeSource(url, sources)));
  workspace.provide("operatorSession", {token: () => token, clear() {}});
  workspace.provide("entityStream", {
    on: (event, handler) => (handlers[event] ||= []).push(handler),
    off: (event, handler) => { handlers[event] = (handlers[event] || []).filter((h) => h !== handler); },
  });
  workspace.provide("reconGlobe", globe);
  workspace.provide("reconView", view);
  workspace.provide("reconConsole", {message: (text, kind) => messages.push([text, kind]), notify: (title, text, kind) => notes.push([title, text, kind])});
  const warnings = [];
  const log = {...quiet, warn: (...a) => warnings.push(a.join(" "))};
  const clock = {
    wall: () => WALL,
    perf: () => { perfCalls++; if (perfCalls % 2 === 0) perfClock += durations[(perfCalls / 2 - 1) % durations.length]; return perfClock; },
  };
  const recon = createReconPlugin({log, store, clock});
  workspace.register(recon.tool);
  const emit = (event, data) => (handlers[event] || []).forEach((h) => h(data));
  return {workspace, timers, requests, sources, messages, notes, globeCalls, viewCalls, store, view, globe, handlers, emit, warnings, controls: recon.controls};
}

const ok = (extra) => ({status: "ok", ...extra});
const E1 = {entity_id: "a", name: "Bogey", disposition: "HOSTILE", threat_level: "HIGH", location: {lat: 1, lon: 2}};
const E2 = {entity_id: "b", name: "Friendly", disposition: "FRIEND", threat_level: "NONE", location: {lat: 3, lon: 4}};
const entitiesReply = (entities = [E1, E2], extra = {}) => ({"GET /api/recon/entities": () => ok({entities, entity_count: entities.length, ...extra})});
const alertsReply = (alerts = [], extra = {}) => ({"GET /api/recon/alerts": () => ok({alerts, alert_count: alerts.length, ...extra})});

async function started(options = {}) {
  const s = setup({...options, replies: {...entitiesReply(), ...alertsReply(), ...options.replies}});
  await s.workspace.activate(RECON_ID);
  s.controls.initialize({id: "viewer"});
  return s;
}
const sync = (type, id, data, entityType = "RECON_ENTITY") => ({event_type: type, entity_id: id, entity_type: entityType, entity_data: data});

// --- controls while inactive ---

test("the controls do nothing while the tool is inactive, and the store's map and the description are still there", () => {
  const s = setup();
  const c = s.controls;
  assert.equal(c.entities, s.store.entities);
  assert.equal(c.visible, false);
  assert.deepEqual(c.alerts, []);
  assert.deepEqual(c.perfMetrics, {updateCount: 0, lastUpdateMs: 0, avgUpdateMs: 0, entityUpdates: 0});
  assert.equal(c.createEntityDescription(E1), describeEntity(E1));
  c.show(); c.hide(); c.renderEntities(); c.addEntityMarker(E1); c.removeEntityMarker("a"); c.flyToEntity("a");
  assert.equal(c.createEntityIcon("red"), undefined);
  assert.deepEqual(s.requests, []);
  assert.deepEqual(s.globeCalls, []);
});

test("the async controls resolve to nothing while inactive", async () => {
  const s = setup();
  assert.equal(await s.controls.loadEntities(), undefined);
  assert.equal(await s.controls.loadAlerts(), undefined);
  assert.equal(await s.controls.createTaskForEntity("a"), undefined);
  assert.equal(await s.controls.updateDisposition("a"), undefined);
  assert.deepEqual(s.requests, []);
});

test("initialize returns the controls", () => {
  const s = setup();
  assert.equal(s.controls.initialize({id: "v"}), s.controls);
});

test("a viewer handed over before activation is attached when the tool activates", async () => {
  const s = setup({replies: {...entitiesReply(), ...alertsReply()}});
  s.controls.initialize({id: "early"});
  assert.deepEqual(s.globeCalls, []);
  await s.workspace.activate(RECON_ID);
  assert.deepEqual(s.globeCalls.filter((c) => c[0] === "attach"), [["attach", {id: "early"}]]);
  assert.equal(s.view.binds, 1);
});

test("initialize attaches the viewer and wires the panel once, however often it is called", async () => {
  const s = await started();
  s.controls.initialize({id: "again"});
  assert.deepEqual(s.globeCalls.filter((c) => c[0] === "attach").map((c) => c[1].id), ["viewer", "again"]);
  assert.equal(s.view.binds, 1);
});

// --- the panel's controls ---

test("the Recon checkbox shows the layer (loading what the server has) and hides it again", async () => {
  const s = await started();
  s.view.boundWith.showEntities(true);
  assert.equal(s.controls.visible, true);
  await settle();
  assert.deepEqual(s.globeCalls.slice(1, 3), [["visible", true], ["render"]]);
  assert.deepEqual(s.viewCalls.slice(0, 1), [["filters", true]]);
  assert.deepEqual(Object.keys(s.store.entities), ["a", "b"]);
  s.globeCalls.length = 0; s.viewCalls.length = 0; s.messages.length = 0;
  s.view.boundWith.showEntities(false);
  assert.equal(s.controls.visible, false);
  assert.deepEqual(s.globeCalls, [["visible", false], ["clear"]]);
  assert.deepEqual(s.viewCalls, [["filters", false]]);
  assert.deepEqual(s.messages, [["Recon auto-update stopped", "command"]], "even though nothing was running");
});

test("the rings and labels checkboxes go to the drawing, and the reference and alert buttons make their requests", async () => {
  const s = await started();
  s.globeCalls.length = 0;
  s.view.boundWith.showRings(false);
  s.view.boundWith.showLabels(true);
  assert.deepEqual(s.globeCalls, [["rings", false], ["labels", true]]);
  s.view.boundWith.checkAlerts();
  s.view.boundWith.setReference();
  await settle();
  assert.deepEqual(s.requests.map((r) => r.key).filter((k) => /alerts/.test(k)), ["GET /api/recon/alerts"]);
});

test("the auto-update checkbox starts and stops the auto-update", async () => {
  const s = await started();
  s.view.boundWith.autoUpdate(true);
  assert.equal(s.timers.live("interval").length, 1);
  s.view.boundWith.autoUpdate(false);
  assert.equal(s.timers.live("interval").length, 0);
});

// --- loading ---

test("loading fills the store in place, draws, refreshes the list and the counts, loads the alerts and says so", async () => {
  const s = await started();
  const held = s.store.entities;
  s.store.upsert({entity_id: "stale"});
  await s.controls.loadEntities();
  await settle();
  assert.equal(s.store.entities, held);
  assert.deepEqual(Object.keys(held), ["a", "b"]);
  assert.deepEqual(s.requests.map((r) => r.key), ["GET /api/recon/entities", "GET /api/recon/alerts"]);
  assert.deepEqual(s.viewCalls.filter((c) => c[0] === "entityCount"), [["entityCount", 2]]);
  assert.deepEqual(s.globeCalls.filter((c) => c[0] === "render"), [["render"]]);
  assert.equal(s.viewCalls.filter((c) => c[0] === "list").length, 1);
  assert.deepEqual(s.messages.slice(0, 2), [["Loading Auto-Recon entities...", "command"], ["Loaded 2 reconnaissance entities", "response"]]);
  assert.deepEqual(s.notes, [["Recon Data Loaded", "Tracking 2 entities", "info"]]);
});

test("loading lifts entities nested under data and skips those without a numeric location", async () => {
  const s = await started({replies: entitiesReply([E1, {id: "n", data: {name: "Nested", location: {lat: 5, lon: 6}}}, {entity_id: "bad"}, {entity_id: "text", location: {lat: "1", lon: 2}}], {entity_count: 4})});
  await s.controls.loadEntities();
  assert.deepEqual(Object.keys(s.store.entities), ["a", "n"]);
  assert.equal(s.store.entities.n.entity_id, "n");
  assert.ok(s.warnings.some((w) => /Skipping entity bad/.test(w)));
  assert.ok(s.warnings.some((w) => /Skipping entity text/.test(w)));
});

test("when the drawing did not draw (no viewer, or hidden) the list is not refreshed", async () => {
  const s = await started({drawn: false});
  await s.controls.loadEntities();
  assert.equal(s.viewCalls.filter((c) => c[0] === "list").length, 0);
});

test("a reply that is not ok changes nothing, and a failure says so", async () => {
  const s = await started({replies: {"GET /api/recon/entities": () => ({status: "error"})}});
  s.store.upsert({entity_id: "keep"});
  await s.controls.loadEntities();
  assert.deepEqual(Object.keys(s.store.entities), ["keep"]);
  assert.deepEqual(s.messages, [["Loading Auto-Recon entities...", "command"]]);
  const failing = await started({replies: {"GET /api/recon/entities": () => { throw new Error("offline"); }}});
  await failing.controls.loadEntities();
  assert.deepEqual(failing.messages.at(-1), ["Error loading recon entities: offline", "alert"]);
});

test("an answer that arrives after the tool deactivated changes nothing", async () => {
  const g = gate();
  const s = await started({replies: {"GET /api/recon/entities": () => g.promise}});
  const pending = s.controls.loadEntities();
  await s.workspace.deactivate(RECON_ID);
  g.open(ok({entities: [E1], entity_count: 1}));
  await pending;
  assert.deepEqual(Object.keys(s.store.entities), []);
  assert.deepEqual(s.notes, []);
});

test("loading the alerts shows the count and the list, and warns when there are some", async () => {
  const alerts = [{entity_id: "a", name: "Bogey", threat_level: "HIGH", disposition: "HOSTILE", distance_nm: 2.5, bearing_deg: 90}];
  const s = await started({replies: alertsReply(alerts)});
  await s.controls.loadAlerts();
  assert.deepEqual(s.viewCalls, [["alertCount", 1], ["alertList", alertListHtml(alerts)]]);
  assert.deepEqual(s.messages, [["⚠️ 1 proximity alerts active!", "alert"]]);
  assert.deepEqual(s.controls.alerts, alerts);
});

test("no alerts is quiet, and a failing or refused request leaves the alerts as they were", async () => {
  const quietOne = await started({replies: alertsReply([])});
  await quietOne.controls.loadAlerts();
  assert.deepEqual(quietOne.messages, []);
  assert.deepEqual(quietOne.viewCalls, [["alertCount", 0], ["alertList", alertListHtml([])]]);
  const refused = await started({replies: {"GET /api/recon/alerts": () => ({status: "error"})}});
  await refused.controls.loadAlerts();
  assert.deepEqual(refused.viewCalls, []);
  const failing = await started({replies: {"GET /api/recon/alerts": () => { throw new Error("x"); }}});
  await failing.controls.loadAlerts();
  assert.deepEqual([failing.viewCalls, failing.messages], [[], []]);
});

// --- EntitySync events ---

test("a CREATE event stores the entity, redraws its marker and refreshes the list and the count", async () => {
  const s = await started();
  s.controls.show(); await settle(); s.globeCalls.length = 0; s.viewCalls.length = 0;
  s.emit("create", sync("CREATE", "c", {entity_id: "c", name: "N", location: {lat: 1, lon: 2}}));
  assert.equal(s.store.entities.c.name, "N");
  assert.deepEqual(s.globeCalls, [["remove", "c"], ["add", "c"]]);
  assert.deepEqual(s.viewCalls, [["list"], ["entityCount", 3]]);
});

test("an event while the layer is hidden updates the store and the panel but draws nothing", async () => {
  const s = await started();
  s.globeCalls.length = 0;
  s.emit("create", sync("CREATE", "c", {entity_id: "c"}));
  s.emit("delete", sync("DELETE", "c", {}));
  assert.deepEqual(Object.keys(s.store.entities), []);
  assert.deepEqual(s.globeCalls, []);
  assert.deepEqual(s.viewCalls.filter((c) => c[0] === "list").length, 2);
});

test("an UPDATE merges into the held entity, and the entity is filed under the event's id", async () => {
  const s = await started();
  s.controls.show(); await settle();
  s.store.upsert({entity_id: "x", name: "old", mine: 1});
  s.globeCalls.length = 0;
  s.emit("update", sync("UPDATE", "x", {entity_id: "x", name: "new"}));
  assert.equal(s.store.entities.x.name, "new");
  assert.equal(s.store.entities.x.mine, 1);
  assert.deepEqual(s.globeCalls, [["remove", "x"], ["add", "x"]]);
  s.globeCalls.length = 0;
  s.emit("create", sync("CREATE", "from-event", {entity_id: "from-data"}));
  assert.ok("from-event" in s.store.entities);
  assert.deepEqual(s.globeCalls, [["remove", "from-event"], ["add", "from-data"]]);
});

test("a DELETE removes the entity and its marker; one for an entity not held still refreshes the panel", async () => {
  const s = await started();
  s.controls.show(); await settle();
  s.globeCalls.length = 0; s.viewCalls.length = 0;
  s.emit("delete", sync("DELETE", "a", {}));
  assert.deepEqual(Object.keys(s.store.entities), ["b"]);
  assert.deepEqual(s.globeCalls, [["remove", "a"]]);
  s.globeCalls.length = 0; s.viewCalls.length = 0;
  s.emit("delete", sync("DELETE", "gone", {}));
  assert.deepEqual(s.globeCalls, []);
  assert.deepEqual(s.viewCalls, [["list"], ["entityCount", 1]]);
});

test("an event for another kind of entity, or of a kind it does not handle, changes nothing and refreshes nothing", async () => {
  const s = await started();
  s.globeCalls.length = 0; s.viewCalls.length = 0;
  s.emit("create", sync("CREATE", "a", {entity_id: "a"}, "SENSOR"));
  s.emit("create", sync("MOVE", "a", {entity_id: "a"}));
  s.emit("create", null);
  assert.deepEqual([s.globeCalls, s.viewCalls, Object.keys(s.store.entities)], [[], [], []]);
});

test("the tool subscribes to the four EntitySync events once per activation and unsubscribes when it deactivates", async () => {
  const s = await started();
  assert.deepEqual(Object.fromEntries(Object.entries(s.handlers).map(([k, v]) => [k, v.length])), {preexisting: 1, create: 1, update: 1, delete: 1});
  await s.workspace.deactivate(RECON_ID);
  assert.deepEqual(Object.fromEntries(Object.entries(s.handlers).map(([k, v]) => [k, v.length])), {preexisting: 0, create: 0, update: 0, delete: 0});
  await s.workspace.activate(RECON_ID);
  assert.deepEqual(Object.fromEntries(Object.entries(s.handlers).map(([k, v]) => [k, v.length])), {preexisting: 1, create: 1, update: 1, delete: 1}, "not doubled");
});

// --- the entity stream ---

test("the auto-update opens the entity stream once; its messages become sync events", async () => {
  const s = await started();
  s.controls.show(); await settle();
  s.view.boundWith.autoUpdate(true);
  s.view.boundWith.autoUpdate(true);
  assert.equal(s.sources.length, 1);
  assert.equal(s.sources[0].url, "/api/recon/entities/stream");
  s.sources[0].send({entity: {entity_id: "n", name: "one", keep: 1}});
  assert.equal(s.store.entities.n.name, "one");
  s.sources[0].send({entity: {entity_id: "n", name: "two"}});
  assert.equal(s.store.entities.n.name, "two");
  assert.equal(s.store.entities.n.keep, 1, "a message for a held entity is an update, which merges");
  s.sources[0].send({type: "entity_deleted", entity: {entity_id: "n"}});
  assert.equal("n" in s.store.entities, false);
});

test("the stream's greeting, a message without an id and text that is not JSON are ignored", async () => {
  const s = await started();
  s.view.boundWith.autoUpdate(true);
  const before = JSON.stringify(s.store.entities);
  s.globeCalls.length = 0; s.viewCalls.length = 0;
  s.sources[0].send({type: "connected"});
  s.sources[0].send({entity: {name: "no id"}});
  s.sources[0].send("not json");
  assert.equal(JSON.stringify(s.store.entities), before);
  assert.deepEqual([s.globeCalls, s.viewCalls], [[], []]);
});

test("a dropped stream is closed and, while the layer shows, retried in five seconds with a reload", async () => {
  const s = await started();
  s.controls.show(); await settle();
  s.view.boundWith.autoUpdate(true);
  s.sources[0].onerror();
  assert.equal(s.sources[0].closed, true);
  const retry = s.timers.live("timeout");
  assert.equal(retry.length, 1);
  assert.equal(retry[0].ms, 5000);
  s.requests.length = 0;
  retry[0].fn();
  assert.deepEqual(s.requests.map((r) => r.key).slice(0, 1), ["GET /api/recon/entities"]);
  assert.equal(s.sources.length, 2, "bound again");
});

test("a dropped stream is retried even while the layer is hidden, but then without the reload", async () => {
  const s = await started();
  s.view.boundWith.autoUpdate(true);
  s.sources[0].onerror();
  assert.equal(s.timers.live("timeout").length, 1);
  s.requests.length = 0;
  s.timers.live("timeout")[0].fn();
  assert.deepEqual(s.requests, [], "layer hidden: no reload");
  assert.equal(s.sources.length, 2);
});

test("stopping the auto-update cancels a pending retry, so the stream is not rebound after it", async () => {
  const s = await started();
  s.view.boundWith.autoUpdate(true);
  s.sources[0].onerror();
  s.view.boundWith.autoUpdate(false);
  assert.equal(s.timers.live("timeout").length, 0);
  assert.equal(s.sources.length, 1);
});

test("a retry that fires after the update was stopped and started again does not open a second stream", async () => {
  const s = await started();
  s.view.boundWith.autoUpdate(true);
  s.sources[0].onerror();
  const retry = s.timers.live("timeout")[0];
  s.view.boundWith.autoUpdate(false);
  s.view.boundWith.autoUpdate(true);
  assert.equal(s.sources.length, 2);
  retry.fn();   // a timer that was not cancelled (a late firing)
  assert.equal(s.sources.length, 2);
});

test("deactivating closes the stream, and a message or an error after that does nothing", async () => {
  const s = await started();
  s.view.boundWith.autoUpdate(true);
  const source = s.sources[0];
  await s.workspace.deactivate(RECON_ID);
  assert.equal(source.closed, true);
  source.send({entity: {entity_id: "late", name: "n"}});
  source.onerror();
  assert.deepEqual(Object.keys(s.store.entities), []);
  assert.equal(s.timers.live("timeout").length, 0);
});

test("a stream that cannot be opened is reported and the update goes on without it", async () => {
  const t = setup({replies: {...entitiesReply(), ...alertsReply()}, openEventSource: () => { throw new Error("blocked"); }});
  await t.workspace.activate(RECON_ID);
  t.controls.initialize({id: "v"});
  t.view.boundWith.autoUpdate(true);
  assert.equal(t.timers.live("interval").length, 1);
  assert.ok(t.warnings.some((w) => /SSE bind failed/.test(w)));
});

// --- the auto-update ---

test("starting the auto-update notes the time, ticks every two seconds, says so, and once only", async () => {
  const s = await started();
  s.view.boundWith.autoUpdate(true);
  s.view.boundWith.autoUpdate(true);
  const intervals = s.timers.live("interval");
  assert.equal(intervals.length, 1);
  assert.equal(intervals[0].ms, 2000);
  assert.deepEqual(s.messages.filter(([text]) => /started/.test(text)), [["Recon auto-update started (incremental mode)", "command"]]);
});

const changes = (extra) => ({"GET /api/recon/simulate": () => ({}), "GET /api/recon/changes?since=1700000000": () => ok({timestamp: 2000, entity_count: 0, entities: [], ...extra})});

test("a tick simulates movement, asks for the changes since the last one, stores and moves what changed and refreshes the alerts", async () => {
  const s = await started({replies: changes({entity_count: 2, entities: [{entity_id: "p"}, {entity_id: "q"}]})});
  s.controls.show(); await settle();
  s.view.boundWith.autoUpdate(true);
  s.requests.length = 0; s.globeCalls.length = 0;
  await s.timers.live("interval")[0].fn();
  await settle();
  assert.deepEqual(s.requests.map((r) => r.key), ["GET /api/recon/simulate", "GET /api/recon/changes?since=1700000000", "GET /api/recon/alerts"]);
  assert.deepEqual(Object.keys(s.store.entities), ["a", "b", "p", "q"]);
  assert.deepEqual(s.globeCalls, [["position", "p"], ["position", "q"]]);
  assert.equal(s.controls.perfMetrics.entityUpdates, 2);
  assert.equal(s.controls.perfMetrics.updateCount, 1);
});

test("the next tick asks since the time the server gave", async () => {
  const s = await started({replies: {...changes(), "GET /api/recon/changes?since=2000": () => ok({timestamp: 3000, entity_count: 0, entities: []})}});
  s.view.boundWith.autoUpdate(true);
  s.controls.show(); await settle();
  await s.timers.live("interval")[0].fn();
  await s.timers.live("interval")[0].fn();
  assert.ok(s.requests.some((r) => r.key === "GET /api/recon/changes?since=2000"));
});

test("the tick's timing is a running average over the ticks", async () => {
  const s = await started({replies: changes({timestamp: 1700000000}), durations: [10, 20, 30]});
  s.view.boundWith.autoUpdate(true);
  s.controls.show(); await settle();
  const averages = [];
  for (let i = 0; i < 3; i++) {
    await s.timers.live("interval")[0].fn();
    averages.push(s.controls.perfMetrics.avgUpdateMs);
  }
  assert.equal(s.controls.perfMetrics.updateCount, 3);
  assert.equal(s.controls.perfMetrics.lastUpdateMs, 30);
  assert.deepEqual(averages, [10, 15, 20]);
});

test("a tick with no changes still counts, a hidden layer does nothing, and a reply that is not ok changes nothing", async () => {
  const quietOne = await started({replies: changes()});
  quietOne.view.boundWith.autoUpdate(true);
  quietOne.controls.show(); await settle();
  await quietOne.timers.live("interval")[0].fn();
  assert.equal(quietOne.controls.perfMetrics.updateCount, 1);
  const hidden = await started({replies: changes()});
  hidden.view.boundWith.autoUpdate(true);
  hidden.requests.length = 0;
  await hidden.timers.live("interval")[0].fn();
  assert.deepEqual(hidden.requests, []);
  const bad = await started({replies: {"GET /api/recon/simulate": () => ({}), "GET /api/recon/changes?since=1700000000": () => ({status: "error"})}});
  bad.view.boundWith.autoUpdate(true);
  bad.controls.show(); await settle();
  await bad.timers.live("interval")[0].fn();
  assert.equal(bad.controls.perfMetrics.updateCount, 0);
});

test("a tick that reports no changes need not list any", async () => {
  const s = await started({replies: {"GET /api/recon/simulate": () => ({}), "GET /api/recon/changes?since=1700000000": () => ok({timestamp: 5, entity_count: 0})}});
  s.view.boundWith.autoUpdate(true);
  s.controls.show(); await settle();
  await s.timers.live("interval")[0].fn();
  assert.equal(s.controls.perfMetrics.updateCount, 1);
});

test("the auto-update can be stopped and started again, and stopping closes the stream so starting opens a new one", async () => {
  const s = await started();
  s.view.boundWith.autoUpdate(true);
  s.view.boundWith.autoUpdate(false);
  assert.equal(s.sources[0].closed, true);
  assert.equal(s.timers.live("interval").length, 0);
  s.view.boundWith.autoUpdate(true);
  assert.equal(s.timers.live("interval").length, 1);
  assert.equal(s.sources.length, 2);
  assert.equal(s.sources[1].closed, false);
  s.view.boundWith.autoUpdate(false);
  s.view.boundWith.autoUpdate(true);
  assert.equal(s.sources.length, 3, "stopping forgot the second stream too");
});

test("a tick that fails is logged and the next one still runs", async () => {
  const s = await started({replies: {"GET /api/recon/simulate": () => { throw new Error("down"); }}});
  s.view.boundWith.autoUpdate(true);
  s.controls.show(); await settle();
  await s.timers.live("interval")[0].fn();
  assert.equal(s.controls.perfMetrics.updateCount, 0);
  assert.equal(s.timers.live("interval").length, 1);
});

test("stopping clears the interval and the stream and says so; a tick answering after deactivation changes nothing", async () => {
  const g = gate();
  const s = await started({replies: {"GET /api/recon/simulate": () => ({}), "GET /api/recon/changes?since=1700000000": () => g.promise}});
  s.view.boundWith.autoUpdate(true);
  s.controls.show(); await settle();
  const pending = s.timers.live("interval")[0].fn();
  await settle();
  await s.workspace.deactivate(RECON_ID);
  g.open(ok({timestamp: 9, entity_count: 1, entities: [{entity_id: "late"}]}));
  await pending;
  assert.equal("late" in s.store.entities, false);
  assert.equal(s.controls.perfMetrics.updateCount, 0);
  assert.equal(s.timers.live("interval").length, 0);
});

// --- the reference point, tasks and dispositions ---

test("setting the reference point from the camera posts it, tells the drawing, and reloads", async () => {
  const s = await started({replies: {"POST /api/recon/reference": () => ok({reference_point: {lat: 36.1, lon: -86.6}})}});
  await s.controls.loadEntities();
  s.requests.length = 0;
  s.view.boundWith.setReference();
  await settle();
  assert.equal(s.requests[0].init.body, '{"lat":36.123456,"lon":-86.654321}');
  assert.deepEqual(s.requests[0].init.headers, {"Content-Type": "application/json"});
  assert.deepEqual(s.globeCalls.filter((c) => c[0] === "reference"), [["reference", {lat: 36.1, lon: -86.6}]]);
  assert.ok(s.messages.some(([text, kind]) => text === "Reference point set to: 36.1235, -86.6543" && kind === "response"));
  assert.equal(s.requests[1].key, "GET /api/recon/entities");
});

test("with no viewer the reference point button does nothing; a refusal or a failure is handled", async () => {
  const s = setup({replies: {...entitiesReply(), ...alertsReply()}});
  await s.workspace.activate(RECON_ID);
  s.controls.initialize(null);   // attaches nothing usable
  s.globe.viewer = null;
  s.view.boundWith.setReference();
  await settle();
  assert.deepEqual(s.requests, []);
  const refused = await started({replies: {"POST /api/recon/reference": () => ({status: "error"})}});
  refused.view.boundWith.setReference();
  await settle();
  assert.deepEqual(refused.messages, []);
  const failing = await started({replies: {"POST /api/recon/reference": () => { throw new Error("no"); }}});
  failing.view.boundWith.setReference();
  await settle();
  assert.deepEqual(failing.messages, [["Error setting reference point: no", "alert"]]);
});

const TASK = {task_id: "T-1", entity_name: "Bogey"};

test("creating a task posts it, says so, and draws its line", async () => {
  const s = await started({replies: {"POST /api/recon/task": () => ok({task: TASK})}});
  await s.controls.createTaskForEntity("a");
  assert.deepEqual(JSON.parse(s.requests[0].init.body), {entity_id: "a", task_type: "INVESTIGATE", priority: 5});
  assert.deepEqual(s.messages, [["Task T-1 created for Bogey", "response"]]);
  assert.deepEqual(s.notes, [["Task Created", "Investigation task assigned: T-1", "info"]]);
  assert.deepEqual(s.globeCalls.filter((c) => c[0] === "taskLine"), [["taskLine", "T-1"]]);
});

test("a refused task and a failing request are reported", async () => {
  const refused = await started({replies: {"POST /api/recon/task": () => ({status: "error", message: "busy"})}});
  await refused.controls.createTaskForEntity("a");
  assert.deepEqual(refused.messages, [["Failed to create task: busy", "alert"]]);
  const failing = await started({replies: {"POST /api/recon/task": () => { throw new Error("no"); }}});
  await failing.controls.createTaskForEntity("a");
  assert.deepEqual(failing.messages, [["Error creating task: no", "alert"]]);
});

const heldA = {entity_id: "a", name: "Bogey", disposition: "UNKNOWN"};
async function dispositionSetup(answer, reply, token = null) {
  const s = await started({answer, token, replies: {"POST /api/recon/entity/a/disposition": (init) => { s.sent = init; return reply; }}});
  s.store.upsert({...heldA});
  s.globeCalls.length = 0;
  return s;
}

test("a new disposition is sent with the operator's token, the entity the server returns replaces the held one, and the layer is redrawn", async () => {
  const s = await dispositionSetup("hostile", ok({entity: {entity_id: "a", name: "Bogey", disposition: "HOSTILE"}}), "tok");
  await s.controls.updateDisposition("a");
  assert.deepEqual(JSON.parse(s.sent.body), {disposition: "HOSTILE"});
  assert.deepEqual(s.sent.headers, {"Content-Type": "application/json", "X-Session-Token": "tok"});
  assert.equal(s.store.entities.a.disposition, "HOSTILE");
  assert.deepEqual(s.globeCalls, [["render"]]);
  assert.ok(s.messages.some((m) => m[0] === "Bogey disposition changed to HOSTILE" && m[1] === "response"));
  const [ask] = s.viewCalls.filter((c) => c[0] === "ask");
  assert.equal(ask[1], "Update disposition for Bogey\nCurrent: UNKNOWN\n\nOptions: UNKNOWN, PENDING, ASSUMED_FRIEND, FRIEND, NEUTRAL, SUSPICIOUS, HOSTILE");
  assert.equal(ask[2], "UNKNOWN");
});

test("without a token no token header is sent", async () => {
  const s = await dispositionSetup("HOSTILE", ok({entity: {entity_id: "a"}}));
  await s.controls.updateDisposition("a");
  assert.deepEqual(s.sent.headers, {"Content-Type": "application/json"});
});

test("the entity from the server is filed under the id that was asked for", async () => {
  const s = await dispositionSetup("HOSTILE", ok({entity: {entity_id: "other", disposition: "HOSTILE"}}));
  await s.controls.updateDisposition("a");
  assert.ok("a" in s.store.entities);
  assert.equal("other" in s.store.entities, false);
});

test("no change is made when there is no such entity, or the prompt is cancelled or unchanged, or the server says no", async () => {
  for (const answer of [null, "", "unknown"]) {
    const s = await dispositionSetup(answer, ok({entity: {}}));
    await s.controls.updateDisposition("a");
    assert.equal(s.sent, undefined, String(answer));
    assert.equal(s.store.entities.a.disposition, "UNKNOWN");
  }
  const none = await dispositionSetup("HOSTILE", ok({entity: {}}));
  await none.controls.updateDisposition("nonesuch");
  assert.equal(none.sent, undefined);
  assert.deepEqual(none.viewCalls.filter((c) => c[0] === "ask"), []);
  const refused = await dispositionSetup("HOSTILE", {status: "error", message: "no"});
  await refused.controls.updateDisposition("a");
  assert.equal(refused.store.entities.a.disposition, "UNKNOWN");
  assert.deepEqual(refused.messages.at(-1), ["Failed to update disposition: no", "alert"]);
});

test("a failing disposition request is reported", async () => {
  const s = await started({answer: "HOSTILE", replies: {"POST /api/recon/entity/a/disposition": () => { throw new Error("down"); }}});
  s.store.upsert({...heldA});
  await s.controls.updateDisposition("a");
  assert.deepEqual(s.messages.at(-1), ["Error updating disposition: down", "alert"]);
});

// --- the controls that pass through to the drawing ---

test("the marker, icon and flight controls go to the drawing while the tool is active", async () => {
  const s = await started();
  s.globeCalls.length = 0;
  s.controls.addEntityMarker(E1);
  s.controls.removeEntityMarker("a");
  s.controls.flyToEntity("a");
  assert.equal(s.controls.createEntityIcon("red", "HOSTILE", true), "icon-data");
  s.controls.renderEntities();
  assert.deepEqual(s.globeCalls, [["add", "a"], ["remove", "a"], ["flyTo", "a"], ["icon", "red", "HOSTILE", true], ["render"]]);
});

test("renderEntities draws and refreshes the list when it drew", async () => {
  const s = await started();
  s.viewCalls.length = 0;
  s.controls.renderEntities();
  assert.deepEqual(s.viewCalls, [["list"]]);
});

// --- deactivating ---

test("deactivating takes the drawing down, unwires the panel, stops the timers and the stream, and goes quiet", async () => {
  const s = await started();
  s.controls.show(); await settle();
  s.view.boundWith.autoUpdate(true);
  s.globeCalls.length = 0;
  const errors = await s.workspace.deactivate(RECON_ID);
  assert.deepEqual(errors, []);
  assert.deepEqual(s.globeCalls, [["detach"]]);
  assert.equal(s.view.unbound, 1);
  assert.equal(s.timers.live("interval").length, 0);
  assert.equal(s.sources[0].closed, true);
  s.globeCalls.length = 0; s.requests.length = 0;
  s.controls.show(); s.controls.addEntityMarker(E1);
  assert.deepEqual([s.globeCalls, s.requests], [[], []]);
  assert.equal(s.controls.visible, false);
});

test("the store survives the tool: what was in it is still there after deactivating", async () => {
  const s = await started();
  await s.controls.loadEntities();
  await s.workspace.deactivate(RECON_ID);
  assert.deepEqual(Object.keys(s.controls.entities), ["a", "b"]);
});

test("a second activation starts clean: not visible, no alerts, fresh metrics", async () => {
  const s = await started();
  s.controls.show(); await settle();
  await s.controls.loadAlerts();
  await s.workspace.deactivate(RECON_ID);
  await s.workspace.activate(RECON_ID);
  assert.equal(s.controls.visible, false);
  assert.deepEqual(s.controls.alerts, []);
  assert.equal(s.controls.perfMetrics.updateCount, 0);
});

// --- answers that arrive after the tool deactivated ---

test("a failure that arrives after deactivation says nothing", async () => {
  const g = gate();
  const s = await started({replies: {"GET /api/recon/entities": () => g.promise}});
  const pending = s.controls.loadEntities();
  s.messages.length = 0;
  await s.workspace.deactivate(RECON_ID);
  g.open(new Error("late"));
  const gone = setup({replies: {"GET /api/recon/entities": () => { throw new Error("late"); }}});
  await gone.workspace.activate(RECON_ID);
  const second = gone.controls.loadEntities();
  await gone.workspace.deactivate(RECON_ID);
  await second;
  await pending;
  assert.deepEqual(gone.messages.filter(([text]) => /Error loading/.test(text)), []);
});

test("alerts, a reference point and a task that answer after deactivation change nothing", async () => {
  const ga = gate(), gr = gate(), gt = gate();
  const s = await started({replies: {"GET /api/recon/alerts": () => ga.promise, "POST /api/recon/reference": () => gr.promise, "POST /api/recon/task": () => gt.promise}});
  const alerts = s.controls.loadAlerts();
  s.view.boundWith.setReference();
  const task = s.controls.createTaskForEntity("a");
  await settle();
  s.viewCalls.length = 0; s.messages.length = 0; s.globeCalls.length = 0; s.notes.length = 0;
  await s.workspace.deactivate(RECON_ID);
  s.globeCalls.length = 0;
  ga.open(ok({alerts: [], alert_count: 3}));
  gr.open(ok({reference_point: {lat: 1, lon: 2}}));
  gt.open(ok({task: TASK}));
  await Promise.all([alerts, task]);
  await settle();
  assert.deepEqual([s.viewCalls, s.messages, s.globeCalls, s.notes], [[], [], [], []]);
});

test("a disposition that answers after deactivation changes nothing", async () => {
  const g = gate();
  const s = await started({answer: "HOSTILE", replies: {"POST /api/recon/entity/a/disposition": () => g.promise}});
  s.store.upsert({...heldA});
  const pending = s.controls.updateDisposition("a");
  await settle();
  await s.workspace.deactivate(RECON_ID);
  g.open(ok({entity: {entity_id: "a", disposition: "HOSTILE"}}));
  await pending;
  assert.equal(s.store.entities.a.disposition, "UNKNOWN");
});

test("an early viewer is attached once: a second activation does not reuse it", async () => {
  const s = setup({replies: {...entitiesReply(), ...alertsReply()}});
  s.controls.initialize({id: "early"});
  await s.workspace.activate(RECON_ID);
  await s.workspace.deactivate(RECON_ID);
  await s.workspace.activate(RECON_ID);
  assert.equal(s.globeCalls.filter((c) => c[0] === "attach").length, 1);
});
