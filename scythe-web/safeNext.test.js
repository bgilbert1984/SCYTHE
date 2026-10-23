import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";

// safe_next.js is a classic browser script (login.html loads it); run it as one.
const context = {URL, window: undefined};
vm.createContext(context);
vm.runInContext(readFileSync(new URL("../assets/js/safe_next.js", import.meta.url), "utf8"), context);
const {safeNext, isInstanceId} = context.SCYTHE_SAFE_NEXT;
const ORIGIN = "https://scythe.example";
const HOME = "/rf_scythe_home.html";
const go = (raw) => safeNext(raw, ORIGIN, HOME);

test("a path on this origin is followed, with its query and hash", () => {
  assert.equal(go("/scythe/i/scythe-ab12/command-ops-visualization.html?mission=m-1&select=n5"), "/scythe/i/scythe-ab12/command-ops-visualization.html?mission=m-1&select=n5");
  assert.equal(go("/cesium-hypergraph-globe.html#x"), "/cesium-hypergraph-globe.html#x");
  assert.equal(go("relative.html?a=1"), "/relative.html?a=1");
  assert.equal(go(`${ORIGIN}/a/b`), "/a/b");
});

test("another host is never followed", () => {
  for (const raw of ["https://evil.example/x", "http://scythe.example/x", "//evil.example/x", "///evil.example", "/\\evil.example", "\\\\evil.example",
    "/\t/evil.example", "https://scythe.example.evil.example/", "https://scythe.example@evil.example/"]) {
    assert.equal(go(raw), HOME, raw);
  }
});

test("a scheme with no host resolves against this origin, so it stays here", () => {
  assert.equal(go("https:evil.example"), "/evil.example");
});

test("script and data URLs are never followed", () => {
  for (const raw of ["javascript:alert(1)", "JaVaScRiPt:alert(1)", " javascript:alert(1)", "java\nscript:alert(1)", "data:text/html,<script>1</script>", "vbscript:x", "blob:https://scythe.example/x"]) {
    assert.equal(go(raw), HOME, raw);
  }
});

test("a path that would read as a host once normalized is refused", () => {
  assert.equal(go("/.//evil.example/x"), HOME);
  assert.equal(go("/a/..//evil.example"), HOME);
});

test("nothing, or not text, gives the fallback", () => {
  for (const raw of [null, undefined, "", 7, {}, []]) assert.equal(go(raw), HOME);
});

test("an instance id is a short token that cannot leave a path", () => {
  for (const id of ["scythe-ab12", "a", "A.b_c-1", "x".repeat(64)]) assert.equal(isInstanceId(id), true, id);
  for (const id of ["", "..", ".", "../x", "a/b", "a\\b", "a?b", "a#b", "a b", "-a", ".a", "x".repeat(65), "a%2fb", null, undefined, 5]) assert.equal(isInstanceId(id), false, String(id));
});
