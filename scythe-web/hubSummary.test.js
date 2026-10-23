import assert from "node:assert/strict";
import test from "node:test";

import {ageSeconds, countsLine, instanceCounts, instanceHealth, summarizeMissions} from "./hubSummary.js";
import {mountHub} from "./hubSummaryView.js";
import {instanceDeepLink} from "./deepLink.js";

const NOW = Date.parse("2026-10-23T12:00:30Z");
// The shape GET /api/scythe/instances returns (scythe_orchestrator.py), authority being the instance's /api/authority/state.
const RUNNING = {id: "scythe-ab12", name: "ops-1", port: 51001, status: "running", socket_io_ready: true, last_health: "2026-10-23T12:00:25Z",
  info: {node_count: 9, edge_count: 8, session_count: 1, behavior_groups: 0},
  authority: {ok: true, authoritative_state: {sessions: 2, nodes: 2183, edges: 8417, bsgs: 4}, tutorial_state: "T6_ANALYSIS_READY",
    health: {graph_engine: true, bsg_engine: true, authority: "postgres", llm: false}, write_access: {enabled: false, reason: "No authenticated operator provenance"}}};

const byKey = (rows) => Object.fromEntries(rows.map((row) => [row.key, row]));

test("counts come from the authority state, else the instance info, else are not reported", () => {
  assert.deepEqual(instanceCounts(RUNNING), {nodes: 2183, edges: 8417, sessions: 2, bsgs: 4, source: "authority"});
  assert.deepEqual(instanceCounts({info: RUNNING.info, authority: {}}), {nodes: 9, edges: 8, sessions: 1, bsgs: 0, source: "info"});
  assert.deepEqual(instanceCounts({info: {}, authority: {}}), {nodes: null, edges: null, sessions: null, bsgs: null, source: null});
  assert.deepEqual(instanceCounts({authority: {authoritative_state: {nodes: -1, edges: "x", sessions: 0}}}),
    {nodes: null, edges: null, sessions: 0, bsgs: null, source: "authority"});
  assert.equal(countsLine(instanceCounts(RUNNING)), "2183 nodes · 8417 edges · 2 sessions · 4 BSGs");
  assert.equal(countsLine(instanceCounts({})), "nodes: not reported · edges: not reported · sessions: not reported · BSGs: not reported");
});

test("health reports each thing the instance says, and unknown for what it does not", () => {
  const rows = byKey(instanceHealth(RUNNING, NOW));
  assert.deepEqual([rows.instance.state, rows.live.state, rows.graph.state, rows.authority.state, rows.llm.state, rows.writes.state, rows.checked.state],
    ["ok", "ok", "ok", "ok", "down", "locked", "ok"]);
  assert.equal(rows.authority.detail, "postgres");
  assert.equal(rows.writes.detail, "No authenticated operator provenance");
  assert.equal(rows.checked.detail, "5s ago");
  const bare = byKey(instanceHealth({id: "x"}, NOW));
  for (const key of ["instance", "live", "graph", "authority", "llm", "writes", "checked"]) assert.equal(bare[key].state, "unknown", key);
});

test("a write gate that is open says why", () => {
  const open = {...RUNNING, authority: {...RUNNING.authority, write_access: {enabled: true, reason: null}}};
  assert.deepEqual(byKey(instanceHealth(open, NOW)).writes, {key: "writes", label: "Writes", state: "ok", detail: "an operator session exists"});
});

test("instance state and staleness", () => {
  assert.equal(byKey(instanceHealth({status: "dead"}, NOW)).instance.state, "down");
  assert.equal(byKey(instanceHealth({status: "starting"}, NOW)).instance.state, "pending");
  assert.equal(byKey(instanceHealth({socket_io_ready: false}, NOW)).live.state, "pending");
  assert.equal(byKey(instanceHealth({last_health: "2026-10-23T11:59:00Z"}, NOW)).checked.state, "stale");
  assert.equal(byKey(instanceHealth({last_health: "garbage"}, NOW)).checked.state, "unknown");
  assert.equal(ageSeconds("garbage", NOW), null);
  assert.equal(ageSeconds("2026-10-23T12:05:00Z", NOW), 0);   // a clock ahead of ours is not a negative age
});

test("missions are counted by status and the newest few listed", () => {
  const missions = [
    {mission_id: "a", name: "A", status: "open", updated_at: 100}, {mission_id: "b", status: "closed", updated_at: 300},
    {mission_id: "c", name: "C", status: "open", created_at: 200}, {mission_id: "d", status: "weird", updated_at: 50},
    {mission_id: "e", name: "E", status: "open", updated_at: 400}, {mission_id: "f", name: "F", status: "open", updated_at: 10},
    {mission_id: "g", name: "G", status: "closed", updated_at: 20}, null, "x", {name: "no id"}, {mission_id: ""}];
  const summary = summarizeMissions(missions);
  assert.deepEqual([summary.total, summary.open, summary.closed, summary.other], [7, 4, 2, 1]);
  assert.deepEqual(summary.recent.map((m) => m.id), ["e", "b", "c", "a", "d"]);
  assert.equal(summary.recent[1].name, "b");   // a nameless mission shows its id
  assert.deepEqual(summarizeMissions(undefined), {total: 0, open: 0, closed: 0, other: 0, recent: []});
});

