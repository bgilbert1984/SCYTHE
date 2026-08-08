import assert from "node:assert/strict";
import test from "node:test";
import {bodyFixedCartesian, celestialBody, moonEllipsoid} from "./celestialBodies.js";

test("Moon body-fixed conversion uses the lunar ellipsoid and rejects Earth leakage", () => {
  const moon = {name:"moon", cartographicToCartesian:value => ({...value, ellipsoid:"moon"})};
  const Cesium = {Ellipsoid:{MOON:moon,WGS84:{name:"earth"}},
    Cartographic:{fromDegrees:(longitude,latitude,height)=>({longitude,latitude,height})}};
  assert.equal(celestialBody("MOON").referenceFrame, "MOON_ME_DE421");
  assert.equal(moonEllipsoid(Cesium), moon);
  assert.deepEqual(bodyFixedCartesian(Cesium,"MOON",10,-89,100),
    {longitude:10,latitude:-89,height:100,ellipsoid:"moon"});
  assert.throws(()=>celestialBody("MARS"), /Unsupported celestial body/);
});
