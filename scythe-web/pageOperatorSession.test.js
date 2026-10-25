import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

/** The page's OperatorSession object literal, from `const OperatorSession = {` to its closing `};`. */
const objectSource = (() => {
  const start = page.indexOf("const OperatorSession = {") + "const OperatorSession = ".length;
  const end = page.indexOf("        };\n        \n        // What the page does when the session changes", start);
  assert.ok(start > 30 && end > start, "OperatorSession is in the page");
  return page.slice(start, end + "        }".length);
})();

/** The source of a function declaration in the page, from its `function name(` to its closing brace at the same indent. */
function functionSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start > 0, `${name} is in the page`);
  const indent = page.lastIndexOf("\n", start) + 1;
  const pad = page.slice(indent, start);
  const end = page.indexOf(`\n${pad}}\n`, start);
  assert.ok(end > start);
  return page.slice(start, end + pad.length + 2);
}
const wireSource = functionSource("wireOperatorSessionEffects");

const NOW = Date.UTC(2026, 9, 25, 12, 0, 0);
const OPERATOR = {operator_id: "op-1", callsign: "VIPER", email: "viper@example.org", role: "operator", team_id: "t1"};
const SESSION = {session_id: "s-1", session_token: "tok-1"};

/**
 * The page's own OperatorSession, run against fakes for everything it touches. Nothing here is a real token: they are opaque strings.
 * `replies` maps "METHOD url" (GET by default) to the JSON body, or a function returning it (a function that throws fails the request).
 */
function world({replies = {}, auth = {}, viewer = null, storage = {}, failStorage = [], failCookie = false, gates = {}, wired = true} = {}) {
  const log = [], requests = [], messages = [], cookies = [], store = {...storage}, timers = [];
  const note = (...a) => log.push(a);
  const localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { if (failStorage.includes(k)) throw new Error("quota"); store[k] = String(v); note("set", k); },
    removeItem: (k) => { delete store[k]; note("remove", k); },
  };
  const document = {};
  Object.defineProperty(document, "cookie", {set: (v) => { if (failCookie) throw new Error("blocked"); cookies.push(v); }, get: () => ""});
  class FixedDate extends Date { constructor(...a) { super(...(a.length ? a : [NOW])); } }
  const warnings = [], errors = [], logs = [];
  const consoleFake = {log: (...a) => logs.push(a.join(" ")), warn: (...a) => warnings.push(a.join(" ")), error: (...a) => errors.push(a.join(" ")), debug: (...a) => note("debug", a[0])};
  const win = {
    viewer, WebSocketSync: gates.WebSocketSync, EntitySync: gates.EntitySync, RoomManager: gates.RoomManager,
    GraphOpsBotRebind: gates.GraphOpsBotRebind, GraphOpsBotReset: gates.GraphOpsBotReset,
  };
  const SCYTHE_AUTH = {
    restoreSession: () => auth.token ?? null,
    validateSession: async (token) => { note("validate", token); if (auth.throws) throw new Error("bad"); return auth.result ?? {valid: false}; },
  };
  win.SCYTHE_AUTH = SCYTHE_AUTH;   // validateSession reaches it as window.SCYTHE_AUTH
  const scope = {
    fetch: async (url, init = {}) => {
      const key = `${init.method || "GET"} ${url}`;
      requests.push({key, init});
      if (!(key in replies)) throw new Error(`no reply for ${key}`);
      const reply = typeof replies[key] === "function" ? replies[key](init) : replies[key];
      return {json: async () => reply};
    },
    localStorage, document, window: win, console: consoleFake, SCYTHE_AUTH, Date: FixedDate,
    setInterval: (fn, ms) => { timers.push({fn, ms, cleared: false}); return timers.length; },
    clearInterval: (id) => { timers[id - 1].cleared = true; note("clearInterval"); },
    Cesium: {Math: {toDegrees: (r) => r * 100}},
    addConsoleMessage: (text, kind) => { messages.push([text, kind]); note("message"); },
    updateOperatorStatusUI: () => note("status"),
    escapeHtml: (s) => s,
    WebSocketSync: gates.WebSocketSync, EntitySync: gates.EntitySync, RoomManager: gates.RoomManager, populateRooms: gates.populateRooms,
  };
  const keys = Object.keys(scope);
  const session = new Function(...keys, `return (${objectSource});`)(...keys.map((k) => scope[k]));
  if (wired) new Function(...keys, `return (${wireSource});`)(...keys.map((k) => scope[k]))(session);   // what the page does with the session's events
  return {session, log, requests, messages, cookies, store, timers, warnings, errors, logs, win, note};
}

