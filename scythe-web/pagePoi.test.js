import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

// The members the controls have (poiPlugin.js). Anything else the page calls on POIVisualization would be a TypeError.
const MEMBERS = new Set(["initialize", "pois", "categories", "categoryVisibility", "visible", "loadPOIs", "addPOI", "deletePOI", "flyToPOI", "flyToAllPOIs",
  "setVisible", "setCategoryVisible", "toggleLabels"]);

test("the page no longer defines the object, and the alias nobody read is gone", () => {
  assert.doesNotMatch(page, /const POIVisualization\s*=/);
  assert.doesNotMatch(page, /window\.POIVisualization\s*=/);
  assert.doesNotMatch(page, /poiViz\b/);
});

test("every member of POIVisualization the page uses is one the tool's controls have", () => {
  const used = new Set([...page.matchAll(/\bPOIVisualization\.([A-Za-z_]+)/g)].map((m) => m[1]));
  assert.ok(used.size >= 8, [...used].join());
  for (const name of used) assert.ok(MEMBERS.has(name), `the page uses POIVisualization.${name}`);
});

test("the boot module activates the poi tool", () => {
  assert.match(page, /^\s*workspace\.activate\("poi"\)/m);
});
