import assert from "node:assert/strict";
import test from "node:test";
import {formatAddressContextPrompt, prepareAddressContext} from "./addressContextWorkflow.js";

test("multicast context sends only the pinned graph selection", async () => {
  let request; const selection={kind:"graph-node",entityId:"host:ff02::1:3",graphRevision:"graph-1"};
  const payload={status:"prepared",bounded:true,rawPacketsExposed:false,selection,
    address:{address:"ff02::1:3",ipVersion:6,addressClass:"MULTICAST_GROUP",scope:"LINK_LOCAL",
      knownService:"LLMNR",knownPurpose:"LINK-LOCAL MULTICAST NAME RESOLUTION"},
    passiveEvidence:{incidentFlowCount:2,protocolCounts:{udp:2},observedSenders:["fe80::1"]},
    activeMeasurement:{status:"NOT_APPLICABLE",reason:"ZERO OR MANY RESPONDERS"},
    boundary:"NOT A UNIQUE HOST",suggestedQuestion:"Explain observed senders."};
  const fetchImpl=async(url,init)=>{request={url,init};return new Response(JSON.stringify(payload));};
  await prepareAddressContext({...selection,unsafe:true},{fetchImpl});
  assert.deepEqual(JSON.parse(request.init.body),{selection});
  assert.match(formatAddressContextPrompt(payload),/LLMNR/);
  assert.match(formatAddressContextPrompt(payload),/ACTIVE TRACE \/\/ NOT_APPLICABLE/);
});
