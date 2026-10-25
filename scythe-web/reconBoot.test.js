import test from "node:test";
import assert from "node:assert/strict";
import {bootWorkspace} from "./workspaceBoot.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}, info() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));

/** An element with just what the Recon panel's wiring touches. */
function element() {
  const listeners = {};
  return {
    listeners, style: {}, textContent: "", innerHTML: "",
    addEventListener(event, fn) { (listeners[event] ||= []).push(fn); },
    removeEventListener(event, fn) { listeners[event] = (listeners[event] || []).filter((f) => f !== fn); },
    fire(event, detail = {}) { for (const fn of listeners[event] || []) fn({target: this, ...detail}); },
  };
}

/** A Cesium whose values describe themselves, and a viewer whose entity collection records what is drawn into it. */
function describing(path) {
  return new Proxy(function () {}, {
    get: (t, k) => (k === Symbol.toPrimitive || k === "toJSON" ? () => path : typeof k === "symbol" ? undefined : k === "toCssColorString" ? () => `css(${path})` : describing(`${path}.${k}`)),
    apply: (t, self, args) => describing(`${path}(${args.map((a) => (typeof a === "object" ? JSON.stringify(a) : String(a))).join(",")})`),
    construct: (t, args) => describing(`new ${path}(${args.map((a) => (typeof a === "object" ? JSON.stringify(a) : String(a))).join(",")})`),
  });
}

function fakeViewer() {
  const items = [];
  const flights = [];
  return {
    items, flights,
    entities: {
      get values() { return [...items]; }, add(spec) { const e = {...spec}; items.push(e); return e; }, getById: (id) => items.find((i) => i.id === id),
      remove(e) { const i = items.indexOf(e); if (i >= 0) items.splice(i, 1); }, removeAll() { items.length = 0; }, contains: (e) => items.includes(e),
    },
    trackedEntity: undefined, scene: {screenSpaceCameraController: {}},
    camera: {position: describing("camera.position"), heading: 0, lookAtTransform() {}, flyTo: (o) => flights.push(o)},
  };
}

/** A window for booting the workspace with the recon tool: a panel, a fake Cesium, fake page functions. */
function reconWindow({console: consoleOverride, ids = ["toggleReconEntities", "reconShowRings", "reconShowLabels", "reconAutoUpdate", "reconSetReference", "reconCheckAlerts", "reconFilters", "recon-entity-count", "recon-alert-count", "recon-alert-list"], replies = {}} = {}) {
  const els = Object.fromEntries(ids.map((id) => [id, element()]));
  const viewer = fakeViewer();
  const messages = [], notes = [], requests = [], prompts = [];
  const sources = [];
  const win = {
    console: consoleOverride || quiet, Event: class { constructor(type) { this.type = type; } }, dispatchEvent() {},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    performance: {now: () => 5},
    Cesium: new Proxy({}, {get: (t, k) => (k === "Cartographic" ? {fromCartesian: () => ({latitude: 0.015, longitude: 0.025})} : k === "Math" ? {toDegrees: (r) => r * 100, toRadians: (d) => `rad(${d})`} : describing(String(k)))}),
    document: {
      getElementById: (id) => els[id] || null,
      createElement: () => ({getContext: () => new Proxy({}, {get: () => () => ({addColorStop() {}}), set: () => true}), toDataURL: () => "data:icon"}),
      addEventListener() {}, removeEventListener() {},
    },
    EventSource: class { constructor(url) { this.url = url; sources.push(this); } close() { this.closed = true; } },
    addConsoleMessage: (text, kind) => messages.push([text, kind]), showNotification: (title, text, kind) => notes.push([title, text, kind]),
    prompt: (message, current) => { prompts.push([message, current]); return "HOSTILE"; },
    updateReconList: () => { win.listRefreshes = (win.listRefreshes || 0) + 1; },
    fetch: async (url, init = {}) => {
      const key = `${init.method || "GET"} ${url}`;
      requests.push(key);
      if (!replies[key]) throw new Error(`no reply for ${key}`);
      return {ok: true, json: async () => replies[key]};
    },
  };
  const drawn = () => viewer.items.map((i) => i.id);
  return {win, els, viewer, drawn, messages, notes, requests, prompts, sources};
}

