import test from "node:test";
import assert from "node:assert/strict";
import {filterEntries, paletteEntries} from "./commandPalette.js";

const instances = [{id: "scythe-ab12", name: "ops-1", status: "running"}, {id: "scythe-dead", name: "old", status: "dead"}, {id: "scythe-new", status: "running"}, null, {name: "no id"}];
const actions = [{id: "new", label: "New instance", run() {}}, {id: "account", label: "Account", run() {}}];

test("entries are the hub's actions plus three views of each running instance, and nothing for the rest", () => {
  const {entries, skipped} = paletteEntries({instances, actions});
  assert.deepEqual(entries.map((e) => e.label), ["New instance", "Account", "ops-1 — Globe", "ops-1 — Regional", "ops-1 — Mission Ops",
    "scythe-new — Globe", "scythe-new — Regional", "scythe-new — Mission Ops"]);
  assert.equal(skipped, 1);
  assert.equal(entries[2].href, "scythe/i/scythe-ab12/cesium-hypergraph-globe.html");
});

test("missions appear only from lists the hub already loaded, and their links carry the mission", () => {
  const missions = new Map([["scythe-ab12", {summary: {recent: [{id: "m-e", name: "Mission E"}]}}], ["scythe-dead", {summary: {recent: [{id: "m-x", name: "X"}]}}]]);
  const {entries} = paletteEntries({instances, missions});
  const found = entries.filter((e) => e.kind === "mission");
  assert.equal(found.length, 3);
  assert.equal(found[0].href, "scythe/i/scythe-ab12/cesium-hypergraph-globe.html?mission=m-e");
  assert.ok(!entries.some((e) => e.label.includes("(m-x)")), "a dead instance offers no missions");
});

test("a mission with an unusable id gets a link without it, never a guessed one", () => {
  const missions = new Map([["scythe-ab12", {summary: {recent: [{id: "bad id!", name: "Odd"}]}}]]);
  const {entries} = paletteEntries({instances, missions});
  assert.equal(entries.find((e) => e.kind === "mission").href, "scythe/i/scythe-ab12/cesium-hypergraph-globe.html");
});

test("filtering needs every word, ranks a leading match first, and keeps order otherwise", () => {
  const {entries} = paletteEntries({instances, actions});
  assert.deepEqual(filterEntries(entries, "globe").map((e) => e.label), ["ops-1 — Globe", "scythe-new — Globe"]);
  assert.deepEqual(filterEntries(entries, "OPS globe").map((e) => e.label), ["ops-1 — Globe"]);
  assert.deepEqual(filterEntries(entries, "account").map((e) => e.label), ["Account"]);
  assert.deepEqual(filterEntries(entries, "nothing like this"), []);
  assert.equal(filterEntries(entries, "").length, entries.length);
  assert.equal(filterEntries(entries, "", 3).length, 3);
  const order = filterEntries([{label: "zz new"}, {label: "new one"}, {label: "a new"}], "new").map((e) => e.label);
  assert.deepEqual(order, ["new one", "zz new", "a new"]);
});

test("bad input yields no entries instead of throwing", () => {
  assert.deepEqual(paletteEntries({instances: null}).entries, []);
  assert.deepEqual(filterEntries([], undefined), []);
});
