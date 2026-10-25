import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import * as ReconLogic from "./reconLogic.js";
import {bootWorkspace} from "./workspaceBoot.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");
const lines = page.split("\n");

/** Line numbers (1-based) of the lines that match. */
const where = (pattern) => lines.flatMap((line, i) => (pattern.test(line) ? [i + 1] : []));

/** The page's object spans from `const AutoReconVisualization = {` to its `window.AutoReconVisualization = AutoReconVisualization;`. */

test("the page's recon writers go through ReconStore: ten upserts, and no assignment into the map anywhere", () => {
  assert.equal(where(/ReconStore\.upsert\(/).length, 10);
  assert.deepEqual(where(/(?:AutoReconVisualization|arv|this|self)\.entities\[[^\]]+\]\s*=[^=]/), [], "no writer reaches into the map");
  assert.deepEqual(where(/window\.AutoReconVisualization = window\.AutoReconVisualization \|\| \{\}/), [], "no writer creates a bare AutoReconVisualization to write into");
  assert.deepEqual(where(/window\.AutoReconVisualization\.entities = window\.AutoReconVisualization\.entities \|\| \{\}/), []);
});

test("the page no longer has the recon controller or its drawing: both are modules now", () => {
  assert.equal(/const (?:AutoReconVisualization|ReconDrawing) = \{/.test(page), false);
  assert.equal(page.includes("window.ReconDrawing"), false);
  for (const gone of ["loadEntities", "_bindReconSSE", "applyEntitySyncEvent", "subscribeToEntitySync", "addProximityRings", "createTacticalClusterIcon", "addThreatIndicator", "createOwnShipIcon"]) {
    assert.equal(new RegExp(`\\b${gone}\\b`).test(page), false, `${gone} is not in the page`);
  }
});

test("the object's entity map is never replaced after it is made", () => {
  const replaced = where(/(?:this|AutoReconVisualization|arv|window\.AutoReconVisualization)\.entities\s*=[^=]/);
  assert.deepEqual(replaced, [], "a reassignment would cut the store off from the object");
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

test("booting without a page object makes an empty store, and AutoReconVisualization is the tool's controls", () => {
  const win = {
    console: {log() {}, warn() {}, error() {}, info() {}, debug() {}},
    Event: class { constructor(type) { this.type = type; } }, dispatchEvent() {},
    fetch: async () => ({ok: true, json: async () => ({})}),
  };
  bootWorkspace(win);
  assert.ok(win.ReconStore);
  assert.equal(win.AutoReconVisualization.entities, win.ReconStore.entities);
  win.ReconStore.upsert({entity_id: "x"});
  assert.deepEqual(Object.keys(win.AutoReconVisualization.entities), ["x"]);
});

test("the page activates the recon tool, after the render tool whose clustered data source the drawing takes", () => {
  const render = page.indexOf('workspace.activate("render")');
  const recon = page.indexOf('workspace.activate("recon")');
  assert.ok(render > 0 && recon > render, "recon is activated after render");
  assert.equal(page.includes("window.AutoReconVisualization = AutoReconVisualization"), false, "the page no longer sets the controller itself");
});
