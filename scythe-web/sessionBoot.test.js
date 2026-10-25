import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {bootWorkspace} from "./workspaceBoot.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}, info() {}};
const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");
const NOW = Date.UTC(2026, 9, 25, 12);
const OPERATOR = {operator_id: "op-1", callsign: "VIPER", email: "v@e.org", role: "operator", team_id: "t1"};

/** Runs `fn` with the clock fixed, as the cookie's expiry reads it. */
async function atNow(fn) {
  const Real = globalThis.Date;
  globalThis.Date = class extends Real { constructor(...a) { super(...(a.length ? a : [NOW])); } static now() { return NOW; } };
  try { return await fn(); } finally { globalThis.Date = Real; }
}

/** A fake window for the real boot module: storage, a cookie jar, a fake SCYTHE_AUTH, a viewer, and the objects the effects call. */
function sessionWindow({viewer, auth = {}, replies = {}} = {}) {
  const store = {}, cookies = [], requests = [], messages = [], calls = [], timers = [], authCalls = [];
  const win = {
    console: quiet, Event: class { constructor(type) { this.type = type; } }, dispatchEvent() {},
    setInterval: (fn, ms) => { timers.push({fn, ms, cleared: false}); return timers.length; }, clearInterval: (id) => { timers[id - 1].cleared = true; },
    setTimeout: () => 1, clearTimeout() {},
    localStorage: {getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; }},
    document: {}, viewer, Cesium: {Math: {toDegrees: (r) => r * 100}},
    SCYTHE_AUTH: {restoreSession: () => auth.token ?? null, validateSession: async () => auth.result ?? {valid: false}, logout: () => authCalls.push("logout")},
    fetch: async (url, init = {}) => { const key = `${init.method || "GET"} ${url}`; requests.push({key, init}); if (!(key in replies)) throw new Error(`no reply for ${key}`); return {json: async () => replies[key]}; },
    addConsoleMessage: (text, kind) => messages.push([text, kind]), updateOperatorStatusUI: () => calls.push("status"),
  };
  Object.defineProperty(win.document, "cookie", {set: (v) => cookies.push(v), get: () => ""});
  // the boot module sets the real EntitySync and RoomManager controls, so the stand-ins that record the effects go in after it
  const install = () => {
    win.EntitySync = {connect: () => calls.push("es.connect"), disconnect: () => calls.push("es.disconnect")};
    win.RoomManager = {fetchRooms: () => calls.push("rooms.all"), fetchMyRooms: () => calls.push("rooms.mine"), reset: () => calls.push("rooms.reset")};
  };
  return {win, store, cookies, requests, messages, calls, timers, authCalls, install};
}
const loginReplies = {"POST /api/operator/login": {status: "ok", session: {session_id: "s", session_token: "tok-1"}, operator: OPERATOR}};

test("booting registers the session tool inactive and sets OperatorSession to its controls", () => {
  const {win} = sessionWindow();
  const workspace = bootWorkspace(win);
  assert.equal(workspace.state("session"), "inactive");
  for (const name of ["init", "validateSession", "login", "register", "logout", "clearSession", "getActiveOperators", "on", "off"]) assert.equal(typeof win.OperatorSession[name], "function", name);
  assert.equal(win.OperatorSession.isAuthenticated, false);
});

test("the saved session store reads SCYTHE_AUTH when it is asked, writes localStorage, and on clearing removes both old copies and signs SCYTHE_AUTH out", async () => {
  const {win, store, cookies, authCalls} = sessionWindow({auth: {token: "saved", result: {valid: true, operator: OPERATOR}}});
  store.operatorSession = '{"sessionToken":"old"}';
  const workspace = bootWorkspace(win);
  await workspace.activate("session");
  win.OperatorSession.init();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(win.OperatorSession.sessionToken, "saved");
  assert.equal(win.OperatorSession.callsign, "VIPER");
  assert.equal(store.scythe_session_token, "saved");
  assert.deepEqual([cookies, authCalls], [[], []], "no cookie is written, and SCYTHE_AUTH is left alone while signed in");
  win.OperatorSession.clearSession();
  assert.deepEqual(store, {});
  assert.deepEqual(cookies, ["operatorSessionToken=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/"]);
  assert.deepEqual(authCalls, ["logout"]);
});

