import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

// The members the controls have (renderPlugin.js). Anything else the page calls on HierarchicalRenderManager would be a TypeError.
const MEMBERS = new Set(["initialize", "viewer", "currentAltitude", "shouldShowDetail", "createClusteredDataSource", "updateVisibilityForAltitude"]);

test("the page no longer defines the manager or builds the quadtree nobody read", () => {
  assert.doesNotMatch(page, /const HierarchicalRenderManager\s*=/);
  assert.doesNotMatch(page, /window\.HierarchicalRenderManager\s*=/);
  assert.doesNotMatch(page, /quadtree|Quadtree/);
});

test("every member of HierarchicalRenderManager the page uses is one the tool's controls have", () => {
  const used = new Set([...page.matchAll(/HierarchicalRenderManager\.([A-Za-z_]+)/g)].map((m) => m[1]));
  assert.ok(used.size >= 4, [...used].join());
  for (const name of used) assert.ok(MEMBERS.has(name), `the page calls HierarchicalRenderManager.${name}`);
});

test("the boot module activates the render tool", () => {
  assert.match(page, /^\s*workspace\.activate\("render"\)/m);
});

test("the page keeps window.hierarchicalRenderer, which the metrics gauge reads", () => {
  assert.match(page, /window\.hierarchicalRenderer = HierarchicalRenderManager;/);
});
