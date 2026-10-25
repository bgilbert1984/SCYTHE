import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {bootKeepingSession as bootWorkspace} from "./bootForTest.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}, info() {}};
const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

class FakeSocket {
  constructor(url, options) { this.url = url; this.options = options; this.handlers = {}; }
  on(event, fn) { (this.handlers[event] ||= []).push(fn); }
  trigger(event, data) { for (const fn of this.handlers[event] || []) fn(data); }
  disconnect() { this.trigger("disconnect"); }
}

function socketWindow({io = true, status = {components: {rooms: {websocket_available: true}}}, token = "tok", bootstrap} = {}) {
  const sockets = [], messages = [], feed = [], rooms = [];
  const win = {
    console: quiet, Event: class { constructor(type) { this.type = type; } }, dispatchEvent() {},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    location: {origin: "https://scythe.example"}, __SCYTHE_BOOTSTRAP__: bootstrap,
    OperatorSession: {sessionToken: token}, addConsoleMessage: (text, kind) => messages.push([text, kind]),
    fetch: async () => ({ok: true, json: async () => status}),
  };
  if (io) win.io = (url, options) => { const s = new FakeSocket(url, options); sockets.push(s); return s; };
  return {win, sockets, messages, feed, rooms};
}

test("booting registers the websocket tool inactive and sets WebSocketSync to its controls", () => {
  const {win} = socketWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("websocket-sync"), "inactive");
  for (const name of ["init", "connect", "disconnect", "on", "off"]) assert.equal(typeof win.WebSocketSync[name], "function", name);
  for (const gone of ["send", "joinRoom", "leaveRoom", "createRoom", "publishEntity", "sendMessage", "sendHeartbeat"]) assert.equal(gone in win.WebSocketSync, false, gone);
});

test("the page's socket.io, origin, bootstrap record and session token are what the tool uses", async () => {
  const {win, sockets} = socketWindow({bootstrap: {path_prefix: "/i/x", socketio_path: "/i/x/socket.io"}, token: "t k"});
  const workspace = bootWorkspace(win);
  await workspace.activate("websocket-sync");
  await win.WebSocketSync.init();
  assert.equal(win.WebSocketSync.useWebSocket, true);
  win.WebSocketSync.connect();
  assert.equal(sockets[0].url, "https://scythe.example?token=t%20k");
  assert.deepEqual(sockets[0].options, {path: "/i/x/socket.io", transports: ["polling"], upgrade: false, query: {token: "t k"}});
  assert.equal(win.WebSocketSync.socket, sockets[0]);
});

test("without socket.io the tool falls back to EntitySync's stream, through the real EntitySync controls", async () => {
  const {win} = socketWindow({io: false});
  const workspace = bootWorkspace(win);
  const asked = [];
  win.EntitySync.connect = () => asked.push("connect");   // the boot module's EntitySync controls, read at call time
  await workspace.activate("websocket-sync");
  await win.WebSocketSync.init();
  win.WebSocketSync.connect();
  assert.deepEqual(asked, ["connect"]);
});

test("entity events reach EntitySync's ingest and the room events reach the room manager's controls", async () => {
  const {win, sockets, messages} = socketWindow();
  const workspace = bootWorkspace(win);
  const ingested = [], rooms = [];
  win.EntitySync.ingest = (type, data) => ingested.push([type, data.entity_id]);
  win.RoomManager.fetchMyRooms = () => rooms.push("mine");
  win.RoomManager.fetchRooms = () => rooms.push("all");
  win.RoomManager.setRooms = (list) => rooms.push(["set", list.length]);
  await workspace.activate("websocket-sync");
  await win.WebSocketSync.init();
  win.WebSocketSync.connect();
  sockets[0].trigger("entity_event", {event_type: "UPDATE", entity_id: "e1"});
  sockets[0].trigger("room_joined", {}); sockets[0].trigger("room_created", {}); sockets[0].trigger("rooms_list", {rooms: [{}, {}]});
  sockets[0].trigger("connect");
  assert.deepEqual(ingested, [["UPDATE", "e1"]]);
  assert.deepEqual(rooms, ["mine", "all", ["set", 2]]);
  assert.deepEqual(messages, [["🔌 WebSocket connected", "response"]]);
});

test("what the ADS-B and AIS tools' services read follows the tool: the socket, whether it is connected, and a connected event", async () => {
  const {win, sockets} = socketWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("websocket-sync");
  await win.WebSocketSync.init();
  win.WebSocketSync.connect();
  const heard = [];
  win.WebSocketSync.on("connected", () => heard.push("connected"));
  sockets[0].trigger("connect");
  assert.deepEqual(heard, ["connected"]);
  assert.equal(win.WebSocketSync.connected, true);
  assert.equal(win.WebSocketSync.socket, sockets[0]);
});

test("deactivating closes the socket", async () => {
  const {win, sockets} = socketWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("websocket-sync");
  await win.WebSocketSync.init();
  win.WebSocketSync.connect();
  const heard = [];
  win.WebSocketSync.on("disconnected", () => heard.push(1));
  await workspace.deactivate("websocket-sync");
  assert.deepEqual(heard, []);
  assert.equal(win.WebSocketSync.socket, null);
});

// --- the page ---

test("the page has no WebSocketSync object of its own; its init uses the controls", () => {
  assert.equal(/const WebSocketSync = \{/.test(page), false);
  assert.equal(page.includes("window.WebSocketSync = WebSocketSync"), false);
  assert.match(page, /WebSocketSync\.init\(\);/);
  assert.equal(/WebSocketSync\.(?:send|joinRoom|publishEntity|sendHeartbeat)\b/.test(page), false);
  assert.match(page, /workspace\.activate\("websocket-sync"\)/);
});

test("the tool is activated after EntitySync and the rooms tool, whose controls it uses", () => {
  const at = (id) => page.indexOf(`workspace.activate("${id}")`);
  assert.ok(at("entity-sync") > 0 && at("rooms") > 0 && at("websocket-sync") > at("rooms"));
  assert.ok(at("websocket-sync") > at("entity-sync"));
});