test("a link from the hub goes to one instance's page and carries the mission", () => {
  assert.equal(instanceDeepLink("scythe-ab12", "globe", {mission: "m1"}), "scythe/i/scythe-ab12/cesium-hypergraph-globe.html?mission=m1");
  assert.equal(instanceDeepLink("scythe-ab12", "regional"), "scythe/i/scythe-ab12/scythe-web/regional-rf-demo.html");
  assert.equal(instanceDeepLink("scythe-ab12", "ops", {mission: "bad id"}), "scythe/i/scythe-ab12/command-ops-visualization.html");
  for (const bad of ["", "../x", "a/b", "a b", ".x", "x".repeat(65)]) assert.throws(() => instanceDeepLink(bad, "globe"), RangeError, bad);
});

class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.className = ""; this._text = ""; this.listeners = {}; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
  set textContent(value) { this._text = value; this.children = []; }
  append(...nodes) { this.children.push(...nodes); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  find(predicate, out = []) { if (predicate(this)) out.push(this); this.children.forEach((c) => c.find(predicate, out)); return out; }
}
const doc = {createElement: (tag) => new Element(tag), hidden: false};
const response = (body, ok = true, status = 200) => ({ok, status, json: async () => body});
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function fakeFetch(routes) {
  const calls = [];
  const impl = async (url) => { calls.push(url); const route = routes[url]; return route instanceof Error ? Promise.reject(route) : route ?? response({}, false, 404); };
  impl.calls = calls;
  return impl;
}

test("the hub shows a card per instance with its counts, health and links, and loads missions only when asked", async () => {
  const fetchImpl = fakeFetch({
    "/api/scythe/instances": response({instances: [RUNNING, {id: "scythe-dead", name: "old", status: "dead"}]}),
    "/scythe/i/scythe-ab12/api/missions": response({missions: [{mission_id: "m1", name: "M1", status: "open", updated_at: 5}]})});
  const root = new Element("div");
  const hub = mountHub({root, fetchImpl, now: () => NOW, setTimer: null, document: doc});
  await settle();
  const cards = root.find((n) => n.tag === "article");
  assert.deepEqual(cards.map((c) => c.dataset.instance), ["scythe-ab12", "scythe-dead"]);
  assert.match(cards[0].textContent, /2183 nodes · 8417 edges · 2 sessions · 4 BSGs/);
  assert.match(cards[0].textContent, /Writes.*No authenticated operator provenance/);
  assert.match(cards[1].textContent, /nodes: not reported/);
  assert.deepEqual(cards[0].find((n) => n.tag === "a").map((a) => a.href),
    ["scythe/i/scythe-ab12/cesium-hypergraph-globe.html", "scythe/i/scythe-ab12/scythe-web/regional-rf-demo.html", "scythe/i/scythe-ab12/command-ops-visualization.html"]);
  assert.equal(cards[1].find((n) => n.tag === "button").length, 0, "a dead instance has no missions to load");
  assert.ok(!fetchImpl.calls.some((url) => url.includes("/api/missions")), "no mission list is fetched until a card is expanded");

  cards[0].find((n) => n.tag === "button")[0].listeners.click();
  await settle();
  const row = root.find((n) => n.dataset?.mission === "m1")[0];
  assert.ok(row, "the mission is listed");
  assert.deepEqual(row.find((n) => n.tag === "a").map((a) => a.href)[0], "scythe/i/scythe-ab12/cesium-hypergraph-globe.html?mission=m1");
  assert.match(root.textContent, /1 mission: 1 open, 0 closed/);
  assert.equal(fetchImpl.calls.filter((url) => url.includes("/api/missions")).length, 1);
  hub.state.expanded.clear();
});

test("an unreachable orchestrator, and a failing mission list, are said", async () => {
  const root = new Element("div");
  mountHub({root, fetchImpl: fakeFetch({"/api/scythe/instances": new Error("down")}), setTimer: null, document: doc});
  await settle();
  assert.match(root.textContent, /Orchestrator unreachable \(down\)/);

  const root2 = new Element("div");
  const hub = mountHub({root: root2, fetchImpl: fakeFetch({"/api/scythe/instances": response({instances: [RUNNING]})}), now: () => NOW, setTimer: null, document: doc});
  await settle();
  root2.find((n) => n.tag === "button")[0].listeners.click();
  await settle();
  assert.match(root2.textContent, /Missions unavailable \(404\)/);
  assert.equal(hub.state.instances.length, 1);
});

test("an empty registry says so", async () => {
  const root = new Element("div");
  mountHub({root, fetchImpl: fakeFetch({"/api/scythe/instances": response({instances: []})}), setTimer: null, document: doc});
  await settle();
  assert.match(root.textContent, /No instances/);
});

test("when a later poll fails the last report is kept and the failure said", async () => {
  const routes = {"/api/scythe/instances": response({instances: [RUNNING]})};
  const fetchImpl = fakeFetch(routes);
  const root = new Element("div");
  const hub = mountHub({root, fetchImpl, now: () => NOW, setTimer: null, document: doc});
  await settle();
  routes["/api/scythe/instances"] = response({}, false, 503);
  await hub.refresh();
  assert.match(root.textContent, /Showing the last report; the orchestrator did not answer \(503\)/);
  assert.equal(root.find((n) => n.tag === "article").length, 1);
});
