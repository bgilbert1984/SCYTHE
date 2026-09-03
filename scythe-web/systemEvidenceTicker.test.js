import assert from "node:assert/strict";
import test from "node:test";

import {sanitizeTickerText, SystemEvidenceTicker, tickerItemsFromGraphUpdate,
  tickerItemsFromRfStatus} from "./systemEvidenceTicker.js";

class Element {
  constructor() { this.children=[]; this.dataset={}; this.attributes={}; this.listeners={}; this.textContent=""; }
  querySelector(selector) { return this.queries?.[selector] ?? null; }
  replaceChildren(...children) { this.children=children; }
  setAttribute(name,value) { this.attributes[name]=String(value); }
  addEventListener(name,listener) { this.listeners[name]=listener; }
  removeEventListener(name,listener) { if (this.listeners[name]===listener) delete this.listeners[name]; }
}

function tickerFixture({fetchImpl = () => new Promise(()=>{}), now = () => 1788051600000} = {}) {
  const root=new Element(),track=new Element(),summary=new Element(),toggle=new Element();
  root.queries={"[data-system-ticker-track]":track,"[data-system-ticker-summary]":summary,
    "[data-system-ticker-toggle]":toggle};
  root.ownerDocument={createElement:()=>new Element()};
  return {root,track,summary,toggle,ticker:new SystemEvidenceTicker({root,fetchImpl,now,rfRefreshMilliseconds:60_000})};
}

test("ticker summarizes only bounded graph, Eve, direction, liveness, and tension state", () => {
  const items = tickerItemsFromGraphUpdate({available:true,detail:{tier:"MAX"},eve:{committed:42,replayed:3},graph:{
    detectedNodeCount:10,detectedEdgeCount:20,nodes:[{id:"host:a",liveness:{state:"active"}},
      {id:"host:b",contradictions:["source disagreement"]}],edges:[{id:"flow:1",kind:"network_flow",
        labels:{proto:"tcp",operational_direction:"OUTBOUND"}},{id:"flow:2",kind:"network_flow",
        labels:{app_proto:"dns",operational_direction:"INBOUND"}}]}});
  assert.match(items[0],/2\/10 NODES · 2\/20 EDGES · MAX LENS/);
  assert.match(items[1],/42 COMMITTED/); assert.match(items[2],/DNS 1 · TCP 1/);
  assert.match(items[3],/INBOUND 1 · OUTBOUND 1/); assert.match(items[4],/BOUNDED HOST STATE \/\/ 1 ACTIVE/);
  assert.match(items[5],/1 DECLARED CONTRADICTIONS/);
});

test("ticker declares RF products independently from connection state", () => {
  const items = tickerItemsFromRfStatus({observations:{signal_classifications:{
      digital:3,analogue:2,unclassified:7,total:12}},bridge:{bridge_state:"streaming",iq_connected:true,
    products:{fft_frames:{state:"stale"},sparse_supports:{state:"live"}},
    config:{sensor_id:"NESDR-SMART",center_frequency_hz:100e6,sample_rate_hz:2.048e6}}});
  assert.match(items[0],/STREAMING · IQ CONNECTED/);
  assert.equal(items[1],"RF PRODUCTS // FFT STALE · SPARSE EVENTS LIVE · RAW IQ LOCAL ONLY");
  assert.equal(items[2],"RF DETECTIONS // DIGITAL 3 · ANALOGUE 2 · UNCLASSIFIED 7 · RETAINED EVENTS 12 · DERIVED SUMMARY");
  assert.equal(items[3],"RF AXES // MODULATION UNDECLARED · SYMBOL CLOCK UNDECLARED · PROTOCOL DECODER UNDECLARED");
  assert.equal(items[4],"RF CLASSIFIER // UNDECLARED · ANALOGUE DETECTOR UNDECLARED");
  assert.match(items[5],/100.000 MHz · 2.048 MS\/s/);
});

