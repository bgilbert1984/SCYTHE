import assert from "node:assert/strict";
import test from "node:test";

import {formatFlowEvidencePrompt, prepareFlowEvidence} from "./flowEvidenceWorkflow.js";

test("flow click prepares only a pinned edge reference", async () => {
  let request;
  const payload = {status:"prepared", bounded:true, rawPacketsExposed:false,
    evidenceId:"flow-evidence-1", selection:{kind:"graph-edge",entityId:"flow:1",graphRevision:"graph-1"},
    flow:{id:"flow:1", evidenceClass:"OBSERVED", transport:{src_ip:"10.0.0.1",dest_ip:"8.8.8.8",proto:"tcp"}},
    packetDissections:[], coverage:{status:"TRANSPORT_SUMMARY_ONLY"}, boundary:"NO PAYLOAD"};
  const fetchImpl = async (url, init) => { request={url,init}; return new Response(JSON.stringify(payload)); };
  await prepareFlowEvidence({...payload.selection, unsafe:"drop"}, {fetchImpl});
  assert.equal(request.url, "/api/graphops/flow-evidence");
  assert.deepEqual(JSON.parse(request.init.body), {selection:payload.selection});
  assert.doesNotMatch(request.init.body, /unsafe/);
});

test("prepared flow prompt separates decoded fields from inference", () => {
  const output = formatFlowEvidencePrompt({evidenceId:"flow-evidence-1",
    selection:{graphRevision:"graph-1"}, flow:{id:"flow:1",evidenceClass:"OBSERVED",
      transport:{src_ip:"10.0.0.1",src_port:"50000",dest_ip:"8.8.8.8",dest_port:"443",proto:"tcp"},
      counters:{observationCount:3}, displayType:"TLS", displayTypeBasis:"OBSERVED_DECODED",
      direction:{operational_direction:"OUTBOUND",direction_basis:"DISCOVERED_SENSOR_INTERFACE",
        source_zone:"LOCAL",destination_zone:"NON_LOCAL"},
      motion:{motion_forward_delta_packets:3,motion_reverse_delta_packets:1,motion_interval_ms:500,
        motion_basis:"OBSERVED_SURICATA_COUNTER_DELTA"}},
    packetDissections:[{eventId:"eve-1",eventType:"tls",observedAt:"2026-08-15T00:00:00Z",
      fields:{app_proto:"tls",tls_sni:"example.org"}}],
    temporalDissection:{retainedEventCount:1,ringLimit:32,ordering:"OBSERVED_AT_ASCENDING",
      windowStart:"2026-08-15T00:00:00Z",windowEnd:"2026-08-15T00:00:00Z",
      durationMilliseconds:0,eventsOmittedBeforeRing:2,
      sequenceAuthority:"BOUNDED_DECODED_EVENT_TAIL; NOT A COMPLETE PACKET SEQUENCE"},
    coverage:{status:"DECODED_FIELDS_AVAILABLE"}, boundary:"INTENT IS INFERRED",
    suggestedQuestion:"Classify with alternatives."});
  assert.match(output, /TLS_SNI \/\/ example.org/);
  assert.match(output, /TEMPORAL DISSECTION RING \/\/ 1 \/ 32 EVENTS/);
  assert.match(output, /EVENT 01 \/\/ 2026-08-15T00:00:00Z/);
  assert.match(output, /OPERATIONAL DIRECTION \/\/ OUTBOUND/);
  assert.match(output, /MOTION \/\/ 3 FORWARD · 1 REVERSE PACKETS \/ 500 ms/);
  assert.match(output, /CLOUD \/\/ NOT YET DISCLOSED/);
  assert.match(output, /INTENT IS INFERRED/);
});

test("failed Suricata app protocol means decoder-unclassified, not failed activity", () => {
  const output = formatFlowEvidencePrompt({evidenceId:"flow-evidence-1",
    selection:{graphRevision:"graph-1"}, flow:{id:"flow:1", evidenceClass:"OBSERVED",
      transport:{src_ip:"10.0.0.1",dest_ip:"239.255.255.250",dest_port:1900,proto:"udp"}},
    packetDissections:[{fields:{app_proto:"failed"}}], coverage:{status:"DECODED_FIELDS_AVAILABLE"}});
  assert.match(output, /APP_PROTO \/\/ UNCLASSIFIED/);
  assert.match(output, /NOT AN APPLICATION FAILURE/);
  assert.doesNotMatch(output, /APP_PROTO \/\/ failed/i);
});
