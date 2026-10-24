import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {bootWorkspace} from "./workspaceBoot.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");
const quiet = {warn() {}, debug() {}, log() {}};

function fakeWindow(extra = {}) {
  const events = [];
  return {
    console: quiet, events,
    Event: class { constructor(type) { this.type = type; } },
    dispatchEvent(event) { events.push(event.type); },
    fetch: async () => ({ok: true, json: async () => ({tracks: []})}),
    ...extra,
  };
}

test("booting registers ADS-B inactive, exposes the workspace and announces it", () => {
  const win = fakeWindow();
  const workspace = bootWorkspace(win);
  assert.equal(win.ScytheWorkspace, workspace);
  assert.deepEqual(win.events, ["scythe:workspace_ready"]);
  assert.equal(workspace.state("adsb"), "inactive");
});

test("the services read the page's globals when called, not when the module loads", async () => {
  const win = fakeWindow();
  const workspace = bootWorkspace(win);          // AutoReconVisualization and WebSocketSync do not exist yet
  const listeners = [];
  const upserts = [], removes = [];
  win.WebSocketSync = {socket: {on: (n) => listeners.push(n), off() {}}};
  win.AutoReconVisualization = {addEntityMarker: (e) => upserts.push(e.entity_id), removeEntityMarker: (id) => removes.push(id)};
  await workspace.activate("adsb");
  assert.deepEqual(listeners.sort(), ["adsb_snapshot", "adsb_update"]);
  win.WebSocketSync.socket.on = (n, fn) => { if (n === "adsb_update") fn({icao: "X1", lat: 1, lon: 2}); };
  await workspace.deactivate("adsb");
  await workspace.activate("adsb");
  assert.deepEqual(upserts, ["adsb_X1"]);
  await workspace.deactivate("adsb");
  assert.deepEqual(removes, ["adsb_X1"]);
});