test("ticker states whether a classifier ran, not only what it counted", () => {
  const items = tickerItemsFromRfStatus({bridge:{bridge_state:"streaming",config:{}},
    observations:{signal_classifications:{digital:0,analogue:0,unclassified:6,total:6},
      classification_reasons:{NOT_ATTEMPTED:6},
      classifier:{state:"NOT_IMPLEMENTED",analogue_detector:"NOT_IMPLEMENTED",
        contract_phase:"0",reason_codes:{},
        axes:{modulation:{detector:"NOT_IMPLEMENTED"},protocol:{decoder:"NOT_IMPLEMENTED"}}}}});
  assert.equal(items[3],"RF AXES // MODULATION NOT_IMPLEMENTED · SYMBOL CLOCK NOT_IMPLEMENTED"
    + " · PROTOCOL DECODER NOT_IMPLEMENTED");
  assert.equal(items[4],"RF CLASSIFIER // NOT_IMPLEMENTED · ANALOGUE DETECTOR NOT_IMPLEMENTED"
    + " · UNCLASSIFIED BECAUSE NO CLASSIFIER RAN 6");
});

test("degraded and missing RF inputs remain explicit", () => {
  assert.match(tickerItemsFromGraphUpdate({available:false,retained:true})[0],/RETAINING LAST SNAPSHOT/);
  assert.equal(tickerItemsFromRfStatus(null)[0],"RF RECEIVER // STATUS UNAVAILABLE");
  const missing = tickerItemsFromRfStatus({bridge:{bridge_state:"ok",config:{}}});
  assert.match(missing[0],/UNNAMED SENSOR/); assert.match(missing[1],/FFT UNAVAILABLE/);
  assert.equal(missing[2],"RF DETECTIONS // COUNTS UNAVAILABLE");
  // An absent axes block reads UNDECLARED, never as a detector that ran.
  assert.equal(missing[3],"RF AXES // MODULATION UNDECLARED · SYMBOL CLOCK UNDECLARED · PROTOCOL DECODER UNDECLARED");
  assert.equal(missing[4],"RF CLASSIFIER // UNDECLARED · ANALOGUE DETECTOR UNDECLARED");
  assert.match(missing[5],/UNAVAILABLE · RATE UNAVAILABLE/);
});

test("ticker text removes control characters, collapses whitespace, and remains bounded", () => {
  const value=sanitizeTickerText(" host:\u0000evil\n\t<script>  "+"x".repeat(200),"",32);
  assert.equal(/[\u0000-\u001f\u007f]/.test(value),false); assert.equal(value.length,32);
  assert.match(value,/host: evil <script>/);
});

test("pause is labelled as motion-only while material announcements are throttled", () => {
  const {ticker,toggle,summary,track}=tickerFixture(); ticker.start();
  const update={available:true,detail:{tier:"OVERVIEW"},eve:{committed:1},graph:{graphRevision:"graph-1",
    nodes:[],edges:[]}};
  ticker.updateGraph(update); const firstAnnouncement=summary.textContent;
  ticker.updateGraph({...update,eve:{committed:2}});
  assert.equal(summary.textContent,firstAnnouncement); assert.match(track.children[0].textContent,/2 COMMITTED/);
  ticker.updateGraph({...update,detail:{tier:"MAX"}}); assert.notEqual(summary.textContent,firstAnnouncement);
  toggle.listeners.click(); assert.equal(toggle.attributes["aria-label"],"Resume evidence ticker motion");
  assert.equal(toggle.attributes["aria-pressed"],"true");
  toggle.listeners.click(); assert.equal(toggle.attributes["aria-label"],"Pause evidence ticker motion");
  ticker.destroy();
});

test("destroy aborts an in-flight RF status request", () => {
  let signal;
  const {ticker}=tickerFixture({fetchImpl:(_url,init)=>{signal=init.signal; return new Promise(()=>{});}});
  ticker.start(); assert.equal(signal.aborted,false); ticker.destroy(); assert.equal(signal.aborted,true);
});
