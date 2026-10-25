import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createWebSocketSyncPlugin, WEBSOCKET_SYNC_ID} from "./websocketSyncPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };

/** A socket.io client stand-in: `on` registers, `trigger` is the server speaking, and `disconnect()` fires the disconnect event at once, as socket.io does. */
class FakeSocket {
  constructor(url, options) { this.url = url; this.options = options; this.handlers = {}; this.closed = 0; }
  on(event, fn) { (this.handlers[event] ||= []).push(fn); }
  trigger(event, data) { for (const fn of this.handlers[event] || []) fn(data); }
  disconnect() { this.closed++; this.trigger("disconnect"); }
}

function setup({status = {components: {rooms: {websocket_available: true}}}, token = "tok", ioLoaded = true, bootstrap, origin = "https://scythe.example"} = {}) {
  const sockets = [], calls = {feed: [], rooms: [], messages: [], requests: []};
  const workspace = createWorkspace({log: quiet});
  let statusReply = status;
  workspace.provide("httpFetch", async (url) => {
    calls.requests.push(url);
    const reply = await (typeof statusReply === "function" ? statusReply() : statusReply);
    return {json: async () => { if (reply instanceof Error) throw reply; return reply; }};
  });
  let currentToken = token;
  workspace.provide("operatorSession", {token: () => currentToken, clear() {}});
  workspace.provide("socketClient", {available: () => ioLoaded, open: (url, options) => { const s = new FakeSocket(url, options); sockets.push(s); return s; }});
  workspace.provide("realtimeEnv", {origin: () => origin, bootstrap: () => bootstrap});
  workspace.provide("entityFeed", {connect: () => calls.feed.push("connect"), ingest: (type, data) => calls.feed.push(["ingest", type, data])});
  workspace.provide("roomSync", {refreshMine: () => calls.rooms.push("mine"), refreshAll: () => calls.rooms.push("all"), setRooms: (list) => calls.rooms.push(["set", list])});
  workspace.provide("realtimeConsole", {message: (text, kind) => calls.messages.push([text, kind])});
  const logs = {warn: [], error: [], log: []};
  const log = {...quiet, warn: (...a) => logs.warn.push(a.join(" ")), error: (...a) => logs.error.push(a.join(" ")), log: (...a) => logs.log.push(a.join(" "))};
  const plugin = createWebSocketSyncPlugin({log});
  workspace.register(plugin.tool);
  return {workspace, sockets, calls, logs, controls: plugin.controls, setToken: (t) => { currentToken = t; }, setStatus: (s) => { statusReply = s; }};
}
async function started(options) { const s = setup(options); await s.workspace.activate(WEBSOCKET_SYNC_ID); await s.controls.init(); return s; }

// --- inactive ---

test("while inactive there is no socket, nothing connects, and init is remembered for activation", async () => {
  const s = setup();
  assert.deepEqual([s.controls.socket, s.controls.connected, s.controls.useWebSocket], [null, false, false]);
  s.controls.connect(); s.controls.disconnect();
  assert.deepEqual([s.calls.feed, s.sockets.length], [[], 0]);
  await s.controls.init();
  assert.deepEqual(s.calls.requests, [], "nothing is asked while inactive");
  await s.workspace.activate(WEBSOCKET_SYNC_ID);
  await settle();
  assert.deepEqual(s.calls.requests, ["/api/status"]);
  assert.equal(s.controls.useWebSocket, true);
});

test("init asked for once is made once, not on every later activation", async () => {
  const s = setup();
  await s.controls.init();
  await s.workspace.activate(WEBSOCKET_SYNC_ID);
  await s.workspace.deactivate(WEBSOCKET_SYNC_ID);
  await s.workspace.activate(WEBSOCKET_SYNC_ID);
  await settle();
  assert.equal(s.calls.requests.length, 1);
});

// --- init ---

