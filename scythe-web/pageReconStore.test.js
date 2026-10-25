import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {createReconStore} from "./reconStore.js";
import * as ReconLogic from "./reconLogic.js";
import {bootWorkspace} from "./workspaceBoot.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");
const lines = page.split("\n");

/** Line numbers (1-based) of the lines that match. */
const where = (pattern) => lines.flatMap((line, i) => (pattern.test(line) ? [i + 1] : []));

/** The page's object spans from `const AutoReconVisualization = {` to its `window.AutoReconVisualization = AutoReconVisualization;`. */
const objectStart = lines.findIndex((line) => /^\s*const AutoReconVisualization = \{/.test(line)) + 1;
const objectEnd = lines.findIndex((line) => /^\s*window\.AutoReconVisualization = AutoReconVisualization;/.test(line)) + 1;

test("the page's recon writers go through ReconStore: fourteen upserts, one clear, one remove, and no assignment into the map anywhere", () => {
  assert.equal(where(/ReconStore\.upsert\(/).length, 14);
  assert.equal(where(/ReconStore\.clear\(/).length, 1);
  assert.equal(where(/ReconStore\.remove\(/).length, 1);
  assert.deepEqual(where(/(?:AutoReconVisualization|arv|this|self)\.entities\[[^\]]+\]\s*=[^=]/), [], "no writer reaches into the map, inside the object or out of it");
  assert.deepEqual(where(/window\.AutoReconVisualization = window\.AutoReconVisualization \|\| \{\}/), [], "no writer creates a bare AutoReconVisualization to write into");
  assert.deepEqual(where(/window\.AutoReconVisualization\.entities = window\.AutoReconVisualization\.entities \|\| \{\}/), []);
});

test("the object's own writers (the disposition update, the change feed, the sync handler) are inside the object and use the store", () => {
  const inside = where(/ReconStore\.(?:upsert|remove)\(/).filter((n) => n > objectStart && n < objectEnd);
  assert.equal(inside.length, 5, "loadEntities, the change feed, the disposition update, and the sync handler's upsert and remove");
});

test("the object's entity map is never replaced after it is made", () => {
  const replaced = where(/(?:this|AutoReconVisualization|arv|window\.AutoReconVisualization)\.entities\s*=[^=]/);
  assert.deepEqual(replaced, [], "a reassignment would cut the store off from the object");
});

/** `loadEntities` as the page has it, run against a fake server and a fake page object that shares the store's map. */
function reload(store, {response, calls = []}) {
  const header = "            loadEntities: async function(";
  assert.equal(page.split(header).length - 1, 1);
  const start = page.indexOf(header);
  const end = page.indexOf("\n            },", start);
  const source = page.slice(start, end + "\n            }".length).trim().replace(/^loadEntities: /, "");
  const object = {entities: store.entities, renderEntities: () => calls.push("render"), loadAlerts: () => calls.push("alerts")};
  const els = {"recon-entity-count": {}};
  const load = new Function("ReconStore", "ReconLogic", "fetch", "addConsoleMessage", "showNotification", "document", "console", `return (${source})`)(
    store, ReconLogic, async () => ({json: async () => response}), () => {}, () => {}, {getElementById: (id) => els[id]}, {warn() {}, error() {}},
  );
  return {object, run: () => load.call(object), els, calls};
}

const AT = (lat, lon) => ({lat, lon});

test("reloading from the server empties the map in place and fills it again, so an earlier reference sees the new entities", async () => {
  const store = createReconStore();
  store.upsert({entity_id: "stale"});
  const held = store.entities;
  const {object, run, els, calls} = reload(store, {response: {status: "ok", entity_count: 2, entities: [
    {entity_id: "a", location: AT(1, 2)},
    {id: "b", data: {name: "nested", location: AT(3, 4)}},
  ]}});
  await run();
  assert.equal(object.entities, held);
  assert.equal(store.entities, held);
  assert.deepEqual(Object.keys(held), ["a", "b"]);
  assert.equal(held.b.name, "nested");
  assert.equal(els["recon-entity-count"].textContent, 2);
  assert.deepEqual(calls, ["render", "alerts"]);
});

test("a write after a reload lands in the map the object reads", async () => {
  const store = createReconStore();
  const {object, run} = reload(store, {response: {status: "ok", entity_count: 0, entities: []}});
  await run();
  store.upsert({entity_id: "late"});
  assert.deepEqual(Object.keys(object.entities), ["late"]);
});

test("an entity without a numeric location is skipped on reload, as before", async () => {
  const store = createReconStore();
  const {run} = reload(store, {response: {status: "ok", entity_count: 3, entities: [
    {entity_id: "ok", location: AT(1, 2)}, {entity_id: "nolocation"}, {entity_id: "text", location: {lat: "1", lon: "2"}},
  ]}});
  await run();
  assert.deepEqual(Object.keys(store.entities), ["ok"]);
});

test("a reply that is not ok leaves the map as it was", async () => {
  const store = createReconStore();
  store.upsert({entity_id: "keep"});
  const {run, calls} = reload(store, {response: {status: "error"}});
  await run();
  assert.deepEqual(Object.keys(store.entities), ["keep"]);
  assert.deepEqual(calls, []);
});

test("booting gives the page ReconStore, sharing the map AutoReconVisualization already holds", () => {
  const early = {entity_id: "early"};
  const win = {
    console: {log() {}, warn() {}, error() {}, info() {}, debug() {}},
    Event: class { constructor(type) { this.type = type; } }, dispatchEvent() {},
    fetch: async () => ({ok: true, json: async () => ({})}),
    AutoReconVisualization: {entities: {early}},
  };
  const held = win.AutoReconVisualization.entities;
  bootWorkspace(win);
  assert.equal(win.ReconStore.entities, held);
  win.ReconStore.upsert({entity_id: "x"});
  assert.deepEqual(Object.keys(win.AutoReconVisualization.entities), ["early", "x"]);
  assert.equal(win.AutoReconVisualization.entities.early, early);
  assert.equal(typeof win.ReconLogic.planSyncEvent, "function", "and the page's pure logic");
  assert.equal(win.ReconLogic.describeEntity, ReconLogic.describeEntity);
});

test("booting without the page's object does not create one", () => {
  const win = {
    console: {log() {}, warn() {}, error() {}, info() {}, debug() {}},
    Event: class { constructor(type) { this.type = type; } }, dispatchEvent() {},
    fetch: async () => ({ok: true, json: async () => ({})}),
  };
  bootWorkspace(win);
  assert.equal("AutoReconVisualization" in win, false);
  assert.ok(win.ReconStore);
});
