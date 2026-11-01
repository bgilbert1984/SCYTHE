import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

// shared_auth.js is a classic browser script (the hub and login pages load it); run it as one.
const source = readFileSync(new URL("../assets/js/shared_auth.js", import.meta.url), "utf8");

function pageWith({role, failRemove = false}) {
  const local = {"scythe_session_token": "legacy", "inst:operatorSession": "mine", "other": "kept"};
  const session = {"scythe:orchestrator:brokerSession": "broker"};
  const events = [];
  const cleared = [];
  const window = {
    SCYTHE_RUNTIME: {runtimeRole: role, storagePrefix: "inst"},
    dispatchEvent: (e) => events.push(e.type),
    location: {origin: "https://scythe.example"},
  };
  const context = {
    window,
    document: {cookie: ""},
    Event: class { constructor(type) { this.type = type; } },
    ScytheTransport: {clearSessionToken: () => cleared.push(true)},
    localStorage: {
      removeItem: (k) => { if (failRemove && k === "scythe_session_token") throw new Error("blocked"); delete local[k]; },
      getItem: (k) => local[k] ?? null,
      setItem: (k, v) => { local[k] = v; },
    },
    sessionStorage: {removeItem: (k) => { delete session[k]; }, getItem: (k) => session[k] ?? null, setItem: () => {}},
  };
  vm.createContext(context);
  vm.runInContext(source + "\nthis.__auth = SCYTHE_AUTH;", context);
  return {auth: context.__auth, local, session, events, cleared};
}

for (const role of ["broker", "instance"]) {
  test(`${role}: logout removes the legacy scythe_session_token and leaves unrelated keys`, () => {
    const page = pageWith({role});
    page.auth.logout();
    assert.equal("scythe_session_token" in page.local, false);
    assert.equal(page.local.other, "kept");
    assert.deepEqual(page.events, ["scythe:auth-changed"]);
    assert.equal(page.cleared.length, 1);
  });
}

test("blocked storage does not stop the rest of logout", () => {
  const page = pageWith({role: "instance", failRemove: true});
  assert.doesNotThrow(() => page.auth.logout());
  assert.deepEqual(page.events, ["scythe:auth-changed"]);
  assert.equal(page.cleared.length, 1);
});
