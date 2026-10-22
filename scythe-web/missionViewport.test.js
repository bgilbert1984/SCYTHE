import assert from "node:assert/strict";
import test from "node:test";

import {classifyEvent, describeEvent, entityProvenance, evidenceCounts, missionLifecycle, tailOffset} from "./missionViewport.js";

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