test("a renderer that is not there yet is tolerated, not thrown on", async () => {
  const win = fakeWindow({WebSocketSync: {socket: {on: (n, fn) => { if (n === "adsb_update") fn({icao: "X1", lat: 1, lon: 2}); }, off() {}}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("adsb");
  assert.deepEqual(await workspace.deactivate("adsb"), []);
});

test("the REST seed rejects on an HTTP error instead of parsing an error page", async () => {
  const win = fakeWindow({fetch: async () => ({ok: false, status: 502, json: async () => { throw new Error("not json"); }})});
  const workspace = bootWorkspace(win);
  await workspace.activate("adsb");               // the seed failure is logged, activation still succeeds
  assert.equal(workspace.state("adsb"), "active");
});

// --- the page itself ---

test("the page boots the workspace and AISStreamManager.init activates both tools", () => {
  assert.match(page, /import \{ bootWorkspace \} from "\.\/scythe-web\/workspaceBoot\.js";/);
  assert.match(page, /\['ais-stream', 'adsb'\]\.forEach\(function\(id\) \{\s*window\.ScytheWorkspace\.activate\(id\)/);
  assert.match(page, /addEventListener\('scythe:workspace_ready', activateTools, \{ once: true \}\)/);
});

test("the old inline ADS-B handlers are gone, so aircraft are not handled twice", () => {
  assert.doesNotMatch(page, /handleAircraftUpdate/);
  assert.doesNotMatch(page, /socket\.on\('adsb_update'/);
  assert.doesNotMatch(page, /fetch\('\/api\/adsb\/tracks'\)/);
});

test("the old inline AIS streaming is gone, so the stream is not driven twice", () => {
  assert.doesNotMatch(page, /socket\.on\('ais_update'/);
  assert.doesNotMatch(page, /WebSocketSync\.on\('connected'/);
  assert.doesNotMatch(page, /[^.\w]viewer\.camera\.changed\.addEventListener/);   // the manager's bare `viewer`; HierarchicalRenderManager's `this.viewer` is unrelated
  assert.doesNotMatch(page, /streamSocket/);
  // The operator's "search visible area" action starts the server stream itself and is not part of the manager.
  const manager = page.slice(page.indexOf("const AISStreamManager = {"), page.indexOf("window.AISStreamManager = AISStreamManager;"));
  assert.doesNotMatch(manager, /\/api\/ais\/stream\/(start|stop)/);
});

test("AISStreamManager keeps the entry points the page calls, and the vessel adapter the tool forwards to", () => {
  const start = page.indexOf("const AISStreamManager = {");
  const end = page.indexOf("window.AISStreamManager = AISStreamManager;");
  const manager = page.slice(start, end);
  for (const name of ["init", "checkAndStartStream", "stopStreaming", "handleVesselUpdate"]) {
    assert.match(manager, new RegExp(`\\b${name}: function`), `${name} is still defined`);
  }
  assert.match(page, /AISStreamManager\.checkAndStartStream\(\)/, "the AIS toggle still calls it");
  assert.match(page, /AISStreamManager\.stopStreaming\(\)/);
});

test("AutoReconVisualization.removeEntityMarker removes the marker, its altitude line and threat ring from both collections", () => {
  const start = page.indexOf("            removeEntityMarker: function(entityId) {");
  assert.ok(start > 0, "removeEntityMarker is defined on the page");
  const end = page.indexOf("            clearAllEntities: function() {", start);
  const source = page.slice(start, end).trim().replace(/,$/, "").replace(/^removeEntityMarker: /, "");
  const removeEntityMarker = new Function(`return (${source})`)();

  const collection = (items = {}) => ({
    items: {...items},
    getById(id) { return this.items[id]; },
    contains(m) { return Object.values(this.items).includes(m); },
    remove(m) { for (const k of Object.keys(this.items)) if (this.items[k] === m) delete this.items[k]; return true; },
  });
  const marker = {n: "marker"}, alt = {n: "alt"}, ring = {n: "ring"}, other = {n: "other"};
  const self = {
    dataSource: {entities: collection({recon_entity_adsb_1: marker, recon_entity_adsb_2: other})},
    viewer: {entities: collection({alt_line: alt, threat_ring: ring})},
    entityMarkers: {adsb_1: marker, adsb_1_alt: alt, adsb_1_threat: ring, adsb_2: other},
  };
  removeEntityMarker.call(self, "adsb_1");
  assert.deepEqual(Object.keys(self.dataSource.entities.items), ["recon_entity_adsb_2"]);
  assert.deepEqual(Object.keys(self.viewer.entities.items), []);
  assert.deepEqual(Object.keys(self.entityMarkers), ["adsb_2"]);
  removeEntityMarker.call(self, "adsb_1");                       // removing again is harmless
  removeEntityMarker.call({entityMarkers: {}}, "adsb_9");        // no viewer, no data source
});

// --- the AIS tool's adapters, against the page's real shapes (a Cesium-like camera, fetch) ---

test("booting also registers the AIS stream tool inactive and exposes the toggle's controls", () => {
  const win = fakeWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("ais-stream"), "inactive");
  assert.deepEqual(Object.keys(win.ScytheAisStream).sort(), ["checkAndStart", "status", "stop"]);
  assert.equal(win.ScytheAisStream.status(), null, "inactive: no state to report");
});

function aisWindow({altitude = 5000, rect = {west: -2.1, south: 0.65, east: -2.0, north: 0.66}, post} = {}) {
  const listeners = new Set();
  const posts = [];
  const win = fakeWindow({
    Cesium: {Math: {toDegrees: (r) => r * 180 / Math.PI}},
    viewer: {camera: {
      positionCartographic: {height: altitude},
      computeViewRectangle: () => rect,
      changed: {addEventListener(fn) { listeners.add(fn); return () => listeners.delete(fn); }},
    }},
    WebSocketSync: {connected: false, socket: {on() {}, off() {}}, on() {}, off() {}},
    fetch: post || (async (url, init) => { posts.push({url, body: init?.body && JSON.parse(init.body)}); return {ok: true, json: async () => ({status: "started"})}; }),
  });
  return {win, listeners, posts};
}

test("the camera adapter turns Cesium's view rectangle into AISStream's [[[lon,lat],[lon,lat]]] box in degrees", async () => {
  const {win, posts} = aisWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("ais-stream");
  await win.ScytheAisStream.checkAndStart();
  assert.equal(posts[0].url, "/api/ais/stream/start");
  const [[[west, south], [east, north]]] = posts[0].body.bounding_box;
  assert.ok(Math.abs(west - (-2.1 * 180 / Math.PI)) < 1e-9 && Math.abs(north - (0.66 * 180 / Math.PI)) < 1e-9);
  assert.ok(west < east && south < north);
});

test("the camera listener is added on activation and removed on deactivation (the old manager never removed it)", async () => {
  const {win, listeners} = aisWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("ais-stream");
  assert.equal(listeners.size, 1);
  await workspace.deactivate("ais-stream");
  assert.equal(listeners.size, 0);
});

test("deactivating tells the server to stop streaming, and only if it was streaming", async () => {
  const {win, posts} = aisWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("ais-stream");
  await workspace.deactivate("ais-stream");
  assert.deepEqual(posts.map((p) => p.url), [], "never streaming: no stop call");
  await workspace.activate("ais-stream");
  await win.ScytheAisStream.checkAndStart();
  await workspace.deactivate("ais-stream");
  assert.deepEqual(posts.map((p) => p.url), ["/api/ais/stream/start", "/api/ais/stream/stop"]);
});

test("an HTTP error from the stream endpoint is an error, not a parsed error page", async () => {
  // json() would parse fine and say "started": only the HTTP status says it did not
  const {win} = aisWindow({post: async () => ({ok: false, status: 502, json: async () => ({status: "started"})})});
  const workspace = bootWorkspace(win);
  await workspace.activate("ais-stream");
  await win.ScytheAisStream.checkAndStart();           // logged, not thrown
  assert.equal(win.ScytheAisStream.status().active, false);
});

test("with no viewer yet the tool still activates: no camera to follow, and an unknown altitude means no streaming", async () => {
  const win = fakeWindow({WebSocketSync: {connected: false, on() {}, off() {}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("ais-stream");
  await win.ScytheAisStream.checkAndStart();
  assert.equal(win.ScytheAisStream.status().active, false);
  assert.deepEqual(await workspace.deactivate("ais-stream"), []);
});

test("vessels reach AISStreamManager.handleVesselUpdate through the tool", async () => {
  const seen = [];
  let handler;
  const {win} = aisWindow();
  win.WebSocketSync.socket = {on: (name, fn) => { if (name === "ais_update") handler = fn; }, off() {}};
  win.AISStreamManager = {handleVesselUpdate: (v) => seen.push(v.mmsi)};
  const workspace = bootWorkspace(win);
  await workspace.activate("ais-stream");
  handler({mmsi: 123});
  assert.deepEqual(seen, [123]);
});

test("the AIS toggle's entry points on the page delegate to the tool's controls, and are harmless before it exists", () => {
  const page2 = page.slice(page.indexOf("const AISStreamManager = {"), page.indexOf("window.AISStreamManager = AISStreamManager;"));
  const grab = (name) => page2.match(new RegExp(`${name}: function\\(\\) \\{[^\\n]*\\},`))[0];
  const build = (win) => new Function("window", `return {${grab("checkAndStartStream")} ${grab("stopStreaming")}};`)(win);
  const calls = [];
  const manager = build({ScytheAisStream: {checkAndStart: () => calls.push("start"), stop: () => { calls.push("stop"); return "stopped"; }}});
  manager.checkAndStartStream();
  assert.equal(manager.stopStreaming(), "stopped");
  assert.deepEqual(calls, ["start", "stop"]);
  const early = build({});
  early.checkAndStartStream();
  assert.equal(early.stopStreaming(), undefined);
});

// --- the chat tool's adapters: DOM, room manager, event source, timers ---

function clockWindow(extra = {}) {
  let now = 0, next = 1;
  const timers = new Map();
  const win = fakeWindow({
    setTimeout: (fn, ms) => { const id = next++; timers.set(id, {at: now + ms, fn, every: 0}); return id; },
    setInterval: (fn, ms) => { const id = next++; timers.set(id, {at: now + ms, fn, every: ms}); return id; },
    clearTimeout: (id) => timers.delete(id),
    clearInterval: (id) => timers.delete(id),
    ...extra,
  });
  win.advance = async (ms) => {
    const target = now + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [id, t] = due;
      now = t.at;
      if (t.every) t.at += t.every; else timers.delete(id);
      t.fn();
      await new Promise((resolve) => setImmediate(resolve));
    }
    now = target;
  };
  win.pendingTimers = () => timers.size;
  return win;
}

function fakeElement(extra = {}) {
  const listeners = new Map();
  return {
    value: "", textContent: "", innerHTML: "", listeners,
    addEventListener(type, fn) { listeners.set(type, [...(listeners.get(type) || []), fn]); },
    removeEventListener(type, fn) { listeners.set(type, (listeners.get(type) || []).filter((f) => f !== fn)); },
    fire(type, event = {}) { for (const fn of listeners.get(type) || []) fn(event); },
    count: (type) => (listeners.get(type) || []).length,
    querySelector: () => null,
    ...extra,
  };
}

function chatWindow({history = {messages: []}, storage = {}} = {}) {
  const elements = {
    "lc-room-label": fakeElement(), "lc-feed": fakeElement(), "lc-callsign": fakeElement(),
    "lc-msg-input": fakeElement(), "lc-send-btn": fakeElement(),
  };
  const rendered = [], requests = [], sources = [];
  const win = clockWindow({
    document: {getElementById: (id) => elements[id] || null},
    localStorage: {getItem: (k) => storage[k] ?? null, setItem: (k, v) => { storage[k] = v; }},
    LiveChat: {addMsg: (m) => rendered.push(m.message)},
    fetch: async (url, init) => {
      requests.push({url, method: init?.method || "GET", body: init?.body && JSON.parse(init.body)});
      return {ok: true, json: async () => (url.endsWith("/messages") ? history : {ok: true})};
    },
  });
  const workspace = bootWorkspace(win);
  win.EventSource = class { constructor(url) { this.url = url; this.closed = false; sources.push(this); } close() { this.closed = true; } };
  return {win, workspace, elements, rendered, requests, sources, storage};
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("booting registers the chat tool inactive and exposes the controls LiveChat calls", () => {
  const win = fakeWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("chat"), "inactive");
  assert.deepEqual(Object.keys(win.ScytheChat).sort(), ["room", "send", "switchRoom"]);
});

test("activating shows 'Connecting…', renders history through LiveChat.addMsg and opens the room's stream", async () => {
  const {win, workspace, elements, rendered, sources} = chatWindow({history: {messages: [{message: "one", timestamp: "t"}]}});
  await workspace.activate("chat");
  await settle();
  assert.equal(elements["lc-room-label"].textContent, "GLOBAL");
  assert.deepEqual(rendered, ["one"]);
  assert.equal(sources[0].url, "/api/chat/GLOBAL/stream", "EventSource is looked up when called, so the page's wrapped one is used");
  assert.equal(win.ScytheChat.room(), "GLOBAL");
});

test("the send button and Enter send; Shift+Enter does not; listeners are removed on deactivate", async () => {
  const {workspace, elements, requests} = chatWindow();
  await workspace.activate("chat");
  await settle();
  elements["lc-msg-input"].value = "  hello  ";
  elements["lc-callsign"].value = " ALPHA ";
  elements["lc-send-btn"].fire("click");
  await settle();
  assert.deepEqual(requests.at(-1), {url: "/api/chat/GLOBAL/send", method: "POST", body: {message: "hello", callsign: "ALPHA"}});
  assert.equal(elements["lc-msg-input"].value, "", "cleared after a successful send");

  let prevented = 0;
  elements["lc-msg-input"].value = "enter";
  elements["lc-msg-input"].fire("keydown", {key: "Enter", shiftKey: false, preventDefault() { prevented += 1; }});
  await settle();
  assert.equal(prevented, 1);
  const sent = requests.filter((r) => r.method === "POST").length;
  elements["lc-msg-input"].value = "no";
  elements["lc-msg-input"].fire("keydown", {key: "Enter", shiftKey: true, preventDefault() {}});
  elements["lc-msg-input"].fire("keydown", {key: "a", shiftKey: false, preventDefault() {}});
  await settle();
  assert.equal(requests.filter((r) => r.method === "POST").length, sent);

  await workspace.deactivate("chat");
  assert.equal(elements["lc-send-btn"].count("click") + elements["lc-msg-input"].count("keydown") + elements["lc-callsign"].count("change"), 0);
});

test("re-activating does not stack the view's listeners (the old init added them every time)", async () => {
  const {workspace, elements} = chatWindow();
  for (let i = 0; i < 3; i += 1) { await workspace.activate("chat"); await workspace.deactivate("chat"); }
  await workspace.activate("chat");
  assert.equal(elements["lc-send-btn"].count("click"), 1);
});

test("the callsign is restored on activation and saved when it changes; blocked storage is tolerated", async () => {
  const first = chatWindow({storage: {rpChatCallsign: "SAVED"}});
  await first.workspace.activate("chat");
  assert.equal(first.elements["lc-callsign"].value, "SAVED");
  first.elements["lc-callsign"].value = "  NEW ";
  first.elements["lc-callsign"].fire("change");
  assert.equal(first.storage.rpChatCallsign, "NEW");

  const blocked = chatWindow();
  blocked.win.localStorage = {getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }};
  await blocked.workspace.activate("chat");
  blocked.elements["lc-callsign"].fire("change");
  assert.equal(blocked.workspace.state("chat"), "active");
});

test("a room manager that does not exist yet is waited for (300 ms, then every 500 ms) and then followed", async () => {
  const {win, workspace, sources} = chatWindow();
  await workspace.activate("chat");
  await win.advance(300);
  await win.advance(500);
  assert.equal(win.RoomManager, undefined);
  let handler;
  win.RoomManager = {on: (event, fn) => { assert.equal(event, "current_room_changed"); handler = fn; }, off() {}};
  await win.advance(500);
  handler({roomId: "OPS"});
  await settle();
  assert.equal(sources.at(-1).url, "/api/chat/OPS/stream");
  handler({});                                            // a change without a room id is ignored
  handler(undefined);
  assert.equal(sources.length, 2);
});

test("deactivating cancels a room-manager lookup still waiting, and unhooks one already attached", async () => {
  const waiting = chatWindow();
  await waiting.workspace.activate("chat");
  await waiting.workspace.deactivate("chat");
  assert.equal(waiting.win.pendingTimers(), 0, "no retry timer survives");
  waiting.win.RoomManager = {on() { throw new Error("attached after deactivation"); }, off() {}};
  await waiting.win.advance(5000);

  const attached = chatWindow();
  const offs = [];
  attached.win.RoomManager = {on() {}, off: (event) => offs.push(event)};
  await attached.workspace.activate("chat");
  await attached.win.advance(300);
  await attached.workspace.deactivate("chat");
  assert.deepEqual(offs, ["current_room_changed"]);
});

test("deactivating leaves no timers or open streams behind", async () => {
  const {win, workspace, sources} = chatWindow();
  await workspace.activate("chat");
  await settle();
  sources[0].onerror();                                   // polling interval starts
  assert.ok(win.pendingTimers() > 0);
  assert.deepEqual(await workspace.deactivate("chat"), []);
  assert.equal(win.pendingTimers(), 0);
  assert.equal(sources.every((s) => s.closed), true);
});

// --- the page ---

test("the old inline chat connection is gone from LiveChat, so the room is not connected twice", () => {
  const live = page.slice(page.indexOf("const LiveChat = (function() {"), page.indexOf("window.LiveChat = LiveChat;"));
  assert.ok(live.length > 100);
  assert.doesNotMatch(live, /new EventSource/);
  assert.doesNotMatch(live, /_pollTimer/);
  assert.doesNotMatch(live, /setInterval/);
  assert.doesNotMatch(live, /_hookRoomManager/);
  assert.match(page, /window\.LiveChat = LiveChat;/);
});

test("LiveChat keeps its rendering, and its entry points delegate to the tool and are harmless before it exists", () => {
  const live = page.slice(page.indexOf("const LiveChat = (function() {"), page.indexOf("window.LiveChat = LiveChat;"));
  for (const name of ["_badgeColor", "_myCallsign", "addMsg", "_esc"]) assert.match(live, new RegExp(`function ${name}\\(`), `${name} is kept`);
  const grab = (name) => live.match(new RegExp(`function ${name}\\([^)]*\\) \\{[^\\n]*\\}`))[0];
  const build = (win) => new Function("window", `${grab("connect")} ${grab("switchRoom")} ${grab("send")} return {connect, switchRoom, send};`)(win);
  const calls = [];
  const chat = build({ScytheChat: {switchRoom: (r) => calls.push(r), send: () => "sent"}});
  chat.connect(); chat.switchRoom("OPS");
  assert.equal(chat.send(), "sent");
  assert.deepEqual(calls, ["GLOBAL", "OPS"]);
  const early = build({});
  early.connect("x");
  assert.equal(early.send(), undefined);
});

test("LiveChat.init activates the chat tool, waiting for the workspace if the module has not run yet", () => {
  const live = page.slice(page.indexOf("const LiveChat = (function() {"), page.indexOf("window.LiveChat = LiveChat;"));
  const init = live.match(/function init\(\) \{[\s\S]*?\n            \}\n/)[0];
  const run = (win) => new Function("window", "console", `${init} init();`)(win, quiet);
  const activated = [];
  run({ScytheWorkspace: {activate: (id) => { activated.push(id); return Promise.resolve(); }}});
  assert.deepEqual(activated, ["chat"]);
  let waiting;
  run({addEventListener: (name, fn, opts) => { waiting = {name, fn, opts}; }});
  assert.equal(waiting.name, "scythe:workspace_ready");
  assert.deepEqual(waiting.opts, {once: true});
});

// --- metrics ---

function metricsWindow(extra = {}) {
  const posts = [], hooks = new Set();
  const win = fakeWindow({
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    addEventListener: (event) => hooks.add(event), removeEventListener: (event) => hooks.delete(event),
    navigator: {userAgent: "UA"}, innerWidth: 1000, innerHeight: 700,
    fetch: async (url, init) => { posts.push({url, body: init?.body ? JSON.parse(init.body) : null}); return {ok: true, json: async () => ({})}; },
    ...extra,
  });
  return {win, posts, hooks};
}
const gaugesSent = (posts) => posts.filter((p) => p.url === "/api/metrics/log").flatMap((p) => p.body.batch).map((m) => `${m.module}.${m.metric_name}=${m.value}`);

test("booting registers the metrics tool inactive; the page's logging globals exist and do nothing until it is active", async () => {
  const {win, posts} = metricsWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("metrics"), "inactive");
  assert.equal(typeof win.metricsLogger.sessionId, "string");
  win.logMetric("m", "n", 1);
  win.logInteraction("click", "x");
  assert.deepEqual(posts, []);
  await workspace.activate("metrics");
  win.logInteraction("click", "x");
  assert.deepEqual(posts.map((p) => p.body.action), ["session_start", "click"]);
  assert.deepEqual(posts[0].body.details, {userAgent: "UA", screenWidth: 1000, screenHeight: 700, timestamp: posts[0].body.details.timestamp});
  assert.equal(win.metricsLogger._interactionLog.length, 2);
});

test("the gauges read the page's counters, in the old order, tolerating a plain object or an EntityCollection", async () => {
  const {win, posts} = metricsWindow({
    reconViz: {perfMetrics: {updateCount: 3, lastUpdateMs: 4, avgUpdateMs: 5}, entities: {values: [1, 2]}},
    aisViz: {vessels: {a: 1, b: 2, c: 3}},
    viewer: {scene: {}, entities: {values: [1]}, camera: {position: "pos"}},
    Cesium: {Cartographic: {fromCartesian: () => ({height: 1234})}},
    hierarchicalRenderer: {currentAltitude: 0},
    viewshedManager: {viewsheds: {v: 1}},
    hypergraphVisualizer: {nodes: [1, 2], edges: [1]},
    performance: {memory: {usedJSHeapSize: 2 * 1024 * 1024, totalJSHeapSize: 4 * 1024 * 1024}},
  });
  const workspace = bootWorkspace(win);
  await workspace.activate("metrics");
  await win.metricsLogger.collectAndLogMetrics();
  assert.deepEqual(gaugesSent(posts), [
    "recon.update_count=3", "recon.last_update_ms=4", "recon.avg_update_ms=5", "recon.entity_count=2",
    "ais.vessel_count=3", "cesium.entity_count=1", "cesium.camera_altitude_m=1234", "render.current_altitude=0",
    "viewshed.active_count=1", "hypergraph.node_count=2", "hypergraph.edge_count=1", "browser.heap_used_mb=2", "browser.heap_total_mb=4",
  ]);
  win.reconViz.entities = {x: 1, y: 2, z: 3};   // a plain object counts its keys
  posts.length = 0;
  await win.metricsLogger.collectAndLogMetrics();
  assert.ok(gaugesSent(posts).includes("recon.entity_count=3"));
});

test("with nothing on the page yet the gauges are empty, a camera with no position skips only that gauge, and the config switches apply", async () => {
  const {win, posts} = metricsWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("metrics");
  await win.metricsLogger.collectAndLogMetrics();
  assert.deepEqual(gaugesSent(posts), []);
  Object.assign(win, {
    viewer: {scene: {}, entities: {values: []}, camera: {}},
    Cesium: {Cartographic: {fromCartesian: () => { throw new Error("no position"); }}},
    hypergraphVisualizer: {nodes: [1]},
  });
  win.metricsLogger.config.logPerformance = false;
  await win.metricsLogger.collectAndLogMetrics();
  assert.deepEqual(gaugesSent(posts), ["hypergraph.node_count=1"]);
  win.metricsLogger.config.logPerformance = true;
  win.metricsLogger.config.logHypergraph = false;
  posts.length = 0;
  await win.metricsLogger.collectAndLogMetrics();
  assert.deepEqual(gaugesSent(posts), ["cesium.entity_count=0"]);
});

test("the metrics requests use the page's fetch as it is when they are made, and deactivating releases the unload hook", async () => {
  const {win, posts, hooks} = metricsWindow();
  const workspace = bootWorkspace(win);
  const patched = [];
  win.fetch = async (url) => { patched.push(url); return {ok: true, json: async () => ({})}; };   // the page patches fetch after boot
  await workspace.activate("metrics");
  assert.deepEqual(patched, ["/api/metrics/interaction"]);
  assert.deepEqual(posts, []);
  assert.ok(hooks.has("beforeunload"));
  await workspace.deactivate("metrics");
  assert.ok(!hooks.has("beforeunload"));
});

test("the old inline MetricsLogger is gone; the boot module starts the tool; the explorer still reads the same globals", () => {
  assert.doesNotMatch(page, /const MetricsLogger = \{/);
  assert.doesNotMatch(page, /window\.metricsLogger = MetricsLogger/);
  assert.doesNotMatch(page, /fetch\('\/api\/metrics\/(log|interaction)'/);   // the explorer's own reads (session, query) stay on the page
  assert.match(page, /workspace\.activate\("metrics"\)/);
  const boot = readFileSync(new URL("./workspaceBoot.js", import.meta.url), "utf8");   // the explorer's reads moved here (liveSources)
  assert.match(boot, /win\.metricsLogger\?\._interactionLog/);
  assert.match(boot, /win\.metricsLogger\?\._lastMetrics/);
  const unguarded = page.split("\n").filter((line) => /window\.(logMetric|logInteraction)\(/.test(line) && !/^\s*window\.(logMetric|logInteraction) = /.test(line));
  assert.ok(unguarded.length > 10);   // the callers exist, and the guard test below covers them
});

test("every page caller of the logging globals checks that the global exists first", () => {
  const lines = page.split("\n");
  const bad = [];
  lines.forEach((line, i) => {
    if (!/window\.(logMetric|logInteraction)\(/.test(line)) return;
    const context = lines.slice(Math.max(0, i - 25), i + 1).join("\n");
    if (!/if \((this\.CONFIG\.logPerformance && )?window\.(logMetric|logInteraction)\b/.test(context)) bad.push(i + 1);
  });
  assert.deepEqual(bad, []);
});

// --- metrics explorer ---

function panelElement(extra = {}) {
  const listeners = new Map();
  return {
    textContent: "", innerHTML: "", value: "", children: [], listeners,
    classList: {contains: (c) => (extra.classes || []).includes(c)},
    addEventListener: (e, f) => listeners.set(e, [...(listeners.get(e) || []), f]),
    removeEventListener: (e, f) => listeners.set(e, (listeners.get(e) || []).filter((g) => g !== f)),
    appendChild(child) { this.children.push(child); },
    ...extra,
  };
}
function explorerWindow(elements = {}, extra = {}) {
  const requests = [];
  const win = fakeWindow({
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    document: {getElementById: (id) => elements[id] || null, createElement: () => panelElement()},
    fetch: async (url) => { requests.push(url); return {ok: true, json: async () => (url.startsWith("/api/metrics/query") ? {metrics: []} : {})}; },
    ...extra,
  });
  return {win, requests};
}
const EXPLORER_IDS = ["session-duration", "total-metrics-count", "module-bars", "live-heap-used", "live-entity-count", "live-vessel-count",
  "live-update-rate", "metrics-table-body", "interactions-list", "total-interactions-count", "metrics-filter-module", "metrics-filter-name",
  "metrics-filter-limit", "metrics-panel", "refresh-metrics-btn"];

test("booting registers the explorer inactive and exposes what showPanel('metrics') calls", () => {
  const {win} = explorerWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("metrics-explorer"), "inactive");
  assert.equal(typeof win.MetricsExplorer.fetchAllMetrics, "function");
  assert.equal(win.MetricsExplorer.fetchAllMetrics(), undefined);
});

test("every element id the explorer's view uses exists in the page", () => {
  for (const id of EXPLORER_IDS) assert.match(page, new RegExp(`id="${id}"`), id);
});

test("the explorer fills the page's elements, defaults an empty limit to 50, and the filter and button listeners come off on deactivate", async () => {
  const els = Object.fromEntries(EXPLORER_IDS.map((id) => [id, panelElement()]));
  const {win, requests} = explorerWindow(els);
  const workspace = bootWorkspace(win);
  await workspace.activate("metrics-explorer");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(requests.slice(0, 2), ["/api/metrics/session", "/api/metrics/query?limit=50"]);
  assert.match(els["metrics-table-body"].innerHTML, /No metrics found/);
  assert.equal(els["session-duration"].textContent, "0s");
  assert.equal(els["refresh-metrics-btn"].listeners.get("click").length, 1);
  assert.equal(els["metrics-filter-name"].listeners.get("change").length, 1);
  await workspace.deactivate("metrics-explorer");
  assert.equal(els["refresh-metrics-btn"].listeners.get("click").length, 0);
  for (const id of ["metrics-filter-module", "metrics-filter-name", "metrics-filter-limit"]) assert.equal(els[id].listeners.get("change").length, 0);
});

test("the metric-name filter is rebuilt from the server's names and keeps the current choice when it is still offered", async () => {
  const select = panelElement({value: "vessel_count"});
  const {win} = explorerWindow({"metrics-filter-name": select}, {
    fetch: async (url) => ({ok: true, json: async () => (url === "/api/metrics/session" ? {metrics: {"ais.vessel_count": {count: 1}, "ais.other": {count: 1}}} : {metrics: []})}),
  });
  const workspace = bootWorkspace(win);
  await workspace.activate("metrics-explorer");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(select.innerHTML, '<option value="">All Metrics</option>');
  assert.deepEqual(select.children.map((o) => o.value), ["other", "vessel_count"]);
  assert.deepEqual(select.children.map((o) => Boolean(o.selected)), [false, true]);
});

test("the explorer reads the metrics tool's logs and the page's counters when asked, and tolerates a page with none of its elements", async () => {
  const {win} = explorerWindow({}, {
    metricsLogger: {_interactionLog: [{timestamp: 1, action: "a", target: ""}], _lastMetrics: {entity_count: 4}},
  });
  const workspace = bootWorkspace(win);
  await workspace.activate("metrics-explorer");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(workspace.state("metrics-explorer"), "active");
  assert.deepEqual(await workspace.deactivate("metrics-explorer"), []);
});

test("the old inline MetricsExplorer is gone; the boot module starts the tool 2 s after the DOM is ready; showPanel still calls it", () => {
  assert.doesNotMatch(page, /const MetricsExplorer = \{/);
  assert.doesNotMatch(page, /MetricsExplorer\.init\(\)/);
  assert.match(page, /workspace\.activate\("metrics-explorer"\)[\s\S]{0,120}, 2000\)/);
  assert.match(page, /document\.readyState === "loading"\) document\.addEventListener\("DOMContentLoaded", startExplorer/);
  assert.match(page, /typeof MetricsExplorer !== 'undefined' && MetricsExplorer\.fetchAllMetrics\)/);
});

test("without the metrics tool's values the explorer reads the recon counters and the browser heap from the page", async () => {
  const els = Object.fromEntries(EXPLORER_IDS.map((id) => [id, panelElement()]));
  const {win} = explorerWindow(els, {
    RF_SCYTHE: {AutoReconVisualization: {perfMetrics: {entityUpdates: 7, avgUpdateMs: 2}}},
    performance: {memory: {usedJSHeapSize: 3 * 1024 * 1024}},
  });
  const workspace = bootWorkspace(win);
  delete win.metricsLogger;   // boot always sets it, and its `_lastMetrics` is truthy even when empty: the fallback needs it gone
  await workspace.activate("metrics-explorer");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(els["live-entity-count"].textContent, "7");
  assert.equal(els["live-update-rate"].textContent, "2.0 ms");
  assert.equal(els["live-heap-used"].textContent, "3.0 MB");
});

test("a missing interactions list is reported as missing, so the count is left alone", async () => {
  const els = Object.fromEntries(EXPLORER_IDS.filter((id) => id !== "interactions-list").map((id) => [id, panelElement()]));
  const {win} = explorerWindow(els, {metricsLogger: {_interactionLog: [{timestamp: 1, action: "a", target: ""}], _lastMetrics: null}});
  const workspace = bootWorkspace(win);
  await workspace.activate("metrics-explorer");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(els["total-interactions-count"].textContent, "");
});

// --- propagation analysis ---

const PROPAGATION_IDS = ["atm-k-factor", "atm-refractivity", "atm-condition", "atm-humidity", "atm-humidity-val", "atm-temp", "atm-temp-val",
  "atm-gradient", "apply-atmosphere-btn", "tx-lat", "tx-lon", "tx-height", "tx-freq", "tx-power", "rx-lat", "rx-lon", "rx-height",
  "rx-sensitivity", "calculate-path-btn", "visualize-path-btn", "path-analysis-results", "propagation-history-body", "clear-paths-btn"];

function propagationWindow(elements = {}, manager) {
  const calls = [];
  const analysis = {
    geometry: {distance_km: 5}, losses: {total_db: 100, freeSpace_db: 90, diffraction_db: 0, atmospheric_db: 1},
    linkBudget: {linkViable: true, linkMargin_db: 3, receivedPower_dbm: -90, rxSensitivity_dbm: -100}, terrain: {sampleCount: 8, obstructions: 0}, processingTime_ms: 1,
  };
  const win = fakeWindow({
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    document: {getElementById: (id) => elements[id] || null},
    RFPropagationManager: manager === null ? undefined : {
      atmosphericConditions: {kFactor: 1.3333, surfaceRefractivity: 315},
      classifyAtmosphere: () => ({condition: "standard"}),
      setAtmosphericConditions: (c) => calls.push(["set", c]),
      calculatePathLoss: async (tx, rx) => { calls.push(["calc", tx, rx]); return analysis; },
      visualizePropagationPath: async (id) => { calls.push(["vis", id]); return analysis; },
      removePropagationPath: (id) => calls.push(["remove", id]),
      clearAllPaths: () => calls.push(["clear"]),
    },
  });
  return {win, calls};
}

test("booting registers propagation inactive and exposes what showPanel('propagation') calls", () => {
  const {win} = propagationWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("propagation-ui"), "inactive");
  assert.equal(win.PropagationUI.updateAtmosphericDisplay(), undefined);
});

test("every element id the propagation view uses exists in the page", () => {
  for (const id of PROPAGATION_IDS) assert.match(page, new RegExp(`id="${id}"`), id);
});

test("the propagation view reads the page's boxes, fills its elements, labels the sliders, and its listeners come off on deactivate", async () => {
  const els = Object.fromEntries(PROPAGATION_IDS.map((id) => [id, panelElement({value: "7", style: {}})]));
  els["atm-condition"].style = {};
  const {win, calls} = propagationWindow(els);
  const workspace = bootWorkspace(win);
  win.logMetric = (...a) => calls.push(["metric", ...a]);   // after the boot, which installs the metrics tool's; read at call time
  win.logInteraction = (...a) => calls.push(["interaction", ...a]);
  await workspace.activate("propagation-ui");
  assert.equal(els["atm-k-factor"].textContent, "1.333");
  assert.equal(els["atm-refractivity"].textContent, "315 N");
  assert.equal(els["atm-condition"].style.color, "#00ff88");
  els["atm-humidity"].listeners.get("input")[0]({target: {value: "65"}});
  els["atm-temp"].listeners.get("input")[0]({target: {value: "-3"}});
  assert.equal(els["atm-humidity-val"].textContent, "65%");
  assert.equal(els["atm-temp-val"].textContent, "-3°C");
  els["apply-atmosphere-btn"].listeners.get("click")[0]();
  els["calculate-path-btn"].listeners.get("click")[0]();
  await new Promise((resolve) => setImmediate(resolve));
  els["visualize-path-btn"].listeners.get("click")[0]();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls[0], ["set", {humidity: 7, temperature: 7, refractivityGradient: 7}]);
  assert.deepEqual(calls[1], ["metric", "propagation", "atmosphere_updated", 1]);
  assert.deepEqual(calls[2], ["calc", {lat: 7, lon: 7, height_m: 7, frequency_mhz: 7, power_dbm: 7}, {lat: 7, lon: 7, height_m: 7, sensitivity_dbm: 7}]);
  assert.deepEqual(calls[3], ["interaction", "calculate_path_loss", "5.0 km"]);
  assert.deepEqual(calls[4], ["vis", "path_1"]);
  assert.match(els["path-analysis-results"].innerHTML, /5\.00 km/);
  els["clear-paths-btn"].listeners.get("click")[0]();
  assert.deepEqual(calls.at(-2), ["clear"]);
  assert.match(els["propagation-history-body"].innerHTML, /No calculations yet/);
  await workspace.deactivate("propagation-ui");
  for (const id of ["atm-humidity", "atm-temp", "apply-atmosphere-btn", "calculate-path-btn", "visualize-path-btn", "clear-paths-btn"]) {
    for (const list of els[id].listeners.values()) assert.equal(list.length, 0, id);
  }
  assert.deepEqual(await workspace.deactivate("propagation-ui"), []);
});

test("propagation tolerates a page with none of its elements and no manager, and finds the manager when it appears later", async () => {
  const {win} = propagationWindow({}, null);
  const workspace = bootWorkspace(win);
  await workspace.activate("propagation-ui");
  assert.equal(workspace.state("propagation-ui"), "active");
  win.PropagationUI.updateAtmosphericDisplay();   // no manager, no elements: nothing to do
  assert.deepEqual(await workspace.deactivate("propagation-ui"), []);

  const kFactor = panelElement();
  const later = propagationWindow({"atm-k-factor": kFactor}, null);
  const booted = bootWorkspace(later.win);
  await booted.activate("propagation-ui");
  assert.equal(kFactor.textContent, "");
  later.win.RFPropagationManager = {atmosphericConditions: {kFactor: 1, surfaceRefractivity: 1}, classifyAtmosphere: () => ({condition: "standard"})};
  later.win.PropagationUI.updateAtmosphericDisplay();   // read at call time
  assert.equal(kFactor.textContent, "1.000");
});

test("deactivating takes down the paths that were drawn, through the manager", async () => {
  const els = {"visualize-path-btn": panelElement(), "path-analysis-results": panelElement()};
  const {win, calls} = propagationWindow(els);
  const workspace = bootWorkspace(win);
  await workspace.activate("propagation-ui");
  els["visualize-path-btn"].listeners.get("click")[0]();
  await new Promise((resolve) => setImmediate(resolve));
  await workspace.deactivate("propagation-ui");
  assert.deepEqual(calls, [["vis", "path_1"], ["remove", "path_1"]]);
});

test("an empty box reads as an empty string, not undefined", async () => {
  const els = {"tx-lat": panelElement({value: undefined})};
  const {win, calls} = propagationWindow({...els, "calculate-path-btn": panelElement()});
  const workspace = bootWorkspace(win);
  await workspace.activate("propagation-ui");
  win.document.getElementById("calculate-path-btn").listeners.get("click")[0]();
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(Number.isNaN(calls[0][1].lat));
});

test("the old inline PropagationUI is gone; the boot module starts the tool 2.5 s after the DOM is ready; showPanel still calls it", () => {
  assert.doesNotMatch(page, /const PropagationUI = \{/);
  assert.doesNotMatch(page, /PropagationUI\.init\(\)/);
  assert.match(page, /workspace\.activate\("propagation-ui"\)[\s\S]{0,120}, 2500\)/);
  assert.match(page, /document\.readyState === "loading"\) document\.addEventListener\("DOMContentLoaded", startPropagation/);
  assert.match(page, /typeof PropagationUI !== 'undefined' && PropagationUI\.updateAtmosphericDisplay\)/);
});

// --- entity stream ---

function entityWindow(extra = {}) {
  const console_ = [], sources = [], requests = [];
  class Source {
    constructor(url) { this.url = url; this.listeners = new Map(); this.closed = false; sources.push(this); }
    addEventListener(type, fn) { this.listeners.set(type, [...(this.listeners.get(type) || []), fn]); }
    removeEventListener(type, fn) { this.listeners.set(type, (this.listeners.get(type) || []).filter((f) => f !== fn)); }
    close() { this.closed = true; }
    emit(type, data) { for (const fn of this.listeners.get(type) || []) fn({data: JSON.stringify(data)}); }
  }
  const win = fakeWindow({
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    EventSource: Source,
    fetch: async (url, init) => { requests.push([url, init]); return {status: 200, ok: true, json: async () => ({status: "ok"})}; },
    addConsoleMessage: (text, kind) => console_.push([text, kind]),
    OperatorSession: {sessionToken: "tok-9", cleared: 0, clearSession() { this.cleared++; }},
    ...extra,
  });
  return {win, sources, requests, console_};
}
const entityStub = () => ({
  _stub: true, _queued: [], _pendingConnect: null,
  on(event, handler) { this._queued.push([event, handler]); return this; },
});

test("booting registers the entity stream inactive and replaces the page's stub with its controls", () => {
  const {win} = entityWindow({EntitySync: entityStub()});
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("entity-sync"), "inactive");
  assert.equal(win.EntitySync._stub, undefined);
  assert.equal(typeof win.EntitySync.connect, "function");
});

test("subscriptions and a connect made on the stub before the boot are replayed", async () => {
  const stub = entityStub();
  const seen = [];
  stub.on("create", (d) => seen.push(d.entity_id));
  stub._pendingConnect = ["seq-1"];
  const {win, sources, requests} = entityWindow({EntitySync: stub});
  const workspace = bootWorkspace(win);
  await workspace.activate("entity-sync");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(requests[0][0], "/api/entities/stream?token=tok-9&since=seq-1");
  sources[0].emit("CREATE", {entity_id: "early"});
  assert.deepEqual(seen, ["early"]);
});

test("without a stub the boot still works", async () => {
  const {win} = entityWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("entity-sync");
  assert.equal(workspace.state("entity-sync"), "active");
});

test("the entity stream reads the operator session and the page's console when used, and a 401 clears the session", async () => {
  const {win, sources, requests, console_} = entityWindow({EntitySync: entityStub()});
  const workspace = bootWorkspace(win);
  await workspace.activate("entity-sync");
  win.OperatorSession.sessionToken = "tok-new";   // read at call time
  await win.EntitySync.connect();
  assert.match(requests[0][0], /token=tok-new/);
  sources[0].listeners.get("open")[0]();
  assert.deepEqual(console_.at(-1), ["📡 Connected to real-time entity stream", "response"]);
  win.fetch = async () => ({status: 401, ok: false});
  await win.EntitySync.connect();
  assert.equal(win.OperatorSession.cleared, 1);
  assert.deepEqual(console_.at(-1), ["Entity stream unauthorized - please log in", "alert"]);
  win.OperatorSession = undefined;   // logged out entirely: no token, nothing thrown
  await win.EntitySync.connect();
  await workspace.deactivate("entity-sync");
});

test("the old inline EntitySync is gone; WebSocketSync hands events to EntitySync.ingest; the boot module starts the tool", () => {
  assert.doesNotMatch(page, /const EntitySync = \{/);
  assert.doesNotMatch(page, /getEntitiesByType|deleteEntity|lastEventId/);
  assert.doesNotMatch(page, /_cacheInserts|EntitySync\._notifyHandlers/);
  assert.match(page, /window\.EntitySync = \{\s*_stub: true/);
  assert.match(page, /EntitySync\.ingest\(eventType, data\);/);
  assert.match(page, /workspace\.activate\("entity-sync"\)/);
});

test("everything the page calls on EntitySync exists on the stub or the controls", () => {
  const called = new Set([...page.matchAll(/\bEntitySync\??\.(\w+)/g)].map((m) => m[1]));
  const stub = new Set(["on", "off", "connect", "disconnect", "connected", "entityCache", "_stub", "_queued", "_pendingConnect"]);
  const {win} = entityWindow({EntitySync: entityStub()});
  bootWorkspace(win);
  const real = new Set(Object.keys(Object.getOwnPropertyDescriptors(win.EntitySync)));
  for (const name of called) {
    assert.ok(real.has(name), `${name} is not on the controls`);
    if (!["ingest", "publishEntity"].includes(name)) assert.ok(stub.has(name), `${name} is not on the stub`);
  }
});

test("a window.EntitySync that is not the page's stub is replaced without being read", async () => {
  const {win} = entityWindow({EntitySync: {}});
  const workspace = bootWorkspace(win);
  await workspace.activate("entity-sync");
  assert.equal(typeof win.EntitySync.on, "function");
});

// --- sensors ---

const SENSOR_IDS = ["sensorList", "sensors-panel-count", "sensorLoadBtn", "sensorCreateBtn", "sensor-create-form", "sensor-cancel-btn", "sensor-save-btn",
  "sensor-label", "sensor-type", "sensor-tx-freq", "sensor-rx-freq", "sensor-tx-power", "sensor-rx-sens", "sensor-lat", "sensor-lon", "sensor-assign-entity"];

/** A button in the sensor list: `closest(selector)` finds it when its class is in the selector. */
function sensorButton(cls, attrs) {
  return {getAttribute: (name) => attrs[name], closest(selector) { return selector.split(",").some((part) => part.trim() === `.${cls}`) ? this : null; }};
}

function sensorWindow({sensors = [], recon, viewer, selectValue = ""} = {}) {
  const els = Object.fromEntries(SENSOR_IDS.map((id) => [id, panelElement({value: "", style: {}})]));
  els["sensor-create-form"].style.display = "none";
  const select = els["sensor-assign-entity"];
  select.value = selectValue;
  Object.defineProperty(select, "innerHTML", {get() { return this._html; }, set(html) { this._html = html; this.children = []; }});
  const requests = [], bodyChildren = [], dialogParts = {};
  const document_ = {
    getElementById: (id) => els[id] || null,
    createElement: (tag) => {
      const element = panelElement({style: {}, tag, parentNode: null});
      element.querySelector = (selector) => (dialogParts[selector] ||= panelElement({value: "E1"}));
      return element;
    },
    body: {
      appendChild(child) { child.parentNode = this; bodyChildren.push(child); },
      removeChild(child) { bodyChildren.splice(bodyChildren.indexOf(child), 1); child.parentNode = null; },
    },
  };
  const win = fakeWindow({
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    document: document_, confirm: (text) => { win.confirmed.push(text); return true; }, confirmed: [],
    AutoReconVisualization: recon ? {entities: recon} : undefined, OperatorSession: {sessionToken: "tok"},
    consoleMessages: [], addConsoleMessage(text, kind) { win.consoleMessages.push([text, kind]); },
    fetch: async (url, init = {}) => {
      requests.push([`${init.method || "GET"} ${url}`, init.body ? JSON.parse(init.body) : undefined]);
      if (url === "/api/sensors" && !init.method) return {ok: true, json: async () => ({sensors})};
      if (url === "/api/sensors" && init.method === "POST") return {ok: true, json: async () => ({created: true, sensor: {sensor_id: "n1", label: JSON.parse(init.body).label}})};
      return {ok: true, json: async () => ({})};
    },
    ...(viewer || {}),
  });
  return {win, els, requests, bodyChildren, dialogParts};
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
const S1 = {sensor_id: "s1", label: "Roof", type: "RADAR", position: [36, -86, 5], status: "ACTIVE"};

test("booting registers the sensors tool inactive and exposes what the page's other modules read", () => {
  const {win} = sensorWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("sensors"), "inactive");
  assert.deepEqual(win.SensorManager.sensors, {});
  assert.equal(typeof win.SensorManager.flyTo, "function");
});

test("every element id the sensor view uses exists in the page", () => {
  for (const id of SENSOR_IDS) assert.match(page, new RegExp(`id="${id}"`), id);
});

test("the sensor view: Load fills the count and the list; the create form toggles, cancels and saves, then hides and clears", async () => {
  const {win, els, requests} = sensorWindow({sensors: [S1], recon: {E1: {name: "Falcon"}, E2: {}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  els.sensorLoadBtn.listeners.get("click")[0]();
  await flush();
  assert.equal(els["sensors-panel-count"].textContent, 1);
  assert.match(els.sensorList.innerHTML, /data-sensor-id="s1"/);
  const form = els["sensor-create-form"];
  els.sensorCreateBtn.listeners.get("click")[0]();
  assert.equal(form.style.display, "block");
  assert.equal(els["sensor-assign-entity"].innerHTML, '<option value="">-- None --</option>');
  assert.deepEqual(els["sensor-assign-entity"].children.map((o) => [o.value, o.textContent]), [["E1", "Falcon"], ["E2", "E2"]]);
  els.sensorCreateBtn.listeners.get("click")[0]();
  assert.equal(form.style.display, "none");
  els.sensorCreateBtn.listeners.get("click")[0]();
  els["sensor-cancel-btn"].listeners.get("click")[0]();
  assert.equal(form.style.display, "none");
  els["sensor-label"].value = "New";
  els["sensor-lat"].value = "1";
  els["sensor-lon"].value = "2";
  els["sensor-tx-freq"].value = "915";
  form.style.display = "block";
  requests.length = 0;
  els["sensor-save-btn"].listeners.get("click")[0]();
  await flush();
  assert.deepEqual(requests, [["POST /api/sensors", {label: "New", type: "SDR", tx_config: {frequency_mhz: 915, power_dbm: null}, rx_config: null, position: {lat: 1, lon: 2}}]]);
  assert.equal(form.style.display, "none");
  for (const id of ["sensor-label", "sensor-lat", "sensor-lon", "sensor-tx-freq"]) assert.equal(els[id].value, "", id);
});

test("the sensor list's buttons are handled by one listener on the list; moving a group stops the click; deactivating removes every listener", async () => {
  const {win, els, requests} = sensorWindow({sensors: [S1], recon: {E1: {name: "Falcon"}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  const click = (target) => els.sensorList.listeners.get("click")[0]({target, stopPropagation() { target.stopped = true; }});
  assert.equal(els.sensorList.listeners.get("click").length, 1);
  const track = sensorButton("sensor-track-btn", {"data-sensor-id": "s1"});
  click(track);
  assert.equal(track.stopped, undefined);
  assert.ok(win.consoleMessages.some((m) => m[0] === "Tracking sensor: Roof"));   // the Track button reached flyTo
  click(sensorButton("sensor-delete-btn", {"data-sensor-id": "s1"}));
  await flush();
  assert.deepEqual(win.confirmed, ["Delete sensor s1?"]);
  assert.ok(requests.some((r) => r[0] === "DELETE /api/sensors/s1"));
  const move = sensorButton("group-move-btn", {"data-group": "E1", "data-action": "down"});
  click(move);
  assert.equal(move.stopped, true);
  click({closest: () => null});
  click({});
  await workspace.deactivate("sensors");
  for (const id of ["sensorLoadBtn", "sensorCreateBtn", "sensor-cancel-btn", "sensor-save-btn", "sensorList"]) {
    for (const handlers of els[id].listeners.values()) assert.equal(handlers.length, 0, id);
  }
});

test("the group buttons reorder the list through the plugin: the data-group and data-action reach moveGroup in order", async () => {
  const {win, els} = sensorWindow({sensors: [S1, {...S1, sensor_id: "s2", label: "Mast"}], recon: {E1: {name: "One"}, E2: {name: "Two"}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await workspace.activate("entity-sync");
  await win.SensorManager.load();
  for (const [s, e] of [["s1", "E1"], ["s2", "E2"]]) {
    win.EntitySync.ingest("CREATE", {entity_id: `edge:sensor_assigned:sensor:${s}->recon:${e}`, entity_type: "SENSOR_ASSIGNMENT", entity_data: {nodes: [`sensor:${s}`, `recon:${e}`]}});
  }
  const firstGroup = () => els.sensorList.innerHTML.match(/TASK FORCE:<\/span>(\w+) \(/)[1];
  assert.equal(firstGroup(), "One");
  els.sensorList.listeners.get("click")[0]({target: sensorButton("group-move-btn", {"data-group": "E1", "data-action": "down"}), stopPropagation() {}});
  assert.equal(firstGroup(), "Two");
  els.sensorList.listeners.get("click")[0]({target: sensorButton("group-move-btn", {"data-group": "E1", "data-action": "up"}), stopPropagation() {}});
  assert.equal(firstGroup(), "One");
});

test("a declined delete confirmation sends nothing", async () => {
  const {win, els, requests} = sensorWindow({sensors: [S1]});
  win.confirm = () => false;
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  requests.length = 0;
  els.sensorList.listeners.get("click")[0]({target: sensorButton("sensor-delete-btn", {"data-sensor-id": "s1"})});
  await flush();
  assert.deepEqual(requests, []);
});

test("without the create form the toggle and cancel are not bound; without any element nothing throws", async () => {
  const {win, els} = sensorWindow();
  delete els["sensor-create-form"];
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  assert.equal(els.sensorCreateBtn.listeners.size, 0);
  assert.equal(els["sensor-cancel-btn"].listeners.size, 0);
  assert.equal(els.sensorLoadBtn.listeners.get("click").length, 1);
  const bare = sensorWindow();
  for (const id of Object.keys(bare.els)) delete bare.els[id];
  const w2 = bootWorkspace(bare.win);
  await w2.activate("sensors");
  bare.win.SensorManager.populateEntityDropdown();
  await bare.win.SensorManager.load();
  await w2.deactivate("sensors");
});

test("no entity chosen in the create form means no assignment is made; a chosen one is assigned", async () => {
  const none = sensorWindow({selectValue: ""});
  let workspace = bootWorkspace(none.win);
  await workspace.activate("sensors");
  none.els["sensor-label"].value = "A";
  none.els["sensor-save-btn"].listeners.get("click")[0]();
  await flush();
  assert.ok(!none.requests.some((r) => /assign_sensor/.test(r[0])));
  const chosen = sensorWindow({selectValue: "E1"});
  workspace = bootWorkspace(chosen.win);
  await workspace.activate("sensors");
  chosen.els["sensor-label"].value = "B";
  chosen.els["sensor-save-btn"].listeners.get("click")[0]();
  await flush();
  assert.ok(chosen.requests.some((r) => r[0] === "POST /api/recon/entity/E1/assign_sensor" && r[1].sensor_id === "n1"));
});

test("the assign dialog lists the entities (escaped), stays until the assignment finishes, and Cancel and deactivating close it", async () => {
  const {win, els, requests, bodyChildren, dialogParts} = sensorWindow({sensors: [{...S1, label: "<b>Roof</b>"}], recon: {E1: {name: "<i>Falcon</i>"}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  const assign = () => els.sensorList.listeners.get("click")[0]({target: sensorButton("sensor-assign-btn", {"data-sensor-id": "s1"})});
  assign();
  assert.equal(bodyChildren.length, 1);
  const html = bodyChildren[0].innerHTML;
  assert.doesNotMatch(html, /<i>|<b>/);
  assert.match(html, /Assign Sensor: &lt;b&gt;Roof&lt;\/b&gt;/);
  assert.match(html, /<option value="E1">&lt;i&gt;Falcon&lt;\/i&gt;<\/option>/);
  dialogParts[".assign-cancel-btn"].listeners.get("click").at(-1)();
  assert.equal(bodyChildren.length, 0);
  assign();
  requests.length = 0;
  dialogParts[".assign-confirm-btn"].listeners.get("click").at(-1)();
  assert.equal(bodyChildren.length, 1);   // still open: the request is in flight
  await flush();
  await flush();
  assert.ok(requests.some((r) => r[0] === "POST /api/recon/entity/E1/assign_sensor"));
  assert.equal(bodyChildren.length, 0);
  assign();
  assert.equal(bodyChildren.length, 1);
  await workspace.deactivate("sensors");
  assert.equal(bodyChildren.length, 0);
});

test("confirming a dialog that has been replaced does not close the newer one", async () => {
  const {win, els, bodyChildren, dialogParts} = sensorWindow({sensors: [S1], recon: {E1: {name: "F"}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  const assign = () => els.sensorList.listeners.get("click")[0]({target: sensorButton("sensor-assign-btn", {"data-sensor-id": "s1"})});
  assign();
  assign();
  assert.equal(bodyChildren.length, 1);
  dialogParts[".assign-confirm-btn"].listeners.get("click")[0]();   // the first dialog's own button
  await flush();
  await flush();
  assert.equal(bodyChildren.length, 1);
  dialogParts[".assign-cancel-btn"].listeners.get("click")[0]();    // likewise
  assert.equal(bodyChildren.length, 1);
});

test("opening a second assign dialog replaces the first", async () => {
  const {win, els, bodyChildren} = sensorWindow({sensors: [S1], recon: {E1: {name: "F"}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  const assign = () => els.sensorList.listeners.get("click")[0]({target: sensorButton("sensor-assign-btn", {"data-sensor-id": "s1"})});
  assign();
  assign();
  assert.equal(bodyChildren.length, 1);
});

// the globe

function fakeCesium() {
  const calls = {flights: [], viewerFlights: []};
  const entities = {
    values: [],
    add(spec) {
      if (spec.id && this.values.some((v) => v.id === spec.id)) throw new Error(`An entity with id ${spec.id} already exists in this collection.`);
      const e = {...spec};
      this.values.push(e);
      return e;
    },
    contains(e) { return this.values.includes(e); },
    removed: [],
    remove(e) { this.removed.push(e); const i = this.values.indexOf(e); if (i >= 0) this.values.splice(i, 1); },
  };
  const Cesium = {
    Color: {
      ...Object.fromEntries(["CYAN", "ORANGE", "YELLOW", "PURPLE", "LIME", "WHITE", "BLACK", "ALICEBLUE", "MAGENTA", "GREEN", "RED", "GRAY"].map((n) => [n, {name: n.toLowerCase(), withAlpha: (a) => ({name: n.toLowerCase(), alpha: a})}])),
      fromCssColorString: (css) => ({css}),
    },
    Cartesian3: class {
      static fromDegrees(lon, lat, h) { return {lon, lat, h}; }
      static distance(a, b) { return Math.hypot((a.lon - b.lon) * 1e5, (a.lat - b.lat) * 1e5); }
      static fromDegreesArray(points) { if (points.some((p) => Number.isNaN(p))) throw new Error("bad coordinates"); return {array: points}; }
      static fromDegreesArrayHeights(points) { return {arrayHeights: points}; }
      static midpoint(a, b, result) { return Object.assign(result, {lon: (a.lon + b.lon) / 2, lat: (a.lat + b.lat) / 2, mid: true}); }
    },
    PolylineArrowMaterialProperty: class { constructor(color) { this.color = color; } },
    DistanceDisplayCondition: class { constructor(near, far) { this.near = near; this.far = far; } },
    NearFarScalar: class { constructor(...a) { this.args = a; } },
    Cartesian2: class { constructor(x, y) { this.x = x; this.y = y; } },
    HeightReference: {RELATIVE_TO_GROUND: "relative"},
    CallbackProperty: class { constructor(fn) { this.fn = fn; } },
    ColorMaterialProperty: class { constructor(color) { this.color = color; } },
    LabelStyle: {FILL_AND_OUTLINE: "fill"}, VerticalOrigin: {BOTTOM: "bottom", TOP: "top"}, ArcType: {GEODESIC: "geodesic"},
    PolylineDashMaterialProperty: class { constructor(o) { this.options = o; } },
    Math: {toRadians: (d) => d * Math.PI / 180},
  };
  const viewer = {entities, camera: {flyTo: (o) => calls.viewerFlights.push(o)}};
  return {Cesium, viewer, entities, calls};
}

test("the sensor globe: a marker per sensor with a position (colour by type), moved but not relabelled when it changes, removed when the sensor goes", async () => {
  const globe = fakeCesium();
  const {win} = sensorWindow({sensors: [S1, {sensor_id: "s2", label: "Mast", type: "CAMERA", position: [1, 2, 3]}, {sensor_id: "s3", label: "Lost"}], viewer: {viewer: globe.viewer, Cesium: globe.Cesium}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  const markers = globe.entities.values;
  assert.deepEqual(markers.map((m) => m.id), ["sensor-s1", "sensor-s2"]);
  assert.deepEqual(markers[0].position, {lon: -86, lat: 36, h: 100});
  assert.equal(markers[0].point.color.name, "orange");
  assert.equal(markers[1].point.color.name, "yellow");
  assert.equal(markers[0].label.text, "Roof");
  assert.equal(markers[0].point.pixelSize, 14);
  assert.match(markers[0].description, /<strong>Roof<\/strong>/);
  // the same sensors again, one moved and relabelled
  win.fetch = async () => ({ok: true, json: async () => ({sensors: [{...S1, label: "Renamed", position: [40, -80, 0]}]})});
  await win.SensorManager.load();
  assert.deepEqual(globe.entities.values.map((m) => m.id), ["sensor-s1"]);   // s2 is gone
  assert.deepEqual(globe.entities.values[0].position, {lon: -80, lat: 40, h: 100});
  assert.equal(globe.entities.values[0].label.text, "Roof");                    // PINNED: the label is not updated
  await workspace.deactivate("sensors");
  assert.deepEqual(globe.entities.values, []);
});

test("the sensor globe colours markers by type: SDR cyan, RADAR orange, CAMERA yellow, ACOUSTIC purple, SIGINT lime", async () => {
  const globe = fakeCesium();
  const types = ["SDR", "RADAR", "CAMERA", "ACOUSTIC", "SIGINT"];
  const {win} = sensorWindow({sensors: types.map((type, i) => ({sensor_id: `t${i}`, label: type, type, position: [i, i, 0]})), viewer: {viewer: globe.viewer, Cesium: globe.Cesium}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  assert.deepEqual(globe.entities.values.map((m) => m.point.color.name), ["cyan", "orange", "yellow", "purple", "lime"]);
});

test("the sensor globe: an unknown sensor type gets the default colour; the description is escaped", async () => {
  const globe = fakeCesium();
  const {win} = sensorWindow({sensors: [{sensor_id: "s1", label: "<img src=x>", type: "WEIRD", position: [1, 2, 3]}], viewer: {viewer: globe.viewer, Cesium: globe.Cesium}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  assert.equal(globe.entities.values[0].point.color.name, "cyan");
  assert.doesNotMatch(globe.entities.values[0].description, /<img/);
});

test("the sensor globe: assignment lines are replaced on each redraw, skip points under a metre apart, and come down on deactivate", async () => {
  const globe = fakeCesium();
  const sensors = [S1, {sensor_id: "s2", label: "Twin", position: [10, 20, 0]}];
  const {win} = sensorWindow({sensors, recon: {E1: {location: {lat: 11, lon: 21}}, E2: {location: {lat: 10, lon: 20}}}, viewer: {viewer: globe.viewer, Cesium: globe.Cesium}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  await workspace.activate("entity-sync");
  const assignment = (sensor, entity) => ({entity_id: `edge:sensor_assigned:sensor:${sensor}->recon:${entity}`, entity_type: "SENSOR_ASSIGNMENT", entity_data: {nodes: [`sensor:${sensor}`, `recon:${entity}`]}});
  win.EntitySync.ingest("CREATE", assignment("s1", "E1"));
  win.EntitySync.ingest("CREATE", assignment("s2", "E2"));
  const lines = () => globe.entities.values.filter((e) => e.id.startsWith("sensor-assignment-"));
  assert.deepEqual(lines().map((l) => l.id), ["sensor-assignment-s1-E1"]);   // s2 and E2 are at the same place
  assert.equal(lines()[0].polyline.width, 2);
  assert.equal(lines()[0].polyline.arcType, "geodesic");
  assert.deepEqual(lines()[0].polyline.positions, [{lon: -86, lat: 36, h: 100}, {lon: 21, lat: 11, h: 100}]);
  assert.equal(lines()[0].polyline.material.options.dashLength, 16);
  win.EntitySync.ingest("CREATE", assignment("s1", "E1"));
  assert.equal(lines().length, 1);   // replaced, not stacked
  await workspace.deactivate("sensors");
  assert.deepEqual(globe.entities.values, []);
});

test("the sensor globe flies with flyToWithReset when the page has it, else with the camera; nothing without a viewer", async () => {
  const globe = fakeCesium();
  const {win} = sensorWindow({sensors: [S1], viewer: {viewer: globe.viewer, Cesium: globe.Cesium}});
  const workspace = bootWorkspace(win);
  await workspace.activate("sensors");
  await win.SensorManager.load();
  win.SensorManager.flyTo("s1");
  assert.equal(globe.calls.viewerFlights.length, 1);
  assert.deepEqual(globe.calls.viewerFlights[0].destination, {lon: -86, lat: 36, h: 10000});
  assert.equal(globe.calls.viewerFlights[0].duration, 1.5);
  assert.equal(globe.calls.viewerFlights[0].orientation.heading, 0);
  assert.equal(globe.calls.viewerFlights[0].orientation.roll, 0);
  assert.ok(Math.abs(globe.calls.viewerFlights[0].orientation.pitch + Math.PI / 4) < 1e-9);
  win.flyToWithReset = (options) => globe.calls.flights.push(options);
  win.SensorManager.flyTo("s1");
  assert.equal(globe.calls.flights.length, 1);
  assert.equal(globe.calls.viewerFlights.length, 1);
  const none = sensorWindow({sensors: [S1]});
  const w2 = bootWorkspace(none.win);
  await w2.activate("sensors");
  await none.win.SensorManager.load();
  none.win.SensorManager.flyTo("s1");   // no viewer: reports, does not throw
  await w2.deactivate("sensors");
});

test("the old inline SensorManager is gone; the boot module activates the tool; the page calls the renamed dropdown refresh", () => {
  assert.doesNotMatch(page, /const SensorManager = \{/);
  assert.doesNotMatch(page, /window\.SensorManager\.init\(\)|subscribeToEntitySync\(\), 200/);
  assert.doesNotMatch(page, /_populateEntityDropdown/);
  assert.match(page, /workspace\.activate\("sensors"\)/);
  assert.match(page, /window\.SensorManager\.populateEntityDropdown\(\)/);
  assert.match(page, /typeof SensorManager !== 'undefined' && SensorManager\.load\)/);
});

// --- fusion overlays ---

function fusionWindow({withViewer = true, sensors} = {}) {
  const globe = fakeCesium();
  const timers = [], errors = [];
  const win = fakeWindow({
    setInterval: () => 1, clearInterval() {},
    console: {warn() {}, log() {}, debug() {}, error: (...a) => errors.push(a)},
    setTimeout: (fn, ms) => { timers.push({fn, ms, live: true}); return timers.length - 1; },
    clearTimeout: (id) => { if (timers[id]) timers[id].live = false; },
    ...(withViewer ? {viewer: globe.viewer, Cesium: globe.Cesium} : {}),
    OperatorSession: {sessionToken: "tok"},
  });
  return {win, globe, timers, errors, sensors};
}
const fire = (timers, i) => { timers[i].live = false; timers[i].fn(); };
const activityEvent = (kind, payload, sensorId = "s1") => ({entity_id: `activity:${sensorId}:1`, entity_type: "SENSOR_ACTIVITY", entity_data: {sensor_id: sensorId, kind, payload}});

test("booting registers fusion inactive and assigns window.FusionVisualization, which the PCAP demo's guarded calls use", () => {
  const {win} = fusionWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("fusion"), "inactive");
  assert.equal(typeof win.FusionVisualization.updateGeoNode, "function");
  assert.equal(typeof win.FusionVisualization.drawFlow, "function");
  assert.equal(win.FusionVisualization.drawFlow("s", {src_ip: "10.0.0.1", dst_ip: "10.0.0.2"}), undefined);   // inactive: nothing
  assert.match(page, /if\(window\.FusionVisualization\) window\.FusionVisualization\.updateGeoNode\(payload\)/);
  assert.match(page, /if\(window\.FusionVisualization\) window\.FusionVisualization\.drawFlow\(sensorId, payload\)/);
});

test("the fusion globe draws an AoA ray, a TDoA line, a flow arc and a geo point as Cesium entities, each removed after its lifetime", async () => {
  const {win, globe, timers} = fusionWindow();
  const workspace = bootWorkspace(win);
  win.SensorManager = {sensors: {s1: {lat: 36, lon: -86, alt: 100}, s2: {lat: 36.1, lon: -86.1, alt: 5}}};   // read at call time
  await workspace.activate("fusion");
  await workspace.activate("entity-sync");
  const ingest = (event) => win.EntitySync.ingest("CREATE", event);
  ingest(activityEvent("aoa_measured", {bearing_deg: 0}));
  ingest(activityEvent("tdoa_measured", {ref_sensor_id: "s2", tau_ns: 12.34}));
  ingest(activityEvent("geoip_resolved", {ip: "8.8.8.8", geo: {lat: 37, lon: -122, city: "MV"}}));
  ingest(activityEvent("ndpi_flow_aggregate", {src_ip: "10.0.0.1", dst_ip: "8.8.8.8", bytes: 1000, protocol_hist: {TLS: 3}}));
  const [aoa, tdoa, geo, flow] = globe.entities.values;
  assert.deepEqual(aoa.polyline.positions[0], {lon: -86, lat: 36, h: 100});
  assert.ok(aoa.polyline.positions[1].lat > 36);
  assert.equal(aoa.polyline.width, 4);
  assert.equal(aoa.polyline.material.options.dashLength, 16.0);
  assert.equal(aoa.polyline.material.options.color.name, "yellow");
  assert.equal(tdoa.polyline.width, 2);
  assert.deepEqual(tdoa.polyline.material, {name: "cyan", alpha: 0.2});
  assert.equal(tdoa.label.text, "Δt: 12.3ns");
  assert.equal(tdoa.label.position.mid, true);   // PINNED: the label carries a position of its own, which Cesium ignores
  assert.equal(tdoa.label.font, "12px monospace");
  assert.equal(tdoa.label.disableDepthTestDistance, Number.POSITIVE_INFINITY);
  assert.deepEqual(tdoa.label.scaleByDistance.args, [1.5e2, 1.0, 1.5e7, 0.5]);
  assert.deepEqual(geo.position, {lon: -122, lat: 37, h: 0});
  assert.equal(geo.point.pixelSize, 8);
  assert.equal(geo.point.color.css, "#4a9eff");
  assert.deepEqual([geo.point.outlineColor.name, geo.point.outlineWidth], ["black", 1]);
  assert.equal(geo.label.verticalOrigin, "top");
  assert.equal(geo.label.font, "10px monospace");
  assert.equal(geo.label.text, "8.8.8.8\nMV");
  assert.deepEqual([geo.label.distanceDisplayCondition.near, geo.label.distanceDisplayCondition.far], [0, 5000000]);
  assert.equal(flow.polyline.arcType, "geodesic");
  assert.equal(flow.polyline.material.color.name, "lime");
  assert.equal(flow.polyline.material.color.alpha, undefined);   // not made transparent
  assert.ok(Math.abs(flow.polyline.width - 3) < 1e-9);
  assert.deepEqual(flow.polyline.positions, [{lon: -118.2437, lat: 34.0522, h: 0}, {lon: -122, lat: 37, h: 0}]);
  assert.deepEqual(timers.map((t) => t.ms), [3000, 3000, 60000, 10000]);
  fire(timers, 0);
  assert.equal(globe.entities.values.length, 3);
  assert.ok(!globe.entities.values.includes(aoa));
  fire(timers, 3);
  assert.ok(!globe.entities.values.includes(flow));
});

test("the fusion globe's flow arc takes the colour name as given, with the fallback half-transparent", async () => {
  const {win, globe} = fusionWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("fusion");
  await workspace.activate("entity-sync");
  win.EntitySync.ingest("CREATE", activityEvent("ndpi_flow_aggregate", {src_ip: "10.0.0.1", dst_ip: "10.0.0.2", bytes: 10, protocol_hist: {FTP: 1}}));
  assert.deepEqual(globe.entities.values[0].polyline.material.color, {name: "white", alpha: 0.5});
});

test("deactivating fusion removes every overlay still showing and cancels their timers; a timer that fires later does nothing", async () => {
  const {win, globe, timers} = fusionWindow();
  const workspace = bootWorkspace(win);
  win.SensorManager = {sensors: {s1: {lat: 36, lon: -86, alt: 1}}};
  await workspace.activate("fusion");
  await workspace.activate("entity-sync");
  win.EntitySync.ingest("CREATE", activityEvent("aoa_measured", {bearing_deg: 10}));
  win.EntitySync.ingest("CREATE", activityEvent("geoip_resolved", {ip: "8.8.8.8", geo: {lat: 1, lon: 2}}));
  assert.equal(globe.entities.values.length, 2);
  await workspace.deactivate("fusion");
  assert.deepEqual(globe.entities.values, []);
  assert.deepEqual(timers.map((t) => t.live), [false, false]);
  fire(timers, 0);   // a stale callback: the entity is already gone, nothing throws
});

test("each overlay is removed from the globe once: by its timer or by clear, never both, and clearing twice removes nothing more", async () => {
  const {win, globe, timers} = fusionWindow();
  const workspace = bootWorkspace(win);
  win.SensorManager = {sensors: {s1: {lat: 36, lon: -86, alt: 1}}};
  await workspace.activate("fusion");
  await workspace.activate("entity-sync");
  win.EntitySync.ingest("CREATE", activityEvent("aoa_measured", {bearing_deg: 10}));
  win.EntitySync.ingest("CREATE", activityEvent("geoip_resolved", {ip: "8.8.8.8", geo: {lat: 1, lon: 2}}));
  fire(timers, 0);                                 // the AoA ray's timer
  assert.equal(globe.entities.removed.length, 1);
  await workspace.deactivate("fusion");            // clears the geo point only
  assert.equal(globe.entities.removed.length, 2);
  await workspace.activate("fusion");
  await workspace.deactivate("fusion");            // nothing left to clear
  assert.equal(globe.entities.removed.length, 2);
});

test("without a viewer fusion draws nothing and throws nothing; the page clears its viewer later and a timer still does not throw", async () => {
  const none = fusionWindow({withViewer: false});
  const workspace = bootWorkspace(none.win);
  none.win.SensorManager = {sensors: {s1: {lat: 36, lon: -86, alt: 1}, s2: {lat: 1, lon: 2, alt: 1}}};
  await workspace.activate("fusion");
  await workspace.activate("entity-sync");
  for (const event of [activityEvent("aoa_measured", {bearing_deg: 1}), activityEvent("tdoa_measured", {ref_sensor_id: "s2", tau_ns: 1}),
    activityEvent("geoip_resolved", {ip: "8.8.8.8", geo: {lat: 1, lon: 2}}), activityEvent("ndpi_flow_aggregate", {src_ip: "10.0.0.1", dst_ip: "10.0.0.2", bytes: 1, protocol_hist: {TLS: 1}})]) {
    none.win.EntitySync.ingest("CREATE", event);
  }
  assert.deepEqual(none.timers, []);
  assert.deepEqual(none.errors, []);   // nothing was thrown out of the stream handlers either
  const later = fusionWindow();
  const w2 = bootWorkspace(later.win);
  later.win.SensorManager = {sensors: {s1: {lat: 36, lon: -86, alt: 1}}};
  await w2.activate("fusion");
  await w2.activate("entity-sync");
  later.win.EntitySync.ingest("CREATE", activityEvent("aoa_measured", {bearing_deg: 1}));
  later.win.viewer = undefined;
  fire(later.timers, 0);   // the viewer went away: the removal is skipped
  await w2.deactivate("fusion");
});

test("fusion reads the sensors from the Sensors panel's SensorManager when it draws, and ignores an AoA event for an unknown one", async () => {
  const {win, globe} = fusionWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("fusion");
  await workspace.activate("entity-sync");
  win.EntitySync.ingest("CREATE", activityEvent("aoa_measured", {bearing_deg: 1}));   // the real SensorManager is inactive: no sensors
  assert.deepEqual(globe.entities.values, []);
  win.SensorManager = undefined;
  win.EntitySync.ingest("CREATE", activityEvent("aoa_measured", {bearing_deg: 1}));
  assert.deepEqual(globe.entities.values, []);
});

test("the PCAP simulation's own events draw their geo points and flows through window.FusionVisualization", async () => {
  // The simulation's mock events and the shape of the payloads it builds are read from the page, so this follows it if it changes.
  const events = new Function(`return [${page.match(/const events = \[([\s\S]*?)\n                         \];/)[1]}];`)();
  assert.match(page, /ip: e\.ip,\s*geo: \{ lat: e\.lat, lon: e\.lon, city: e\.city, country: e\.country \},\s*asn: \{ asn: e\.asn, org: e\.org \}/);
  assert.match(page, /src_ip: e\.src,\s*dst_ip: e\.dst,\s*bytes: e\.bytes,\s*protocol_hist: e\.proto,/);
  const {win, globe, timers} = fusionWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("fusion");
  const sensorId = "SENSOR-VM-172-234-197-23";
  for (const e of events.filter((x) => x.type !== "flow")) {
    win.FusionVisualization.updateGeoNode({ip: e.ip, geo: {lat: e.lat, lon: e.lon, city: e.city, country: e.country}, asn: {asn: e.asn, org: e.org}});
  }
  assert.equal(globe.entities.values.length, 6);
  assert.deepEqual(globe.entities.values.map((x) => x.label.text.split("\n")[1]), ["Mountain View", "Sydney", "San Francisco", "Mountain View", "Norwell", "San Francisco"]);
  for (const e of events.filter((x) => x.type === "flow")) {
    win.FusionVisualization.drawFlow(sensorId, {src_ip: e.src, dst_ip: e.dst, bytes: e.bytes, protocol_hist: e.proto, timestamp: 1});
  }
  const arcs = globe.entities.values.filter((x) => x.polyline);
  assert.equal(arcs.length, 3);
  assert.deepEqual(arcs.map((x) => x.polyline.material.color.name), ["lime", "magenta", "orange"]);   // TLS, QUIC, SSH
  for (const arc of arcs) assert.deepEqual(arc.polyline.positions[0], {lon: -118.2437, lat: 34.0522, h: 0});   // from the simulated sensor in Los Angeles
  assert.deepEqual(arcs[1].polyline.positions[1], {lon: -122.4194, lat: 37.7749, h: 0});   // 172.67.198.10 was located: its position wins over the "private" placement
  assert.deepEqual(timers.map((t) => t.ms), [60000, 60000, 60000, 60000, 60000, 60000, 10000, 10000, 10000]);
  await workspace.deactivate("fusion");
  assert.deepEqual(globe.entities.values, []);
});

test("the old inline FusionVisualization is gone and the boot module activates the tool", () => {
  assert.doesNotMatch(page, /const FusionVisualization = \{/);
  assert.doesNotMatch(page, /FusionVisualization\.init\(\)/);
  assert.match(page, /workspace\.activate\("fusion"\)/);
});

// --- viewsheds ---

function viewshedWindow({elevation = () => 0, withManager = true} = {}) {
  const globe = fakeCesium();
  const metrics = [], initialized = [];
  const win = fakeWindow({
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    viewer: globe.viewer, Cesium: globe.Cesium,
    RFPropagationManager: withManager ? {initialize: (v) => initialized.push(v), _estimateElevation: elevation} : undefined,
  });
  return {win, globe, metrics, initialized};
}
const rays = (globe) => globe.entities.values.filter((e) => e.id.startsWith("viewshed_ray_"));

test("booting registers the viewsheds tool inactive and exposes it as window.ViewshedManager", () => {
  const {win} = viewshedWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("viewsheds"), "inactive");
  assert.deepEqual(win.ViewshedManager.viewsheds, {});
  assert.equal(win.ViewshedManager.DEFAULT_CONFIG.horizontalFov, 60);
  assert.equal(win.ViewshedManager.createTowerViewshed({name: "T", position: [1, 2]}), null);
});

test("the viewshed globe draws an observer, a wedge and the rays as Cesium entities with the old properties", async () => {
  const {win, globe, initialized} = viewshedWindow();
  const workspace = bootWorkspace(win);
  win.logMetric = (...a) => (win.metricsSeen ||= []).push(a);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(win.viewer);
  assert.deepEqual(initialized, [win.viewer]);   // the terrain model is initialized with the viewer, as before
  const viewshed = win.ViewshedManager.createTowerViewshed({name: "T1", position: [-86, 36], height: 60, range: 8000});
  const [observer, coverage, ...fan] = globe.entities.values;
  assert.equal(observer.id, "viewshed_observer_tower_T1");
  assert.deepEqual(observer.position, {lon: -86, lat: 36, h: 60});
  assert.equal(observer.point.pixelSize, 12);
  assert.deepEqual([observer.point.color.name, observer.point.outlineColor.name, observer.point.outlineWidth], ["cyan", "white", 2]);
  assert.equal(observer.label.text, "📡 LOS: tower_T1");
  assert.deepEqual([observer.label.font, observer.label.fillColor.name, observer.label.outlineColor.name, observer.label.outlineWidth], ["11px sans-serif", "cyan", "black", 2]);
  assert.equal(observer.label.style, "fill");
  assert.equal(observer.label.verticalOrigin, "bottom");
  assert.deepEqual([observer.label.pixelOffset.x, observer.label.pixelOffset.y], [0, -15]);
  assert.equal(coverage.id, "viewshed_coverage_tower_T1");
  assert.equal(coverage.polygon.hierarchy.array.length, 54);
  assert.deepEqual(coverage.polygon.material, {name: "green", alpha: 0.25});
  assert.equal(coverage.polygon.outline, true);
  assert.deepEqual(coverage.polygon.outlineColor, {name: "yellow", alpha: 0.6});
  assert.deepEqual([coverage.polygon.outlineWidth, coverage.polygon.height, coverage.polygon.heightReference], [2, 60, "relative"]);
  assert.equal(fan.length, 36);
  assert.equal(fan[0].id, "viewshed_ray_tower_T1_0");
  assert.equal(fan[0].polyline.width, 1);
  assert.deepEqual(fan[0].polyline.material, {name: "green", alpha: 0.25});   // clear: the visible colour
  assert.equal(fan[0].polyline.positions.arrayHeights.length, 6);
  assert.deepEqual(fan[0].polyline.positions.arrayHeights.slice(0, 3), [-86, 36, 60]);
  assert.equal(fan[0].polyline.positions.arrayHeights[5], 0);
  assert.equal(viewshed.handles.length, 38);
  assert.equal(win.metricsSeen.length, 4);
});

test("a blocked ray is dashed in the hidden colour and ends at the terrain; the globe's viewsheds are counted", async () => {
  const {win, globe} = viewshedWindow({elevation: () => 5000});
  const workspace = bootWorkspace(win);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(win.viewer);
  win.ViewshedManager.createTowerViewshed({name: "T", position: [-86, 36], height: 60, range: 8000});
  const first = rays(globe)[0].polyline;
  assert.equal(first.material.options.dashLength, 8.0);
  assert.deepEqual(first.material.options.color, {name: "red", alpha: 0.25});
  assert.equal(first.positions.arrayHeights[5], 5000);
  win.viewshedManager = win.ViewshedManager;
  assert.equal(Object.keys(win.viewshedManager.viewsheds).length, 1);   // what the Settings panel counts
});

test("without RFPropagationManager the rays are blocked at random; it is not initialized", async () => {
  const {win, globe, initialized} = viewshedWindow({withManager: false});
  const workspace = bootWorkspace(win);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(win.viewer);
  assert.deepEqual(initialized, []);
  const random = Math.random;
  Math.random = () => 0.99;
  try { win.ViewshedManager.createTowerViewshed({name: "T", position: [-86, 36], range: 20000}); } finally { Math.random = random; }
  assert.ok(rays(globe).every((r) => r.polyline.material.options));   // all dashed: blocked
});

test("a wedge Cesium refuses is reported and left out; the observer and rays are still drawn", async () => {
  const {win, globe} = viewshedWindow();
  const warnings = [];
  win.console = {warn: (...a) => warnings.push(a), log() {}, debug() {}};
  const workspace = bootWorkspace(win);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(win.viewer);
  win.ViewshedManager.DEFAULT_CONFIG.rayCount = 2;
  const viewshed = win.ViewshedManager.createTowerViewshed({name: "T", position: [-86, NaN]});   // NaN coordinates: Cesium refuses the wedge
  assert.equal(warnings[0][0], "Error creating coverage wedge:");
  assert.ok(!globe.entities.values.some((e) => e.id.startsWith("viewshed_coverage_")));
  assert.equal(globe.entities.values.filter((e) => e.id.startsWith("viewshed_observer_")).length, 1);
  assert.equal(viewshed.handles.length, 1 + 2);
});

test("clearing removes the viewsheds' entities; removal of one that is already gone is skipped; deactivating removes the rest", async () => {
  const {win, globe} = viewshedWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(win.viewer);
  const a = win.ViewshedManager.createTowerViewshed({name: "A", position: [-86, 36]});
  win.ViewshedManager.createTowerViewshed({name: "B", position: [-85, 35]});
  const gone = a.handles[0];
  globe.entities.remove(gone);   // something else already removed one
  const removedBefore = globe.entities.removed.length;
  win.ViewshedManager.clearAllViewsheds();
  assert.deepEqual(globe.entities.values, []);
  assert.equal(globe.entities.removed.length - removedBefore, 2 * 38 - 1);   // the missing one is not removed again
  win.ViewshedManager.createTowerViewshed({name: "C", position: [-84, 34]});
  assert.equal(globe.entities.values.length, 38);
  await workspace.deactivate("viewsheds");
  assert.deepEqual(globe.entities.values, []);
  assert.deepEqual(win.ViewshedManager.viewsheds, {});
});

test("the viewer stays attached when the tool is deactivated and activated again", async () => {
  const {win, globe} = viewshedWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(win.viewer);
  await workspace.deactivate("viewsheds");
  await workspace.activate("viewsheds");
  assert.ok(win.ViewshedManager.createTowerViewshed({name: "T", position: [-86, 36]}));
  assert.equal(globe.entities.values.length, 38);
});

test("initialize with no viewer leaves the globe unattached", async () => {
  const {win, globe} = viewshedWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(null);
  assert.equal(win.ViewshedManager.createTowerViewshed({name: "T", position: [-86, 36]}), null);
  assert.deepEqual(globe.entities.values, []);
});

test("the old inline ViewshedManager is gone; the page still hands it the viewer and the Settings panel still calls it; the link onto the manager is gone", () => {
  assert.doesNotMatch(page, /const ViewshedManager = \{/);
  assert.doesNotMatch(page, /_checkLineOfSight|getActiveViewsheds|setViewshedVisible/);
  assert.doesNotMatch(page, /viewshedManager\.propagationManager/);
  assert.match(page, /ViewshedManager\.initialize\(viewer\);\s*window\.viewshedManager = ViewshedManager;/);
  assert.match(page, /window\.viewshedManager\.DEFAULT_CONFIG\.horizontalFov = parseInt\(this\.value\)/);
  assert.match(page, /window\.viewshedManager\.DEFAULT_CONFIG\.distance = parseInt\(this\.value\) \* 1000/);
  assert.match(page, /workspace\.activate\("viewsheds"\)/);
});

test("the terrain model is asked for the elevation at (latitude, longitude) of each sample", async () => {
  const asked = [];
  const {win} = viewshedWindow({elevation: (lat, lon) => { asked.push([lat, lon]); return 0; }});
  const workspace = bootWorkspace(win);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(win.viewer);
  win.ViewshedManager.createTowerViewshed({name: "T", position: [-86, 36], range: 1000});
  assert.equal(asked.length, 36 * 9);
  for (const [lat, lon] of asked) { assert.ok(Math.abs(lat - 36) < 0.05); assert.ok(Math.abs(lon + 86) < 0.05); }
});

test("deactivating removes only the viewshed entities that are still showing", async () => {
  const {win, globe} = viewshedWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("viewsheds");
  win.ViewshedManager.initialize(win.viewer);
  const viewshed = win.ViewshedManager.createTowerViewshed({name: "A", position: [-86, 36]});
  globe.entities.remove(viewshed.handles[3]);   // something else took one down
  const before = globe.entities.removed.length;
  await workspace.deactivate("viewsheds");
  assert.equal(globe.entities.removed.length - before, 37);
  assert.deepEqual(globe.entities.values, []);
});

// --- LPI ---

const LPI_IDS = ["lpiList", "lpi-event-count", "lpi-summary-container", "lpi-details-panel", "lpi-details-json", "lpi-clear-btn", "lpi-run-sim-btn"];

function lpiWindow({elements = LPI_IDS, viewer = true} = {}) {
  const els = Object.fromEntries(elements.map((id) => [id, panelElement({style: {}})]));
  const cards = {};
  if (els["lpi-summary-container"]) {
    const container = els["lpi-summary-container"];
    container.innerText = "Waiting for signal activity...";
    container.innerHTML = '<div>Waiting for signal activity...</div>';
    container.children = [];
    container.prepend = (card) => { container.children.unshift(card); cards[card.id] = card; container.innerText = ""; };
  }
  const globe = fakeCesium();
  const frames = new Map(), timers = [], requests = [];
  let nextFrame = 1;
  const win = fakeWindow({
    setInterval: () => 1, clearInterval() {},
    setTimeout: (fn, ms) => { timers.push({fn, ms, live: true}); return timers.length - 1; }, clearTimeout: (id) => { if (timers[id]) timers[id].live = false; },
    requestAnimationFrame: (fn) => { const id = nextFrame++; frames.set(id, fn); return id; }, cancelAnimationFrame: (id) => frames.delete(id),
    document: {getElementById: (id) => els[id] || cards[id] || null, createElement: () => panelElement({style: {}})},
    ...(viewer ? {viewer: globe.viewer, Cesium: globe.Cesium} : {}),
    fetch: async (url, init) => { requests.push([url, init]); return {ok: true, json: async () => ({})}; },
    SensorManager: undefined, consoleMessages: [], addConsoleMessage(text, kind) { win.consoleMessages.push([text, kind]); },
  });
  const runFrames = () => { const pending = [...frames.entries()]; frames.clear(); for (const [, fn] of pending) fn(); };
  return {win, els, cards, globe, frames, timers, requests, runFrames};
}
const lpiEvent = (kind, payload = {}, sensorId = "s1") => ({entity_id: `a:${sensorId}`, entity_type: "SENSOR_ACTIVITY", entity_data: {sensor_id: sensorId, kind, payload}});
async function lpiUp(w, sensors = {s1: {position: {lat: 36, lon: -86}}}) {
  const workspace = bootWorkspace(w.win);
  w.win.SensorManager = {sensors, flyTo: (id) => (w.flights ||= []).push(id)};
  await workspace.activate("lpi");
  await workspace.activate("entity-sync");
  return workspace;
}
const withDate = async (ms, fn) => { const real = Date.now; Date.now = () => ms; try { return await fn(); } finally { Date.now = real; } };

test("booting registers lpi inactive and does not assign window.LPIManager", () => {
  const {win} = lpiWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("lpi"), "inactive");
  assert.equal(win.LPIManager, undefined);
});

test("every element id the LPI view uses exists in the page", () => {
  for (const id of LPI_IDS) assert.match(page, new RegExp(`id="${id}"`), id);
});

test("the LPI view fills the feed, the count and one status card per sensor on the next frame; the first card replaces the placeholder, newest card first", async () => {
  const w = lpiWindow();
  const workspace = await lpiUp(w, {s1: {}, s2: {}});
  await withDate(1_700_000_000_000, async () => {
    w.win.EntitySync.ingest("CREATE", lpiEvent("lpi_candidate_detected", {snr_db: 8, timestamp: 1_700_000_000}));
    assert.equal(w.els.lpiList.innerHTML, "");   // nothing until the frame
    w.runFrames();
    assert.match(w.els.lpiList.innerHTML, /s1 \/ LPI CANDIDATE DETECTED/);
    assert.equal(w.els["lpi-event-count"].textContent, 1);
    assert.deepEqual(Object.keys(w.cards), ["lpi-card-s1"]);
    assert.equal(w.els["lpi-summary-container"].innerHTML, "");   // the placeholder was cleared
    assert.equal(w.cards["lpi-card-s1"].style.borderColor, "#fd0");
    assert.match(w.cards["lpi-card-s1"].innerHTML, /LPI SIGNAL DETECTED/);
    assert.equal(w.cards["lpi-card-s1"].className, "settings-group");
    w.win.EntitySync.ingest("CREATE", lpiEvent("tf_computed", {snr_db: 5, timestamp: 1_700_000_001}, "s2"));
    w.win.EntitySync.ingest("CREATE", lpiEvent("tf_computed", {snr_db: 6, timestamp: 1_700_000_002}, "s1"));
    w.runFrames();
    assert.deepEqual(w.els["lpi-summary-container"].children.map((c) => c.id), ["lpi-card-s2", "lpi-card-s1"]);   // s2 was prepended; s1's card was reused
    assert.equal(w.els["lpi-event-count"].textContent, 3);
  });
  await workspace.deactivate("lpi");
});

test("clicking a feed row shows its event; Clear empties the feed and resets the summary; Run simulation posts", async () => {
  const w = lpiWindow();
  const workspace = await lpiUp(w);
  w.win.EntitySync.ingest("CREATE", lpiEvent("tf_computed", {snr_db: 5, timestamp: 100}));
  w.runFrames();
  const click = (target) => w.els.lpiList.listeners.get("click")[0]({target});
  click({closest: (selector) => (selector === "li[data-index]" ? {getAttribute: () => "0"} : null)});
  assert.equal(w.els["lpi-details-panel"].style.display, "block");
  assert.match(w.els["lpi-details-json"].textContent, /"activity_type": "tf_computed"/);
  w.els["lpi-details-json"].textContent = "untouched";
  click({closest: () => null});
  click({});
  assert.equal(w.els["lpi-details-json"].textContent, "untouched");
  w.els["lpi-clear-btn"].listeners.get("click")[0]();
  assert.equal(w.els.lpiList.innerHTML, "");
  assert.equal(w.els["lpi-event-count"].textContent, "0");
  assert.match(w.els["lpi-summary-container"].innerHTML, /Waiting for signal activity/);
  w.els["lpi-run-sim-btn"].listeners.get("click")[0]();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(w.requests[0][0], /^\/api\/sensors\/s1\/process\/lpi$/);
  assert.ok(w.win.consoleMessages.some((m) => m[0] === "Starting LPI Simulation..." && m[1] === "response"));
  await workspace.deactivate("lpi");
  for (const id of ["lpi-clear-btn", "lpi-run-sim-btn", "lpiList"]) for (const handlers of w.els[id].listeners.values()) assert.equal(handlers.length, 0, id);
});

test("without the LPI panel's elements nothing throws", async () => {
  const w = lpiWindow({elements: []});
  const workspace = await lpiUp(w);
  w.win.EntitySync.ingest("CREATE", lpiEvent("waveform_classified", {snr_db: 5, classes: [{class: "fmcw", confidence: 1}]}));
  w.runFrames();
  await workspace.deactivate("lpi");
  const partial = lpiWindow({elements: ["lpiList", "lpi-summary-container"]});
  const w2 = await lpiUp(partial);
  partial.win.EntitySync.ingest("CREATE", lpiEvent("tf_computed", {snr_db: 5}));
  partial.runFrames();
  assert.equal(partial.els["lpi-summary-container"].children.length, 1);
  partial.els["lpiList"].listeners.get("click")[0]({target: {closest: () => ({getAttribute: () => "0"})}});   // no details panel: skipped
  await w2.deactivate("lpi");
});

test("a count element without a feed is left alone: nothing is drawn for a missing feed", async () => {
  const w = lpiWindow({elements: ["lpi-event-count", "lpi-summary-container"]});
  const workspace = await lpiUp(w);
  w.win.EntitySync.ingest("CREATE", lpiEvent("tf_computed", {snr_db: 5}));
  w.runFrames();
  assert.equal(w.els["lpi-event-count"].textContent, "");
  assert.equal(w.els["lpi-summary-container"].children.length, 1);
  await workspace.deactivate("lpi");
});

test("the LPI globe animates a pulse that grows and fades, removes it when its time is up, and floats a label that times out", async () => {
  const w = lpiWindow();
  const workspace = await lpiUp(w);
  await withDate(5_000_000, async () => {
    w.win.EntitySync.ingest("CREATE", lpiEvent("waveform_classified", {snr_db: 5, classes: [{class: "fmcw", confidence: 1}]}));
  });
  const [pulse, label] = w.globe.entities.values;
  assert.equal(pulse.id, "lpi-pulse-s1-5000000");
  assert.deepEqual(pulse.position, {lon: -86, lat: 36, h: undefined});
  assert.equal(pulse.ellipse.height, 50.0);
  assert.equal(pulse.ellipse.semiMajorAxis.fn(), 100);
  assert.equal(pulse.ellipse.material.color.fn().alpha, 1 - 100 / 5000);
  assert.equal(pulse.ellipse.material.color.fn().name, "red");
  await withDate(5_000_500, async () => w.runFrames());   // a quarter of the way through its 2 s
  assert.equal(pulse.ellipse.semiMajorAxis.fn(), 100 + 5000 * 0.25);
  assert.equal(pulse.ellipse.semiMinorAxis.fn(), 100 + 5000 * 0.25);
  assert.ok(Math.abs(pulse.ellipse.material.color.fn().alpha - (1 - 1350 / 5000)) < 1e-9);
  await withDate(5_001_999, async () => w.runFrames());
  assert.ok(w.globe.entities.values.includes(pulse));
  await withDate(5_002_001, async () => w.runFrames());   // past 2 s: removed, and no further frame is asked for
  assert.ok(!w.globe.entities.values.includes(pulse));
  assert.equal(w.frames.size, 0);
  assert.equal(w.globe.entities.removed.length, 1);
  assert.equal(label.id.startsWith("lpi-label-s1-"), true);
  assert.deepEqual(label.position, {lon: -86, lat: 36, h: 2000});
  assert.equal(label.label.text, "⚠ fmcw");
  assert.deepEqual([label.label.font, label.label.fillColor.name, label.label.outlineColor.name, label.label.outlineWidth], ["16px Monospace", "red", "black", 2]);
  assert.equal(label.label.verticalOrigin, "bottom");
  assert.equal(label.label.pixelOffset.x, 0);
  assert.ok(label.label.pixelOffset.y <= -10 && label.label.pixelOffset.y > -60);
  assert.equal(w.timers[0].ms, 4000);
  w.timers[0].fn();
  assert.ok(!w.globe.entities.values.includes(label));
  assert.equal(w.globe.entities.removed.length, 2);
  await workspace.deactivate("lpi");
  await workspace.activate("lpi");
  await workspace.deactivate("lpi");
  assert.equal(w.globe.entities.removed.length, 2);   // what had already come down is not taken down again
});

test("a candidate pulses yellow without a label; the pulse stops growing at exactly its time", async () => {
  const w = lpiWindow();
  const workspace = await lpiUp(w);
  await withDate(1_000, async () => w.win.EntitySync.ingest("CREATE", lpiEvent("lpi_candidate_detected", {snr_db: 5})));
  assert.equal(w.globe.entities.values.length, 1);
  assert.equal(w.globe.entities.values[0].ellipse.material.color.fn().name, "yellow");
  assert.equal(w.timers.length, 0);
  await withDate(3_000, async () => w.runFrames());   // exactly 2000 ms: not past it, so it grows to its full size
  assert.equal(w.globe.entities.values[0].ellipse.semiMajorAxis.fn(), 5100);
  assert.equal(w.globe.entities.values[0].ellipse.material.color.fn().alpha, 0);
  await workspace.deactivate("lpi");
});

test("deactivating LPI stops the animations and removes every pulse and label; a late frame or timer does nothing", async () => {
  const w = lpiWindow();
  const workspace = await lpiUp(w);
  await withDate(1_000, async () => w.win.EntitySync.ingest("CREATE", lpiEvent("waveform_classified", {snr_db: 5, classes: [{class: "x", confidence: 1}]})));
  assert.equal(w.globe.entities.values.length, 2);
  await workspace.deactivate("lpi");
  assert.deepEqual(w.globe.entities.values, []);
  assert.equal(w.frames.size, 0);
  assert.equal(w.timers[0].live, false);
  w.timers[0].fn();   // a stale timer: the entity is already gone
});

test("a second deactivation does not take down again what the first one did", async () => {
  const w = lpiWindow();
  const workspace = await lpiUp(w);
  await withDate(1_000, async () => w.win.EntitySync.ingest("CREATE", lpiEvent("waveform_classified", {snr_db: 5, classes: [{class: "x", confidence: 1}]})));
  await workspace.deactivate("lpi");
  assert.equal(w.globe.entities.removed.length, 2);   // the pulse and the label
  await workspace.activate("lpi");
  await workspace.deactivate("lpi");
  assert.equal(w.globe.entities.removed.length, 2);
});

test("without a viewer LPI draws no pulse; if the viewer goes away mid-animation the frame and timer still do not throw", async () => {
  const none = lpiWindow({viewer: false});
  const w1 = await lpiUp(none);
  none.win.EntitySync.ingest("CREATE", lpiEvent("waveform_classified", {snr_db: 5, classes: [{class: "x", confidence: 1}]}));
  none.runFrames();
  await w1.deactivate("lpi");
  const gone = lpiWindow();
  const w2 = await lpiUp(gone);
  await withDate(1_000, async () => gone.win.EntitySync.ingest("CREATE", lpiEvent("waveform_classified", {snr_db: 5, classes: [{class: "x", confidence: 1}]})));
  gone.win.viewer = undefined;
  await withDate(9_000, async () => gone.runFrames());
  gone.timers[0].fn();
  await w2.deactivate("lpi");
});

test("the LPI tool reads the Sensors panel when it needs it: no SensorManager means no pulse and the simulation uses SENSOR-TEST", async () => {
  const w = lpiWindow();
  const workspace = bootWorkspace(w.win);
  await workspace.activate("lpi");
  await workspace.activate("entity-sync");
  w.win.EntitySync.ingest("CREATE", lpiEvent("lpi_candidate_detected", {snr_db: 5}));
  assert.deepEqual(w.globe.entities.values, []);
  w.els["lpi-run-sim-btn"].listeners.get("click")[0]();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(w.requests[0][0], "/api/sensors/SENSOR-TEST/process/lpi");
  w.win.SensorManager = {sensors: {s1: {}}, flyTo: (id) => (w.flown ||= []).push(id)};
  w.els["lpi-run-sim-btn"].listeners.get("click")[0]();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(w.flown, ["s1"]);   // the simulation flies the camera through the Sensors panel
  w.win.SensorManager = {sensors: {s1: {}}, flyTo: undefined};   // no flyTo: skipped, not thrown
  w.els["lpi-run-sim-btn"].listeners.get("click")[0]();
  await new Promise((resolve) => setImmediate(resolve));
});

test("the old inline LPIManager is gone; the boot module activates the tool; nothing assigns window.LPIManager", () => {
  assert.doesNotMatch(page, /const LPIManager = \{/);
  assert.doesNotMatch(page, /window\.LPIManager\s*=/);
  assert.doesNotMatch(page, /LPIManager\.init\(\)/);
  assert.match(page, /workspace\.activate\("lpi"\)/);
});

// --- operator presence ---

function presenceWindow({operators = [], myId = "me", viewer = true, list = true} = {}) {
  const globe = fakeCesium();
  const els = list ? {"operator-presence-list": panelElement()} : {};
  const intervals = [], errors = [];
  const win = fakeWindow({
    setInterval: (fn, ms) => { intervals.push({fn, ms, live: true}); return intervals.length - 1; }, clearInterval: (id) => { if (intervals[id]) intervals[id].live = false; },
    setTimeout: () => 1, clearTimeout() {},
    console: {warn() {}, log() {}, debug() {}, error: (...a) => errors.push(a)},
    document: {getElementById: (id) => els[id] || null}, Cesium: globe.Cesium,
    OperatorSession: {operatorId: myId, getActiveOperators: async () => operators},
  });
  return {win, els, globe, intervals, errors, viewer: viewer ? globe.viewer : undefined};
}
const presenceOps = [
  {operator_id: "me", callsign: "ME", role: "admin", current_view: {center: [1, 2]}},
  {operator_id: "a", callsign: "ALPHA", role: "supervisor", current_view: {center: [36, -86]}},
  {operator_id: "b", callsign: "BRAVO", role: "observer", current_view: {center: [10, 20]}},
];
const flushAll = async () => { for (let i = 0; i < 4; i++) await new Promise((resolve) => setImmediate(resolve)); };

test("booting registers presence inactive and exposes what the page's init and the Operators panel's inline handlers call", () => {
  const {win} = presenceWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("presence"), "inactive");
  assert.equal(typeof win.OperatorPresence.initialize, "function");
  assert.equal(typeof win.OperatorPresence.toggleVisibility, "function");
  assert.equal(typeof win.OperatorPresence.fetchActiveOperators, "function");
  assert.deepEqual(win.OperatorPresence.operators, {});
});

test("the page's inline handlers and init call exist as the tool expects them", () => {
  assert.match(page, /id="show-operator-presence" checked onchange="OperatorPresence\.toggleVisibility\(this\.checked\)"/);
  assert.match(page, /onclick="OperatorPresence\.fetchActiveOperators\(\)"/);
  assert.match(page, /OperatorPresence\.initialize\(viewer\);/);
  assert.match(page, /id="operator-presence-list"/);
  assert.match(page, /Object\.keys\(OperatorPresence\.operators\)\.length/);
});

test("operator presence draws the list into the panel and one marker per other operator, replacing them on each refresh", async () => {
  const w = presenceWindow({operators: presenceOps});
  const workspace = bootWorkspace(w.win);
  await workspace.activate("presence");
  w.win.OperatorPresence.initialize(w.viewer);
  await flushAll();
  assert.match(w.els["operator-presence-list"].innerHTML, /ME \(You\)/);
  assert.equal((w.els["operator-presence-list"].innerHTML.match(/operator-presence-item/g) || []).length, 3);
  assert.equal(w.intervals.length, 1);
  assert.equal(w.intervals[0].ms, 10000);
  const [first, second] = w.globe.entities.values;
  assert.equal(w.globe.entities.values.length, 2);
  assert.equal(first.id, "operator_presence_a");
  assert.deepEqual(first.position, {lon: -86, lat: 36, h: 0});
  assert.deepEqual([first.point.pixelSize, first.point.color.name, first.point.outlineColor.name, first.point.outlineWidth], [12, "orange", "white", 2]);
  assert.equal(first.label.text, "👤 ALPHA");
  assert.deepEqual([first.label.font, first.label.fillColor.name, first.label.outlineColor.name, first.label.outlineWidth], ["12px sans-serif", "white", "black", 2]);
  assert.equal(first.label.style, "fill");
  assert.equal(first.label.verticalOrigin, "bottom");
  assert.deepEqual([first.label.pixelOffset.x, first.label.pixelOffset.y], [0, -15]);
  assert.equal(second.id, "operator_presence_b");
  assert.equal(second.point.color.name, "gray");
  w.intervals[0].fn();   // the next poll replaces the markers rather than stacking them
  await flushAll();
  assert.equal(w.globe.entities.values.length, 2);
  assert.equal(w.globe.entities.removed.length, 2);
});

test("operator presence escapes callsigns and roles in the panel's list", async () => {
  const evil = "<img src=x onerror=alert(1)>";
  const w = presenceWindow({operators: [{operator_id: "x", callsign: evil, role: evil}]});
  const workspace = bootWorkspace(w.win);
  await workspace.activate("presence");
  await w.win.OperatorPresence.fetchActiveOperators();
  assert.doesNotMatch(w.els["operator-presence-list"].innerHTML, /<img/);
  assert.match(w.els["operator-presence-list"].innerHTML, /&lt;img/);
});

test("operator presence reads the operator session when it asks: who is me, and the list; a missing session is logged, not thrown", async () => {
  const w = presenceWindow({operators: presenceOps, myId: "a"});
  const workspace = bootWorkspace(w.win);
  await workspace.activate("presence");
  w.win.OperatorSession.operatorId = "b";   // read at call time
  w.win.OperatorPresence.initialize(w.viewer);
  await flushAll();
  assert.deepEqual(w.globe.entities.values.map((e) => e.id), ["operator_presence_me", "operator_presence_a"]);
  w.win.OperatorSession = undefined;
  await w.win.OperatorPresence.fetchActiveOperators();
  assert.equal(w.errors[0][0], "[OperatorPresence] Error fetching operators:");
});

test("the Operators panel's checkbox: off takes the markers down and keeps them down through a refresh, on puts them back; its handlers work without a viewer or a list", async () => {
  const w = presenceWindow({operators: presenceOps});
  const workspace = bootWorkspace(w.win);
  await workspace.activate("presence");
  w.win.OperatorPresence.initialize(w.viewer);
  await flushAll();
  assert.equal(w.globe.entities.values.length, 2);
  w.win.OperatorPresence.toggleVisibility(false);
  assert.equal(w.globe.entities.values.length, 0);
  await w.win.OperatorPresence.fetchActiveOperators();
  assert.equal(w.globe.entities.values.length, 0);
  w.win.OperatorPresence.toggleVisibility(true);
  assert.equal(w.globe.entities.values.length, 2);
  const bare = presenceWindow({operators: presenceOps, list: false});
  const w2 = bootWorkspace(bare.win);
  await w2.activate("presence");
  await bare.win.OperatorPresence.fetchActiveOperators();
  bare.win.OperatorPresence.toggleVisibility(true);
  assert.equal(bare.globe.entities.values.length, 0);   // no viewer handed over
});

test("deactivating operator presence stops the polling and takes the markers down; the viewer stays attached", async () => {
  const w = presenceWindow({operators: presenceOps});
  const workspace = bootWorkspace(w.win);
  await workspace.activate("presence");
  w.win.OperatorPresence.initialize(w.viewer);
  await flushAll();
  assert.equal(w.globe.entities.values.length, 2);
  await workspace.deactivate("presence");
  assert.deepEqual(w.globe.entities.values, []);
  assert.equal(w.intervals[0].live, false);
  assert.deepEqual(w.win.OperatorPresence.operators, {});
  await workspace.activate("presence");
  await workspace.deactivate("presence");
  assert.equal(w.globe.entities.removed.length, 2);   // nothing is taken down twice
});

test("a marker that something else already removed is not removed again", async () => {
  const w = presenceWindow({operators: presenceOps});
  const workspace = bootWorkspace(w.win);
  await workspace.activate("presence");
  w.win.OperatorPresence.initialize(w.viewer);
  await flushAll();
  w.globe.entities.remove(w.globe.entities.values[0]);
  const before = w.globe.entities.removed.length;
  w.win.OperatorPresence.fetchActiveOperators();
  await flushAll();
  assert.equal(w.globe.entities.removed.length - before, 1);   // only the one still showing
});

test("the old inline OperatorPresence is gone; the boot module activates the tool", () => {
  assert.doesNotMatch(page, /const OperatorPresence = \{/);
  assert.doesNotMatch(page, /_createPresenceIndicator|updatePresenceIndicators/);
  assert.match(page, /workspace\.activate\("presence"\)/);
});