test("init learns from the server whether it offers WebSockets", async () => {
  const yes = await started();
  assert.equal(yes.controls.useWebSocket, true);
  assert.deepEqual(yes.calls.requests, ["/api/status"]);
  for (const status of [{components: {rooms: {websocket_available: false}}}, {components: {rooms: {}}}, {components: {}}, {}]) {
    const no = await started({status});
    assert.equal(no.controls.useWebSocket, false, JSON.stringify(status));
  }
});

test("a failing status request means no WebSockets, and says so; a later failure undoes an earlier yes", async () => {
  const s = await started({status: () => { throw new Error("offline"); }});
  assert.equal(s.controls.useWebSocket, false);
  assert.match(s.logs.warn[0], /Could not detect WebSocket support/);
  const t = await started();
  assert.equal(t.controls.useWebSocket, true);
  t.setStatus(new Error("bad json"));
  await t.controls.init();
  assert.equal(t.controls.useWebSocket, false);
});

test("what the server offers is kept across a deactivation", async () => {
  const s = await started();
  await s.workspace.deactivate(WEBSOCKET_SYNC_ID);
  assert.equal(s.controls.useWebSocket, true);
});

test("an answer to init that arrives after deactivation changes nothing", async () => {
  const g = gate();
  const s = setup({status: () => g.promise});
  await s.workspace.activate(WEBSOCKET_SYNC_ID);
  const pending = s.controls.init();
  await settle();
  await s.workspace.deactivate(WEBSOCKET_SYNC_ID);
  g.open({components: {rooms: {websocket_available: true}}});
  await pending;
  assert.equal(s.controls.useWebSocket, false);
});

test("a failure of init that arrives after deactivation says nothing", async () => {
  const g = gate();
  const s = setup({status: () => g.promise});
  await s.workspace.activate(WEBSOCKET_SYNC_ID);
  const pending = s.controls.init();
  await settle();
  await s.workspace.deactivate(WEBSOCKET_SYNC_ID);
  g.open(new Error("late"));
  await pending;
  assert.deepEqual(s.logs.warn, []);
});

// --- connecting ---

test("without WebSockets on offer, or a session, or socket.io, connect asks EntitySync's stream to connect instead", async () => {
  const none = await started({status: {}});
  none.controls.connect();
  assert.deepEqual([none.calls.feed, none.sockets.length], [["connect"], 0]);
  assert.match(none.logs.log.at(-1), /Using SSE fallback/);
  const noSession = await started({token: null});
  noSession.controls.connect();
  assert.deepEqual([noSession.calls.feed, noSession.sockets.length], [["connect"], 0]);
  const noIo = await started({ioLoaded: false});
  noIo.controls.connect();
  assert.deepEqual([noIo.calls.feed, noIo.sockets.length], [["connect"], 0]);
  assert.match(noIo.logs.warn.at(-1), /socket\.io not loaded, falling back to SSE/);
});

test("connecting opens a socket with the token in the URL and the query, websocket first with polling as the fallback", async () => {
  const s = await started({token: "a b&c"});
  s.controls.connect();
  assert.equal(s.sockets.length, 1);
  assert.equal(s.sockets[0].url, "https://scythe.example?token=a%20b%26c");
  assert.deepEqual(s.sockets[0].options, {path: "/socket.io", transports: ["websocket", "polling"], upgrade: true, query: {token: "a b&c"}});
  assert.equal(s.controls.socket, s.sockets[0]);
  assert.deepEqual(s.calls.feed, []);
});

test("behind a path prefix the socket uses the bootstrap's socket.io path, polling only, with no upgrade", async () => {
  const s = await started({bootstrap: {path_prefix: "/i/abc", socketio_path: "/i/abc/socket.io"}});
  s.controls.connect();
  assert.deepEqual(s.sockets[0].options, {path: "/i/abc/socket.io", transports: ["polling"], upgrade: false, query: {token: "tok"}});
  const plain = await started({bootstrap: {socketio_path: "/s/socket.io"}});
  plain.controls.connect();
  assert.deepEqual([plain.sockets[0].options.path, plain.sockets[0].options.transports], ["/s/socket.io", ["websocket", "polling"]]);
  const prefixOnly = await started({bootstrap: {path_prefix: "/x"}});
  prefixOnly.controls.connect();
  assert.equal(prefixOnly.sockets[0].options.path, "/socket.io");
});

