import assert from "node:assert/strict";
import test from "node:test";

import {formatHostTracePrompt, geoPathPoints, runHostTrace} from "./hostTraceWorkflow.js";

const result = {status: "completed", target: "8.8.8.8", maxHops: 20, cached: false,
  selection: {entityId: "host:8.8.8.8", graphRevision: "graph-1"},
  probe: {status: "ok", rtt_avg_ms: 12.5}, evidenceClasses: {rtt: "MEASURED", route: "MEASURED", geography: "INFERRED"},
  traceroute: {hops: [{hop: 1, ip: "10.0.0.1", rtt_ms: 1.2},
    {hop: 2, ip: "203.0.113.4", rtt_ms: 12.5, geo: {lat: 37.4, lon: -122.1, city: "San Jose", confidence: .6}}]},
  geoPath: [{hop: 2, ip: "203.0.113.4", rtt_ms: 12.5,
    geo: {lat: 37.4, lon: -122.1, city: "San Jose", confidence: .6}}],
  boundary: "RTT IS MEASURED; GEOGRAPHY IS ESTIMATED", bounded: true, rawPacketsExposed: false};

test("host trace prompt distinguishes measurement from inferred geography", () => {
  const prompt = formatHostTracePrompt(result);
  assert.match(prompt, /RTT \/\/ 12.50 ms \/\/ MEASURED/);
  assert.match(prompt, /GEO-PATH \/\/ 1 ESTIMATED WAYPOINTS \/\/ INFERRED/);
  assert.match(prompt, /PROMPT \/\/ Explain route anomalies/);
});

test("geo path exposes only finite, explicitly inferred waypoints", () => {
  assert.deepEqual(geoPathPoints(result), [{hop: 2, ip: "203.0.113.4", latitude: 37.4,
    longitude: -122.1, city: "San Jose", confidence: .6, authority: "INFERRED_GEOIP_ESTIMATE"}]);
});

test("host trace sends only the graph selection reference", async () => {
  let request;
  const fetchImpl = async (url, init) => { request = {url, init}; return new Response(JSON.stringify(result), {status: 200}); };
  await runHostTrace({entityId: "host:8.8.8.8", graphRevision: "graph-1", ip: "127.0.0.1"}, {fetchImpl});
  assert.deepEqual(JSON.parse(request.init.body), {entityId: "host:8.8.8.8", graphRevision: "graph-1", maxHops: 20});
});
