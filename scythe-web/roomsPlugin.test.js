import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {
  createRoomsPlugin, ROOMS_ID, roomTypeIcon, indexRooms, renderAvailableRooms, renderJoinedRooms, currentRoomLabel,
  NO_ROOMS, NOT_IN_ANY_ROOM, NO_ROOM_SELECTED,
} from "./roomsPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };

const EVIL = "<img src=x onerror=alert(1)>";
const ESCAPED = "&lt;img src=x onerror=alert(1)&gt;";
/** What the browser does with `attr="..."`: decode the entities, then the value is script. */
const decodeAttribute = (value) => value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
/** The arguments the inline handlers named `fn` are called with, after the browser has decoded the attribute. */
function handlerArguments(html, fn) {
  const calls = [];
  for (const match of html.matchAll(new RegExp(`onclick="RoomManager\\.${fn}\\(([^"]*)\\)"`, "g"))) new Function(fn, `${fn}(${decodeAttribute(match[1])})`)((arg) => calls.push(arg));
  return calls;
}

const R1 = {room_id: "r1", room_name: "Ops", room_type: "mission", member_count: 3, capacity: 10, entity_count: 7};
const R2 = {room_id: "r2", room_name: "Lounge", room_type: "global", capacity: 50, is_private: true};

// --- pure pieces ---

test("a room type has its icon, and any other a speech bubble, including names every object has", () => {
  assert.deepEqual(["global", "mission", "team", "geographic"].map(roomTypeIcon), ["🌐", "🎯", "👥", "📍"]);
  assert.deepEqual(["custom", undefined, "", "constructor", "toString", "__proto__"].map(roomTypeIcon), Array(6).fill("💬"));
});

test("rooms are indexed by id, a later room replacing an earlier one with the same id, and an id called __proto__ is just an id", () => {
  assert.deepEqual(Object.keys(indexRooms([R1, R2])), ["r1", "r2"]);
  assert.equal(indexRooms([R1, {...R1, room_name: "Again"}]).r1.room_name, "Again");
  const odd = indexRooms([{room_id: "__proto__", room_name: "x"}]);
  assert.deepEqual(Object.keys(odd), ["__proto__"]);
  assert.equal(Object.getPrototypeOf(odd), Object.prototype);
  assert.deepEqual(indexRooms([]), {});
});

test("the available list says so when empty", () => {
  assert.equal(renderAvailableRooms({}, {}), NO_ROOMS);
  assert.equal(NO_ROOMS, '<div class="empty-message">No rooms available</div>');
});

