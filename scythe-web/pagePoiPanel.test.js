import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {bootWorkspace} from "./workspaceBoot.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

/** The source of a function declaration in the page, from its `function name(` to its closing brace at the same indent. */
function functionSource(name) {
  const start = page.indexOf(`function ${name}(`);
  assert.ok(start > 0, `${name} is in the page`);
  const indent = page.lastIndexOf("\n", start) + 1;
  const pad = page.slice(indent, start);
  const end = page.indexOf(`\n${pad}}\n`, start);
  assert.ok(end > start);
  return page.slice(start, end + pad.length + 2);
}

const pageEscape = new Function(`const _HTML_ESC = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};\n${functionSource("escapeHtml")}\nreturn escapeHtml;`)();

/** The page's panel functions, run against a fake document and a fake POIVisualization. */
function panel({pois = [], categories = {}, categoryVisibility = {}} = {}) {
  const els = {"poi-list": {innerHTML: ""}, "poi-category-filters": {innerHTML: ""}};
  const source = `${functionSource("updatePOIList")}\n${functionSource("updatePOICategoryFilters")}\nreturn {updatePOIList, updatePOICategoryFilters};`;
  const window = {POIVisualization: {pois, categories, categoryVisibility}};
  const run = new Function("document", "window", "POIVisualization", "escapeHtml", source)({getElementById: (id) => els[id]}, window, window.POIVisualization, pageEscape);
  return {...run, els};
}

const EVIL = "<img src=x onerror=alert(1)>";
const POI = {id: 4, name: "Tower", latitude: 36, longitude: -86, category: "military"};

/** What the browser does with `attr="..."`: decode the entities, then the value is script. */
const decodeAttribute = (value) => value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

test("the POI list shows a name and a category as text, whatever they contain", () => {
  const {updatePOIList, els} = panel({pois: [{...POI, name: EVIL, category: EVIL}]});
  updatePOIList();
  assert.doesNotMatch(els["poi-list"].innerHTML, /<img/);
  assert.equal(els["poi-list"].innerHTML.split("&lt;img src=x onerror=alert(1)&gt;").length - 1, 2);
});

