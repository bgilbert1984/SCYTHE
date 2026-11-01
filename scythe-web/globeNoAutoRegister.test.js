import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

// The globe must never create or sign in an account of its own. cesium-hypergraph-globe.js is far too
// large to load here, so pull out the one method under test and run it against stubs.
const globeJs = readFileSync(new URL("../cesium-hypergraph-globe.js", import.meta.url), "utf8");
const globeHtml = readFileSync(new URL("../cesium-hypergraph-globe.html", import.meta.url), "utf8");

function reAuthMethod() {
  const start = globeJs.indexOf("  async _reAuth() {");
  const end = globeJs.indexOf("  _startStreamHeartbeat() {");
  assert.ok(start > 0 && end > start, "found _reAuth");
  return `({${globeJs.slice(start, end)}})`;
}

function run({current, rejected = null, failStorage = false}) {
  const calls = [];
  const local = {scythe_callsign: "SCYTHE-OLD", scythe_session_token: "stale"};
  const context = {
    console: {warn: () => {}, log: () => {}},
    fetch: (...args) => { calls.push(args); throw new Error("the globe must not call fetch here"); },
    localStorage: {
      removeItem: (k) => { if (failStorage) throw new Error("blocked"); delete local[k]; },
      getItem: (k) => local[k] ?? null,
      setItem: () => { throw new Error("the globe must not store a guest callsign"); },
    },
    window: {},
    SCYTHE_AUTH: current === undefined ? undefined : {restoreSession: () => current},
  };
  context.window.SCYTHE_AUTH = context.SCYTHE_AUTH;
  vm.createContext(context);
  const method = vm.runInContext(reAuthMethod(), context)._reAuth;
  const globe = {_apiBase: "https://x", _streamToken: null, _rejectedToken: rejected};
  return method.call(globe).then(() => ({globe, calls, local}));
}

test("re-auth never registers or logs in, and forgets the old guest callsign", async () => {
  const {calls, local} = await run({current: "op-token"});
  assert.deepEqual(calls, []);
  assert.equal("scythe_callsign" in local, false);
});

test("re-auth adopts the operator's current session when it is not the rejected one", async () => {
  const {globe} = await run({current: "fresh", rejected: "old"});
  assert.equal(globe._streamToken, "fresh");
});

test("re-auth does not retry the token the server just rejected", async () => {
  const {globe} = await run({current: "old", rejected: "old"});
  assert.equal(globe._streamToken, null);
});

test("with no operator session the stream connects without a token", async () => {
  for (const current of [null, undefined]) {
    const {globe, calls} = await run({current});
    assert.equal(globe._streamToken, null);
    assert.deepEqual(calls, []);
  }
});

test("blocked storage does not stop re-auth", async () => {
  const {globe} = await run({current: "fresh", failStorage: true});
  assert.equal(globe._streamToken, "fresh");
});

test("neither globe file contains an account-creating call", () => {
  for (const [name, text] of [["cesium-hypergraph-globe.js", globeJs], ["cesium-hypergraph-globe.html", globeHtml]]) {
    assert.equal(/operator\/register/.test(text), false, `${name} calls register`);
    assert.equal(/auto-\$\{/.test(text), false, `${name} derives a password from a callsign`);
  }
});
