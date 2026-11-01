import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

// shared_auth.js is a classic browser script; run it as one with stubbed storage and transport.
const source = readFileSync(new URL("../assets/js/shared_auth.js", import.meta.url), "utf8");
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const jwt = (claims) => `${b64({alg: "HS256"})}.${b64(claims)}.sig`;
const FUTURE = Math.floor(Date.now() / 1000) + 3600;

function page({role = "instance", reply}) {
  const requests = [];
  const window = {SCYTHE_RUNTIME: {runtimeRole: role, storagePrefix: "inst"}, dispatchEvent() {}, location: {origin: "https://x"}};
  const context = {
    window, console: {log() {}, warn() {}, error() {}},
    atob: (s) => Buffer.from(s, "base64").toString("binary"),
    document: {cookie: ""},
    localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
    ScytheTransport: {
      setSessionToken() {}, clearSessionToken() {},
      fetch: async (path, opts) => { requests.push([path, opts?.headers?.["X-Session-Token"]]); return reply(); },
    },
  };
  vm.createContext(context);
  vm.runInContext(source + "\nthis.__auth = SCYTHE_AUTH;", context);
  return {auth: context.__auth, requests};
}

const ok = (operator) => () => ({ok: true, json: async () => ({status: "ok", operator})});

test("a session token that names nobody gets its operator from the server", async () => {
  const token = jwt({sub: "id-1", iss: "scythe-orchestrator", exp: FUTURE, session_id: "s"});
  const {auth, requests} = page({reply: ok({operator_id: "id-1", callsign: "hub-abc123", email: "e@x", role: "operator"})});
  const r = await auth.validateSession(token);
  assert.equal(r.valid, true);
  assert.deepEqual([r.operator.callsign, r.operator.role, r.operator.operator_id], ["hub-abc123", "operator", "id-1"]);
  assert.deepEqual(requests, [["/api/operator/session", token]]);
});

test("a token that already names the operator needs no request", async () => {
  const token = jwt({sub: "id-2", preferred_username: "alex", exp: FUTURE});
  const {auth, requests} = page({reply: () => { throw new Error("must not be called"); }});
  const r = await auth.validateSession(token);
  assert.equal(r.operator.callsign, "alex");
  assert.deepEqual(requests, []);
});

test("if the server cannot say, the session is still valid with no callsign", async () => {
  const token = jwt({sub: "id-3", exp: FUTURE});
  for (const reply of [() => ({ok: false}), () => { throw new Error("offline"); }, ok(null), ok({callsign: ""})]) {
    const {auth} = page({reply});
    const r = await auth.validateSession(token);
    assert.equal(r.valid, true);
    assert.equal(r.operator.callsign, null);
  }
});

test("an expired token is still invalid and asks nothing", async () => {
  const token = jwt({sub: "id-4", exp: Math.floor(Date.now() / 1000) - 10});
  const {auth, requests} = page({reply: ok({callsign: "x"})});
  assert.equal((await auth.validateSession(token)).valid, false);
  assert.deepEqual(requests, []);
});
