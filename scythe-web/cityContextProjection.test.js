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
