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
