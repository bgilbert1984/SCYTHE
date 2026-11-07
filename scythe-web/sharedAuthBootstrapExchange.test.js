import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

// shared_auth.js is a classic browser script; run it as one, with a page opened at `url`.
const source = readFileSync(new URL("../assets/js/shared_auth.js", import.meta.url), "utf8");

function pageAt({role = "instance", instanceId = "scythe-abc", search = "", respond}) {
  const local = {};
  const calls = [];
  const replaced = [];
  const window = {
    SCYTHE_RUNTIME: {runtimeRole: role, storagePrefix: `scythe:${instanceId}`, instanceId},
    location: {origin: "https://x", pathname: "/scythe/i/scythe-abc/page.html", search, hash: "#h"},
    history: {replaceState: (...args) => replaced.push(args[2])},
    dispatchEvent() {},
  };
  const context = {
    window, URLSearchParams, document: {cookie: ""},
    ScytheTransport: {
      setSessionToken() {}, clearSessionToken() {},
      fetch: async (path, options) => { calls.push({path, body: JSON.parse(options.body)}); return respond(); },
    },
    localStorage: {getItem: (k) => local[k] ?? null, setItem: (k, v) => { local[k] = v; }, removeItem: (k) => { delete local[k]; }},
    sessionStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  };
  vm.createContext(context);
  vm.runInContext(source + "\nthis.__auth = SCYTHE_AUTH;", context);
  return {auth: context.__auth, local, calls, replaced};
}

const ok = (token) => async () => ({ok: true, json: async () => ({session: {session_token: token}})});

test("a bootstrap token is traded for the instance session, stored under the instance key, and removed from the address", async () => {
  const page = pageAt({search: "?mission=m1&bootstrap_token=BT", respond: ok("sess-1")});
  assert.equal(await page.auth.exchangeBootstrapFromUrl(), "sess-1");
  assert.deepEqual(page.calls, [{path: "/api/auth/exchange-bootstrap", body: {bootstrap_token: "BT", instance_id: "scythe-abc"}}]);
  assert.equal(JSON.parse(page.local["scythe:scythe-abc:operatorSession"]).sessionToken, "sess-1");
  assert.deepEqual(page.replaced, ["/scythe/i/scythe-abc/page.html?mission=m1#h"]);
});

test("no bootstrap token: nothing is sent and nothing is stored", async () => {
  const page = pageAt({search: "?mission=m1", respond: ok("x")});
  assert.equal(await page.auth.exchangeBootstrapFromUrl(), null);
  assert.equal(page.calls.length, 0);
  assert.deepEqual(page.local, {});
});

test("a refused exchange stores nothing but still removes the token from the address", async () => {
  const page = pageAt({search: "?bootstrap_token=BT", respond: async () => ({ok: false, status: 401})});
  assert.equal(await page.auth.exchangeBootstrapFromUrl(), null);
  assert.deepEqual(page.local, {});
  assert.deepEqual(page.replaced, ["/scythe/i/scythe-abc/page.html#h"]);
});

test("a network failure is swallowed", async () => {
  const page = pageAt({search: "?bootstrap_token=BT", respond: async () => { throw new Error("down"); }});
  assert.equal(await page.auth.exchangeBootstrapFromUrl(), null);
});

test("the hub (broker) page and an unknown instance never exchange", async () => {
  for (const opts of [{role: "broker"}, {instanceId: "unknown"}]) {
    const page = pageAt({...opts, search: "?bootstrap_token=BT", respond: ok("x")});
    assert.equal(await page.auth.exchangeBootstrapFromUrl(), null);
    assert.equal(page.calls.length, 0);
  }
});