test("booting registers the recon tool inactive and sets AutoReconVisualization to its controls", () => {
  const {win} = reconWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("recon"), "inactive");
  for (const name of ["initialize", "show", "hide", "loadEntities", "loadAlerts", "renderEntities", "addEntityMarker", "removeEntityMarker", "flyToEntity", "createTaskForEntity", "updateDisposition", "createEntityIcon", "createEntityDescription"]) {
    assert.equal(typeof win.AutoReconVisualization[name], "function", name);
  }
  assert.equal(win.AutoReconVisualization.entities, win.ReconStore.entities);
});

test("the controls draw through the globe service into the viewer, and take the markers down again", async () => {
  const {win, viewer, drawn} = reconWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize(viewer);
  const c = win.AutoReconVisualization;
  const entity = {entity_id: "a", name: "A", disposition: "HOSTILE", threat_level: "LOW", location: {lat: 1, lon: 2}};
  c.addEntityMarker(entity);
  assert.deepEqual(drawn(), ["recon_entity_a"]);
  win.ReconStore.upsert(entity);
  c.flyToEntity("a");
  assert.equal(viewer.flights.length, 1);
  assert.equal(c.createEntityIcon(win.Cesium.Color.RED, "HOSTILE", false), "data:icon");
  c.removeEntityMarker("a");
  assert.deepEqual(drawn(), []);
});

test("nothing is drawn until a viewer has been handed over, and the page boots and the tool activates without Cesium", async () => {
  const {win, viewer, drawn} = reconWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.addEntityMarker({entity_id: "a", name: "A", disposition: "D", threat_level: "T", location: {lat: 1, lon: 2}});
  assert.deepEqual(drawn(), []);
  const bare = reconWindow();
  delete bare.win.Cesium;
  const w2 = bootWorkspace(bare.win);
  await w2.activate("recon");
  bare.win.AutoReconVisualization.hide();
  assert.deepEqual((await w2.deactivate("recon")), []);
});

test("the panel's controls are wired to the tool: the checkbox shows the layer, the buttons make requests", async () => {
  const replies = {
    "GET /api/recon/entities": {status: "ok", entity_count: 1, entities: [{entity_id: "a", name: "A", disposition: "HOSTILE", threat_level: "LOW", location: {lat: 1, lon: 2}}]},
    "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0},
  };
  const {win, els, viewer, drawn, requests, messages, notes} = reconWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize(viewer);
  els.toggleReconEntities.checked = true;
  els.toggleReconEntities.fire("change");
  await settle();
  assert.equal(els.reconFilters.style.display, "block");
  assert.equal(els["recon-entity-count"].textContent, 1);
  assert.equal(els["recon-alert-list"].innerHTML, '<div class="no-alerts">No active alerts</div>');
  assert.equal(els["recon-alert-count"].textContent, 0);
  assert.equal(win.listRefreshes, 1);
  assert.deepEqual(drawn(), ["recon_reference_point", "recon_proximity_ring_0", "recon_proximity_ring_1", "recon_proximity_ring_2", "recon_proximity_ring_3", "recon_entity_a"]);
  assert.deepEqual(Object.keys(win.ReconStore.entities), ["a"]);
  assert.deepEqual(notes, [["Recon Data Loaded", "Tracking 1 entities", "info"]]);
  els.reconCheckAlerts.fire("click");
  await settle();
  assert.equal(requests.filter((r) => r === "GET /api/recon/alerts").length, 2);
  const rings = viewer.items.filter((i) => i.id.startsWith("recon_proximity_ring_"));
  els.reconShowRings.checked = false; els.reconShowRings.fire("change");
  assert.deepEqual(rings.map((r) => r.show), [false, false, false, false]);
  els.reconShowRings.checked = true; els.reconShowRings.fire("change");
  assert.deepEqual(rings.map((r) => r.show), [true, true, true, true]);
  const marker = viewer.items.find((i) => i.id === "recon_entity_a");
  els.reconShowLabels.checked = false; els.reconShowLabels.fire("change");
  assert.equal(marker.label.show, false);
  els.reconShowLabels.checked = true; els.reconShowLabels.fire("change");
  assert.equal(marker.label.show, true);
  els.toggleReconEntities.checked = false;
  els.toggleReconEntities.fire("change");
  assert.equal(els.reconFilters.style.display, "none");
  assert.deepEqual(drawn(), []);
  assert.deepEqual(messages.at(-1), ["Recon auto-update stopped", "command"]);
});

