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
const KNOWN_MISSING = new Set(["updateWithImmData"]);   // a call that throws today; not part of the Clear-button fix

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

// --- the Clear Results button ---

/** The page's clearNmapHypergraphData, run against fakes. */
function clearFunction() {
  const start = page.indexOf("function clearNmapHypergraphData() {");
  const end = page.indexOf("\n        }\n", start);
  assert.ok(start > 0 && end > start);
  const source = page.slice(start, end + "\n        }".length);
  return new Function("hypergraphVisualizer", "document", "addConsoleMessage", "showNotification", `${source}\nreturn clearNmapHypergraphData;`);
}

function clearRun({visualizer, elements = {}}) {
  const messages = [], notes = [];
  const document = {getElementById: (id) => elements[id] || null};
  clearFunction()(visualizer, document, (text, kind) => messages.push([text, kind]), (title, text, kind) => notes.push([title, kind]))();
  return {messages, notes};
}

test("Clear Results clears the hypergraph through the Cesium visualizer's clearHypergraph and resets the panel", () => {
  const cleared = [];
  const elements = {"nmap-status": {textContent: "Done"}, "nmap-progress": {style: {display: "block"}}, "central-nodes-list": {innerHTML: "x"}};
  const {messages, notes} = clearRun({visualizer: {clearHypergraph: () => cleared.push(1)}, elements});
  assert.equal(cleared.length, 1);
  assert.equal(elements["nmap-status"].textContent, "Ready to scan");
  assert.equal(elements["nmap-progress"].style.display, "none");
  assert.equal(elements["central-nodes-list"].innerHTML, "No nmap data");
  assert.deepEqual(messages, [["Cleared nmap hypergraph data", "response"]]);
  assert.deepEqual(notes, [["Data Cleared", "info"]]);
});

test("Clear Results still resets the panel when there is no visualizer yet, and when the panel's elements are missing", () => {
  assert.equal(clearRun({visualizer: null}).messages.length, 1);
  assert.equal(clearRun({visualizer: {clearHypergraph() {}}}).notes.length, 1);
});