test("the socket's connect and disconnect events set connected, say so, and tell the subscribers", async () => {
  const s = await started();
  const heard = [];
  s.controls.on("connected", () => heard.push("connected")).on("disconnected", () => heard.push("disconnected"));
  s.controls.connect();
  s.sockets[0].trigger("connect");
  assert.equal(s.controls.connected, true);
  assert.deepEqual(s.calls.messages, [["🔌 WebSocket connected", "response"]]);
  s.sockets[0].trigger("disconnect");
  assert.equal(s.controls.connected, false);
  assert.deepEqual(heard, ["connected", "disconnected"]);
});

test("an error from the socket reaches the subscribers, and the server's greeting is only logged", async () => {
  const s = await started();
  const heard = [];
  s.controls.on("error", (d) => heard.push(d));
  s.controls.connect();
  const error = new Error("boom");
  s.sockets[0].trigger("error", error);
  assert.deepEqual(heard, [{error}]);
  assert.match(s.logs.error[0], /\[WebSocketSync\] Error:/);
  s.sockets[0].trigger("connected", {operator: "x"});
  assert.match(s.logs.log.at(-1), /Authenticated:/);
});

test("an entity event goes to EntitySync, then to the handlers for its type (lower case) and for entity", async () => {
  const s = await started();
  const heard = [];
  s.controls.on("create", (d) => heard.push(["create", d])).on("entity", (d) => heard.push(["entity", d])).on("update", (d) => heard.push(["update", d]));
  s.controls.connect();
  const data = {event_type: "CREATE", entity_id: "a"};
  s.sockets[0].trigger("entity_event", data);
  assert.deepEqual(s.calls.feed, [["ingest", "CREATE", data]]);
  assert.deepEqual(heard, [["create", data], ["entity", {eventType: "CREATE", event_type: "CREATE", entity_id: "a"}]]);
});

test("an entity event with no type reaches EntitySync and then throws, so the handlers are not told", async () => {
  const s = await started();
  const heard = [];
  s.controls.on("entity", (d) => heard.push(d));
  s.controls.connect();
  assert.throws(() => s.sockets[0].trigger("entity_event", {entity_id: "a"}), TypeError);
  assert.deepEqual(s.calls.feed, [["ingest", undefined, {entity_id: "a"}]]);
  assert.deepEqual(heard, []);
});

test("the room events refresh the room lists they concern, and a list of rooms replaces the available rooms", async () => {
  const s = await started();
  s.controls.connect();
  s.sockets[0].trigger("room_joined", {room_id: "r"});
  s.sockets[0].trigger("room_left", {room_id: "r"});
  s.sockets[0].trigger("room_created", {room_id: "r"});
  s.sockets[0].trigger("rooms_list", {rooms: [{room_id: "r1"}]});
  s.sockets[0].trigger("rooms_list", {});
  s.sockets[0].trigger("heartbeat_ack", {});
  assert.deepEqual(s.calls.rooms, ["mine", "mine", "all", ["set", [{room_id: "r1"}]]]);
  assert.match(s.logs.log.join("\n"), /Room joined[\s\S]*Room left[\s\S]*Room created/);
});

test("connecting again closes the first socket, which tells the subscribers it disconnected, and opens another", async () => {
  const s = await started();
  const heard = [];
  s.controls.on("disconnected", () => heard.push("disconnected"));
  s.controls.connect();
  s.sockets[0].trigger("connect");
  s.controls.connect();
  assert.equal(s.sockets[0].closed, 1);
  assert.deepEqual(heard, ["disconnected"]);
  assert.equal(s.sockets.length, 2);
  assert.equal(s.controls.socket, s.sockets[1]);
  assert.equal(s.controls.connected, false);
});

