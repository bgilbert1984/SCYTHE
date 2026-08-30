import assert from "node:assert/strict";
import test from "node:test";

import {projectCityContext} from "./cityContextProjection.js";

test("city context is bounded, inferred, and display-only", () => {
  const graph={graphRevision:"g",nodes:[
    {id:"host:a",enrichment:{geo:{city:"Seattle",region:"Washington",country:"United States",latitude:47.61,longitude:-122.33}}},
    {id:"host:b",enrichment:{geo:{city:"Seattle",region:"Washington",country:"United States",latitude:47.62,longitude:-122.34}}},
    {id:"host:c",enrichment:{geo:{city:"Everett",latitude:47.98,longitude:-122.20}}},
    {id:"host:d",enrichment:{geo:{latitude:1,longitude:2}}}],edges:[]};
  const result=projectCityContext(graph,{cityLimit:1,membershipLimit:10});
  const city=result.nodes.find((node)=>node.kind==="geographic_city_context");
  assert.equal(result.cityContext.nodeCount,1); assert.equal(city.evidenceClass,"INFERRED");
  assert.equal(city.labels.name,"Seattle"); assert.equal(city.labels.host_count,"2");
  assert.equal(city.display.selectionDisabled,true); assert.equal(result.edges.length,2);
  assert.equal(graph.nodes.length,4); assert.equal(graph.edges.length,0);
});

test("RF receiver is projected as interactive display context without mutating graph authority", () => {
  const graph={graphRevision:"g",nodes:[],edges:[],rfSensorContext:{sensorId:"NESDR-SMART-V5-14530058",
    model:"Nooelec NESDR SMArt",bridgeState:"reconnecting",iqConnected:false,centerFrequencyHz:100e6,
    sampleRateHz:2.048e6,captureOwner:"orchestrator",latitude:47.79,longitude:-122.36,
    accuracyMeters:24,locationAuthority:"MEASURED_BROWSER_GEOLOCATION"}};
  const result=projectCityContext(graph); const sensor=result.nodes.find((node)=>node.kind==="rf_receiver_sensor");
  assert.equal(result.rfSensorDisplayContext.nodeCount,1); assert.equal(sensor.id,"sensor:NESDR-SMART-V5-14530058");
  assert.equal(sensor.display.selectionPurpose,"RF_SENSOR_CONTEXT");
  assert.equal(sensor.display.selectionDisabled,false); assert.equal(sensor.metadata.device_presence,"CONFIGURED_NOT_USB_ATTESTED");
  assert.equal(sensor.enrichment.geo.authority,"MEASURED_BROWSER_GEOLOCATION");
  assert.deepEqual(graph.nodes,[]);
});
