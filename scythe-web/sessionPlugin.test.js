import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createWorkspace} from "./lifecycle.js";
import {createSessionPlugin, SESSION_ID, HEARTBEAT_INTERVAL} from "./sessionPlugin.js";
import {wireSessionEffects} from "./workspaceBoot.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");
const OPERATOR = {operator_id: "op-1", callsign: "VIPER", email: "viper@example.org", role: "operator", team_id: "t1"};
const SESSION = {session_id: "s-1", session_token: "tok-1"};
const settle = () => new Promise((resolve) => setImmediate(resolve));

/**
 * The session tool against fakes for everything it touches; nothing here is a real token. `replies` maps "METHOD url" (GET by default) to the JSON body
 * or a function returning it (a function that throws fails the request). `wired` puts the boot module's effects on the session's events, as the page has them.
 */
function world({replies = {}, auth = {}, viewer = null, storage = {}, failStorage = [], failRemove = [], failCookie = false, failForget = false, gates = {}, wired = true, activate = true} = {}) {
  const log = [], requests = [], messages = [], cookies = [], authSignOuts = [], store = {...storage}, timers = [], warnings = [], errors = [], logs = [];
  const note = (...a) => log.push(a);
  const workspace = createWorkspace({log: {warn() {}, error() {}, log() {}, info() {}, debug() {}}, timers: {
    setInterval: (fn, ms) => { timers.push({fn, ms, cleared: false}); return timers.length; },
    clearInterval: (id) => { timers[id - 1].cleared = true; note("clearInterval"); },
    setTimeout: () => 0, clearTimeout() {},
  }});
  workspace.provide("httpFetch", async (url, init = {}) => {
    const key = `${init.method || "GET"} ${url}`;
    requests.push({key, init});
    if (!(key in replies)) throw new Error(`no reply for ${key}`);
    const reply = await (typeof replies[key] === "function" ? replies[key](init) : replies[key]);   // a held reply holds the request itself
    return {json: async () => reply};
  });
  workspace.provide("sessionStore", {
    restoreToken: () => auth.token ?? null,
    validate: async (token) => { note("validate", token); if (auth.hold) return auth.hold; if (auth.throws) throw new Error("bad"); return auth.result ?? {valid: false}; },
    setItem: (k, v) => { if (failStorage.includes(k)) throw new Error("quota"); store[k] = String(v); note("set", k); },
    removeItem: (k) => { if (failRemove.includes(k)) throw new Error("locked"); delete store[k]; note("remove", k); },
    clearCookie: (name) => { if (failCookie) throw new Error("blocked"); cookies.push([name, "cleared"]); },
    forget: () => { if (failForget) throw new Error("auth gone"); authSignOuts.push(true); note("forget"); },
  });
  workspace.provide("cameraView", {position: () => (viewer ? {center: [50, 25], altitude: 1234, heading: 10} : null)});
  const plugin = createSessionPlugin({log: {log: (...a) => logs.push(a.join(" ")), warn: (...a) => warnings.push(a.join(" ")), error: (...a) => errors.push(a.join(" "))}});
  workspace.register(plugin.tool);
  const win = {
    addConsoleMessage: (text, kind) => { messages.push([text, kind]); note("message"); }, updateOperatorStatusUI: () => note("status"),
    WebSocketSync: gates.WebSocketSync, EntitySync: gates.EntitySync, RoomManager: gates.RoomManager, GraphOpsBotRebind: gates.GraphOpsBotRebind,
    GraphOpsBotReset: gates.GraphOpsBotReset, populateRooms: gates.populateRooms, console: {debug: (...a) => note("debug", a[0])},
  };
  if (wired) wireSessionEffects(win, plugin.controls);
  const ready = activate ? workspace.activate(SESSION_ID) : Promise.resolve();
  return {session: plugin.controls, workspace, ready, log, requests, messages, cookies, authSignOuts, store, timers, warnings, errors, logs, note, plugin};
}
async function started(options) { const w = world(options); await w.ready; return w; }

const loginReply = (extra = {}) => ({"POST /api/operator/login": {status: "ok", session: SESSION, operator: OPERATOR, ...extra}});
const signedIn = async (options = {}) => { const w = await started({replies: loginReply(), ...options}); await w.session.login("VIPER", "pw"); w.log.length = 0; return w; };

// --- the state ---