test("clearing with a SCYTHE_AUTH that has no logout still clears the rest, and warns of nothing", async () => {
  const {win, store} = sessionWindow();
  const warnings = [];
  win.console = {...quiet, warn: (...a) => warnings.push(a.join(" "))};
  delete win.SCYTHE_AUTH.logout;
  const workspace = bootWorkspace(win);
  await workspace.activate("session");
  store.scythe_session_token = "x";
  win.OperatorSession.clearSession();
  assert.deepEqual([store, warnings], [{}, []]);
});

test("the heartbeat sends the camera's view: latitude and longitude, altitude and heading in degrees", async () => {
  const {win, requests, timers} = sessionWindow({viewer: {camera: {positionCartographic: {latitude: 0.5, longitude: 0.25, height: 1234}, heading: 0.1}}, replies: {...loginReplies, "POST /api/operator/heartbeat": {}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("session");
  await win.OperatorSession.login("V", "pw");
  assert.equal(timers[0].ms, 30000);
  await timers[0].fn();
  assert.deepEqual(JSON.parse(requests.at(-1).init.body), {current_view: {center: [50, 25], altitude: 1234, heading: 10}});
});

test("the heartbeat sends no view when the page has no viewer or no camera", async () => {
  for (const viewer of [undefined, {}]) {
    const {win, requests, timers} = sessionWindow({viewer, replies: {...loginReplies, "POST /api/operator/heartbeat": {}}});
    const workspace = bootWorkspace(win);
    await workspace.activate("session");
    await win.OperatorSession.login("V", "pw");
    await timers[0].fn();
    assert.equal(requests.at(-1).init.body, '{"current_view":null}');
  }
});

test("login and logout run the page's effects through the boot module, in order", async () => {
  const {win, calls, messages, install} = sessionWindow({replies: {...loginReplies, "POST /api/operator/logout": {}}});
  win.GraphOpsBotRebind = () => calls.push("graphops.rebind");
  win.GraphOpsBotReset = (why) => calls.push("graphops.reset:" + why);
  const workspace = bootWorkspace(win);
  install();
  await workspace.activate("session");
  await win.OperatorSession.login("V", "pw");
  assert.deepEqual(calls, ["status", "graphops.rebind", "es.connect", "rooms.all", "rooms.mine"]);
  assert.deepEqual(messages, [["👤 Operator VIPER logged in", "info"]]);
  calls.length = 0;
  await win.OperatorSession.logout();
  assert.deepEqual(calls, ["status", "graphops.reset:Operator logged out", "es.disconnect", "rooms.reset"]);
  assert.deepEqual(messages.at(-1), ["👤 Operator logged out", "info"]);
});

test("the callsign in the console messages is escaped, since the page puts them in as HTML", async () => {
  const evil = "<img src=x onerror=alert(1)>";
  const {win, messages} = sessionWindow({replies: {"POST /api/operator/login": {status: "ok", session: {session_id: "s", session_token: "t"}, operator: {...OPERATOR, callsign: evil}}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("session");
  await win.OperatorSession.login("V", "pw");
  assert.deepEqual(messages, [["👤 Operator &lt;img src=x onerror=alert(1)&gt; logged in", "info"]]);
});

test("the other tools' services follow the session: the token they read, the id, the clear, and the list of operators", async () => {
  const {win} = sessionWindow({replies: {...loginReplies, "GET /api/operator/active": {operators: [{operator_id: "x"}]}}});
  const workspace = bootWorkspace(win);
  await workspace.activate("session");
  await win.OperatorSession.login("V", "pw");
  assert.equal(win.OperatorSession.sessionToken, "tok-1");
  assert.equal(win.OperatorSession.operatorId, "op-1");
  assert.deepEqual(await win.OperatorSession.getActiveOperators(), [{operator_id: "x"}]);
  win.OperatorSession.clearSession();
  assert.equal(win.OperatorSession.sessionToken, null);
});

test("the page activates the session tool first, and keeps the globe's and the services' reads working", () => {
  const at = (id) => page.indexOf(`workspace.activate("${id}")`);
  assert.ok(at("session") > 0 && at("session") < at("entity-sync"));
  assert.match(page, /window\.OperatorSession\?\.sessionToken/);
});
