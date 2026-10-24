import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {createAdsbPlugin, trackToEntity, TRACKS_URL} from "./adsbPlugin.js";

const quiet = {warn() {}, debug() {}};

function fakeSocket() {
  const handlers = new Map();
  return {
    handlers,
    on(name, fn) { handlers.set(name, [...(handlers.get(name) || []), fn]); },
    off(name, fn) { handlers.set(name, (handlers.get(name) || []).filter((h) => h !== fn)); },
    emit(name, payload) { for (const fn of handlers.get(name) || []) fn(payload); },
    count: (name) => (handlers.get(name) || []).length,
  };
}

function setup({socket = fakeSocket(), fetchJson = async () => ({tracks: []})} = {}) {
  const drawn = new Map();
  const removed = [];
  const ws = createWorkspace({log: quiet});
  ws.provide("entityMarkers", {upsert: (e) => drawn.set(e.entity_id, e), remove: (id) => { drawn.delete(id); removed.push(id); }});
  ws.provide("realtimeSocket", () => socket);
  ws.provide("fetchJson", fetchJson);
  ws.register(createAdsbPlugin({log: quiet}));
  return {ws, socket, drawn, removed};
}
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const track = (over = {}) => ({icao: "A2F721", callsign: "UAL12", lat: 37.5, lon: -122.1, alt_ft: 10000, ...over});

test("a track becomes a recon entity the renderer can describe, with altitude in metres", () => {
  const entity = trackToEntity(track());
  assert.equal(entity.entity_id, "adsb_A2F721");
  assert.equal(entity.name, "UAL12");
  assert.deepEqual(entity.location, {lat: 37.5, lon: -122.1, altitude_m: 3048});
  assert.deepEqual(entity.ontology, ["aircraft", "ads-b", "safari"]);
  assert.equal(entity.disposition, "NEUTRAL");
});

test("the entity carries threat_level and disposition as strings: the description renderer lowercases both", () => {
  // createEntityDescription does entity.threat_level.toLowerCase(); a track without it threw on every aircraft.
  const entity = trackToEntity(track());
  assert.equal(entity.threat_level.toLowerCase(), "none");
  assert.equal(entity.disposition.toLowerCase(), "neutral");
});

test("a track that cannot be placed is skipped, never drawn at a guessed spot", () => {
  assert.equal(trackToEntity(null), null);
  assert.equal(trackToEntity({lat: 1, lon: 2}), null, "no icao");
  assert.equal(trackToEntity(track({lat: null})), null);
  assert.equal(trackToEntity(track({lon: undefined})), null);
  assert.equal(trackToEntity(track({lat: "north"})), null);
  assert.equal(trackToEntity(track({callsign: undefined})).name, "A2F721", "callsign falls back to the icao");
  assert.equal(trackToEntity(track({alt_ft: undefined})).location.altitude_m, 0);
});

test("while active, live updates and snapshots are drawn", async () => {
  const {ws, socket, drawn} = setup();
  await ws.activate("adsb");
  socket.emit("adsb_update", track());
  socket.emit("adsb_snapshot", {tracks: [track({icao: "B1"}), track({icao: "B2"}), {icao: "NOPE"}]});
  assert.deepEqual([...drawn.keys()].sort(), ["adsb_A2F721", "adsb_B1", "adsb_B2"]);
});

test("activation seeds from the REST tracks endpoint without waiting for it", async () => {
  let asked, release;
  const {ws, drawn} = setup({fetchJson: (url) => { asked = url; return new Promise((r) => { release = r; }); }});
  await ws.activate("adsb");               // resolves while the fetch is still pending
  await flush();
  assert.equal(asked, TRACKS_URL);
  release({tracks: [track()]});
  await flush();
  assert.ok(drawn.has("adsb_A2F721"));
});

test("a failing seed does not fail activation", async () => {
  const {ws} = setup({fetchJson: async () => { throw new Error("offline"); }});
  await ws.activate("adsb");
  await flush();
  assert.equal(ws.state("adsb"), "active");
});

test("deactivating removes every aircraft it drew and stops listening", async () => {
  const {ws, socket, drawn, removed} = setup();
  await ws.activate("adsb");
  socket.emit("adsb_update", track());
  socket.emit("adsb_update", track({icao: "C3"}));
  assert.deepEqual(await ws.deactivate("adsb"), []);
  assert.equal(drawn.size, 0);
  assert.deepEqual(removed.sort(), ["adsb_A2F721", "adsb_C3"]);
  assert.equal(socket.count("adsb_update"), 0);
  assert.equal(socket.count("adsb_snapshot"), 0);
  socket.emit("adsb_update", track({icao: "LATE"}));
  assert.equal(drawn.size, 0, "nothing arrives after deactivation");
});

test("a seed that resolves after deactivation is dropped", async () => {
  let release;
  const {ws, drawn} = setup({fetchJson: () => new Promise((r) => { release = r; })});
  await ws.activate("adsb");
  await flush();
  await ws.deactivate("adsb");
  release({tracks: [track()]});
  await flush();
  assert.equal(drawn.size, 0);
});

test("it only removes its own aircraft, and re-activation draws again", async () => {
  const {ws, socket, drawn} = setup();
  drawn.set("recon_other", {entity_id: "recon_other"});
  await ws.activate("adsb");
  socket.emit("adsb_update", track());
  await ws.deactivate("adsb");
  assert.deepEqual([...drawn.keys()], ["recon_other"]);
  await ws.activate("adsb");
  socket.emit("adsb_update", track());
  assert.ok(drawn.has("adsb_A2F721"));
  assert.equal(socket.count("adsb_update"), 1, "listeners are not stacked across re-activation");
});

test("with no socket yet it still activates and seeds from REST", async () => {
  const {ws, drawn} = setup({socket: null, fetchJson: async () => ({tracks: [track()]})});
  await ws.activate("adsb");
  await flush();
  assert.ok(drawn.has("adsb_A2F721"));
  assert.deepEqual(await ws.deactivate("adsb"), []);
  assert.equal(drawn.size, 0);
});

test("it is refused when the renderer service is missing", async () => {
  const ws = createWorkspace({log: quiet});
  ws.provide("realtimeSocket", () => null);
  ws.provide("fetchJson", async () => ({}));
  ws.register(createAdsbPlugin({log: quiet}));
  await assert.rejects(ws.activate("adsb"), /entityMarkers/);
});
