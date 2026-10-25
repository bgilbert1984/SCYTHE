import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {bootWorkspace} from "./workspaceBoot.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}, info() {}};
const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

function roomWindow({ids = ["available-rooms-list", "joined-rooms-list", "current-room-name"], replies = {}, token = "tok", console: consoleOverride} = {}) {
  const els = Object.fromEntries(ids.map((id) => [id, {innerHTML: "", textContent: ""}]));
  const requests = [], messages = [];
  const win = {
    console: consoleOverride || quiet, Event: class { constructor(type) { this.type = type; } }, dispatchEvent() {},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    document: {getElementById: (id) => els[id] || null},
    OperatorSession: {sessionToken: token},
    addConsoleMessage: (text, kind) => messages.push([text, kind]),
    fetch: async (url, init = {}) => {
      const key = `${init.method || "GET"} ${url}`;
      requests.push([key, init.headers]);
      if (!(key in replies)) throw new Error(`no reply for ${key}`);
      return {ok: true, json: async () => replies[key]};
    },
  };
  return {win, els, requests, messages};
}

test("booting registers the rooms tool inactive and sets RoomManager to its controls", () => {
  const {win} = roomWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("rooms"), "inactive");
  for (const name of ["fetchRooms", "fetchMyRooms", "createRoom", "joinRoom", "leaveRoom", "setCurrentRoom", "setRooms", "reset", "on", "off"]) assert.equal(typeof win.RoomManager[name], "function", name);
  for (const gone of ["getRoomDetails", "publishToRoom", "sendMessage"]) assert.equal(gone in win.RoomManager, false, gone);
});

test("the widgets are drawn into the page's elements: the two lists and the current room's name", async () => {
  const replies = {
    "GET /api/rooms": {status: "ok", rooms: [{room_id: "r1", room_name: "Ops", room_type: "mission", capacity: 10}]},
    "GET /api/rooms/my": {status: "ok", rooms: [{room_id: "r1", room_name: "Ops", room_type: "mission"}]},
  };
  const {win, els} = roomWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("rooms");
  await win.RoomManager.fetchRooms();
  await win.RoomManager.fetchMyRooms();
  win.RoomManager.setCurrentRoom("r1");
  assert.match(els["available-rooms-list"].innerHTML, /<div class="room-name">Ops<\/div>/);
  assert.doesNotMatch(els["joined-rooms-list"].innerHTML, /joined active/, "PINNED: making a room current does not redraw the joined list");
  assert.equal(els["current-room-name"].textContent, "Ops");
  await win.RoomManager.fetchMyRooms();
  assert.match(els["joined-rooms-list"].innerHTML, /room-item joined active/);
  win.RoomManager.reset();
  assert.equal(els["joined-rooms-list"].innerHTML, '<div class="empty-message">Not in any rooms</div>');
  assert.equal(els["current-room-name"].textContent, "No room selected");
  win.RoomManager.setRooms([]);
  assert.equal(els["available-rooms-list"].innerHTML, '<div class="empty-message">No rooms available</div>');
});

test("the session token is the page's operator session's", async () => {
  const {win, requests} = roomWindow({replies: {"GET /api/rooms/my": {status: "ok", rooms: []}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("rooms");
  await win.RoomManager.fetchMyRooms();
  assert.deepEqual(requests, [["GET /api/rooms/my", {"X-Session-Token": "tok"}]]);
  win.OperatorSession.sessionToken = null;
  assert.deepEqual(await win.RoomManager.fetchMyRooms(), [], "a session that ended is noticed");
  assert.equal(requests.length, 1);
  delete win.OperatorSession;
  assert.deepEqual(await win.RoomManager.createRoom("x"), {success: false, message: "Not authenticated"});
});

test("a page without the widgets is fine, and the lists are not built for it", async () => {
  const errors = [];
  const replies = {"GET /api/rooms": {status: "ok", rooms: [{get room_id() { throw new Error("built"); }}]}};
  const {win} = roomWindow({ids: [], replies, console: {...quiet, error: (...a) => errors.push(a.join(" "))}});
  const workspace = bootWorkspace(win);
  await workspace.activate("rooms");
  win.RoomManager.setCurrentRoom("x");
  win.RoomManager.reset();
  await win.RoomManager.fetchRooms();
  assert.ok(errors.some((e) => /Error fetching rooms/.test(e)), "the room list itself is unreadable: that is the error");
  const lazy = roomWindow({ids: [], console: {...quiet, error: (...e) => errors.push("lazy:" + e.join(" "))}, replies: {"GET /api/rooms": {status: "ok", rooms: [{room_id: "r", get room_name() { throw new Error("built"); }}]}}});
  const w3 = bootWorkspace(lazy.win);
  await w3.activate("rooms");
  await lazy.win.RoomManager.fetchRooms();
  assert.deepEqual(errors.filter((e) => e.startsWith("lazy:")), [], "no element, so the list is not built, so nothing throws");
  const calm = roomWindow({ids: [], replies: {"GET /api/rooms": {status: "ok", rooms: [{room_id: "r", room_name: "n"}]}}});
  const w2 = bootWorkspace(calm.win);
  await w2.activate("rooms");
  assert.equal((await calm.win.RoomManager.fetchRooms()).length, 1);
});

test("console messages carrying a room's name are escaped, since the page puts them in as HTML", async () => {
  const replies = {"POST /api/rooms/create": {status: "ok", room: {room_id: "r"}}, "GET /api/rooms": {status: "ok", rooms: []}, "GET /api/rooms/my": {status: "ok", rooms: []}};
  const {win, messages} = roomWindow({replies});
  const workspace = bootWorkspace(win);
  await workspace.activate("rooms");
  await win.RoomManager.createRoom("<img src=x onerror=alert(1)>");
  assert.deepEqual(messages, [["🏠 Created room: &lt;img src=x onerror=alert(1)&gt;", "response"]]);
});

test("the chat tool is told when a room becomes current", async () => {
  const {win} = roomWindow();
  const workspace = bootWorkspace(win);
  await workspace.activate("rooms");
  const heard = [];
  win.RoomManager.on("current_room_changed", (d) => heard.push(d.roomId));
  win.RoomManager.setCurrentRoom("OPS");
  assert.deepEqual(heard, ["OPS"]);
});

// --- the page ---

test("the page has no RoomManager object of its own; the operator session's logout uses the controls", () => {
  assert.equal(/const RoomManager = \{/.test(page), false);
  assert.equal(page.includes("window.RoomManager = RoomManager"), false);
  assert.equal(/RoomManager\.(?:rooms|joinedRooms|currentRoomId)\s*=[^=]/.test(page), false, "nothing assigns into the manager");
  assert.match(page, /RoomManager\.reset\(\);/);
  for (const dead of ["getRoomDetails", "publishToRoom"]) assert.equal(page.includes(dead), false, dead);
});

test("the page activates the rooms tool", () => {
  assert.match(page, /workspace\.activate\("rooms"\)/);
});
