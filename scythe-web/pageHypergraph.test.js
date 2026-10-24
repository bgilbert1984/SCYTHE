import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

test("the Three.js hypergraph visualizer and nmap scanner are gone (nothing ever created the visualizer)", () => {
  assert.doesNotMatch(page, /class HypergraphRFVisualizer\b/);
  assert.doesNotMatch(page, /class NmapHypergraphScanner\b/);
  assert.doesNotMatch(page, /new HypergraphRFVisualizer\b|new NmapHypergraphScanner\b/);
  assert.doesNotMatch(page, /\.nmapScanner\b/);
});

// The page's one hypergraph is the Cesium object from RF_SCYTHE.createHypergraphVisualizer. The deleted class had 45 methods, and
// some callers were written against it; this finds calls on the real object to methods it does not have.
const KNOWN_MISSING = new Set(["clearNmapData", "updateWithImmData"]);   // calls that throw today; see the PR that removed the classes

test("every method the page calls on hypergraphVisualizer exists on the Cesium object, except the ones known to be missing", () => {
  const start = page.indexOf("window.RF_SCYTHE.createHypergraphVisualizer = function");
  const end = page.indexOf("class WebGLHypergraphRenderer");
  assert.ok(start > 0 && end > start);
  const defined = new Set([...page.slice(start, end).matchAll(/^ {16}(\w+): (?:async )?function/gm)].map((m) => m[1]));
  assert.ok(defined.has("clearHypergraph") && defined.has("loadData"));
  const called = new Set([...page.matchAll(/\bhypergraphVisualizer\??\.(\w+)\(/g)].map((m) => m[1]));
  const missing = [...called].filter((name) => !defined.has(name)).sort();
  assert.deepEqual(missing, [...KNOWN_MISSING].sort());
});