const loginReply = (extra = {}) => ({"POST /api/operator/login": {status: "ok", session: SESSION, operator: OPERATOR, ...extra}});
const signedIn = async (options = {}) => { const w = world({replies: loginReply(), ...options}); await w.session.login("VIPER", "pw"); w.log.length = 0; return w; };
const settle = () => new Promise((resolve) => setImmediate(resolve));

// --- the state ---

test("a new session is signed out, with nothing set, and the heartbeat is every 30 seconds", () => {
  const {session} = world();
  assert.deepEqual([session.operatorId, session.operatorName, session.callsign, session.role, session.teamId, session.sessionToken, session.sessionId, session.isAuthenticated, session.heartbeatInterval],
    [null, null, null, null, null, null, null, false, null]);
  assert.equal(session.HEARTBEAT_INTERVAL, 30000);
});

// --- restoring and validating ---

test("init does nothing without a saved token", async () => {
  const w = world({auth: {token: null}});
  w.session.init();
  await settle();
  assert.deepEqual([w.session.sessionToken, w.log], [null, []]);
});

test("init keeps the saved token and validates it", async () => {
  const w = world({auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  w.session.init();
  assert.equal(w.session.sessionToken, "saved");
  await settle();
  assert.deepEqual(w.log.filter((e) => e[0] === "validate"), [["validate", "saved"]]);
  assert.equal(w.session.isAuthenticated, true);
});

test("a valid session is filled in from the operator, starts the heartbeat, and runs the restored effects", async () => {
  const calls = [];
  const w = world({auth: {token: "saved", result: {valid: true, operator: OPERATOR}}, gates: {EntitySync: {connect: () => calls.push("entity")}, RoomManager: {fetchRooms: () => calls.push("rooms"), fetchMyRooms: () => calls.push("mine")}}});
  w.session.sessionToken = "saved";
  assert.equal(await w.session.validateSession(), true);
  assert.deepEqual([w.session.sessionId, w.session.operatorId, w.session.operatorName, w.session.callsign, w.session.role, w.session.teamId, w.session.isAuthenticated],
    ["op-1", "op-1", "viper@example.org", "VIPER", "operator", "t1", true]);
  assert.equal(w.timers.length, 1);
  assert.equal(w.timers[0].ms, 30000);
  assert.deepEqual(w.messages, [["👤 Session restored for VIPER", "info"]]);
  assert.deepEqual(calls, ["entity", "rooms", "mine"]);
});

test("a valid session whose operator has no id gets no session id", async () => {
  const w = world({auth: {result: {valid: true, operator: {...OPERATOR, operator_id: undefined}}}});
  w.session.sessionToken = "saved";
  await w.session.validateSession();
  assert.equal(w.session.sessionId, null);
});

test("validating with no token is false and asks nobody", async () => {
  const w = world();
  assert.equal(await w.session.validateSession(), false);
  assert.deepEqual(w.log, []);
});

test("an invalid session, or a validation that throws, clears the session and is false; a throw is warned about", async () => {
  const invalid = world({auth: {result: {valid: false}}});
  invalid.session.sessionToken = "saved";
  assert.equal(await invalid.session.validateSession(), false);
  assert.equal(invalid.session.sessionToken, null);
  const throwing = world({auth: {throws: true}});
  throwing.session.sessionToken = "saved";
  assert.equal(await throwing.session.validateSession(), false);
  assert.equal(throwing.session.sessionToken, null);
  assert.match(throwing.warnings[0], /Session validation failed/);
});

// --- logging in and registering ---

test("logging in posts the callsign and password, fills the session in, starts the heartbeat, and runs the login effects", async () => {
  const calls = [];
  const w = world({replies: loginReply(), gates: {WebSocketSync: {useWebSocket: true, connect: () => calls.push("ws")}, EntitySync: {connect: () => calls.push("entity")}, RoomManager: {fetchRooms: () => calls.push("rooms"), fetchMyRooms: () => calls.push("mine")}}});
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
  const w = world({replies: {"POST /api/operator/login": {status: "error", message: "bad credentials"}}});
  assert.deepEqual(await w.session.login("VIPER", "pw"), {success: false, message: "bad credentials"});
  assert.deepEqual([w.session.isAuthenticated, w.session.sessionToken, w.timers.length, w.messages, w.cookies], [false, null, 0, [], []]);
});

test("a login that fails to reach the server returns the error's message and logs it", async () => {
  const w = world({replies: {"POST /api/operator/login": () => { throw new Error("offline"); }}});
  assert.deepEqual(await w.session.login("VIPER", "pw"), {success: false, message: "offline"});
  assert.match(w.errors[0], /Login error/);
});

test("registering posts the details (the role defaults to operator), and a good registration logs in and returns the login's result", async () => {
  const w = world({replies: {"POST /api/operator/register": {status: "ok"}, ...loginReply()}});
  const result = await w.session.register("VIPER", "v@example.org", "pw");
  assert.deepEqual(result, {success: true, operator: OPERATOR});
  assert.deepEqual(w.requests.map((r) => r.key), ["POST /api/operator/register", "POST /api/operator/login"]);
  assert.equal(w.requests[0].init.body, '{"callsign":"VIPER","email":"v@example.org","password":"pw","role":"operator"}');
  assert.equal(w.session.isAuthenticated, true);
});

test("registering sends the role it is given, and a refusal or a failure comes back as a message without logging in", async () => {
  const w = world({replies: {"POST /api/operator/register": {status: "error", message: "taken"}}});
  assert.deepEqual(await w.session.register("A", "a@b.c", "pw", "observer"), {success: false, message: "taken"});
  assert.equal(JSON.parse(w.requests[0].init.body).role, "observer");
  assert.equal(w.requests.length, 1);
  const failing = world({replies: {"POST /api/operator/register": () => { throw new Error("offline"); }}});
  assert.deepEqual(await failing.session.register("A", "a@b.c", "pw"), {success: false, message: "offline"});
  assert.match(failing.errors[0], /Registration error/);
});

// --- what a session persists ---

test("a session is saved in three places: localStorage.operatorSession, the cookie operatorSessionToken for seven days, and localStorage.scythe_session_token", async () => {
  const w = world({replies: loginReply()});
  await w.session.login("VIPER", "pw");
  assert.equal(w.store.operatorSession, '{"sessionToken":"tok-1"}');
  assert.equal(w.store.scythe_session_token, "tok-1");
  assert.deepEqual(w.cookies, [`operatorSessionToken=tok-1; expires=${new Date(NOW + 7 * 24 * 3600 * 1000).toUTCString()}; path=/`]);
});

test("each of the three writes may fail without stopping the others; the first two say so", async () => {
  const first = world({replies: loginReply(), failStorage: ["operatorSession"]});
  await first.session.login("VIPER", "pw");
  assert.equal(first.cookies.length, 1);
  assert.equal(first.store.scythe_session_token, "tok-1");
  assert.match(first.warnings[0], /Could not persist operatorSession/);
  const last = world({replies: loginReply(), failStorage: ["scythe_session_token"]});
  await last.session.login("VIPER", "pw");
  assert.equal(last.store.operatorSession, '{"sessionToken":"tok-1"}');
  assert.deepEqual(last.warnings, [], "the last write fails silently");
  assert.equal(last.session.isAuthenticated, true);
});

test("a blocked cookie is warned about and the session is still set", async () => {
  const w = world({replies: loginReply(), failCookie: true});
  await w.session.login("VIPER", "pw");
  assert.match(w.warnings[0], /Could not set session cookie/);
  assert.equal(w.store.scythe_session_token, "tok-1");
  assert.equal(w.session.isAuthenticated, true);
});

test("a session with no token is saved with an empty cookie value", async () => {
  const w = world({replies: loginReply({session: {session_id: "s"}})});
  await w.session.login("VIPER", "pw");
  assert.deepEqual(w.cookies.map((c) => c.split(";")[0]), ["operatorSessionToken="]);
});

test("PINNED: clearing the session forgets the fields, stops the heartbeat, and removes localStorage.operatorSession and the cookie, but leaves scythe_session_token and anything SCYTHE_AUTH keeps", async () => {
  const w = await signedIn({storage: {"scythe:orchestrator:operatorSession": '{"sessionToken":"auth-copy"}'}});
  w.session.clearSession();
  assert.deepEqual([w.session.operatorId, w.session.operatorName, w.session.callsign, w.session.role, w.session.teamId, w.session.sessionToken, w.session.sessionId, w.session.isAuthenticated],
    [null, null, null, null, null, null, null, false]);
  assert.equal(w.session.heartbeatInterval, null);
  assert.equal(w.timers[0].cleared, true);
  assert.equal("operatorSession" in w.store, false);
  assert.equal(w.cookies.at(-1), "operatorSessionToken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/");
  assert.equal(w.store.scythe_session_token, "tok-1", "PINNED: left behind");
  assert.equal(w.store["scythe:orchestrator:operatorSession"], '{"sessionToken":"auth-copy"}', "PINNED: SCYTHE_AUTH's copy is not touched");
});

// --- logging out ---

test("logging out with no session does nothing", async () => {
  const w = world();
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

const camera = {positionCartographic: {latitude: 0.5, longitude: 0.25, height: 1234}, heading: 0.1};

test("starting the heartbeat stops the one before, and ticks every 30 seconds", async () => {
  const w = await signedIn();
  w.session.startHeartbeat();
  assert.equal(w.timers.length, 2);
  assert.equal(w.timers[0].cleared, true);
  assert.deepEqual([w.timers[1].ms, w.timers[1].cleared], [30000, false]);
});

test("a tick without a session does nothing", async () => {
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/heartbeat": {}}});
  w.session.sessionToken = null;
  await w.timers[0].fn();
  assert.equal(w.requests.filter((r) => /heartbeat/.test(r.key)).length, 0);
});

test("a tick posts the token and the camera's centre (latitude, longitude), altitude and heading in degrees", async () => {
  const w = await signedIn({viewer: {camera}, replies: {...loginReply(), "POST /api/operator/heartbeat": {}}});
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

test("stopping the heartbeat clears it once and is harmless twice", async () => {
  const w = await signedIn();
  w.session.stopHeartbeat();
  w.session.stopHeartbeat();
  assert.deepEqual([w.session.heartbeatInterval, w.log.filter((e) => e[0] === "clearInterval").length], [null, 1]);
});

// --- the other operators ---

test("the active operators are the server's list, or none; a failure is logged", async () => {
  const w = world({replies: {"GET /api/operator/active": {operators: [{operator_id: "a"}]}}});
  assert.deepEqual(await w.session.getActiveOperators(), [{operator_id: "a"}]);
  const none = world({replies: {"GET /api/operator/active": {}}});
  assert.deepEqual(await none.session.getActiveOperators(), []);
  const failing = world({replies: {"GET /api/operator/active": () => { throw new Error("offline"); }}});
  assert.deepEqual(await failing.session.getActiveOperators(), []);
  assert.match(failing.errors[0], /Error getting active operators/);
});

// --- the three callbacks ---

test("on login the streams connect through WebSocketSync if it is on offer, else through EntitySync, else not at all", async () => {
  const via = async (gates) => { const calls = []; const w = world({gates: gates(calls)}); w.session.callsign = "V"; w.session._emit("login"); return calls; };
  assert.deepEqual(await via((c) => ({WebSocketSync: {useWebSocket: true, connect: () => c.push("ws")}, EntitySync: {connect: () => c.push("entity")}})), ["ws"]);
  assert.deepEqual(await via((c) => ({WebSocketSync: {useWebSocket: false, connect: () => c.push("ws")}, EntitySync: {connect: () => c.push("entity")}})), ["entity"]);
  assert.deepEqual(await via((c) => ({EntitySync: {connect: () => c.push("entity")}})), ["entity"]);
  assert.deepEqual(await via(() => ({})), []);
});

test("on login the effects run in order: message, status, GraphOps, streams, rooms, then the DSL explorer's room list", () => {
  let w;
  const step = (name) => () => w.note(name);
  w = world({gates: {
    GraphOpsBotRebind: step("graphops"), WebSocketSync: {useWebSocket: true, connect: step("stream")},
    RoomManager: {fetchRooms: step("rooms"), fetchMyRooms: step("mine")}, populateRooms: step("populate"),
  }});
  w.session.callsign = "V";
  w.session._emit("login");
  const order = w.log.filter((e) => !["set", "remove", "clearInterval"].includes(e[0])).map((e) => e[0]);
  assert.deepEqual(order, ["message", "status", "graphops", "stream", "rooms", "mine", "populate"]);
  assert.deepEqual(w.messages, [["👤 Operator V logged in", "info"]]);
});

test("a DSL explorer room list that throws is only noted, and without one nothing happens", () => {
  const w = world({gates: {populateRooms: () => { throw new Error("boom"); }}});
  w.session.callsign = "V";
  w.session._emit("login");
  assert.deepEqual(w.log.filter((e) => e[0] === "debug"), [["debug", "populateRooms failed:"]]);
  const without = world();
  without.session.callsign = "V";
  without.session._emit("login");
});

test("on restore the effects run as on login, but with no DSL explorer room list and the restored message", () => {
  const order = [];
  const w = world({gates: {GraphOpsBotRebind: () => order.push("graphops"), EntitySync: {connect: () => order.push("stream")}, RoomManager: {fetchRooms: () => order.push("rooms"), fetchMyRooms: () => order.push("mine")}, populateRooms: () => order.push("populate")}});
  w.session.callsign = "V";
  w.session._emit("restored");
  assert.deepEqual(order, ["graphops", "stream", "rooms", "mine"]);
  assert.deepEqual(w.messages, [["👤 Session restored for V", "info"]]);
  assert.deepEqual(w.log.filter((e) => ["message", "status"].includes(e[0])).map((e) => e[0]), ["message", "status"], "the message, then the status");
  assert.deepEqual(w.log.filter((e) => e[0] === "status").length, 1);
});

test("on logout each part is optional: GraphOps, the two streams and the rooms are only told if they exist", () => {
  const none = world();
  none.session._emit("logout");
  assert.deepEqual(none.messages, [["👤 Operator logged out", "info"]]);
  const some = world({gates: {EntitySync: {disconnect() { some.note("entity"); }}}});
  some.session._emit("logout");
  assert.deepEqual(some.log.filter((e) => e[0] === "entity"), [["entity"]]);
});

test("the object has no callbacks any more, only events; nothing outside it writes to its fields", () => {
  assert.equal(/\bon(?:LoginSuccess|SessionRestored|Logout)\s*[:(=]|\.on(?:LoginSuccess|SessionRestored|Logout)\b/.test(page), false, "no member, call or override of the old callbacks");
  const outside = page.replace(objectSource, "");
  assert.equal(/OperatorSession\.(?:operatorId|operatorName|callsign|role|teamId|sessionToken|sessionId|isAuthenticated)\s*=[^=]/.test(outside), false);
});

// --- what is not there ---

test("the sign-in is logged once, and the session has no cookie reader, since nothing reads a cookie from it", async () => {
  const w = world({replies: loginReply()});
  await w.session.login("VIPER", "pw");
  assert.deepEqual(w.logs.filter((l) => /Logged in as/.test(l)), ["[OperatorSession] Logged in as VIPER (operator)"]);
  assert.equal("getCookie" in w.session, false);
  assert.deepEqual(["setCookie", "clearCookie"].map((n) => typeof w.session[n]), ["function", "function"]);
});

test("the Operators panel's hook no longer calls a presence refresh that did not exist", () => {
  assert.equal(page.includes("refreshPresence"), false);
  assert.match(page, /if \(panelName === 'operators'\) \{\s*if \(typeof RoomManager !== 'undefined' && RoomManager\.fetchRooms\) \{/);
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

// --- the events ---

test("a session with nothing wired to it still logs in, restores and logs out: emitting with no handler is harmless", async () => {
  const w = world({wired: false, replies: {...loginReply(), "POST /api/operator/logout": {}}, auth: {result: {valid: true, operator: OPERATOR}}});
  assert.equal((await w.session.login("V", "pw")).success, true);
  w.session.sessionToken = "saved";
  assert.equal(await w.session.validateSession(), true);
  await w.session.logout();
  assert.deepEqual(w.messages, []);
});

test("handlers for an event run in the order they were added, each once per emit; on and off return the session; off removes one", () => {
  const w = world({wired: false});
  const order = [];
  const a = () => order.push("a");
  assert.equal(w.session.on("login", a), w.session);
  w.session.on("login", () => order.push("b"));
  w.session.on("logout", () => order.push("c"));
  w.session._emit("login");
  assert.deepEqual(order, ["a", "b"]);
  assert.equal(w.session.off("login", a), w.session);
  w.session._emit("login");
  assert.deepEqual(order, ["a", "b", "b"]);
  w.session.off("never_added", a);
  w.session._emit("unknown");
});

test("login, restore and logout each emit their own event, and only after the state is set", async () => {
  const w = world({wired: false, replies: {...loginReply(), "POST /api/operator/logout": {}}, auth: {result: {valid: true, operator: OPERATOR}}});
  const seen = [];
  for (const event of ["login", "restored", "logout"]) w.session.on(event, () => seen.push([event, w.session.isAuthenticated, w.session.callsign]));
  await w.session.login("V", "pw");
  w.session.sessionToken = "saved";
  await w.session.validateSession();
  await w.session.logout();
  assert.deepEqual(seen, [["login", true, "VIPER"], ["restored", true, "VIPER"], ["logout", false, null]]);
});

test("PINNED: an error in a handler reaches the caller, as an error in the old callbacks did: a failing login effect makes login report failure although the session is set", async () => {
  const w = world({wired: false, replies: loginReply()});
  w.session.on("login", () => { throw new Error("effect failed"); });
  assert.deepEqual(await w.session.login("V", "pw"), {success: false, message: "effect failed"});
  assert.equal(w.session.isAuthenticated, true);
});

test("PINNED: a failing restore effect makes the restore fail and signs the session out", async () => {
  const w = world({wired: false, auth: {result: {valid: true, operator: OPERATOR}}});
  w.session.on("restored", () => { throw new Error("effect failed"); });
  w.session.sessionToken = "saved";
  assert.equal(await w.session.validateSession(), false);
  assert.equal(w.session.isAuthenticated, false);
  assert.match(w.warnings[0], /Session validation failed/);
});

test("PINNED: a failing logout effect makes logout reject, after the session was cleared", async () => {
  const w = await signedIn({replies: {...loginReply(), "POST /api/operator/logout": {}}, wired: false});
  w.session.on("logout", () => { throw new Error("effect failed"); });
  await assert.rejects(w.session.logout(), /effect failed/);
  assert.equal(w.session.sessionToken, null);
});

test("an error in a handler stops the handlers after it", () => {
  const w = world({wired: false});
  const order = [];
  w.session.on("login", () => { order.push(1); throw new Error("x"); });
  w.session.on("login", () => order.push(2));
  assert.throws(() => w.session._emit("login"), /x/);
  assert.deepEqual(order, [1]);
});

test("the page wires the effects to the session once, and the wiring registers exactly the three events", () => {
  assert.equal(page.split("wireOperatorSessionEffects(OperatorSession);").length - 1, 1);
  const w = world({wired: false});
  const seen = [];
  const fake = {callsign: "V", on(event) { seen.push(event); return fake; }};
  new Function("window", "WebSocketSync", "EntitySync", "RoomManager", "addConsoleMessage", "updateOperatorStatusUI", "escapeHtml", "populateRooms", "console", `return (${wireSource});`)({}, undefined, undefined, undefined, () => {}, () => {}, (x) => x, undefined, console)(fake);
  assert.deepEqual(seen, ["login", "restored", "logout"]);
  assert.equal(Object.keys(w.session.eventHandlers).length, 0);
});