test("a new session is signed out with nothing set, and the heartbeat is every 30 seconds", async () => {
  const {session} = await started();
  assert.deepEqual([session.operatorId, session.operatorName, session.callsign, session.role, session.teamId, session.sessionToken, session.sessionId, session.isAuthenticated],
    [null, null, null, null, null, null, null, false]);
  assert.equal(HEARTBEAT_INTERVAL, 30000);
});

test("the state is read-only to the page: assigning to a field changes nothing", async () => {
  const w = await signedIn();
  assert.throws(() => { "use strict"; w.session.sessionToken = "other"; }, TypeError);
  assert.equal(w.session.sessionToken, "tok-1");
});

test("while inactive the session is signed out, and every call answers without doing anything", async () => {
  const w = world({activate: false, replies: loginReply()});
  const c = w.session;
  assert.deepEqual([c.sessionToken, c.isAuthenticated, c.callsign], [null, false, null]);
  assert.deepEqual(await c.login("V", "pw"), {success: false, message: "Session not started"});
  assert.deepEqual(await c.register("V", "e", "pw"), {success: false, message: "Session not started"});
  assert.equal(await c.validateSession(), false);
  assert.equal(await c.logout(), undefined);
  assert.deepEqual(await c.getActiveOperators(), []);
  c.clearSession();
  assert.deepEqual([w.requests, w.log, w.messages], [[], [], []]);
});

// --- restoring and validating ---

test("init does nothing without a saved token", async () => {
  const w = await started({auth: {token: null}});
  w.session.init();
  await settle();
  assert.deepEqual([w.session.sessionToken, w.log], [null, []]);
});