test("the panel's wiring skips controls the page does not have, and deactivating unwires the ones it has", async () => {
  const {win, els} = reconWindow({ids: ["toggleReconEntities", "recon-entity-count"]});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  assert.equal(els.toggleReconEntities.listeners.change.length, 1);
  await workspace.deactivate("recon");
  assert.equal(els.toggleReconEntities.listeners.change.length, 0);
});

test("the console and notification texts are escaped, since the page puts them in as HTML", async () => {
  const replies = {"POST /api/recon/task": {status: "ok", task: {task_id: "<b>T</b>", entity_name: "<img src=x onerror=1>"}}};
  const {win, messages, notes} = reconWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  await win.AutoReconVisualization.createTaskForEntity("a");
  assert.deepEqual(messages, [["Task &lt;b&gt;T&lt;/b&gt; created for &lt;img src=x onerror=1&gt;", "response"]]);
  assert.deepEqual(notes, [["Task Created", "Investigation task assigned: &lt;b&gt;T&lt;/b&gt;", "info"]]);
});

test("the disposition prompt comes from the page's prompt and an alert list that fails to build is not written", async () => {
  const replies = {"GET /api/recon/alerts": {status: "ok", alert_count: 1, alerts: [{entity_id: "a", name: "n", disposition: "d", distance_nm: 1, bearing_deg: 1}]}};
  const {win, els, prompts} = reconWindow({replies: {...replies, "POST /api/recon/entity/a/disposition": {status: "ok", entity: {entity_id: "a", name: "A", disposition: "HOSTILE"}}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  win.ReconStore.upsert({entity_id: "a", name: "A", disposition: "UNKNOWN"});
  await win.AutoReconVisualization.updateDisposition("a");
  assert.equal(prompts.length, 1);
  assert.equal(prompts[0][1], "UNKNOWN");
  assert.equal(win.ReconStore.entities.a.disposition, "HOSTILE");
  await win.AutoReconVisualization.loadAlerts();   // an alert without a threat level throws while the list is built
  assert.equal(els["recon-alert-list"].innerHTML, "");
});

test("a failing list refresh is swallowed, and with no updateReconList the panel just goes without", async () => {
  const {win, messages} = reconWindow({replies: {"GET /api/recon/entities": {status: "ok", entity_count: 0, entities: []}, "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0}}});
  win.updateReconList = () => { throw new Error("boom"); };
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  await win.AutoReconVisualization.loadEntities();
  assert.deepEqual(messages.filter(([text]) => /Error loading/.test(text)), []);
  assert.deepEqual(messages.at(-1), ["Loaded 0 reconnaissance entities", "response"]);
  delete win.updateReconList;
  await win.AutoReconVisualization.loadEntities();
  assert.deepEqual(messages.filter(([text]) => /Error loading/.test(text)), []);
});

test("the list is refreshed only when the drawing drew: not before a viewer is attached and the layer shown", async () => {
  const replies = {"GET /api/recon/entities": {status: "ok", entity_count: 0, entities: []}, "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0}};
  const {win, viewer} = reconWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  await win.AutoReconVisualization.loadEntities();   // no viewer, layer hidden
  assert.equal(win.listRefreshes, undefined);
  win.AutoReconVisualization.initialize(viewer);
  await win.AutoReconVisualization.loadEntities();   // a viewer, but the layer is still hidden
  assert.equal(win.listRefreshes, undefined);
  win.AutoReconVisualization.show();
  await settle();
  assert.equal(win.listRefreshes, 1);
});

test("hiding the layer takes the drawing down", async () => {
  const replies = {"GET /api/recon/entities": {status: "ok", entity_count: 0, entities: []}, "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0}};
  const {win, viewer, drawn} = reconWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize(viewer);
  win.AutoReconVisualization.show();
  await settle();
  assert.ok(drawn().length > 0);
  win.AutoReconVisualization.hide();
  assert.deepEqual(drawn(), []);
});

test("the reference point button reads the camera, posts it, moves the reference marker, and does nothing without a viewer", async () => {
  const replies = {"POST /api/recon/reference": {status: "ok", reference_point: {lat: 9, lon: 8}}, "GET /api/recon/entities": {status: "ok", entity_count: 0, entities: []}, "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0}};
  const {win, els, viewer, requests, messages} = reconWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize(viewer);
  win.AutoReconVisualization.show();
  els.reconSetReference.fire("click");
  await settle();
  assert.ok(requests.includes("POST /api/recon/reference"));
  assert.ok(messages.some(([text]) => text === "Reference point set to: 1.5000, 2.5000"));
  assert.equal(String(viewer.items.find((i) => i.id === "recon_reference_point").position), "Cartesian3.fromDegrees(8,9,0)");
  const bare = reconWindow({replies});
  const w2 = bootWorkspace(bare.win);
  await w2.activate("recon");
  bare.win.AutoReconVisualization.initialize(null);   // no viewer to read the camera from
  bare.els.reconSetReference.fire("click");
  await settle();
  assert.deepEqual(bare.requests, []);
});

test("changes move markers and a task draws its line, through the globe service", async () => {
  const replies = {
    "GET /api/recon/simulate": {}, "GET /api/recon/changes?since=1700000000": {status: "ok", timestamp: 1, entity_count: 1, entities: [{entity_id: "p", location: {lat: 7, lon: 8}}]},
    "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0}, "POST /api/recon/task": {status: "ok", task: {task_id: "T", entity_name: "n", entity_id: "p"}},
    "GET /api/recon/entities": {status: "ok", entity_count: 1, entities: [{entity_id: "p", name: "P", disposition: "UNKNOWN", threat_level: "NONE", location: {lat: 1, lon: 2}}]},
  };
  const {win, els, viewer, drawn} = reconWindow({replies});
  win.setInterval = (fn) => { win.tick = fn; return 1; };
  const realNow = Date.now; Date.now = () => 1700000000000;
  try {
    const workspace = bootWorkspace(win);
    await workspace.activate("recon");
    win.AutoReconVisualization.initialize(viewer);
    win.AutoReconVisualization.show(); await settle();
    const marker = viewer.items.find((i) => i.id === "recon_entity_p");
    assert.equal(String(marker.position), "Cartesian3.fromDegrees(2,1,0)");
    els.reconAutoUpdate.checked = true; els.reconAutoUpdate.fire("change");
    await win.tick(); await settle();
    assert.equal(String(marker.position), "Cartesian3.fromDegrees(8,7,0)");
  } finally { Date.now = realNow; }
  await win.AutoReconVisualization.createTaskForEntity("p");
  assert.ok(drawn().includes("recon_task_T"));
});

test("an alert list that cannot be built is not even attempted when the page has no list", async () => {
  const replies = {"GET /api/recon/alerts": {status: "ok", alert_count: 1, alerts: [{entity_id: "a", name: "n", disposition: "d", distance_nm: 1, bearing_deg: 1}]}};
  const errors = [];
  const {win} = reconWindow({ids: ["recon-alert-count"], replies, console: {...quiet, error: (...a) => errors.push(a.join(" "))}});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  await win.AutoReconVisualization.loadAlerts();
  assert.deepEqual(errors, []);
});

test("the tool subscribes to the page's EntitySync once it is active, and unsubscribes when it deactivates", async () => {
  const {win} = reconWindow();
  const workspace = bootWorkspace(win);
  const subscribed = [];
  const real = win.EntitySync;
  win.EntitySync = {...real, on: (event, handler) => subscribed.push(["on", event]), off: (event) => subscribed.push(["off", event])};
  await workspace.activate("recon");
  assert.deepEqual(subscribed.filter(([k]) => k === "on").map(([, e]) => e), ["preexisting", "create", "update", "delete"]);
  await workspace.deactivate("recon");
  assert.deepEqual(subscribed.filter(([k]) => k === "off").map(([, e]) => e).sort(), ["create", "delete", "preexisting", "update"]);
});

test("the auto-update opens the page's event source and the tool closes it on deactivation", async () => {
  const {win, els, sources} = reconWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  els.reconAutoUpdate.checked = true;
  els.reconAutoUpdate.fire("change");
  assert.equal(sources.length, 1);
  assert.equal(sources[0].url, "/api/recon/entities/stream");
  await workspace.deactivate("recon");
  assert.equal(sources[0].closed, true);
});
