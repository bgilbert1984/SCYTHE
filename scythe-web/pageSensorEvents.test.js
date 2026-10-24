import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import * as sensorEvents from "./sensorEvents.js";
import {bootWorkspace} from "./workspaceBoot.js";

const page = readFileSync(new URL("../command-ops-visualization.html", import.meta.url), "utf8");

// The three page modules that listen for sensor events (SensorManager, LPIManager, FusionVisualization) are workspace tools now, each tested
// in its own file against the shape the entity stream delivers; this keeps the page itself from reading the old shape again.
test("the page no longer reads the event shape that never matched, and the boot exposes the helpers", () => {
  assert.doesNotMatch(page, /event\.entity\?\.type \|\| event\.type/);
  assert.doesNotMatch(page, /const entity = evt\.entity \|\| evt/);
  assert.doesNotMatch(page, /entity\.type === 'SENSOR_ACTIVITY'/);
  const win = {console: {warn() {}, log() {}, debug() {}}, Event: class { constructor(t) { this.type = t; } }, dispatchEvent() {}, fetch: async () => ({ok: true, json: async () => ({})})};
  bootWorkspace(win);
  assert.equal(win.ScytheSensorEvents, sensorEvents);
});