test("the available list has a row per room with its icon, name, lock, counts, and a Join button, or a Leave button once joined", () => {
  const html = renderAvailableRooms(indexRooms([R1, R2]), {r1: R1});
  const rows = html.split('<div class="room-item').slice(1);
  assert.equal(rows.length, 2);
  assert.match(rows[0], /^ joined" data-room-id="r1">/);
  assert.match(rows[0], /<div class="room-icon">🎯<\/div>/);
  assert.match(rows[0], /<div class="room-name">Ops<\/div>/);
  assert.match(rows[0], /3\/10 members/);
  assert.match(rows[0], /<button class="small-btn danger" onclick="RoomManager\.leaveRoom\(&quot;r1&quot;\)">Leave<\/button>/);
  assert.match(rows[1], /^ " data-room-id="r2">/);
  assert.match(rows[1], /<div class="room-name">Lounge 🔒<\/div>/);
  assert.match(rows[1], /0\/50 members/);
  assert.match(rows[1], /<button class="small-btn primary" onclick="RoomManager\.joinRoom\(&quot;r2&quot;\)">Join<\/button>/);
});

test("the available list shows names and counts as text, whatever they contain, and attributes cannot be broken out of", () => {
  const html = renderAvailableRooms(indexRooms([{room_id: EVIL, room_name: EVIL, room_type: "x", member_count: EVIL, capacity: EVIL}]), {});
  assert.doesNotMatch(html, /<img/);
  assert.equal(html.split(ESCAPED).length - 1, 5, "the id in its attribute, the name, the count, the capacity, and the id inside the handler");
});

test("the handlers are given exactly the room id, once the browser has decoded the attribute, whatever the id contains", () => {
  for (const id of ["r1", "x');alert(1);//", 'x");alert(1);//', "a\\b", "a\nb", "</script><b>", "o'brien", EVIL]) {
    const rooms = indexRooms([{room_id: id, room_name: "n"}]);
    assert.deepEqual(handlerArguments(renderAvailableRooms(rooms, {}), "joinRoom"), [id], JSON.stringify(id));
    assert.deepEqual(handlerArguments(renderAvailableRooms(rooms, {[id]: rooms[id]}), "leaveRoom"), [id], JSON.stringify(id));
    assert.deepEqual(handlerArguments(renderJoinedRooms(rooms, null), "setCurrentRoom"), [id], JSON.stringify(id));
  }
});

test("a number or missing id reaches the handlers as a string", () => {
  assert.deepEqual(handlerArguments(renderAvailableRooms({a: {room_id: 5, room_name: "n"}, b: {room_name: "n"}}, {}), "joinRoom"), ["5", "undefined"]);
});

test("a room is joined only if it is held under its own id: not because every object has a constructor", () => {
  const rooms = indexRooms([{room_id: "constructor", room_name: "n"}]);
  assert.match(renderAvailableRooms(rooms, {}), /joinRoom/);
  assert.doesNotMatch(renderAvailableRooms(rooms, {}), /leaveRoom/);
});

test("the joined list says so when empty, marks the current room active, and a click makes a room current", () => {
  assert.equal(renderJoinedRooms({}, null), NOT_IN_ANY_ROOM);
  assert.equal(NOT_IN_ANY_ROOM, '<div class="empty-message">Not in any rooms</div>');
  const html = renderJoinedRooms(indexRooms([R1, R2]), "r1");
  const rows = html.split('<div class="room-item').slice(1);
  assert.match(rows[0], /^ joined active" /);
  assert.match(rows[1], /^ joined " /);
  assert.match(rows[0], /data-room-id="r1"/);
  assert.match(rows[0], /onclick="RoomManager\.setCurrentRoom\(&quot;r1&quot;\)"/);
  assert.match(rows[0], /<div class="room-icon">🎯<\/div>/);
  assert.match(rows[0], /<div class="room-meta">7 entities<\/div>/);
  assert.match(rows[1], /<div class="room-meta">0 entities<\/div>/);
  assert.match(rows[1], /<div class="room-name">Lounge<\/div>/, "no lock in the joined list");
});

test("the joined list shows names as text", () => {
  const html = renderJoinedRooms(indexRooms([{room_id: "r", room_name: EVIL, entity_count: EVIL}]), null);
  assert.doesNotMatch(html, /<img/);
  assert.equal(html.split(ESCAPED).length - 1, 2);
});

test("the joined list's attributes cannot be broken out of by an id", () => {
  const html = renderJoinedRooms(indexRooms([{room_id: EVIL, room_name: "n"}]), null);
  assert.doesNotMatch(html, /<img/);
  assert.equal(html.split(ESCAPED).length - 1, 2, "in data-room-id and inside the handler");
});

test("the current room's label is its name, or says none is selected", () => {
  assert.equal(currentRoomLabel({r1: R1}, "r1"), "Ops");
  assert.equal(currentRoomLabel({r1: R1}, "r9"), NO_ROOM_SELECTED);
  assert.equal(currentRoomLabel({}, null), "No room selected");
  assert.equal(currentRoomLabel({}, "constructor"), NO_ROOM_SELECTED);
});

// --- the tool, against fakes ---

/** `replies` maps "METHOD url" (GET by default) to a function returning the JSON body, a Promise of it, or throwing. */
function setup({replies = {}, token = "tok"} = {}) {
  const requests = [], messages = [], views = [];
  let currentToken = token;
  const workspace = createWorkspace({log: quiet});
  workspace.provide("httpFetch", async (url, init = {}) => {
    const key = `${init.method || "GET"} ${url}`;
    requests.push({key, init});
    const reply = replies[key];
    if (!reply) throw new Error(`no reply for ${key}`);
    const body = await reply(init);
    return {json: async () => { if (body instanceof Error) throw body; return body; }};
  });
  workspace.provide("operatorSession", {token: () => currentToken, clear() {}});
  workspace.provide("roomView", {
    setAvailable: (html) => views.push(["available", html()]),
    setJoined: (html) => views.push(["joined", html()]),
    setCurrent: (text) => views.push(["current", text]),
  });
  workspace.provide("roomConsole", {message: (text, kind) => messages.push([text, kind])});
  const errors = [];
  const rooms = createRoomsPlugin({log: {...quiet, error: (...a) => errors.push(a.join(" "))}});
  workspace.register(rooms.tool);
  return {workspace, requests, messages, views, errors, controls: rooms.controls, setToken: (t) => { currentToken = t; }};
}
const ok = (extra) => ({status: "ok", ...extra});
const roomsReply = (list = [R1, R2]) => ({"GET /api/rooms": () => ok({rooms: list})});
const myReply = (list = [R1]) => ({"GET /api/rooms/my": () => ok({rooms: list})});
async function started(options = {}) { const s = setup(options); await s.workspace.activate(ROOMS_ID); return s; }

test("the controls do nothing while the tool is inactive", async () => {
  const s = setup();
  const c = s.controls;
  assert.deepEqual([c.rooms, c.joinedRooms, c.currentRoomId], [{}, {}, null]);
  assert.deepEqual(await c.fetchRooms(), []);
  assert.deepEqual(await c.fetchMyRooms(), []);
  assert.deepEqual(await c.createRoom("x"), {success: false, message: "Not authenticated"});
  assert.deepEqual(await c.joinRoom("x"), {success: false, message: "Not authenticated"});
  assert.deepEqual(await c.leaveRoom("x"), {success: false, message: "Not authenticated"});
  c.setCurrentRoom("x"); c.setRooms([R1]); c.reset();
  assert.deepEqual([s.requests, s.views, s.messages], [[], [], []]);
});

test("fetching the rooms keeps them by id, tells the handlers, draws the list and returns the rooms", async () => {
  const s = await started({replies: roomsReply()});
  const heard = [];
  s.controls.on("rooms_updated", (rooms) => heard.push(Object.keys(rooms)));
  assert.deepEqual(await s.controls.fetchRooms(), [R1, R2]);
  assert.deepEqual(Object.keys(s.controls.rooms), ["r1", "r2"]);
  assert.deepEqual(heard, [["r1", "r2"]]);
  assert.deepEqual(s.views, [["available", renderAvailableRooms(indexRooms([R1, R2]), {})]]);
  assert.equal(s.requests[0].init.headers, undefined, "no token needed");
});

test("fetching the rooms again replaces them", async () => {
  let list = [R1, R2];
  const s = await started({replies: {"GET /api/rooms": () => ok({rooms: list})}});
  await s.controls.fetchRooms();
  list = [R2];
  await s.controls.fetchRooms();
  assert.deepEqual(Object.keys(s.controls.rooms), ["r2"]);
});

test("a reply that is not ok, or a failure, leaves the rooms as they were and returns nothing", async () => {
  const refused = await started({replies: {"GET /api/rooms": () => ({status: "error"})}});
  refused.controls.setRooms([R1]);
  assert.deepEqual(await refused.controls.fetchRooms(), []);
  assert.deepEqual(Object.keys(refused.controls.rooms), ["r1"]);
  const failing = await started({replies: {"GET /api/rooms": () => { throw new Error("offline"); }}});
  assert.deepEqual(await failing.controls.fetchRooms(), []);
  assert.match(failing.errors[0], /Error fetching rooms/);
  const notJson = await started({replies: {"GET /api/rooms": () => new Error("bad json")}});
  assert.deepEqual(await notJson.controls.fetchRooms(), []);
});

test("my rooms need a session; with one they are fetched with its token, kept, announced and drawn", async () => {
  const none = await started({token: null, replies: myReply()});
  assert.deepEqual(await none.controls.fetchMyRooms(), []);
  assert.deepEqual(none.requests, []);
  const s = await started({replies: myReply()});
  const heard = [];
  s.controls.on("my_rooms_updated", (rooms) => heard.push(Object.keys(rooms)));
  assert.deepEqual(await s.controls.fetchMyRooms(), [R1]);
  assert.deepEqual(s.requests[0].init.headers, {"X-Session-Token": "tok"});
  assert.deepEqual(Object.keys(s.controls.joinedRooms), ["r1"]);
  assert.deepEqual(heard, [["r1"]]);
  assert.deepEqual(s.views, [["joined", renderJoinedRooms(indexRooms([R1]), null)]]);
});

test("a refused or failing fetch of my rooms leaves them as they were", async () => {
  const refused = await started({replies: {"GET /api/rooms/my": () => ({status: "error"})}});
  assert.deepEqual(await refused.controls.fetchMyRooms(), []);
  const failing = await started({replies: {"GET /api/rooms/my": () => { throw new Error("x"); }}});
  assert.deepEqual(await failing.controls.fetchMyRooms(), []);
  assert.match(failing.errors[0], /Error fetching my rooms/);
});

const CREATE = "POST /api/rooms/create";

test("creating a room posts it with its defaults and the token, says so, refetches both lists, and returns the room", async () => {
  const s = await started({replies: {[CREATE]: () => ok({room: R1}), ...roomsReply(), ...myReply()}});
  const result = await s.controls.createRoom("Ops");
  assert.deepEqual(result, {success: true, room: R1});
  assert.deepEqual(s.requests.map((r) => r.key), [CREATE, "GET /api/rooms", "GET /api/rooms/my"]);
  assert.deepEqual(s.requests[0].init.headers, {"Content-Type": "application/json", "X-Session-Token": "tok"});
  assert.equal(s.requests[0].init.body, '{"room_name":"Ops","room_type":"custom","capacity":50,"is_private":false}');
  assert.deepEqual(s.messages, [["🏠 Created room: Ops", "response"]]);
});

test("creating a room passes its options on", async () => {
  const s = await started({replies: {[CREATE]: () => ok({room: R1}), ...roomsReply(), ...myReply()}});
  await s.controls.createRoom("Secret", {type: "team", capacity: 5, isPrivate: true, password: "pw", metadata: {a: 1}});
  assert.deepEqual(JSON.parse(s.requests[0].init.body), {room_name: "Secret", room_type: "team", capacity: 5, is_private: true, password: "pw", metadata: {a: 1}});
});

test("creating a room needs a session, and a refusal or failure comes back as a message", async () => {
  const none = await started({token: null});
  assert.deepEqual(await none.controls.createRoom("x"), {success: false, message: "Not authenticated"});
  assert.deepEqual(none.requests, []);
  const refused = await started({replies: {[CREATE]: () => ({status: "error", message: "name taken"})}});
  assert.deepEqual(await refused.controls.createRoom("x"), {success: false, message: "name taken"});
  assert.deepEqual(refused.messages, []);
  const failing = await started({replies: {[CREATE]: () => { throw new Error("offline"); }}});
  assert.deepEqual(await failing.controls.createRoom("x"), {success: false, message: "offline"});
  assert.match(failing.errors[0], /Error creating room/);
});

test("joining a room posts the password, files the room the server returned (or the one already listed), says so, tells the handlers, and redraws the joined list only", async () => {
  const s = await started({replies: {...roomsReply(), "POST /api/rooms/r1/join": () => ok({room: R1}), "POST /api/rooms/r2/join": () => ok({})}});
  await s.controls.fetchRooms();
  s.views.length = 0;
  const heard = [];
  s.controls.on("room_joined", (d) => heard.push([d.roomId, d.room && d.room.room_name]));
  assert.deepEqual(await s.controls.joinRoom("r1", "pw"), {success: true, room: R1});
  assert.equal(s.requests.at(-1).init.body, '{"password":"pw"}');
  assert.deepEqual(s.requests.at(-1).init.headers, {"Content-Type": "application/json", "X-Session-Token": "tok"});
  assert.deepEqual(await s.controls.joinRoom("r2"), {success: true, room: undefined});
  assert.equal(s.requests.at(-1).init.body, '{"password":null}');
  assert.deepEqual(Object.keys(s.controls.joinedRooms), ["r1", "r2"], "r2 from the available list");
  assert.deepEqual(heard, [["r1", "Ops"], ["r2", "Lounge"]]);
  assert.deepEqual(s.messages, [["📥 Joined room: Ops", "response"], ["📥 Joined room: Lounge", "response"]]);
  assert.deepEqual(s.views.map((v) => v[0]), ["joined", "joined"], "the available list is not redrawn: PINNED");
});

test("joining files the room the server returned in preference to the one already listed", async () => {
  const returned = {room_id: "r1", room_name: "Returned"};
  const s = await started({replies: {...roomsReply(), "POST /api/rooms/r1/join": () => ok({room: returned})}});
  await s.controls.fetchRooms();
  await s.controls.joinRoom("r1");
  assert.equal(s.controls.joinedRooms.r1, returned);
  assert.deepEqual(s.messages, [["📥 Joined room: Returned", "response"]]);
});

test("joining a room nobody knows says its id, and keeps nothing", async () => {
  const s = await started({replies: {"POST /api/rooms/zz/join": () => ok({})}});
  await s.controls.joinRoom("zz");
  assert.deepEqual(s.controls.joinedRooms, {});
  assert.deepEqual(s.messages, [["📥 Joined room: zz", "response"]]);
});

test("joining needs a session, and a refusal or failure comes back as a message", async () => {
  const none = await started({token: null});
  assert.deepEqual(await none.controls.joinRoom("r1"), {success: false, message: "Not authenticated"});
  const refused = await started({replies: {"POST /api/rooms/r1/join": () => ({status: "error", message: "wrong password"})}});
  assert.deepEqual(await refused.controls.joinRoom("r1"), {success: false, message: "wrong password"});
  const failing = await started({replies: {"POST /api/rooms/r1/join": () => { throw new Error("offline"); }}});
  assert.deepEqual(await failing.controls.joinRoom("r1"), {success: false, message: "offline"});
  assert.match(failing.errors[0], /Error joining room/);
});

test("leaving a room forgets it, says so, tells the handlers and redraws; leaving the current room does not change the current room", async () => {
  const s = await started({replies: {...myReply([R1, R2]), "POST /api/rooms/r1/leave": () => ok({})}});
  await s.controls.fetchMyRooms();
  s.controls.setCurrentRoom("r1");
  s.views.length = 0;
  const heard = [];
  s.controls.on("room_left", (d) => heard.push(d));
  assert.deepEqual(await s.controls.leaveRoom("r1"), {success: true});
  assert.deepEqual(s.requests.at(-1).init.headers, {"X-Session-Token": "tok"});
  assert.equal(s.requests.at(-1).init.method, "POST");
  assert.deepEqual(Object.keys(s.controls.joinedRooms), ["r2"]);
  assert.deepEqual(heard, [{roomId: "r1"}]);
  assert.deepEqual(s.messages, [["📤 Left room: Ops", "response"]]);
  assert.equal(s.controls.currentRoomId, "r1", "PINNED");
  assert.deepEqual(s.views.map((v) => v[0]), ["joined"]);
});

test("leaving a room you were not in says its id, and leaving needs a session", async () => {
  const s = await started({replies: {"POST /api/rooms/zz/leave": () => ok({})}});
  await s.controls.leaveRoom("zz");
  assert.deepEqual(s.messages, [["📤 Left room: zz", "response"]]);
  const none = await started({token: null});
  assert.deepEqual(await none.controls.leaveRoom("r1"), {success: false, message: "Not authenticated"});
  const refused = await started({replies: {"POST /api/rooms/r1/leave": () => ({status: "error", message: "no"})}});
  assert.deepEqual(await refused.controls.leaveRoom("r1"), {success: false, message: "no"});
  const failing = await started({replies: {"POST /api/rooms/r1/leave": () => { throw new Error("offline"); }}});
  assert.deepEqual(await failing.controls.leaveRoom("r1"), {success: false, message: "offline"});
  assert.match(failing.errors[0], /Error leaving room/);
});

test("making a room current tells the handlers and redraws the current room's name", async () => {
  const s = await started({replies: myReply()});
  await s.controls.fetchMyRooms();
  s.views.length = 0;
  const heard = [];
  s.controls.on("current_room_changed", (d) => heard.push(d));
  s.controls.setCurrentRoom("r1");
  assert.equal(s.controls.currentRoomId, "r1");
  assert.deepEqual(heard, [{roomId: "r1"}]);
  assert.deepEqual(s.views, [["current", "Ops"]]);
  s.controls.setCurrentRoom("r9");
  assert.deepEqual(s.views.at(-1), ["current", "No room selected"]);
});

test("PINNED: making a room current does not redraw the joined list", async () => {
  const s = await started({replies: myReply()});
  await s.controls.fetchMyRooms();
  s.views.length = 0;
  s.controls.setCurrentRoom("r1");
  assert.deepEqual(s.views.map((v) => v[0]), ["current"]);
});

test("the websocket's list of rooms replaces the available rooms and redraws them, without telling the handlers", async () => {
  const s = await started();
  const heard = [];
  s.controls.on("rooms_updated", () => heard.push(1));
  s.controls.setRooms([R1, R2]);
  assert.deepEqual(Object.keys(s.controls.rooms), ["r1", "r2"]);
  assert.deepEqual(s.views, [["available", renderAvailableRooms(indexRooms([R1, R2]), {})]]);
  assert.deepEqual(heard, []);
  s.controls.setRooms([]);
  assert.deepEqual(s.controls.rooms, {});
});

test("logging out forgets the joined rooms and the current room and redraws, without telling the handlers", async () => {
  const s = await started({replies: myReply()});
  await s.controls.fetchMyRooms();
  s.controls.setCurrentRoom("r1");
  const heard = [];
  s.controls.on("current_room_changed", () => heard.push(1));
  s.controls.on("my_rooms_updated", () => heard.push(2));
  s.views.length = 0;
  s.controls.reset();
  assert.deepEqual([s.controls.joinedRooms, s.controls.currentRoomId], [{}, null]);
  assert.deepEqual(s.views, [["joined", NOT_IN_ANY_ROOM], ["current", NO_ROOM_SELECTED]]);
  assert.deepEqual(heard, []);
});

test("handlers are called in order, one that throws does not stop the others, and off removes one; on and off return the controls", async () => {
  const s = await started({replies: roomsReply()});
  const order = [];
  const first = () => { order.push("first"); throw new Error("boom"); };
  const second = () => order.push("second");
  assert.equal(s.controls.on("rooms_updated", first), s.controls);
  s.controls.on("rooms_updated", second);
  await s.controls.fetchRooms();
  assert.deepEqual(order, ["first", "second"]);
  assert.match(s.errors[0], /\[RoomManager\] Handler error for rooms_updated/);
  assert.equal(s.controls.off("rooms_updated", first), s.controls);
  await s.controls.fetchRooms();
  assert.deepEqual(order, ["first", "second", "second"]);
  s.controls.off("never_registered", first);
});

test("handlers outlive an activation: they can be registered before the tool is active and still hear it later", async () => {
  const s = setup({replies: roomsReply()});
  const heard = [];
  s.controls.on("rooms_updated", () => heard.push("early"));
  await s.workspace.activate(ROOMS_ID);
  await s.controls.fetchRooms();
  await s.workspace.deactivate(ROOMS_ID);
  await s.workspace.activate(ROOMS_ID);
  await s.controls.fetchRooms();
  assert.deepEqual(heard, ["early", "early"]);
});

test("after deactivation the controls are inert: they answer as when inactive and make no requests", async () => {
  const s = await started({replies: {...roomsReply(), ...myReply()}});
  await s.workspace.deactivate(ROOMS_ID);
  s.requests.length = 0;
  assert.deepEqual(await s.controls.fetchRooms(), []);
  assert.deepEqual(await s.controls.createRoom("x"), {success: false, message: "Not authenticated"});
  s.controls.setCurrentRoom("r1"); s.controls.reset(); s.controls.setRooms([R1]);
  assert.deepEqual([s.requests, s.views, s.controls.currentRoomId], [[], [], null]);
});

test("the rooms belong to an activation: a new one starts empty", async () => {
  const s = await started({replies: {...roomsReply(), ...myReply()}});
  await s.controls.fetchRooms(); await s.controls.fetchMyRooms(); s.controls.setCurrentRoom("r1");
  await s.workspace.deactivate(ROOMS_ID);
  await s.workspace.activate(ROOMS_ID);
  assert.deepEqual([s.controls.rooms, s.controls.joinedRooms, s.controls.currentRoomId], [{}, {}, null]);
});

test("answers that arrive after the tool deactivated change nothing and say nothing", async () => {
  const ga = gate(), gm = gate(), gc = gate(), gj = gate(), gl = gate();
  const s = await started({replies: {"GET /api/rooms": () => ga.promise, "GET /api/rooms/my": () => gm.promise, [CREATE]: () => gc.promise, "POST /api/rooms/r1/join": () => gj.promise, "POST /api/rooms/r1/leave": () => gl.promise}});
  const pending = [s.controls.fetchRooms(), s.controls.fetchMyRooms(), s.controls.createRoom("x"), s.controls.joinRoom("r1"), s.controls.leaveRoom("r1")];
  await settle();
  const heard = [];
  for (const e of ["rooms_updated", "my_rooms_updated", "room_joined", "room_left"]) s.controls.on(e, () => heard.push(e));
  await s.workspace.deactivate(ROOMS_ID);
  ga.open(ok({rooms: [R1]})); gm.open(ok({rooms: [R1]})); gc.open(ok({room: R1})); gj.open(ok({room: R1})); gl.open(ok({}));
  const results = await Promise.all(pending);
  assert.deepEqual(results.slice(0, 2), [[], []]);
  assert.deepEqual([heard, s.messages, s.views, s.requests.length], [[], [], [], 5]);
});

test("a failure that arrives after deactivation is not logged", async () => {
  const g = gate();
  const s = await started({replies: {"GET /api/rooms": () => g.promise, "GET /api/rooms/my": () => g.promise}});
  const a = s.controls.fetchRooms(), b = s.controls.fetchMyRooms();
  await settle();
  await s.workspace.deactivate(ROOMS_ID);
  g.open(new Error("late"));
  await Promise.all([a, b]);
  assert.deepEqual(s.errors, []);
});
