import assert from "node:assert/strict";
import test from "node:test";

import {NODE_SEMANTIC_CODE, classifyEvent, classifyNode, describeEvent, entityProvenance, eventsByEntity, evidenceCounts,
  edgeEndpoints, linksNotPresent, missionLifecycle, nodeClassCounts, nodesDeletedBy, nodesNotYetCreated, resolveEdgeEvents, MATCH_MODE, eventsUpTo, scrubLabel, tailOffset, timelineBounds} from "./missionViewport.js";

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

test("the timeline spans the loaded events and needs two distinct moments to scrub", () => {
  assert.deepEqual(timelineBounds([{timestamp: 5000}, {timestamp: 1000}, {timestamp: "x"}]), {start: 1000, end: 5000});
  assert.equal(timelineBounds([{timestamp: 1000}, {timestamp: 1000}]), null);
  assert.equal(timelineBounds([]), null);
  assert.equal(timelineBounds(undefined), null);
});

test("scrubbing shows the events up to the chosen moment, and live shows all", () => {
  const events = [{timestamp: 1000}, {timestamp: 2000}, {timestamp: 3000}];
  assert.equal(eventsUpTo(events, 2000).length, 2);
  assert.equal(eventsUpTo(events, 500).length, 0);
  assert.equal(eventsUpTo(events, null).length, 3);
});

test("a node whose newest event up to the scrub time is a delete is hidden, and a later event brings it back", () => {
  const events = [
    {timestamp: 1000, event_type: "NODE_CREATE", entity_id: "gone", seq: 1},
    {timestamp: 3000, event_type: "NODE_DELETE", entity_id: "gone", seq: 2},
    {timestamp: 1000, event_type: "NODE_CREATE", entity_id: "back", seq: 3},
    {timestamp: 2000, event_type: "NODE_DELETE", entity_id: "back", seq: 4},
    {timestamp: 4000, event_type: "NODE_UPDATE", entity_id: "back", seq: 5},
    {timestamp: 1000, event_type: "NODE_CREATE", entity_id: "kept", seq: 6}];
  assert.deepEqual([...nodesDeletedBy(events, 2500)], ["back"]);
  assert.deepEqual([...nodesDeletedBy(events, 3500)].sort(), ["back", "gone"]);
  assert.deepEqual([...nodesDeletedBy(events, 5000)], ["gone"]);
  assert.deepEqual([...nodesDeletedBy(events, 1500)], []);
});

test("node deletes are ordered by time then sequence, and Live hides nothing", () => {
  const sameTime = [{timestamp: 1000, event_type: "NODE_DELETE", entity_id: "n", seq: 1},
    {timestamp: 1000, event_type: "NODE_CREATE", entity_id: "n", seq: 2}];
  assert.equal(nodesDeletedBy(sameTime, 1000).size, 0);
  assert.equal(nodesDeletedBy([{timestamp: 1, event_type: "NODE_DELETE", entity_id: "n"}], null).size, 0);
  assert.equal(nodesDeletedBy([{timestamp: 1, event_type: "EDGE_DELETE", entity_id: "e"}, {timestamp: "x", event_type: "NODE_DELETE", entity_id: "n"}, null], 9).size, 0);
});

test("a deleted node takes the edges that end on it out of the picture", () => {
  const edges = new Map([["e1", {src: "a", dst: "b"}], ["e2", {src: "c", dst: "d"}]]);
  const events = [{timestamp: 1000, event_type: "NODE_DELETE", entity_id: "a"}];
  const links = linksNotPresent({events: [], at: 2000, edges, hyperedges: new Map(), hiddenNodes: nodesDeletedBy(events, 2000)});
  assert.deepEqual([...links.edges], ["e1"]);
});

test("only a creation event in the loaded window can hide a node", () => {
  const events = [{timestamp: 3000, event_type: "NODE_CREATE", entity_id: "late"},
    {timestamp: 1000, event_type: "NODE_CREATE", entity_id: "early"},
    {timestamp: 4000, event_type: "NODE_CREATE", entity_id: "early"},
    {timestamp: 3500, event_type: "probe", entity_id: "older-than-window"}];
  assert.deepEqual([...nodesNotYetCreated(events, 2000)], ["late"]);
  assert.deepEqual([...nodesNotYetCreated(events, 5000)], []);
  assert.equal(nodesNotYetCreated(events, null).size, 0);
});

test("the scrub clock shows the date only when the span crosses midnight", () => {
  const day = Date.UTC(2026, 9, 22, 9, 41, 22);
  assert.equal(scrubLabel(day, {start: day, end: day + 1000}), "09:41:22Z");
  assert.equal(scrubLabel(day, {start: day, end: day + 86400000}), "2026-10-22 09:41:22Z");
});

test("an edge or hyperedge never outlives a missing endpoint, and live hides nothing", () => {
  const edges = new Map([["e1", {src: "a", dst: "late"}], ["e2", {src: "a", dst: "b"}]]);
  const hyperedges = new Map([["h1", {members: ["a", "late"]}], ["h2", {members: ["a", "b"]}]]);
  const hidden = linksNotPresent({events: [], at: 1000, edges, hyperedges, hiddenNodes: new Set(["late"])});
  assert.deepEqual([...hidden.edges], ["e1"]);
  assert.deepEqual([...hidden.hyperedges], ["h1"]);
  const live = linksNotPresent({events: [], at: null, edges, hyperedges, hiddenNodes: new Set(["late"])});
  assert.equal(live.edges.size + live.hyperedges.size, 0);
});

