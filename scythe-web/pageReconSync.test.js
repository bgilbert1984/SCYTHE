import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import * as ReconLogic from "./reconLogic.js";
import {createReconStore} from "./reconStore.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

const objectStart = page.search(/^\s*const AutoReconVisualization = \{/m);
const objectEnd = page.search(/^\s*window\.AutoReconVisualization = AutoReconVisualization;/m);
assert.ok(objectStart > 0 && objectEnd > objectStart);
const reconObject = page.slice(objectStart, objectEnd);

/** A member of the page's AutoReconVisualization object (function or async function), as source: from `name: ` to the member's closing brace. */
function memberSource(name) {
  const header = new RegExp(`^            ${name}: (?:async )?function\\(`, "mg");
  const found = [...reconObject.matchAll(header)];
  assert.equal(found.length, 1, `${name} is defined once in the object`);
  const end = reconObject.indexOf("\n            }", found[0].index);
  return reconObject.slice(found[0].index, end + "\n            }".length).trim();
}

/** The page object with some of its members, sharing a real store's map. */
function recon(names, extra = {}, globals = {}) {
  const store = createReconStore();
  const calls = [];
  const object = {
    visible: true, entities: store.entities, entityMarkers: {}, perfMetrics: {entityUpdates: 0, updateCount: 0, lastUpdateMs: 0, avgUpdateMs: 0},
    updateInterval: null, _reconSSE: null, lastUpdateTimestamp: null,
    removeEntityMarker: (id) => calls.push(["remove", id]), addEntityMarker: (e) => calls.push(["add", e.entity_id]),
    updateEntityPosition: (e) => calls.push(["position", e.entity_id]), loadAlerts: () => calls.push(["alerts"]), loadEntities: () => calls.push(["load"]),
    renderEntities: () => calls.push(["render"]),
    ...extra,
  };
  const els = {"recon-entity-count": {}};
  const scope = {
    ReconLogic, ReconStore: store, console: {log() {}, debug() {}, warn() {}, error() {}}, document: {getElementById: (id) => els[id]},
    window: {updateReconList: () => calls.push(["list"])}, addConsoleMessage: () => {}, ...globals,
  };
  const keys = Object.keys(scope);
  const source = `return {${names.map(memberSource).join(",\n")}};`;
  Object.assign(object, new Function(...keys, source)(...keys.map((k) => scope[k])));
  return {object, store, calls, els, scope};
}

const ev = (type, id, data, entityType = "RECON_ENTITY") => ({event_type: type, entity_id: id, entity_type: entityType, entity_data: data});

test("a CREATE event stores the entity, redraws its marker and refreshes the list and the count", () => {
  const {object, calls, els} = recon(["applyEntitySyncEvent"]);
  object.applyEntitySyncEvent(ev("CREATE", "a", {entity_id: "a", name: "N", location: {lat: 1, lon: 2}}));
  assert.equal(object.entities.a.name, "N");
  assert.deepEqual(calls, [["remove", "a"], ["add", "a"], ["list"]]);
  assert.equal(els["recon-entity-count"].textContent, 1);
});

test("an event while the layer is hidden updates the store and the list but draws nothing", () => {
  const {object, calls} = recon(["applyEntitySyncEvent"], {visible: false});
  object.applyEntitySyncEvent(ev("CREATE", "a", {entity_id: "a"}));
  object.applyEntitySyncEvent(ev("DELETE", "a", {}));
  assert.deepEqual(Object.keys(object.entities), []);
  assert.deepEqual(calls, [["list"], ["list"]]);
});

test("an UPDATE merges into the held entity and redraws it", () => {
  const {object, store, calls} = recon(["applyEntitySyncEvent"]);
  store.upsert({entity_id: "a", name: "old", mine: 1});
  object.applyEntitySyncEvent(ev("UPDATE", "a", {entity_id: "a", name: "new"}));
  assert.equal(object.entities.a.name, "new");
  assert.equal(object.entities.a.mine, 1);
  assert.deepEqual(calls, [["remove", "a"], ["add", "a"], ["list"]]);
});

test("a DELETE removes the entity and its marker; one for an entity not held still refreshes the list", () => {
  const {object, store, calls, els} = recon(["applyEntitySyncEvent"]);
  store.upsert({entity_id: "a"});
  object.applyEntitySyncEvent(ev("DELETE", "a", {}));
  assert.deepEqual(Object.keys(object.entities), []);
  assert.deepEqual(calls, [["remove", "a"], ["list"]]);
  calls.length = 0;
  object.applyEntitySyncEvent(ev("DELETE", "gone", {}));
  assert.deepEqual(calls, [["list"]], "nothing to take down");
  assert.equal(els["recon-entity-count"].textContent, 0);
});

test("an event for another kind of entity, or of a kind it does not handle, changes nothing and refreshes nothing", () => {
  const {object, calls, els} = recon(["applyEntitySyncEvent"]);
  object.applyEntitySyncEvent(ev("CREATE", "a", {entity_id: "a"}, "SENSOR"));
  object.applyEntitySyncEvent(ev("MOVE", "a", {entity_id: "a"}));
  object.applyEntitySyncEvent(null);
  assert.deepEqual(Object.keys(object.entities), []);
  assert.deepEqual(calls, []);
  assert.equal(els["recon-entity-count"].textContent, undefined);
});

test("an event's entity is kept under the event's id and redrawn from there", () => {
  const {object, calls} = recon(["applyEntitySyncEvent"]);
  object.applyEntitySyncEvent(ev("CREATE", "from-event", {entity_id: "from-data"}));
  assert.deepEqual(Object.keys(object.entities), ["from-event"]);
  assert.deepEqual(calls[0], ["remove", "from-event"]);
  assert.deepEqual(calls[1], ["add", "from-data"]);
});

test("a failing list refresh does not stop the count", () => {
  const {object, els, scope} = recon(["applyEntitySyncEvent"]);
  scope.window.updateReconList = () => { throw new Error("boom"); };
  object.applyEntitySyncEvent(ev("CREATE", "a", {entity_id: "a"}));
  assert.equal(els["recon-entity-count"].textContent, 1);
});

/** A fake EventSource and a fake timer for the stream. */
function stream({visible = true, updateInterval = null} = {}) {
  const sources = [], timers = [];
  class FakeSource { constructor(url) { this.url = url; this.closed = false; sources.push(this); } close() { this.closed = true; } }
  const r = recon(["_bindReconSSE", "applyEntitySyncEvent"], {visible, updateInterval}, {EventSource: FakeSource, setTimeout: (fn, ms) => timers.push([fn, ms])});
  return {...r, sources, timers};
}
const message = (source, raw) => source.onmessage({data: typeof raw === "string" ? raw : JSON.stringify(raw)});

test("the stream's messages become sync events: new entities are created, held ones updated, deleted ones removed", () => {
  const {object, sources, store} = stream();
  object._bindReconSSE();
  assert.equal(sources.length, 1);
  assert.equal(sources[0].url, "/api/recon/entities/stream");
  message(sources[0], {entity: {entity_id: "a", name: "one", keep: 1}});
  assert.equal(object.entities.a.name, "one");
  message(sources[0], {entity: {entity_id: "a", name: "two"}});
  assert.equal(object.entities.a.name, "two");
  assert.equal(object.entities.a.keep, 1, "a message for a held entity is an update, which merges");
  message(sources[0], {type: "entity_deleted", entity: {entity_id: "a"}});
  assert.deepEqual(Object.keys(store.entities), []);
});

test("the stream's greeting, a message without an id, and text that is not JSON are ignored", () => {
  const {object, sources, calls} = stream();
  object._bindReconSSE();
  message(sources[0], {type: "connected"});
  message(sources[0], {entity: {name: "no id"}});
  message(sources[0], "not json");
  assert.deepEqual(Object.keys(object.entities), []);
  assert.deepEqual(calls, []);
});

test("the greeting and a message without an id are not handed to the sync handler at all", () => {
  const applied = [];
  const {object, sources} = (() => { const r = stream(); r.object.applyEntitySyncEvent = (e) => applied.push(e); return r; })();
  object._bindReconSSE();
  message(sources[0], {type: "connected"});
  message(sources[0], {entity: {name: "no id"}});
  assert.deepEqual(applied, []);
  message(sources[0], {entity: {entity_id: "a"}});
  assert.equal(applied.length, 1);
});

test("binding twice opens one stream", () => {
  const {object, sources} = stream();
  object._bindReconSSE();
  object._bindReconSSE();
  assert.equal(sources.length, 1);
});

test("a dropped stream is closed, forgotten, and retried in five seconds with a reload", () => {
  const {object, sources, timers, calls} = stream();
  object._bindReconSSE();
  sources[0].onerror();
  assert.equal(sources[0].closed, true);
  assert.equal(object._reconSSE, null);
  assert.equal(timers.length, 1);
  assert.equal(timers[0][1], 5000);
  timers[0][0]();
  assert.deepEqual(calls, [["load"]]);
  assert.equal(sources.length, 2, "bound again");
});

test("a dropped stream is not retried when the layer is hidden and no update is running; with an update running it is, without the reload", () => {
  const hidden = stream({visible: false});
  hidden.object._bindReconSSE();
  hidden.sources[0].onerror();
  assert.equal(hidden.timers.length, 0);
  const running = stream({visible: false, updateInterval: 7});
  running.object._bindReconSSE();
  running.sources[0].onerror();
  assert.equal(running.timers.length, 1);
  running.timers[0][0]();
  assert.deepEqual(running.calls, []);
});

/** The auto-update's one tick, with a fake server. */
function autoUpdate({visible = true, changes = {status: "ok", timestamp: 500, entity_count: 0, entities: []}, durations = [10]} = {}) {
  const requests = [], intervals = [], messages = [];
  let clock = 1000, calls = 0;
  const r = recon(["startAutoUpdate"], {visible, _bindReconSSE() { r.calls.push(["bind"]); }}, {
    setInterval: (fn, ms) => { intervals.push([fn, ms]); return 42; },
    fetch: async (url) => { requests.push(url); return {json: async () => changes}; },
    performance: {now: () => { calls++; if (calls % 2 === 0) clock += durations[(calls / 2 - 1) % durations.length]; return clock; }}, addConsoleMessage: (text, kind) => messages.push([text, kind]),
    Date: {now: () => 1700000000000},
  });
  return {...r, requests, intervals, messages};
}

test("starting the auto-update binds the stream, notes the time and ticks every two seconds, once", () => {
  const {object, intervals, calls, messages} = autoUpdate();
  object.startAutoUpdate();
  object.startAutoUpdate();
  assert.deepEqual(calls, [["bind"]]);
  assert.equal(intervals.length, 1);
  assert.equal(intervals[0][1], 2000);
  assert.equal(object.updateInterval, 42);
  assert.equal(object.lastUpdateTimestamp, 1700000000);
  assert.deepEqual(messages, [["Recon auto-update started (incremental mode)", "command"]]);
});

test("a tick simulates movement, asks for the changes since the last one, stores and moves what changed and refreshes the alerts", async () => {
  const {object, intervals, requests, calls} = autoUpdate({changes: {status: "ok", timestamp: 2000, entity_count: 2, entities: [{entity_id: "a"}, {entity_id: "b"}]}});
  object.startAutoUpdate();
  calls.length = 0;
  await intervals[0][0]();
  assert.deepEqual(requests, ["/api/recon/simulate", "/api/recon/changes?since=1700000000"]);
  assert.equal(object.lastUpdateTimestamp, 2000);
  assert.deepEqual(Object.keys(object.entities), ["a", "b"]);
  assert.deepEqual(calls, [["position", "a"], ["position", "b"], ["alerts"]]);
  assert.equal(object.perfMetrics.entityUpdates, 2);
  assert.equal(object.perfMetrics.updateCount, 1);
});

test("the tick's timing is a running average over the ticks", async () => {
  const {object, intervals} = autoUpdate({durations: [10, 20, 30]});
  object.startAutoUpdate();
  const averages = [];
  for (let i = 0; i < 3; i++) { await intervals[0][0](); averages.push(object.perfMetrics.avgUpdateMs); }
  assert.equal(object.perfMetrics.updateCount, 3);
  assert.equal(object.perfMetrics.lastUpdateMs, 30);
  assert.deepEqual(averages, [10, 15, 20]);
});

test("a tick with no changes still counts and refreshes the alerts; a hidden layer does nothing; a reply that is not ok changes nothing", async () => {
  const quiet = autoUpdate();
  quiet.object.startAutoUpdate();
  quiet.calls.length = 0;
  await quiet.intervals[0][0]();
  assert.deepEqual(quiet.calls, [["alerts"]]);
  assert.equal(quiet.object.perfMetrics.updateCount, 1);
  const hidden = autoUpdate({visible: false});
  hidden.object.startAutoUpdate();
  await hidden.intervals[0][0]();
  assert.deepEqual(hidden.requests, []);
  const bad = autoUpdate({changes: {status: "error"}});
  bad.object.startAutoUpdate();
  bad.calls.length = 0;
  await bad.intervals[0][0]();
  assert.deepEqual(bad.calls, []);
  assert.equal(bad.object.perfMetrics.updateCount, 0);
  assert.equal(bad.object.lastUpdateTimestamp, 1700000000);
});

/** `addEntityMarker` against a stand-in for Cesium that accepts anything. */
function marker({mldc = true} = {}) {
  const anything = () => new Proxy(function () {}, {get: (t, k) => (k === Symbol.toPrimitive ? () => 0 : anything()), apply: () => anything(), construct: () => ({})});
  const labelled = [];
  const win = mldc ? {__MLDC__: {setLabelData: (labels) => labelled.push(labels)}} : {};
  const added = [];
  const viewer = {entities: {getById: () => null, add: (spec) => { added.push(spec); return spec; }}};
  const r = recon(["addEntityMarker"], {viewer, dataSource: null, showLabels: true, DISPOSITION_COLORS: {}, createEntityIcon: () => "icon", createEntityDescription: () => "desc", addThreatIndicator() {}},
    {Cesium: anything(), HierarchicalRenderManager: undefined, window: win, Date: {now: () => 1700000000000}});
  return {...r, labelled, win, added};
}
const located = (id, extra = {}) => ({entity_id: id, name: id.toUpperCase(), disposition: "HOSTILE", threat_level: "HIGH", location: {lat: 36, lon: -86, altitude_m: 0}, ...extra});

test("a marker for an entity pushes its label to the MLDC layer, which keeps every entity's", () => {
  const {object, labelled, win} = marker();
  object.addEntityMarker(located("a"));
  object.addEntityMarker(located("b"));
  assert.deepEqual(Object.keys(win._mldcLabelMap), ["a", "b"]);
  assert.deepEqual(win._mldcLabelMap.a, ReconLogic.mldcLabel(located("a"), 1700000000000));
  assert.equal(labelled.length, 2);
  assert.deepEqual(labelled[1].map((l) => l.id), ["a", "b"]);
});

test("without the MLDC layer a marker makes no label map", () => {
  const {object, win, added} = marker({mldc: false});
  object.addEntityMarker(located("a"));
  assert.equal(added.length, 1);
  assert.equal("_mldcLabelMap" in win, false);
});

test("no marker, and no label, for an entity without a numeric location", () => {
  const {object, win, added} = marker();
  object.addEntityMarker(located("a", {location: {lat: "1", lon: 2}}));
  assert.equal(added.length, 0);
  assert.equal("_mldcLabelMap" in win, false);
});

/** `updateDisposition` with a fake prompt and server. */
function disposition({answer, reply}) {
  const requests = [], messages = [];
  const r = recon(["updateDisposition"], {}, {
    prompt: () => answer, fetch: async (url, init) => { requests.push([url, JSON.parse(init.body)]); return {json: async () => reply}; },
    addConsoleMessage: (text, kind) => messages.push([text, kind]), window: {OperatorSession: {}},
  });
  r.store.upsert({entity_id: "a", name: "Bogey", disposition: "UNKNOWN"});
  return {...r, requests, messages};
}

test("a new disposition is sent, the entity the server returns replaces the one held, and the layer is redrawn", async () => {
  const {object, requests, calls, messages} = disposition({answer: "hostile", reply: {status: "ok", entity: {entity_id: "a", name: "Bogey", disposition: "HOSTILE"}}});
  await object.updateDisposition("a");
  assert.deepEqual(requests, [["/api/recon/entity/a/disposition", {disposition: "HOSTILE"}]]);
  assert.equal(object.entities.a.disposition, "HOSTILE");
  assert.deepEqual(calls, [["render"]]);
  assert.deepEqual(messages, [["Bogey disposition changed to HOSTILE", "response"]]);
});

test("the entity from the server is filed under the id that was asked for", async () => {
  const {object} = disposition({answer: "HOSTILE", reply: {status: "ok", entity: {entity_id: "other", disposition: "HOSTILE"}}});
  await object.updateDisposition("a");
  assert.deepEqual(Object.keys(object.entities), ["a"]);
});

test("no change is made when the prompt is cancelled, unchanged, or the server says no", async () => {
  for (const answer of [null, "", "unknown"]) {
    const {object, requests} = disposition({answer, reply: {status: "ok", entity: {}}});
    await object.updateDisposition("a");
    assert.deepEqual(requests, [], String(answer));
    assert.equal(object.entities.a.disposition, "UNKNOWN");
  }
  const refused = disposition({answer: "HOSTILE", reply: {status: "error", message: "no"}});
  await refused.object.updateDisposition("a");
  assert.equal(refused.object.entities.a.disposition, "UNKNOWN");
  assert.deepEqual(refused.messages, [["Failed to update disposition: no", "alert"]]);
});