test("init keeps the saved token and validates it", async () => {
  const w = await started({auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  w.session.init();
  assert.equal(w.session.sessionToken, "saved");
  await settle();
  assert.deepEqual(w.log.filter((e) => e[0] === "validate"), [["validate", "saved"]]);
  assert.equal(w.session.isAuthenticated, true);
});

test("an init asked for before the tool is active is made when it activates, once", async () => {
  const w = world({activate: false, auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  w.session.init();
  await settle();
  assert.equal(w.log.filter((e) => e[0] === "validate").length, 0, "nothing while inactive");
  await w.workspace.activate(SESSION_ID);
  await settle();
  assert.equal(w.log.filter((e) => e[0] === "validate").length, 1);
  assert.equal(w.session.isAuthenticated, true);
  await w.workspace.deactivate(SESSION_ID);
  await w.workspace.activate(SESSION_ID);
  await settle();
  assert.equal(w.log.filter((e) => e[0] === "validate").length, 1, "not made again");
});

test("a valid session is filled in from the operator, starts the heartbeat, and runs the restored effects", async () => {
  const calls = [];
  const w = await started({auth: {token: "saved", result: {valid: true, operator: OPERATOR}}, gates: {EntitySync: {connect: () => calls.push("entity")}, RoomManager: {fetchRooms: () => calls.push("rooms"), fetchMyRooms: () => calls.push("mine")}}});
  w.session.init();
  await settle();
  assert.deepEqual([w.session.sessionId, w.session.operatorId, w.session.operatorName, w.session.callsign, w.session.role, w.session.teamId, w.session.isAuthenticated],
    ["op-1", "op-1", "viper@example.org", "VIPER", "operator", "t1", true]);
  assert.equal(w.timers.length, 1);
  assert.equal(w.timers[0].ms, 30000);
  assert.deepEqual(w.messages, [["👤 Session restored for VIPER", "info"]]);
  assert.deepEqual(calls, ["entity", "rooms", "mine"]);
});

test("a valid session whose operator has no id gets no session id", async () => {
  const w = await started({auth: {token: "saved", result: {valid: true, operator: {...OPERATOR, operator_id: undefined}}}});
  w.session.init();
  await settle();
  assert.equal(w.session.sessionId, null);
  assert.equal(w.session.isAuthenticated, true);
});

test("validating with no token is false and asks nobody", async () => {
  const w = await started();
  assert.equal(await w.session.validateSession(), false);
  assert.deepEqual(w.log, []);
});

test("an invalid session, or a validation that throws, clears the session and is false; a throw is warned about", async () => {
  const invalid = await started({auth: {token: "saved", result: {valid: false}}});
  invalid.session.init();
  await settle();
  assert.equal(invalid.session.sessionToken, null);
  assert.equal(await invalid.session.validateSession(), false);
  const throwing = await started({auth: {token: "saved", throws: true}});
  throwing.session.init();
  await settle();
  assert.equal(throwing.session.sessionToken, null);
  assert.match(throwing.warnings[0], /Session validation failed/);
});

// --- logging in and registering ---

test("logging in posts the callsign and password, fills the session in, starts the heartbeat, and runs the login effects", async () => {
  const calls = [];
  const w = await started({replies: loginReply(), gates: {WebSocketSync: {useWebSocket: true, connect: () => calls.push("ws")}, EntitySync: {connect: () => calls.push("entity")}, RoomManager: {fetchRooms: () => calls.push("rooms"), fetchMyRooms: () => calls.push("mine")}}});
  const result = await w.session.login("VIPER", "pw");
  assert.deepEqual(result, {success: true, operator: OPERATOR});
  assert.equal(w.requests[0].key, "POST /api/operator/login");
  assert.deepEqual(w.requests[0].init.headers, {"Content-Type": "application/json"});
  assert.equal(w.requests[0].init.body, '{"callsign":"VIPER","password":"pw"}');
  assert.deepEqual([w.session.sessionToken, w.session.sessionId, w.session.callsign, w.session.isAuthenticated], ["tok-1", "s-1", "VIPER", true]);
  assert.equal(w.timers.length, 1);
  assert.deepEqual(w.messages, [["👤 Operator VIPER logged in", "info"]]);
  assert.deepEqual(calls, ["ws", "rooms", "mine"]);
});

test("a login the server refuses returns its message and changes nothing", async () => {
  const w = await started({replies: {"POST /api/operator/login": {status: "error", message: "bad credentials"}}});
  assert.deepEqual(await w.session.login("VIPER", "pw"), {success: false, message: "bad credentials"});
  assert.deepEqual([w.session.isAuthenticated, w.session.sessionToken, w.timers.length, w.messages, w.cookies], [false, null, 0, [], []]);
});

test("a login that fails to reach the server returns the error's message and logs it", async () => {
  const w = await started({replies: {"POST /api/operator/login": () => { throw new Error("offline"); }}});
  assert.deepEqual(await w.session.login("VIPER", "pw"), {success: false, message: "offline"});
  assert.match(w.errors[0], /Login error/);
});

test("registering posts the details (the role defaults to operator), and a good registration logs in and returns the login's result", async () => {
  const w = await started({replies: {"POST /api/operator/register": {status: "ok"}, ...loginReply()}});
  const result = await w.session.register("VIPER", "v@example.org", "pw");
  assert.deepEqual(result, {success: true, operator: OPERATOR});
  assert.deepEqual(w.requests.map((r) => r.key), ["POST /api/operator/register", "POST /api/operator/login"]);
  assert.equal(w.requests[0].init.body, '{"callsign":"VIPER","email":"v@example.org","password":"pw","role":"operator"}');
  assert.equal(w.session.isAuthenticated, true);
});

test("registering sends the role it is given, and a refusal or a failure comes back as a message without logging in", async () => {
  const w = await started({replies: {"POST /api/operator/register": {status: "error", message: "taken"}}});
  assert.deepEqual(await w.session.register("A", "a@b.c", "pw", "observer"), {success: false, message: "taken"});
  assert.equal(JSON.parse(w.requests[0].init.body).role, "observer");
  assert.equal(w.requests.length, 1);
  const failing = await started({replies: {"POST /api/operator/register": () => { throw new Error("offline"); }}});
  assert.deepEqual(await failing.session.register("A", "a@b.c", "pw"), {success: false, message: "offline"});
  assert.match(failing.errors[0], /Registration error/);
});

// --- what a session persists ---

test("a session is saved in localStorage.scythe_session_token only: the old operatorSession key and the cookie are no longer written", async () => {
  const w = await started({replies: loginReply()});
  await w.session.login("VIPER", "pw");
  assert.deepEqual(w.store, {scythe_session_token: "tok-1"});
  assert.deepEqual(w.cookies, []);
  assert.deepEqual(w.authSignOuts, []);
});

test("a session whose write fails still succeeds and says nothing", async () => {
  const w = await started({replies: loginReply(), failStorage: ["scythe_session_token"]});
  await w.session.login("VIPER", "pw");
  assert.deepEqual(w.warnings, []);
  assert.equal(w.session.isAuthenticated, true);
});

test("a session with no token is saved as an undefined token", async () => {
  const w = await started({replies: loginReply({session: {session_id: "s"}})});
  await w.session.login("VIPER", "pw");
  assert.equal(w.store.scythe_session_token, "undefined");
});

test("clearing the session forgets the fields, stops the heartbeat, and removes every copy: the two old ones, scythe_session_token, and SCYTHE_AUTH's", async () => {
  const w = await signedIn({storage: {operatorSession: '{"sessionToken":"old"}', "scythe:orchestrator:operatorSession": '{"sessionToken":"auth-copy"}'}});
  w.session.clearSession();
  assert.deepEqual([w.session.operatorId, w.session.operatorName, w.session.callsign, w.session.role, w.session.teamId, w.session.sessionToken, w.session.sessionId, w.session.isAuthenticated],
    [null, null, null, null, null, null, null, false]);
  assert.equal(w.timers[0].cleared, true);
  assert.equal("operatorSession" in w.store, false);
  assert.equal("scythe_session_token" in w.store, false);
  assert.deepEqual(w.cookies.at(-1), ["operatorSessionToken", "cleared"]);
  assert.deepEqual(w.authSignOuts, [true], "SCYTHE_AUTH is signed out once");
});

test("every copy is removed even when another cannot be: each failure is warned about by name and the rest still go", async () => {
  for (const [options, name] of [[{failRemove: ["operatorSession"]}, "operatorSession"], [{failCookie: true}, "session cookie"], [{failRemove: ["scythe_session_token"]}, "scythe_session_token"], [{failForget: true}, "SCYTHE_AUTH"]]) {
    const w = await signedIn({...options, storage: {operatorSession: "x"}});
    w.session.clearSession();
    assert.equal(w.warnings.length, 1, name);
    assert.match(w.warnings[0], new RegExp(`Could not remove ${name}`));
    const gone = {operatorSession: !options.failRemove?.includes("operatorSession"), scythe_session_token: !options.failRemove?.includes("scythe_session_token")};
    assert.deepEqual([("operatorSession" in w.store) === !gone.operatorSession, ("scythe_session_token" in w.store) === !gone.scythe_session_token], [true, true], name);
    assert.equal(w.authSignOuts.length, options.failForget ? 0 : 1, name);
    assert.equal(w.session.isAuthenticated, false, name);
  }
});

test("a failed restore signs SCYTHE_AUTH out too, so the next load does not try the same token again", async () => {
  const w = await started({auth: {token: "saved", result: {valid: false}}});
  w.session.init();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual([w.session.isAuthenticated, w.authSignOuts], [false, [true]]);
});

test("logging out signs SCYTHE_AUTH out, whether or not the server's answer arrived", async () => {
  const ok = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": {status: "ok"}}});
  await ok.session.logout();
  assert.deepEqual([ok.authSignOuts, "scythe_session_token" in ok.store], [[true], false]);
  const offline = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": () => { throw new Error("offline"); }}});
  await offline.session.logout();
  assert.deepEqual([offline.authSignOuts, "scythe_session_token" in offline.store], [[true], false]);
});

// --- logging out ---

test("logging out with no session does nothing", async () => {
  const w = await started();
  assert.equal(await w.session.logout(), undefined);
  assert.deepEqual([w.requests, w.messages, w.log], [[], [], []]);
});

test("logging out tells the server with the token, clears the session, and runs the logout effects", async () => {
  const calls = [];
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": {}}, gates: {WebSocketSync: {disconnect: () => calls.push("ws")}, EntitySync: {disconnect: () => calls.push("entity")}, RoomManager: {reset: () => calls.push("rooms")}, GraphOpsBotReset: (why) => calls.push("graphops:" + why)}});
  await w.session.logout();
  const logout = w.requests.find((r) => r.key === "POST /api/operator/logout");
  assert.deepEqual(logout.init.headers, {"X-Session-Token": "tok-1"});
  assert.equal(w.session.isAuthenticated, false);
  assert.deepEqual(w.messages.at(-1), ["👤 Operator logged out", "info"]);
  assert.equal(w.log.filter((e) => e[0] === "status").length, 1, "the status UI is redrawn");
  assert.deepEqual(calls, ["graphops:Operator logged out", "ws", "entity", "rooms"]);
});

test("a logout whose request fails is warned about, and the session is cleared and the effects run all the same", async () => {
  const calls = [];
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": () => { throw new Error("offline"); }}, gates: {RoomManager: {reset: () => calls.push("rooms")}}});
  await w.session.logout();
  assert.match(w.warnings.at(-1), /Logout error/);
  assert.equal(w.session.sessionToken, null);
  assert.deepEqual(calls, ["rooms"]);
});

// --- the heartbeat ---

test("starting the heartbeat again stops the one before: a second login does not leave two", async () => {
  const w = await signedIn();
  await w.session.login("VIPER", "pw");
  assert.equal(w.timers.length, 2);
  assert.equal(w.timers[0].cleared, true);
  assert.deepEqual([w.timers[1].ms, w.timers[1].cleared], [30000, false]);
});

test("a tick without a session does nothing", async () => {
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/heartbeat": {}}});
  w.session.clearSession();
  w.timers[0].cleared = false;
  await w.timers[0].fn();
  assert.equal(w.requests.filter((r) => /heartbeat/.test(r.key)).length, 0);
});

test("a tick posts the token and the camera's view", async () => {
  const w = await signedIn({viewer: true, replies: {...loginReply(), "POST /api/operator/heartbeat": {}}});
  await w.timers[0].fn();
  const beat = w.requests.at(-1);
  assert.equal(beat.key, "POST /api/operator/heartbeat");
  assert.deepEqual(beat.init.headers, {"Content-Type": "application/json", "X-Session-Token": "tok-1"});
  assert.deepEqual(JSON.parse(beat.init.body), {current_view: {center: [50, 25], altitude: 1234, heading: 10}});
});

test("a tick without a viewer sends no view, and a failing request is only warned about", async () => {
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/heartbeat": () => { throw new Error("offline"); }}});
  await w.timers[0].fn();
  assert.equal(w.requests.at(-1).init.body, '{"current_view":null}');
  assert.match(w.warnings.at(-1), /Heartbeat failed/);
  assert.equal(w.session.isAuthenticated, true);
});

// --- the other operators ---

test("the active operators are the server's list, or none; a failure is logged", async () => {
  const w = await started({replies: {"GET /api/operator/active": {operators: [{operator_id: "a"}]}}});
  assert.deepEqual(await w.session.getActiveOperators(), [{operator_id: "a"}]);
  const none = await started({replies: {"GET /api/operator/active": {}}});
  assert.deepEqual(await none.session.getActiveOperators(), []);
  const failing = await started({replies: {"GET /api/operator/active": () => { throw new Error("offline"); }}});
  assert.deepEqual(await failing.session.getActiveOperators(), []);
  assert.match(failing.errors[0], /Error getting active operators/);
});

// --- the events and their effects ---

test("a session with nothing wired to it still logs in, restores and logs out: emitting with no handler is harmless", async () => {
  const w = await started({wired: false, replies: {...loginReply(), "POST /api/operator/logout": {}}, auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  assert.equal((await w.session.login("V", "pw")).success, true);
  await w.session.logout();
  w.session.init();
  await settle();
  assert.equal(w.session.isAuthenticated, true);
  assert.deepEqual(w.messages, []);
});

test("handlers for an event run in the order they were added, each once per emit; on and off return the controls; off removes one", async () => {
  const w = await started({wired: false, replies: loginReply()});
  const order = [];
  const a = () => order.push("a");
  assert.equal(w.session.on("login", a), w.session);
  w.session.on("login", () => order.push("b"));
  w.session.on("logout", () => order.push("c"));
  await w.session.login("V", "pw");
  assert.deepEqual(order, ["a", "b"]);
  assert.equal(w.session.off("login", a), w.session);
  await w.session.login("V", "pw");
  assert.deepEqual(order, ["a", "b", "b"]);
  w.session.off("never_added", a);
});

test("handlers outlive an activation: one added before the tool is active hears it afterwards", async () => {
  const w = world({activate: false, wired: false, replies: loginReply()});
  const heard = [];
  w.session.on("login", () => heard.push(1));
  await w.workspace.activate(SESSION_ID);
  await w.session.login("V", "pw");
  await w.workspace.deactivate(SESSION_ID);
  await w.workspace.activate(SESSION_ID);
  await w.session.login("V", "pw");
  assert.deepEqual(heard, [1, 1]);
});

test("login, restore and logout each emit their own event, and only after the state is set", async () => {
  const w = await started({wired: false, replies: {...loginReply(), "POST /api/operator/logout": {}}, auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  const seen = [];
  for (const event of ["login", "restored", "logout"]) w.session.on(event, () => seen.push([event, w.session.isAuthenticated, w.session.callsign]));
  await w.session.login("V", "pw");
  w.session.init();
  await settle();
  await w.session.logout();
  assert.deepEqual(seen, [["login", true, "VIPER"], ["restored", true, "VIPER"], ["logout", false, null]]);
});

test("PINNED: an error in a handler reaches the caller: a failing login effect makes login report failure although the session is set", async () => {
  const w = await started({wired: false, replies: loginReply()});
  w.session.on("login", () => { throw new Error("effect failed"); });
  assert.deepEqual(await w.session.login("V", "pw"), {success: false, message: "effect failed"});
  assert.equal(w.session.isAuthenticated, true);
});

test("PINNED: a failing restore effect makes the restore fail and signs the session out", async () => {
  const w = await started({wired: false, auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  w.session.on("restored", () => { throw new Error("effect failed"); });
  w.session.init();
  await settle();
  assert.equal(w.session.isAuthenticated, false);
  assert.match(w.warnings[0], /Session validation failed/);
});

test("PINNED: a failing logout effect makes logout reject, after the session was cleared", async () => {
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": {}}, wired: false});
  w.session.on("logout", () => { throw new Error("effect failed"); });
  await assert.rejects(w.session.logout(), /effect failed/);
  assert.equal(w.session.sessionToken, null);
});

test("an error in a handler stops the handlers after it", async () => {
  const w = await started({wired: false, replies: loginReply()});
  const order = [];
  w.session.on("login", () => { order.push(1); throw new Error("x"); });
  w.session.on("login", () => order.push(2));
  assert.equal((await w.session.login("V", "pw")).success, false);
  assert.deepEqual(order, [1]);
});

test("on login the streams connect through WebSocketSync if it is on offer, else through EntitySync, else not at all", async () => {
  const via = async (gates) => { const calls = []; const w = await started({replies: loginReply(), gates: gates(calls)}); await w.session.login("V", "pw"); return calls; };
  assert.deepEqual(await via((c) => ({WebSocketSync: {useWebSocket: true, connect: () => c.push("ws")}, EntitySync: {connect: () => c.push("entity")}})), ["ws"]);
  assert.deepEqual(await via((c) => ({WebSocketSync: {useWebSocket: false, connect: () => c.push("ws")}, EntitySync: {connect: () => c.push("entity")}})), ["entity"]);
  assert.deepEqual(await via((c) => ({EntitySync: {connect: () => c.push("entity")}})), ["entity"]);
  assert.deepEqual(await via(() => ({})), []);
});

test("on login the effects run in order: message, status, GraphOps, streams, rooms, then the DSL explorer's room list", async () => {
  let w;
  const step = (name) => () => w.note(name);
  w = world({replies: loginReply(), gates: {GraphOpsBotRebind: step("graphops"), WebSocketSync: {useWebSocket: true, connect: step("stream")}, RoomManager: {fetchRooms: step("rooms"), fetchMyRooms: step("mine")}, populateRooms: step("populate")}});
  await w.ready;
  await w.session.login("V", "pw");
  const order = w.log.filter((e) => !["set", "remove", "clearInterval"].includes(e[0])).map((e) => e[0]);
  assert.deepEqual(order, ["message", "status", "graphops", "stream", "rooms", "mine", "populate"]);
  assert.deepEqual(w.messages, [["👤 Operator VIPER logged in", "info"]]);
});

test("a DSL explorer room list that throws is only noted, and without one nothing happens", async () => {
  const w = await started({replies: loginReply(), gates: {populateRooms: () => { throw new Error("boom"); }}});
  assert.equal((await w.session.login("V", "pw")).success, true);
  assert.deepEqual(w.log.filter((e) => e[0] === "debug"), [["debug", "populateRooms failed:"]]);
  const without = await started({replies: loginReply()});
  assert.equal((await without.session.login("V", "pw")).success, true);
});

test("on restore the effects run as on login, but with no DSL explorer room list and the restored message", async () => {
  const order = [];
  const w = await started({auth: {token: "saved", result: {valid: true, operator: OPERATOR}}, gates: {GraphOpsBotRebind: () => order.push("graphops"), EntitySync: {connect: () => order.push("stream")}, RoomManager: {fetchRooms: () => order.push("rooms"), fetchMyRooms: () => order.push("mine")}, populateRooms: () => order.push("populate")}});
  w.session.init();
  await settle();
  assert.deepEqual(order, ["graphops", "stream", "rooms", "mine"]);
  assert.deepEqual(w.messages, [["👤 Session restored for VIPER", "info"]]);
  assert.deepEqual(w.log.filter((e) => ["message", "status"].includes(e[0])).map((e) => e[0]), ["message", "status"]);
});

test("on logout each part is optional: GraphOps, the two streams and the rooms are only told if they exist", async () => {
  const none = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": {}}});
  await none.session.logout();
  assert.deepEqual(none.messages.at(-1), ["👤 Operator logged out", "info"]);
  const calls = [];
  const some = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": {}}, gates: {EntitySync: {disconnect: () => calls.push("entity")}}});
  await some.session.logout();
  assert.deepEqual(calls, ["entity"]);
});

// --- deactivating ---

test("deactivating stops the heartbeat and forgets the session without telling the subscribers", async () => {
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": {}}});
  const heard = [];
  w.session.on("logout", () => heard.push(1));
  const errors = await w.workspace.deactivate(SESSION_ID);
  assert.deepEqual(errors, []);
  assert.equal(w.timers[0].cleared, true);
  assert.deepEqual([w.session.sessionToken, w.session.isAuthenticated, heard], [null, false, []]);
});

test("answers that arrive after deactivation change nothing", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const w = await started({replies: {"POST /api/operator/login": () => held, "GET /api/operator/active": () => held}, auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  const pending = w.session.login("V", "pw");
  await settle();
  await w.workspace.deactivate(SESSION_ID);
  release({status: "ok", session: SESSION, operator: OPERATOR});
  assert.deepEqual(await pending, {success: false, message: "Deactivated"});
  assert.deepEqual([w.messages, w.timers.length, w.cookies, w.store], [[], 0, [], {}]);
});

test("a validation, a logout and a failure that finish after deactivation change nothing and say nothing", async () => {
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  const w = await started({auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  const validated = await (async () => { w.session.init(); await settle(); return w.session.isAuthenticated; })();
  assert.equal(validated, true);
  await w.workspace.deactivate(SESSION_ID);
  await w.workspace.activate(SESSION_ID);
  assert.equal(w.session.isAuthenticated, false, "a new activation starts signed out");
  const failing = await started({replies: {"POST /api/operator/login": () => held}});
  const pending = failing.session.login("V", "pw");
  await settle();
  await failing.workspace.deactivate(SESSION_ID);
  release(new Error("late"));
  await pending;
  assert.deepEqual(failing.errors, []);
});

// --- more on cleaning up and on answers that arrive after deactivation ---

const deferred = () => { let resolve, reject; const promise = new Promise((res, rej) => { resolve = res; reject = rej; }); return {promise, resolve, reject}; };

test("clearing the session stops the heartbeat once, and clearing again does not stop it twice", async () => {
  const w = await signedIn();
  w.session.clearSession();
  w.session.clearSession();
  assert.equal(w.log.filter((e) => e[0] === "clearInterval").length, 1);
});

test("a login that saves the session whose last write fails still succeeds and logs no error", async () => {
  const w = await started({replies: loginReply(), failStorage: ["scythe_session_token"]});
  assert.deepEqual(await w.session.login("V", "pw"), {success: true, operator: OPERATOR});
  assert.deepEqual(w.errors, []);
});

test("a validation that finishes after deactivation changes nothing: no state, no writes, no heartbeat, no restored event", async () => {
  const d = deferred();
  const w = await started({auth: {token: "saved", hold: d.promise}});
  const heard = [];
  w.session.on("restored", () => heard.push(1));
  w.session.init();
  await settle();
  await w.workspace.deactivate(SESSION_ID);
  d.resolve({valid: true, operator: OPERATOR});
  await settle();
  assert.deepEqual([w.store, w.cookies, w.timers.length, heard, w.messages], [{}, [], 0, [], []]);
});

test("a validation that fails after deactivation says nothing and clears nothing", async () => {
  const d = deferred();
  const w = await started({auth: {token: "saved", hold: d.promise}});
  w.session.init();
  await settle();
  await w.workspace.deactivate(SESSION_ID);
  d.reject(new Error("late"));
  await settle();
  assert.deepEqual([w.warnings, w.log.filter((e) => e[0] === "remove")], [[], []]);
});

test("a login that fails after deactivation is not logged", async () => {
  const d = deferred();
  const w = await started({replies: {"POST /api/operator/login": () => d.promise}});
  const pending = w.session.login("V", "pw");
  await settle();
  await w.workspace.deactivate(SESSION_ID);
  d.reject(new Error("late"));
  assert.deepEqual(await pending, {success: false, message: "late"});
  assert.deepEqual(w.errors, []);
});

test("a registration that finishes after deactivation does not log in", async () => {
  const d = deferred();
  const w = await started({replies: {"POST /api/operator/register": () => d.promise, ...loginReply()}});
  const pending = w.session.register("V", "e@x", "pw");
  await settle();
  await w.workspace.deactivate(SESSION_ID);
  d.resolve({status: "ok"});
  assert.deepEqual(await pending, {success: false, message: "Deactivated"});
  assert.deepEqual(w.requests.map((r) => r.key), ["POST /api/operator/register"]);
});

test("a registration that fails after deactivation is not logged", async () => {
  const d = deferred();
  const w = await started({replies: {"POST /api/operator/register": () => d.promise}});
  const pending = w.session.register("V", "e@x", "pw");
  await settle();
  await w.workspace.deactivate(SESSION_ID);
  d.reject(new Error("late"));
  await pending;
  assert.deepEqual(w.errors, []);
});

test("a logout that finishes after deactivation clears nothing and tells nobody; one that fails is not warned about", async () => {
  const d = deferred();
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": () => d.promise}});
  const heard = [];
  w.session.on("logout", () => heard.push(1));
  const pending = w.session.logout();
  await settle();
  await w.workspace.deactivate(SESSION_ID);
  w.log.length = 0;
  d.resolve({});
  await pending;
  assert.deepEqual([heard, w.log, w.messages.filter(([t]) => /logged out/.test(t))], [[], [], []]);
  const f = deferred();
  const failing = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": () => f.promise}});
  const second = failing.session.logout();
  await settle();
  await failing.workspace.deactivate(SESSION_ID);
  f.reject(new Error("late"));
  await second;
  assert.deepEqual(failing.warnings, []);
});

test("the active operators that fail after deactivation are not logged", async () => {
  const d = deferred();
  const w = await started({replies: {"GET /api/operator/active": () => d.promise}});
  const pending = w.session.getActiveOperators();
  await settle();
  await w.workspace.deactivate(SESSION_ID);
  d.reject(new Error("late"));
  assert.deepEqual(await pending, []);
  assert.deepEqual(w.errors, []);
});

// --- what is not there ---

test("the sign-in is logged once, and the controls have no cookie helpers", async () => {
  const w = await started({replies: loginReply()});
  await w.session.login("VIPER", "pw");
  assert.deepEqual(w.logs.filter((l) => /Logged in as/.test(l)), ["[OperatorSession] Logged in as VIPER (operator)"]);
  for (const gone of ["getCookie", "setCookie", "clearCookie", "onLoginSuccess", "onSessionRestored", "onLogout", "eventHandlers", "heartbeatInterval"]) assert.equal(gone in w.session, false, gone);
});

test("the page has no OperatorSession object of its own: it is set by the boot module, and nothing outside it assigns to the fields", () => {
  assert.equal(/const OperatorSession = \{/.test(page), false);
  assert.equal(page.includes("window.OperatorSession = OperatorSession"), false);
  assert.equal(page.includes("wireOperatorSessionEffects"), false);
  assert.equal(/OperatorSession\.(?:operatorId|operatorName|callsign|role|teamId|sessionToken|sessionId|isAuthenticated)\s*=[^=]/.test(page), false);
  assert.match(page, /workspace\.activate\("session"\)/);
});

test("the Operators panel's hook no longer calls a presence refresh that did not exist", () => {
  assert.equal(page.includes("refreshPresence"), false);
});

test("the page's token helper is the operator session's token, or empty, with no fallback to a SCYTHE_AUTH function that does not exist", () => {
  const start = page.indexOf("function sessionToken() {");
  const helper = page.slice(start, page.indexOf("\n        }\n", start) + "\n        }".length);
  assert.equal(helper.includes("getSessionToken"), false);
  const run = (window) => new Function("window", `${helper}\nreturn sessionToken();`)(window);
  assert.equal(run({OperatorSession: {sessionToken: "tok"}}), "tok");
  assert.equal(run({OperatorSession: {sessionToken: null}}), "");
  assert.equal(run({}), "");
});
