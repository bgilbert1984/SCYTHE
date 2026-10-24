import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createAisStreamPlugin, WORLD_BBOX, AIS_STREAM_ID} from "./aisStreamPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const NEAR = 5000, FAR = 5000000;
const boxA = [[[-123, 37], [-122, 38]]], boxB = [[[-124, 36], [-121, 39]]];

function fakeSocket() {
  const handlers = new Map();
  return {
    on(name, fn) { handlers.set(name, [...(handlers.get(name) || []), fn]); },
    off(name, fn) { handlers.set(name, (handlers.get(name) || []).filter((h) => h !== fn)); },
    emit(name, payload) { for (const fn of handlers.get(name) || []) fn(payload); },
    count: (name) => (handlers.get(name) || []).length,
  };
}

function setup({socket = fakeSocket(), altitude = NEAR, connected = false, startImpl, stopImpl} = {}) {
  const world = {altitude, bbox: boxA, clock: 10000, started: [], stops: 0, vessels: [], notes: []};
  const syncHandlers = new Map();
  const sync = {
    connected,
    on(event, fn) { syncHandlers.set(event, [...(syncHandlers.get(event) || []), fn]); },
    off(event, fn) { syncHandlers.set(event, (syncHandlers.get(event) || []).filter((h) => h !== fn)); },
    fire(event) { for (const fn of syncHandlers.get(event) || []) fn(); },
    count: (event) => (syncHandlers.get(event) || []).length,
  };
  const cameraListeners = new Set();
  const camera = {
    altitude: () => world.altitude,
    boundingBox: () => world.bbox,
    onChange(fn) { cameraListeners.add(fn); return () => cameraListeners.delete(fn); },
    move() { for (const fn of [...cameraListeners]) fn(); },
    count: () => cameraListeners.size,
  };
  const api = {
    start: startImpl || (async (bbox) => { world.started.push(bbox); return {status: "started"}; }),
    stop: stopImpl || (async () => { world.stops += 1; return {status: "stopped"}; }),
  };
  const ws = createWorkspace({log: quiet});
  ws.provide("realtimeSocket", () => socket);
  ws.provide("syncEvents", sync);
  ws.provide("cameraView", camera);
  ws.provide("aisApi", api);
  ws.provide("vesselSink", {upsert: (v) => world.vessels.push(v.mmsi)});
  ws.provide("notify", (text) => world.notes.push(text));
  const ais = createAisStreamPlugin({log: quiet, now: () => world.clock});
  ws.register(ais.tool);
  return {ws, ais, world, socket, sync, camera};
}
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

test("nothing streams on activation alone: the server stream starts when the socket connects while zoomed in", async () => {
  const {ws, world, sync} = setup();
  await ws.activate(AIS_STREAM_ID);
  assert.equal(world.started.length, 0);
  sync.fire("connected");
  await flush();
  assert.deepEqual(world.started, [boxA]);
  assert.deepEqual(world.notes, ["🚢 AIS real-time streaming started"]);
});

test("connecting while zoomed out does not start streaming", async () => {
  const {ws, world, sync} = setup({altitude: FAR});
  await ws.activate(AIS_STREAM_ID);
  sync.fire("connected");
  await flush();
  assert.equal(world.started.length, 0);
});

test("activated after the socket was already connected, it starts instead of waiting for an event that has gone by", async () => {
  const {ws, world} = setup({connected: true});
  await ws.activate(AIS_STREAM_ID);
  await flush();
  assert.equal(world.started.length, 1);
});

test("with no camera reading it falls back to the world box, and an unknown altitude counts as far away", async () => {
  const {ws, ais, world, camera} = setup({altitude: NaN});
  camera.boundingBox = () => null;
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart();
  await flush();
  assert.equal(world.started.length, 0, "unknown altitude is treated as 1,000 km");
  world.altitude = NEAR;
  ais.controls.checkAndStart();
  await flush();
  assert.deepEqual(world.started, [WORLD_BBOX]);
});

test("two start requests in a row make one server call", async () => {
  const {ws, ais, world} = setup();
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart();
  ais.controls.checkAndStart();
  await flush();
  ais.controls.checkAndStart();   // already streaming
  await flush();
  assert.equal(world.started.length, 1);
});

test("vessel updates reach the renderer while active", async () => {
  const {ws, world, socket} = setup();
  await ws.activate(AIS_STREAM_ID);
  socket.emit("ais_update", {mmsi: 111});
  socket.emit("ais_update", {mmsi: 222});
  assert.deepEqual(world.vessels, [111, 222]);
});

test("moving the camera while streaming posts the new box, throttled, and only when it changed", async () => {
  const {ws, ais, world, camera} = setup();
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart(); await flush();
  assert.equal(world.started.length, 1);
  camera.move(); await flush();
  assert.equal(world.started.length, 1, "same box as the stream started with: no post");
  world.bbox = boxB;
  camera.move(); await flush();
  assert.deepEqual(world.started.at(-1), boxB);
  world.bbox = boxA;
  camera.move(); await flush();
  assert.equal(world.started.length, 2, "inside the throttle window: ignored");
  world.clock += 1500;
  camera.move(); await flush();
  assert.deepEqual(world.started.at(-1), boxA);
});

