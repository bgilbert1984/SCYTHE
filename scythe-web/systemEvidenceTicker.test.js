import assert from "node:assert/strict";
import test from "node:test";

import {tickerItemsFromGraphUpdate, tickerItemsFromRfStatus} from "./systemEvidenceTicker.js";

test("ticker summarizes only bounded graph, Eve, direction, liveness, and tension state", () => {
  const items = tickerItemsFromGraphUpdate({available:true,detail:{tier:"MAX"},eve:{committed:42,replayed:3},graph:{
    detectedNodeCount:10,detectedEdgeCount:20,nodes:[{id:"host:a",liveness:{state:"active"}},
      {id:"host:b",contradictions:["source disagreement"]}],edges:[{id:"flow:1",kind:"network_flow",
        labels:{proto:"tcp",operational_direction:"OUTBOUND"}},{id:"flow:2",kind:"network_flow",
        labels:{app_proto:"dns",operational_direction:"INBOUND"}}]}});
  assert.match(items[0],/2\/10 NODES · 2\/20 EDGES · MAX LENS/);
  assert.match(items[1],/42 COMMITTED/); assert.match(items[2],/DNS 1 · TCP 1/);
  assert.match(items[3],/INBOUND 1 · OUTBOUND 1/); assert.match(items[4],/1 ACTIVE/);
  assert.match(items[5],/1 DECLARED CONTRADICTIONS/);
});

test("ticker distinguishes RF bridge state, tuning, and raw-IQ boundary", () => {
  const items = tickerItemsFromRfStatus({bridge:{bridge_state:"reconnecting",iq_connected:false,
    config:{sensor_id:"NESDR-SMART",center_frequency_hz:100e6,sample_rate_hz:2.048e6}}});
  assert.match(items[0],/RECONNECTING · IQ DISCONNECTED/);
  assert.match(items[1],/100.000 MHz · 2.048 MS\/s · RAW IQ NOT EXPOSED/);
});
