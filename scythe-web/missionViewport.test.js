import assert from "node:assert/strict";
import test from "node:test";

import {NODE_SEMANTIC_CODE, classifyEvent, classifyNode, describeEvent, entityProvenance, eventsByEntity, evidenceCounts,
  missionLifecycle, nodeClassCounts, tailOffset} from "./missionViewport.js";

test("the lifecycle only reports states the server records", () => {
  assert.equal(missionLifecycle({}).key, "none");
  assert.equal(missionLifecycle({missionId: "m1", reachable: false}).key, "unknown");
  assert.equal(missionLifecycle({missionId: "m1", mission: null}).label, "Unknown mission");
  assert.equal(missionLifecycle({missionId: "m1", mission: {status: "open"}}).tone, "green");
  assert.equal(missionLifecycle({missionId: "m1", mission: {status: "closed"}}).tone, "purple");
  assert.equal(missionLifecycle({missionId: "m1", mission: {status: "weird"}}).tone, "gray");
});

test("a command waiting for a second operator turns the mission yellow, and a closed mission stays archived", () => {
  const pending = [{approval_mode: "SECOND_PRINCIPAL"}, {approval_mode: "SINGLE_PRINCIPAL"}];
  const waiting = missionLifecycle({missionId: "m1", mission: {status: "open"}, pending});
  assert.equal(waiting.tone, "yellow");
  assert.match(waiting.detail, /1 command waiting/);
  assert.equal(missionLifecycle({missionId: "m1", mission: {status: "closed"}, pending}).key, "archived");
  assert.equal(missionLifecycle({missionId: "m1", mission: {status: "open"}, pending: [{approval_mode: "SINGLE_PRINCIPAL"}]}).key, "active");
});

test("sensor reports, graph writes and SCYTHE's own conclusions are different classes", () => {
  assert.equal(classifyEvent("rf_uav_detection").key, "observation");
  assert.equal(classifyEvent("TLS_SESSION").key, "observation");
  assert.equal(classifyEvent("NODE_UPDATE").key, "graph");
  assert.equal(classifyEvent("EDGE_CREATE").key, "graph");
  assert.equal(classifyEvent("HYPEREDGE_CREATE").key, "correlation");
  assert.equal(classifyEvent("CONFIDENCE_MUTATION").key, "inference");
  assert.equal(classifyEvent("something_new").key, "unclassified");
  assert.equal(classifyEvent(undefined).key, "unclassified");
});

test("hash and WriteBus entry appear only when the event carries them", () => {
  const bare = describeEvent({seq: 3, timestamp: 0, event_type: "probe", entity_id: "a"});
  assert.equal(bare.hash, null);
  assert.equal(bare.writebusEntry, null);
  assert.equal(bare.time, "00:00:00");
  const full = describeEvent({event_type: "probe", payload: {sha256: "ab", write_id: "w1"}});
  assert.equal(full.hash, "ab");
  assert.equal(full.writebusEntry, "w1");
  assert.equal(describeEvent(null).type, "unknown");
});

test("provenance is the entity's own events and counts cover every class", () => {
  const events = [{entity_id: "a", event_type: "probe"}, {entity_id: "b", event_type: "NODE_CREATE"},
    {entity_id: "a", event_type: "CONFIDENCE_MUTATION"}];
  assert.deepEqual(entityProvenance(events, "a").map(row => row.kind.key), ["observation", "inference"]);
  assert.deepEqual(entityProvenance(events, "zzz"), []);
  assert.deepEqual(evidenceCounts(events), {observation: 1, graph: 1, correlation: 0, inference: 1, unclassified: 0});
  assert.equal(tailOffset(250, 100), 150);
  assert.equal(tailOffset(20, 100), 0);
  assert.equal(tailOffset(undefined, 100), 0);
});

test("a node is classified only from what the server recorded about it", () => {
  assert.equal(classifyNode({kind: "hyperedge"}).key, "correlation");
  assert.equal(classifyNode({events: [{event_type: "probe"}, {event_type: "flow"}]}).basis, "2 observation events");
  assert.equal(classifyNode({method: "observed"}).basis, "position observed");
  assert.equal(classifyNode({method: "neighbor_inferred"}).key, "inference");
  assert.equal(classifyNode({method: "recon_fallback"}).basis, "position inferred (recon_fallback)");
  assert.equal(classifyNode({kind: "host"}).key, "unclassified");
  assert.equal(classifyNode().code, 0);
});

test("evidence outranks how a position was resolved, and graph writes alone say nothing", () => {
  assert.equal(classifyNode({method: "neighbor_inferred", events: [{event_type: "probe"}]}).key, "observation");
  assert.equal(classifyNode({events: [{event_type: "NODE_CREATE"}, {event_type: "CONFIDENCE_MUTATION"}]}).key, "unclassified");
  assert.equal(classifyNode({kind: "hyperedge", method: "observed"}).key, "correlation");
  assert.deepEqual(Object.values(NODE_SEMANTIC_CODE), [0, 1, 2, 3]);
});

test("nodes are counted by class and events grouped per entity", () => {
  const classes = [classifyNode({method: "observed"}), classifyNode({method: "x"}), classifyNode({})];
  assert.deepEqual(nodeClassCounts(classes), {observation: 1, inference: 1, correlation: 0, unclassified: 1});
  const grouped = eventsByEntity([{entity_id: "a", event_type: "probe"}, {entity_id: "a"}, {event_type: "x"}]);
  assert.equal(grouped.get("a").length, 2);
  assert.equal(grouped.size, 1);
});