test("an edge is hidden before its creation event or after its newest delete, and only then", () => {
  const events = [
    {entity_id: "e1", event_type: "EDGE_CREATE", timestamp: 3000},
    {entity_id: "e2", event_type: "EDGE_CREATE", timestamp: 1000},
    {entity_id: "e2", event_type: "EDGE_DELETE", timestamp: 2000},
    {entity_id: "e3", event_type: "EDGE_CREATE", timestamp: 1000},
    {entity_id: "e3", event_type: "EDGE_DELETE", timestamp: 2000},
    {entity_id: "e3", event_type: "EDGE_UPDATE", timestamp: 2500},
    {entity_id: "e4", event_type: "EDGE_UPDATE", timestamp: 4000},
    {entity_id: "n", event_type: "NODE_CREATE", timestamp: 9000}];
  const at = (time) => [...linksNotPresent({events, at: time}).edges].sort();
  assert.deepEqual(at(500), ["e1", "e2", "e3"]);
  assert.deepEqual(at(1500), ["e1"]);
  assert.deepEqual(at(2200), ["e1", "e2", "e3"]);
  assert.deepEqual(at(2600), ["e1", "e2"]);
  assert.deepEqual(at(5000), ["e2"]);
});

test("an edge event's ends are read from its payload the way the server's cache reads them", () => {
  assert.deepEqual(edgeEndpoints({payload: {src: "a", dst: "b"}}), {src: "a", dst: "b"});
  assert.deepEqual(edgeEndpoints({payload: {source: "a", target: "b"}}), {src: "a", dst: "b"});
  assert.deepEqual(edgeEndpoints({payload: {nodes: ["a", "x", "b"]}}), {src: "a", dst: "b"});
  assert.equal(edgeEndpoints({payload: {nodes: ["a"]}}), null);
  assert.equal(edgeEndpoints({}), null);
  assert.equal(edgeEndpoints(null), null);
});

test("edge events resolve by id first, then by their ends, and a mismatch is counted rather than silent", () => {
  const edges = new Map([["edge-1", {src: "a", dst: "b"}], ["edge-2", {src: "c", dst: "d"}]]);
  const events = [
    {event_type: "EDGE_CREATE", entity_id: "edge-1", timestamp: 1},
    {event_type: "EDGE_CREATE", entity_id: "other-id", timestamp: 2, payload: {src: "c", dst: "d"}},
    {event_type: "EDGE_CREATE", entity_id: "nowhere", timestamp: 3, payload: {src: "x", dst: "y"}},
    {event_type: "EDGE_CREATE", entity_id: "no-payload", timestamp: 4},
    {event_type: "NODE_CREATE", entity_id: "a", timestamp: 5}];
  const {matches, resolved, identity} = resolveEdgeEvents(events, edges);
  assert.deepEqual(identity, {byId: 1, byEndpoints: 1, unmatched: 2});
  assert.equal(resolved.get("edge-2").length, 1);        // the "other-id" event is about edge-2
  assert.equal(resolved.has("other-id"), false);
  assert.deepEqual(matches.map(m => [m.event.entity_id, m.mode, m.edgeId]), [
    ["edge-1", MATCH_MODE.ID, "edge-1"], ["other-id", MATCH_MODE.ENDPOINTS, "edge-2"],
    ["nowhere", MATCH_MODE.UNMATCHED, null], ["no-payload", MATCH_MODE.UNMATCHED, null]]);
});

test("two rendered edges with the same ends are ambiguous and never matched by their ends", () => {
  const edges = new Map([["e1", {src: "a", dst: "b"}], ["e2", {src: "a", dst: "b"}]]);
  const {identity, matches} = resolveEdgeEvents([{event_type: "EDGE_CREATE", entity_id: "zzz", timestamp: 1, payload: {src: "a", dst: "b"}}], edges);
  assert.deepEqual(identity, {byId: 0, byEndpoints: 0, unmatched: 1});
  assert.equal(matches[0].mode, MATCH_MODE.UNMATCHED);
  assert.equal(linksNotPresent({events: [{event_type: "EDGE_CREATE", entity_id: "zzz", timestamp: 9000, payload: {src: "a", dst: "b"}}], at: 1000, edges}).edges.size, 0);
});

test("an edge whose event carries a different id is still hidden before its creation, through its ends", () => {
  const edges = new Map([["edge-2", {src: "c", dst: "d"}]]);
  const events = [{event_type: "EDGE_CREATE", entity_id: "other-id", timestamp: 3000, payload: {src: "c", dst: "d"}}];
  const hidden = linksNotPresent({events, at: 1000, edges});
  assert.deepEqual([...hidden.edges], ["edge-2"]);
  assert.equal(hidden.identity.byEndpoints, 1);
  assert.equal(linksNotPresent({events, at: null, edges}).identity.byEndpoints, 1);   // reported live too
  assert.equal(linksNotPresent({events, at: 5000, edges}).edges.size, 0);
});
