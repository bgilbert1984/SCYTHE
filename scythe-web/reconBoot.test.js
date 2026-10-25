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

/** A window for booting the workspace with the recon tool: a panel, a fake drawing, fake page functions. */
function reconWindow({console: consoleOverride, ids = ["toggleReconEntities", "reconShowRings", "reconShowLabels", "reconAutoUpdate", "reconSetReference", "reconCheckAlerts", "reconFilters", "recon-entity-count", "recon-alert-count", "recon-alert-list"], replies = {}} = {}) {
  const els = Object.fromEntries(ids.map((id) => [id, element()]));
  const drawing = {viewer: null, calls: [], rendered: true};
  for (const name of ["addEntityMarker", "removeEntityMarker", "updateEntityPosition", "clearAllEntities", "flyToEntity", "addTaskLine", "setVisible", "setShowRings", "setShowLabels", "setReferencePoint"]) {
    drawing[name] = (...args) => { drawing.calls.push([name, ...args]); };
  }
  drawing.attach = (viewer) => { drawing.viewer = viewer; drawing.calls.push(["attach", viewer]); };
  drawing.renderEntities = () => { drawing.calls.push(["renderEntities"]); return drawing.rendered; };
  drawing.createEntityIcon = (...args) => { drawing.calls.push(["createEntityIcon", ...args]); return "icon"; };
  drawing.cameraPosition = () => ({lat: 1.5, lon: 2.5});
  const messages = [], notes = [], requests = [], prompts = [];
  const sources = [];
  const win = {
    console: consoleOverride || quiet, Event: class { constructor(type) { this.type = type; } }, dispatchEvent() {},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    performance: {now: () => 5}, document: {getElementById: (id) => els[id] || null},
    ReconDrawing: drawing, EventSource: class { constructor(url) { this.url = url; sources.push(this); } close() { this.closed = true; } },
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
  return {win, els, drawing, messages, notes, requests, prompts, sources};
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

test("the globe service hands the tool's drawing calls to the page's ReconDrawing", async () => {
  const {win, drawing} = reconWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  drawing.calls.length = 0;
  const c = win.AutoReconVisualization;
  const entity = {entity_id: "a"};
  c.addEntityMarker(entity); c.removeEntityMarker("a"); c.flyToEntity("a");
  assert.equal(c.createEntityIcon("red", "HOSTILE", false), "icon");
  assert.deepEqual(drawing.calls, [["addEntityMarker", entity], ["removeEntityMarker", "a"], ["flyToEntity", "a"], ["createEntityIcon", "red", "HOSTILE", false]]);
});

test("the globe service reports it is unavailable until the drawing has a viewer, and tolerates a page with no drawing", async () => {
  const {win, drawing} = reconWindow({replies: {"POST /api/recon/reference": {status: "ok", reference_point: {lat: 0, lon: 0}}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  assert.equal(drawing.viewer.id, "v");
  const bare = reconWindow();
  delete bare.win.ReconDrawing;
  const w2 = bootWorkspace(bare.win);
  await w2.activate("recon");
  bare.win.AutoReconVisualization.addEntityMarker({entity_id: "x"});
  bare.win.AutoReconVisualization.hide();
  assert.deepEqual((await w2.deactivate("recon")), []);
});

test("the panel's controls are wired to the tool: the checkbox shows the layer, the buttons make requests", async () => {
  const replies = {
    "GET /api/recon/entities": {status: "ok", entity_count: 1, entities: [{entity_id: "a", name: "A", location: {lat: 1, lon: 2}}]},
    "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0},
  };
  const {win, els, drawing, requests, messages, notes} = reconWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  els.toggleReconEntities.checked = true;
  els.toggleReconEntities.fire("change");
  await settle();
  assert.equal(els.reconFilters.style.display, "block");
  assert.equal(els["recon-entity-count"].textContent, 1);
  assert.equal(els["recon-alert-list"].innerHTML, '<div class="no-alerts">No active alerts</div>');
  assert.equal(els["recon-alert-count"].textContent, 0);
  assert.equal(win.listRefreshes, 1);
  assert.deepEqual(drawing.calls.filter(([n]) => n === "setVisible"), [["setVisible", true]]);
  assert.deepEqual(Object.keys(win.ReconStore.entities), ["a"]);
  assert.deepEqual(notes, [["Recon Data Loaded", "Tracking 1 entities", "info"]]);
  els.reconCheckAlerts.fire("click");
  await settle();
  assert.equal(requests.filter((r) => r === "GET /api/recon/alerts").length, 2);
  els.reconShowRings.checked = false; els.reconShowRings.fire("change");
  els.reconShowLabels.checked = true; els.reconShowLabels.fire("change");
  assert.deepEqual(drawing.calls.filter(([n]) => n === "setShowRings" || n === "setShowLabels"), [["setShowRings", false], ["setShowLabels", true]]);
  els.toggleReconEntities.checked = false;
  els.toggleReconEntities.fire("change");
  assert.equal(els.reconFilters.style.display, "none");
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

test("the list is refreshed only when the drawing drew", async () => {
  const replies = {"GET /api/recon/entities": {status: "ok", entity_count: 0, entities: []}, "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0}};
  const {win, drawing} = reconWindow({replies});
  drawing.rendered = false;
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  await win.AutoReconVisualization.loadEntities();
  assert.equal(win.listRefreshes, undefined);
  drawing.rendered = true;
  await win.AutoReconVisualization.loadEntities();
  assert.equal(win.listRefreshes, 1);
});

test("hiding the layer takes the drawing down", async () => {
  const {win, drawing} = reconWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  drawing.calls.length = 0;
  win.AutoReconVisualization.hide();
  assert.deepEqual(drawing.calls, [["setVisible", false], ["clearAllEntities"]]);
});

test("the reference point button reads the camera, posts it, tells the drawing, and does nothing without a viewer", async () => {
  const replies = {"POST /api/recon/reference": {status: "ok", reference_point: {lat: 9, lon: 8}}, "GET /api/recon/entities": {status: "ok", entity_count: 0, entities: []}, "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0}};
  const {win, els, drawing, requests, messages} = reconWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("recon");
  win.AutoReconVisualization.initialize({id: "v"});
  els.reconSetReference.fire("click");
  await settle();
  assert.equal(requests[0], "POST /api/recon/reference");
  assert.deepEqual(drawing.calls.filter(([n]) => n === "setReferencePoint"), [["setReferencePoint", {lat: 9, lon: 8}]]);
  assert.ok(messages.some(([text]) => text === "Reference point set to: 1.5000, 2.5000"));
  const bare = reconWindow({replies});
  bare.drawing.attach = () => {};   // the drawing never gets a viewer
  const w2 = bootWorkspace(bare.win);
  await w2.activate("recon");
  bare.win.AutoReconVisualization.initialize({id: "v"});
  bare.els.reconSetReference.fire("click");
  await settle();
  assert.deepEqual(bare.requests, []);
});

test("changes move markers and a task draws its line, through the drawing", async () => {
  const replies = {
    "GET /api/recon/simulate": {}, "GET /api/recon/changes?since=1700000000": {status: "ok", timestamp: 1, entity_count: 1, entities: [{entity_id: "p"}]},
    "GET /api/recon/alerts": {status: "ok", alerts: [], alert_count: 0}, "POST /api/recon/task": {status: "ok", task: {task_id: "T", entity_name: "n"}},
    "GET /api/recon/entities": {status: "ok", entity_count: 0, entities: []},
  };
  const {win, els, drawing} = reconWindow({replies});
  win.setInterval = (fn) => { win.tick = fn; return 1; };
  const realNow = Date.now; Date.now = () => 1700000000000;
  try {
    const workspace = bootWorkspace(win);
    await workspace.activate("recon");
    win.AutoReconVisualization.initialize({id: "v"});
    win.AutoReconVisualization.show(); await settle();
    els.reconAutoUpdate.checked = true; els.reconAutoUpdate.fire("change");
    await win.tick(); await settle();
  } finally { Date.now = realNow; }
  assert.deepEqual(drawing.calls.filter(([n]) => n === "updateEntityPosition").map(([, e]) => e.entity_id), ["p"]);
  await win.AutoReconVisualization.createTaskForEntity("p");
  assert.deepEqual(drawing.calls.filter(([n]) => n === "addTaskLine"), [["addTaskLine", {task_id: "T", entity_name: "n"}]]);
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