test("PINNED: zooming out does not stop the stream (the old guard tested a field that was never set)", async () => {
  const {ws, ais, world, camera} = setup();
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart(); await flush();
  world.altitude = FAR; world.clock += 5000;
  camera.move(); await flush();
  assert.equal(world.stops, 0);
  assert.equal(ais.controls.status().active, true);
});

test("a camera move before streaming starts does nothing: it does not start the stream either", async () => {
  const {ws, world, camera} = setup();
  await ws.activate(AIS_STREAM_ID);
  camera.move(); await flush();
  assert.equal(world.started.length, 0);
});

test("the page's toggle controls start and stop the server stream while active", async () => {
  const {ws, ais, world} = setup();
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart(); await flush();
  await ais.controls.stop();
  assert.equal(world.stops, 1);
  assert.equal(ais.controls.status().active, false);
  assert.deepEqual(world.notes.at(-1), "🚢 AIS streaming stopped");
});

test("the controls do nothing while the tool is inactive", async () => {
  const {ais, world} = setup();
  assert.equal(await ais.controls.checkAndStart(), undefined);
  assert.equal(await ais.controls.stop(), undefined);
  assert.equal(ais.controls.status(), null);
  assert.equal(world.started.length + world.stops, 0);
});

test("a failed stop leaves the stream marked active, so it can be retried, and the control does not throw", async () => {
  let fail = true;
  const {ws, ais, world} = setup({stopImpl: async () => { if (fail) throw new Error("offline"); world.stops += 1; }});
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart(); await flush();
  await ais.controls.stop();
  assert.equal(ais.controls.status().active, true);
  fail = false;
  await ais.controls.stop();
  assert.equal(ais.controls.status().active, false);
});

test("deactivating removes every listener it added and stops the server's stream", async () => {
  const {ws, ais, world, socket, sync, camera} = setup();
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart(); await flush();
  assert.equal(socket.count("ais_update"), 1);
  assert.equal(sync.count("connected"), 1);
  assert.equal(camera.count(), 1);
  assert.deepEqual(await ws.deactivate(AIS_STREAM_ID), []);
  assert.equal(socket.count("ais_update"), 0);
  assert.equal(sync.count("connected"), 0);
  assert.equal(camera.count(), 0);
  assert.equal(world.stops, 1);
  socket.emit("ais_update", {mmsi: 999});
  assert.deepEqual(world.vessels, [], "nothing is forwarded after deactivation");
});

test("deactivating while not streaming makes no stop call", async () => {
  const {ws, world} = setup();
  await ws.activate(AIS_STREAM_ID);
  await ws.deactivate(AIS_STREAM_ID);
  assert.equal(world.stops, 0);
});

test("a start request still in flight when the tool is deactivated is stopped when it lands", async () => {
  let release;
  const {ws, ais, world} = setup({startImpl: () => new Promise((resolve) => { release = resolve; })});
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart();
  await ws.deactivate(AIS_STREAM_ID);          // nothing to stop yet: the server has not answered
  assert.equal(world.stops, 0);
  release({status: "started"});
  await flush();
  assert.equal(world.stops, 1, "the stream it started after deactivation is stopped, not leaked");
  assert.equal(world.notes.length, 0, "and never announced as running");
});

test("a failed server stop on deactivation is reported by the contract, and listeners are still removed", async () => {
  const {ws, ais, socket} = setup({stopImpl: async () => { throw new Error("offline"); }});
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart(); await flush();
  const errors = await ws.deactivate(AIS_STREAM_ID);
  assert.equal(errors.length, 1);
  assert.equal(socket.count("ais_update"), 0);
  assert.equal(ws.state(AIS_STREAM_ID), "inactive");
});

test("re-activation does not stack listeners and streams again", async () => {
  const {ws, ais, world, socket, sync, camera} = setup();
  for (let i = 0; i < 3; i += 1) {
    await ws.activate(AIS_STREAM_ID);
    ais.controls.checkAndStart(); await flush();
    await ws.deactivate(AIS_STREAM_ID);
  }
  assert.equal(world.started.length, 3);
  assert.equal(world.stops, 3);
  assert.equal(socket.count("ais_update") + sync.count("connected") + camera.count(), 0);
  await ws.activate(AIS_STREAM_ID);
  socket.emit("ais_update", {mmsi: 7});
  assert.deepEqual(world.vessels, [7], "one listener, not three");
});

test("it works with no socket yet", async () => {
  const {ws, ais, world} = setup({socket: null});
  await ws.activate(AIS_STREAM_ID);
  ais.controls.checkAndStart(); await flush();
  assert.equal(world.started.length, 1);
  assert.deepEqual(await ws.deactivate(AIS_STREAM_ID), []);
});

test("it is refused while a service is missing", async () => {
  const ws = createWorkspace({log: quiet});
  ws.provide("realtimeSocket", () => null);
  ws.register(createAisStreamPlugin({log: quiet}).tool);
  await assert.rejects(ws.activate(AIS_STREAM_ID), /syncEvents|cameraView|aisApi|vesselSink|notify/);
});