test("the POI list is as it was for ordinary POIs: name, coordinates, category, colour and the two buttons with the id", () => {
  const {updatePOIList, els} = panel({pois: [POI, {id: 5, name: "Depot", latitude: 35.5, longitude: -85.25, category: "nonesuch"}]});
  updatePOIList();
  const html = els["poi-list"].innerHTML;
  assert.match(html, /border-left: 3px solid #8b0000;/);
  assert.match(html, /border-left: 3px solid #888888;/);
  assert.match(html, />Tower<\/div>/);
  assert.match(html, /36\.0000, -86\.0000/);
  assert.match(html, /35\.5000, -85\.2500/);
  assert.match(html, /text-transform: uppercase;">military</);
  assert.match(html, /onclick="flyToPOI\(4\)"/);
  assert.match(html, /onclick="deletePOI\(4\)"/);
  assert.match(html, /onclick="flyToPOI\(5\)"/);
});

test("the id in the list's buttons is a number, so an id that is script cannot reach the handler", () => {
  const {updatePOIList, els} = panel({pois: [{...POI, id: "1);alert(1);("}]});
  updatePOIList();
  assert.match(els["poi-list"].innerHTML, /onclick="flyToPOI\(NaN\)"/);
  assert.doesNotMatch(els["poi-list"].innerHTML, /alert\(1\)/);
});

test("no POIs is the same message as before", () => {
  const {updatePOIList, els} = panel();
  updatePOIList();
  assert.match(els["poi-list"].innerHTML, /No points of interest loaded/);
});

test("the category filter shows a category as text, whatever it contains", () => {
  const {updatePOICategoryFilters, els} = panel({categories: {[EVIL]: 3}});
  updatePOICategoryFilters();
  assert.doesNotMatch(els["poi-category-filters"].innerHTML, /<img/);
  assert.match(els["poi-category-filters"].innerHTML, /<span>&lt;img src=x onerror=alert\(1\)&gt; \(3\)<\/span>/);
});

test("the category filter's inline handler is given exactly the category, once the browser has decoded the attribute, whatever the category contains", () => {
  const categories = ["military", "x');alert(1);//", 'x");alert(1);//', "a\\b", "a\nb", "</script><b>", "o'brien"];
  const {updatePOICategoryFilters, els} = panel({categories: Object.fromEntries(categories.map((c) => [c, 1]))});
  updatePOICategoryFilters();
  const handlers = [...els["poi-category-filters"].innerHTML.matchAll(/onchange="([^"]*)"/g)].map((m) => decodeAttribute(m[1]));
  assert.equal(handlers.length, categories.length);
  handlers.forEach((handler, i) => {
    const calls = [];
    new Function("togglePOICategory", "alert", "this_", handler.replace("this.checked", "this_.checked"))((...a) => calls.push(a), () => calls.push("ALERT"), {checked: true});
    assert.deepEqual(calls, [[categories[i], true]], categories[i]);
  });
});

test("the category filter shows a count as text too, if the server sends something that is not a number", () => {
  const {updatePOICategoryFilters, els} = panel({categories: {military: EVIL}});
  updatePOICategoryFilters();
  assert.doesNotMatch(els["poi-category-filters"].innerHTML, /<img/);
});

test("the category filter is as it was for ordinary categories: checked unless hidden, with the count", () => {
  const {updatePOICategoryFilters, els} = panel({categories: {military: 2, imported: 5}, categoryVisibility: {imported: false}});
  updatePOICategoryFilters();
  const html = els["poi-category-filters"].innerHTML;
  assert.match(html, /<span>military \(2\)<\/span>/);
  assert.match(html, /<span>imported \(5\)<\/span>/);
  assert.equal(html.split(" checked ").length - 1, 1);
  assert.match(html, /rgba\(74,158,255,0\.2\)/);
  assert.match(html, /rgba\(255,255,255,0\.05\)/);
});

test("the messages the page's POI code writes to the console and notifications escape what the operator or the server supplied", () => {
  for (const needle of [
    "${escapeHtml(name)}\" added successfully", "Importing POIs from ${escapeHtml(filePath)}", "Imported ${escapeHtml(data.count)} POIs",
    "imported ${escapeHtml(data.count)} POIs from ${escapeHtml(filePath)}", "'Import Failed', escapeHtml(data.message)", "'Import failed: ' + escapeHtml(data.message)",
    "'Import error: ' + escapeHtml(error.message)", "Category \"${escapeHtml(category)}\"",
  ]) assert.ok(page.includes(needle), needle);
});

test("the poi tool's console messages are escaped before the page puts them in as HTML (here the server's message, and a name in quotes)", async () => {
  const seen = [];
  let reply = {status: "error", message: EVIL};
  const win = {
    console: {warn() {}, log() {}, debug() {}, error() {}}, Event: class { constructor(t) { this.type = t; } }, dispatchEvent() {},
    setInterval: () => 1, clearInterval() {}, setTimeout: () => 1, clearTimeout() {},
    fetch: async () => ({ok: true, json: async () => reply}), addConsoleMessage: (text, kind) => seen.push([text, kind]),
  };
  const workspace = bootWorkspace(win);
  await workspace.activate("poi");
  assert.equal(await win.POIVisualization.addPOI("x", 1, 2), null);
  assert.deepEqual(seen, [["Failed to add POI: &lt;img src=x onerror=alert(1)&gt;", "alert"]]);
  seen.length = 0;
  reply = {status: "ok", poi_id: 3, pois: [], categories: {}};
  await win.POIVisualization.addPOI('He said "hi"', 1, 2);
  assert.equal(seen[0][0], "POI &quot;He said &quot;hi&quot;&quot; added successfully");
});
