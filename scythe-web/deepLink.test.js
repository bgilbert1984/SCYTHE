import assert from "node:assert/strict";
import test from "node:test";

import {PAGES, deepLink, isEntityId, isMissionId, parseDeepLink, relativePath, selectWhenPresent} from "./deepLink.js";
import {mountDeepLinkNav} from "./deepLinkNav.js";

test("a valid mission and selection are read, and nothing else is", () => {
  assert.deepEqual(parseDeepLink("?mission=RFMAP-20261017-001&select=host:192.168.1.1&token=secret&x=1"),
    {mission: "RFMAP-20261017-001", select: "host:192.168.1.1", rejected: []});
  assert.deepEqual(parseDeepLink(""), {mission: null, select: null, rejected: []});
  assert.deepEqual(parseDeepLink(undefined), {mission: null, select: null, rejected: []});
});

test("an invalid or ambiguous value is reported and ignored, never repaired", () => {
  for (const search of ["?mission=has%20space", "?mission=", "?mission=-lead", `?mission=${"a".repeat(129)}`, "?mission=a&mission=b"]) {
    assert.deepEqual(parseDeepLink(search), {mission: null, select: null, rejected: ["mission"]}, search);
  }
  for (const search of ["?select=", "?select=%20%20", "?select=a%00b", `?select=${"x".repeat(257)}`, "?select=a&select=b"]) {
    assert.deepEqual(parseDeepLink(search), {mission: null, select: null, rejected: ["select"]}, search);
  }
  assert.deepEqual(parseDeepLink("?mission=bad%20id&select=n1"), {mission: null, select: "n1", rejected: ["mission"]});
});

test("the mission rule is the server's", () => {
  assert.ok(isMissionId("m1") && isMissionId("RFMAP-20261017-001") && isMissionId("a".repeat(128)));
  assert.ok(!isMissionId("") && !isMissionId(".x") && !isMissionId("a b") && !isMissionId(7) && !isMissionId(null));
  assert.ok(isEntityId("tls_sni:chatgpt.com") && !isEntityId("") && !isEntityId(7));
});

test("links are relative, so they keep an instance prefix", () => {
  assert.equal(relativePath("globe", "regional"), "scythe-web/regional-rf-demo.html");
  assert.equal(relativePath("regional", "globe"), "../cesium-hypergraph-globe.html");
  assert.equal(relativePath("regional", "ops"), "../command-ops-visualization.html");
  assert.equal(relativePath("ops", "globe"), "cesium-hypergraph-globe.html");
  assert.equal(relativePath("globe", "globe"), "cesium-hypergraph-globe.html");
  for (const from of Object.keys(PAGES)) for (const to of Object.keys(PAGES)) assert.ok(!relativePath(from, to).startsWith("/"));
});

test("a link carries the mission and selection, encoded, and drops what is invalid", () => {
  assert.equal(deepLink("globe", {mission: "m1", select: "host:1.1.1.1"}, "regional"),
    "../cesium-hypergraph-globe.html?mission=m1&select=host%3A1.1.1.1");
  assert.equal(deepLink("regional", {mission: "m1"}, "globe"), "scythe-web/regional-rf-demo.html?mission=m1");
  assert.equal(deepLink("ops", {mission: "bad id", select: ""}, "globe"), "command-ops-visualization.html");
  assert.equal(deepLink("ops"), "command-ops-visualization.html");
  assert.throws(() => deepLink("nowhere", {}, "globe"), RangeError);
  assert.throws(() => deepLink("globe", {}, "nowhere"), RangeError);
});

test("a link parses back to the context it was built from", () => {
  const select = "host:192.168.1.1 & more/odd?chars";
  const url = deepLink("globe", {mission: "m-1", select}, "ops");
  assert.deepEqual(parseDeepLink(url.slice(url.indexOf("?"))), {mission: "m-1", select, rejected: []});
});

test("no token ever rides in a link", () => {
  const url = deepLink("globe", {mission: "m1", select: "n1", token: "secret", bootstrap_token: "secret"}, "ops");
  assert.ok(!/secret|token/.test(url), url);
});

const fakeGlobe = (nodes, {hidden = []} = {}) => {
  const globe = {_graph: {nodes: new Map(nodes.map((id) => [id, {}]))}, _selectedEntityId: null,
    selectNode(id) { if (!hidden.includes(id)) globe._selectedEntityId = id; }};
  return globe;
};

test("a selection is applied as soon as the node is on the globe", async () => {
  const globe = fakeGlobe(["n1"]);
  assert.equal(await selectWhenPresent(globe, "n1", {wait: async () => assert.fail("should not wait")}), "selected");
  assert.equal(globe._selectedEntityId, "n1");
});

test("a node that arrives later is selected when it does", async () => {
  const globe = fakeGlobe([]);
  let waits = 0;
  const result = await selectWhenPresent(globe, "late", {intervalMs: 500, timeoutMs: 5000,
    wait: async () => { if (++waits === 3) globe._graph.nodes.set("late", {}); }});
  assert.equal(result, "selected");
  assert.equal(waits, 3);
});

test("a node that never arrives is not_found after the timeout, and a hidden one is not_selectable", async () => {
  let waits = 0;
  assert.equal(await selectWhenPresent(fakeGlobe([]), "missing", {intervalMs: 500, timeoutMs: 2000, wait: async () => { waits++; }}), "not_found");
  assert.equal(waits, 4);
  assert.equal(await selectWhenPresent(fakeGlobe(["h"], {hidden: ["h"]}), "h"), "not_selectable");
  assert.equal(await selectWhenPresent(null, "x", {timeoutMs: 1, wait: async () => {}}), "not_found");
});

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.className = ""; this.textContent = ""; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
}

test("the nav links to the other pages with the page's context and follows the selection", () => {
  const document = {createElement: (tag) => new Element(tag)};
  const root = new Element("div");
  const {nav, update} = mountDeepLinkNav({root, from: "globe", targets: ["regional", "ops"], mission: "m1", document});
  assert.deepEqual(root.children, [nav]);
  assert.deepEqual(nav.children.map((a) => [a.textContent, a.href]),
    [["Regional", "scythe-web/regional-rf-demo.html?mission=m1"], ["Mission Ops", "command-ops-visualization.html?mission=m1"]]);
  update({select: "host:1.1.1.1"});
  assert.deepEqual(nav.children.map((a) => a.href),
    ["scythe-web/regional-rf-demo.html?mission=m1&select=host%3A1.1.1.1", "command-ops-visualization.html?mission=m1&select=host%3A1.1.1.1"]);
  assert.match(nav.children[0].title, /Regional on host:1\.1\.1\.1/);
  update({select: null});
  assert.equal(nav.children[0].href, "scythe-web/regional-rf-demo.html?mission=m1");
});

test("a rejected parameter is said, not silently dropped", () => {
  const document = {createElement: (tag) => new Element(tag)};
  const {nav} = mountDeepLinkNav({root: new Element("div"), from: "globe", targets: ["ops"], rejected: ["mission", "select"], document});
  assert.equal(nav.children[0].textContent, "ignored in link: mission, select");
  assert.equal(mountDeepLinkNav({root: new Element("div"), from: "globe", targets: ["ops"], document}).nav.children.length, 1);
});