test("disconnecting closes the socket, forgets it, and tells the subscribers; with no socket it just says not connected", async () => {
  const s = await started();
  const heard = [];
  s.controls.on("disconnected", () => heard.push(1));
  s.controls.connect();
  s.sockets[0].trigger("connect");
  s.controls.disconnect();
  assert.equal(s.sockets[0].closed, 1);
  assert.deepEqual([s.controls.socket, s.controls.connected, heard], [null, false, [1]]);
  s.controls.disconnect();
  assert.deepEqual([s.controls.socket, s.controls.connected, heard], [null, false, [1]]);
});

test("disconnecting says not connected even if the socket does not announce its own disconnect", async () => {
  const s = await started();
  s.controls.connect();
  s.sockets[0].trigger("connect");
  s.sockets[0].disconnect = () => { s.sockets[0].closed++; };   // a socket that closes without the event
  s.controls.disconnect();
  assert.equal(s.controls.connected, false);
});

// --- handlers ---

test("handlers are called in order, one that throws does not stop the others and is reported with the tool's name, and off removes one; on and off return the controls", async () => {
  const s = await started();
  const order = [];
  const first = () => { order.push("first"); throw new Error("boom"); };
  assert.equal(s.controls.on("connected", first), s.controls);
  s.controls.on("connected", () => order.push("second"));
  s.controls.connect();
  s.sockets[0].trigger("connect");
  assert.deepEqual(order, ["first", "second"]);
  assert.match(s.logs.error.at(-1), /\[WebSocketSync\] Handler error for connected/);
  assert.equal(s.controls.off("connected", first), s.controls);
  s.sockets[0].trigger("connect");
  assert.deepEqual(order, ["first", "second", "second"]);
});

test("handlers outlive an activation: one registered before the tool is active hears it afterwards", async () => {
  const s = setup();
  const heard = [];
  s.controls.on("connected", () => heard.push("early"));
  await s.workspace.activate(WEBSOCKET_SYNC_ID);
  await s.controls.init();
  s.controls.connect();
  s.sockets[0].trigger("connect");
  await s.workspace.deactivate(WEBSOCKET_SYNC_ID);
  await s.workspace.activate(WEBSOCKET_SYNC_ID);
  s.controls.connect();
  s.sockets[1].trigger("connect");
  assert.deepEqual(heard, ["early", "early"]);
});

// --- deactivating ---

test("deactivating closes the socket quietly: the subscribers are not told, and a late event from it does nothing", async () => {
  const s = await started();
  const heard = [];
  s.controls.on("disconnected", () => heard.push("disconnected")).on("connected", () => heard.push("connected")).on("entity", () => heard.push("entity"));
  s.controls.connect();
  s.sockets[0].trigger("connect");
  heard.length = 0; s.calls.messages.length = 0;
  const errors = await s.workspace.deactivate(WEBSOCKET_SYNC_ID);
  assert.deepEqual(errors, []);
  assert.equal(s.sockets[0].closed, 1);
  assert.deepEqual(heard, []);
  s.sockets[0].trigger("connect");
  s.sockets[0].trigger("entity_event", {event_type: "CREATE"});
  s.sockets[0].trigger("room_joined", {});
  s.sockets[0].trigger("rooms_list", {rooms: []});
  s.sockets[0].trigger("error", new Error("x"));
  assert.deepEqual([heard, s.calls.messages, s.calls.feed, s.calls.rooms], [[], [], [], []]);
});

test("after deactivation the controls are inert and the state is empty; a new activation can connect again", async () => {
  const s = await started();
  s.controls.connect();
  await s.workspace.deactivate(WEBSOCKET_SYNC_ID);
  assert.deepEqual([s.controls.socket, s.controls.connected], [null, false]);
  s.controls.connect();
  assert.equal(s.sockets.length, 1);
  await s.workspace.activate(WEBSOCKET_SYNC_ID);
  s.controls.connect();
  assert.equal(s.sockets.length, 2, "what the server offers was kept, so it opens a socket");
});
