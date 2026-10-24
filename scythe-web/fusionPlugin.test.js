import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {
  createFusionPlugin, FUSION_ID, AOA_MS, TDOA_MS, FLOW_MS, GEO_NODE_MS, protocolColor, flowWidth, isPrivateIp, ipPosition, sensorPoint, aoaEndpoint, tdoaPoint,
} from "./fusionPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const near = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} is not within ${tolerance} of ${expected}`);

// --- pure pieces ---

test("protocolColor: the busiest protocol's colour; ALICEBLUE with no histogram; the fallback is half-transparent white", () => {
  assert.deepEqual(protocolColor(undefined), {name: "ALICEBLUE"});
  assert.deepEqual(protocolColor(null), {name: "ALICEBLUE"});
  for (const [proto, name] of [["TLS", "LIME"], ["QUIC", "MAGENTA"], ["DNS", "CYAN"], ["HTTP", "YELLOW"], ["SSH", "ORANGE"]]) {
    assert.deepEqual(protocolColor({[proto]: 5}), {name}, proto);
  }
  assert.deepEqual(protocolColor({FTP: 5}), {name: "WHITE", alpha: 0.5});
  assert.deepEqual(protocolColor({TLS: 10, HTTP: 2}), {name: "LIME"});
  assert.deepEqual(protocolColor({TLS: 2, HTTP: 10}), {name: "YELLOW"});
  assert.deepEqual(protocolColor({TLS: 5, HTTP: 5}), {name: "YELLOW"});   // a tie goes to the later key
  assert.deepEqual(protocolColor({SSH: 9, tls: 1}), {name: "ORANGE"});    // names are matched exactly
});

test("PINNED: an empty protocol histogram throws", () => {
  assert.throws(() => protocolColor({}), TypeError);
});

test("flowWidth is the order of magnitude of the bytes, from 1 to 8", () => {
  near(flowWidth(154000), Math.log10(154000));
  assert.equal(flowWidth(5), 1);
  assert.equal(flowWidth(0), 1);
  assert.equal(flowWidth(undefined), 1);
  assert.equal(flowWidth(1e12), 8);
  assert.equal(flowWidth(1e8), 8);
});

test("ipPosition: a resolved position first, else Los Angeles for a 'private' address, else nowhere", () => {
  const known = {lat: 1, lon: 2, alt: 0};
  assert.equal(ipPosition("8.8.8.8", {"8.8.8.8": known}), known);
  assert.deepEqual(ipPosition("192.168.1.50", {}), {lat: 34.0522, lon: -118.2437, alt: 0});
  assert.deepEqual(ipPosition("10.0.0.1", {}), {lat: 34.0522, lon: -118.2437, alt: 0});
  assert.equal(ipPosition("8.8.8.8", {}), null);
  assert.equal(ipPosition("11.0.0.1", {}), null);
  assert.equal(ipPosition("192.169.0.1", {}), null);
  assert.equal(ipPosition("192.168.1.50", {"192.168.1.50": known}), known);   // a resolved address wins over the placement
  const a = ipPosition("10.1.1.1", {}), b = ipPosition("10.1.1.1", {});
  assert.notEqual(a, b);   // a fresh object each time
  assert.ok(isPrivateIp("172.16.0.1"));
});

test("PINNED: any 172.x address is 'private', public ones included; an undefined address throws", () => {
  assert.deepEqual(ipPosition("172.67.198.10", {}), {lat: 34.0522, lon: -118.2437, alt: 0});
  assert.throws(() => ipPosition(undefined, {}), TypeError);
});

test("sensorPoint: lat / lon / alt, else those of location, else 0", () => {
  assert.deepEqual(sensorPoint({lat: 1, lon: 2, alt: 3}), {lat: 1, lon: 2, alt: 3});
  assert.deepEqual(sensorPoint({location: {lat: 4, lon: 5, alt: 6}}), {lat: 4, lon: 5, alt: 6});
  assert.deepEqual(sensorPoint({lat: 1, location: {lat: 9, lon: 5, alt: 6}}), {lat: 1, lon: 5, alt: 6});
  assert.deepEqual(sensorPoint({}), {lat: 0, lon: 0, alt: 0});
  assert.deepEqual(sensorPoint({position: {lat: 7, lon: 8}}), {lat: 0, lon: 0, alt: 0});   // the panel's normalized `position` is not read
});

test("aoaEndpoint: 5 km along the bearing, clockwise from north, altitude kept", () => {
  const start = {lat: 36, lon: -86, alt: 100};
  const north = aoaEndpoint(start, 0);
  near(north.lat, 36 + (5000 / 6378137) * (180 / Math.PI));
  near(north.lon, -86);
  assert.equal(north.alt, 100);
  const east = aoaEndpoint(start, 90);
  near(east.lat, 36, 1e-9);
  near(east.lon, -86 + (5000 / (6378137 * Math.cos((36 * Math.PI) / 180))) * (180 / Math.PI));
  const south = aoaEndpoint(start, 180);
  near(south.lat, 36 - (5000 / 6378137) * (180 / Math.PI));
  const west = aoaEndpoint(start, 270);
  assert.ok(west.lon < -86);
  near(aoaEndpoint({lat: 0.5, lon: 10, alt: 0}, 45).lat, 0.5 + (5000 * Math.cos(Math.PI / 4) / 6378137) * (180 / Math.PI));
});

test("tdoaPoint: lat / lon / alt, else location's; throws for a sensor with neither", () => {
  assert.deepEqual(tdoaPoint({lat: 1, lon: 2, alt: 3}), {lat: 1, lon: 2, alt: 3});
  assert.deepEqual(tdoaPoint({location: {lat: 4, lon: 5}}), {lat: 4, lon: 5, alt: 0});
  assert.deepEqual(tdoaPoint({lat: 1, lon: 2, location: {alt: 9}}), {lat: 1, lon: 2, alt: 9});
  assert.deepEqual(tdoaPoint({lat: 1, lon: 2, alt: 7}), {lat: 1, lon: 2, alt: 7});
  assert.throws(() => tdoaPoint({}), TypeError);
  assert.throws(() => tdoaPoint({lat: 1, lon: 2}), TypeError);   // PINNED: no altitude and no `location` to fall back on
});

// --- the tool ---

const activity = (kind, payload, sensorId = "s1", entityType = "SENSOR_ACTIVITY") => ({
  event_type: "CREATE", entity_id: `activity:${sensorId}:1`, entity_type: entityType, entity_data: {sensor_id: sensorId, kind, payload}, sequence_id: 1,
});

function setupControls(options) {
  const plugin = createFusionPlugin({log: quiet});
  const t = setup({...options, plugin});
  return {...t, controls: plugin.controls};
}

function setup({plugin, sensors = {s1: {lat: 36, lon: -86, alt: 100}, s2: {lat: 36.1, lon: -86.1, alt: 5}}, globeAvailable = true} = {}) {
  const state = {stream: new Map(), globe: [], sensors, globeAvailable};
  const globe = {available: () => state.globeAvailable, clear: () => state.globe.push(["clear"])};
  for (const name of ["drawAoa", "drawTdoa", "drawFlow", "drawGeoNode"]) globe[name] = (spec) => state.globe.push([name, spec]);
  const workspace = createWorkspace({log: quiet});
  workspace.provide("entityStream", {
    on: (event, handler) => state.stream.set(event, [...(state.stream.get(event) || []), handler]),
    off: (event, handler) => state.stream.set(event, (state.stream.get(event) || []).filter((h) => h !== handler)),
  });
  workspace.provide("fusionData", {sensor: (id) => state.sensors[id]});
  workspace.provide("fusionGlobe", globe);
  workspace.register((plugin || createFusionPlugin({log: quiet})).tool);
  const emit = (event) => { for (const h of state.stream.get(event.event_type.toLowerCase()) || []) h(event); };
  return {workspace, state, emit};
}
const up = async (t) => { await t.workspace.activate(FUSION_ID); return t; };
const drawn = (t) => t.state.globe.filter((g) => g[0] !== "clear");

test("activating subscribes to create and update only; deactivating unsubscribes", async () => {
  const t = await up(setup());
  assert.deepEqual([...t.state.stream.keys()].sort(), ["create", "update"]);
  assert.equal(t.state.stream.get("create").length, 1);
  assert.deepEqual(await t.workspace.deactivate(FUSION_ID), []);
  for (const handlers of t.state.stream.values()) assert.deepEqual(handlers, []);
  assert.deepEqual(t.state.globe, [["clear"]]);
});

test("the globe is cleared after the subscription is gone, not before", async () => {
  const log = [];
  const workspace = createWorkspace({log: quiet});
  workspace.provide("entityStream", {on() {}, off: () => log.push("off")});
  workspace.provide("fusionData", {sensor: () => undefined});
  workspace.provide("fusionGlobe", {available: () => true, clear: () => log.push("clear")});
  workspace.register(createFusionPlugin({log: quiet}).tool);
  await workspace.activate(FUSION_ID);
  await workspace.deactivate(FUSION_ID);
  assert.deepEqual(log, ["off", "off", "clear"]);
});

test("events that are not sensor activity are ignored; so are activity types it does not draw and activities without a sensor or a kind", async () => {
  const t = await up(setup());
  t.emit({event_type: "CREATE", entity_id: "e", entity_type: "RECON_ENTITY", entity_data: {}});
  t.emit(activity("detection", {snr_db: 3}));
  t.emit({event_type: "CREATE", entity_id: "a", entity_type: "SENSOR_ACTIVITY", entity_data: {kind: "aoa_measured", payload: {bearing_deg: 10}}});   // no sensor
  t.emit({event_type: "CREATE", entity_id: "a", entity_type: "SENSOR_ACTIVITY", entity_data: {sensor_id: "s1", payload: {}}});                       // no kind
  assert.deepEqual(drawn(t), []);
});

test("the activity type is read from activity_type when it differs from kind", async () => {
  const t = await up(setup());
  t.emit({event_type: "CREATE", entity_id: "a", entity_type: "SENSOR_ACTIVITY", entity_data: {sensor_id: "s1", kind: "something_else", activity_type: "aoa_measured", payload: {bearing_deg: 1}}});
  assert.equal(drawn(t).length, 1);
  assert.equal(drawn(t)[0][0], "drawAoa");
});

test("an angle of arrival draws a ray from the sensor, 5 km along the bearing, for 3 s", async () => {
  const t = await up(setup());
  t.emit(activity("aoa_measured", {bearing_deg: 90}));
  assert.equal(drawn(t).length, 1);
  const [name, spec] = drawn(t)[0];
  assert.equal(name, "drawAoa");
  assert.deepEqual(spec.from, {lat: 36, lon: -86, alt: 100});
  near(spec.to.lat, 36, 1e-9);
  assert.ok(spec.to.lon > -86);
  assert.equal(spec.to.alt, 100);
  assert.equal(spec.ttlMs, AOA_MS);
  assert.equal(AOA_MS, 3000);
});

test("an AoA ray with no bearing points north; it also draws for a sensor that keeps its position in `location`, and for the update event", async () => {
  const t = await up(setup({sensors: {s1: {location: {lat: 40, lon: -80}}}}));
  t.emit({...activity("aoa_measured", {}), event_type: "UPDATE"});
  const [, spec] = drawn(t)[0];
  assert.deepEqual(spec.from, {lat: 40, lon: -80, alt: undefined});
  near(spec.to.lon, -80);
  assert.ok(spec.to.lat > 40);
});

test("an AoA ray needs a globe, a known sensor, and a non-zero latitude and longitude (PINNED)", async () => {
  const none = await up(setup({globeAvailable: false}));
  none.emit(activity("aoa_measured", {bearing_deg: 1}));
  assert.deepEqual(drawn(none), []);
  const t = await up(setup({sensors: {s1: {lat: 36, lon: -86}, equator: {lat: 0, lon: -86}, meridian: {lat: 36, lon: 0}, normalized: {position: {lat: 1, lon: 2}}}}));
  t.emit(activity("aoa_measured", {bearing_deg: 1}, "unknown"));
  t.emit(activity("aoa_measured", {bearing_deg: 1}, "equator"));
  t.emit(activity("aoa_measured", {bearing_deg: 1}, "meridian"));
  t.emit(activity("aoa_measured", {bearing_deg: 1}, "normalized"));
  assert.deepEqual(drawn(t), []);
});

test("a time difference of arrival draws a line between the two sensors with its label, for 3 s", async () => {
  const t = await up(setup());
  t.emit(activity("tdoa_measured", {ref_sensor_id: "s2", tau_ns: 123.456}));
  const [name, spec] = drawn(t)[0];
  assert.equal(name, "drawTdoa");
  assert.deepEqual(spec.a, {lat: 36, lon: -86, alt: 100});
  assert.deepEqual(spec.b, {lat: 36.1, lon: -86.1, alt: 5});
  assert.equal(spec.label, "Δt: 123.5ns");
  assert.equal(spec.ttlMs, TDOA_MS);
  assert.equal(TDOA_MS, 3000);
});

test("a TDoA line needs a globe and both sensors; a sensor with no location throws (PINNED), and so does a missing delay", async () => {
  const none = await up(setup({globeAvailable: false}));
  none.emit(activity("tdoa_measured", {ref_sensor_id: "s2", tau_ns: 1}));
  assert.deepEqual(drawn(none), []);
  const t = await up(setup({sensors: {s1: {lat: 1, lon: 2, alt: 1}, s2: {lat: 3, lon: 4, alt: 1}, bare: {}}}));
  t.emit(activity("tdoa_measured", {ref_sensor_id: "nope", tau_ns: 1}));
  t.emit(activity("tdoa_measured", {ref_sensor_id: "s2", tau_ns: 1}, "nope"));
  assert.deepEqual(drawn(t), []);
  assert.throws(() => t.emit(activity("tdoa_measured", {ref_sensor_id: "s2", tau_ns: 1}, "bare")), TypeError);
  assert.throws(() => t.emit(activity("tdoa_measured", {ref_sensor_id: "s2"})), TypeError);
  assert.deepEqual(drawn(t), []);
});

test("a flow draws an arc between two resolved IPs, wide by its bytes and coloured by its busiest protocol, for 10 s", async () => {
  const t = await up(setup());
  t.emit(activity("geoip_resolved", {ip: "142.250.190.46", geo: {lat: 37.4, lon: -122.1}}));
  t.state.globe.length = 0;
  t.emit(activity("ndpi_flow_aggregate", {src_ip: "192.168.1.50", dst_ip: "142.250.190.46", bytes: 154000, protocol_hist: {QUIC: 900, TLS: 100}}));
  assert.equal(drawn(t).length, 1);
  const [name, spec] = drawn(t)[0];
  assert.equal(name, "drawFlow");
  assert.deepEqual(spec.from, {lat: 34.0522, lon: -118.2437, alt: 0});
  assert.deepEqual(spec.to, {lat: 37.4, lon: -122.1, alt: 0});
  near(spec.width, Math.log10(154000));
  assert.deepEqual(spec.color, {name: "MAGENTA"});
  assert.equal(spec.ttlMs, FLOW_MS);
  assert.equal(FLOW_MS, 10000);
});

test("a flow with an unresolved endpoint draws nothing; with no histogram it is ALICEBLUE; with no bytes it is 1 wide", async () => {
  const t = await up(setup());
  t.emit(activity("ndpi_flow_aggregate", {src_ip: "192.168.1.50", dst_ip: "8.8.8.8", bytes: 10, protocol_hist: {TLS: 1}}));
  t.emit(activity("ndpi_flow_aggregate", {src_ip: "8.8.8.8", dst_ip: "1.1.1.1", bytes: 10}));
  assert.deepEqual(drawn(t), []);
  t.emit(activity("ndpi_flow_aggregate", {src_ip: "10.0.0.1", dst_ip: "10.0.0.2"}));
  const [, spec] = drawn(t)[0];
  assert.deepEqual([spec.color, spec.width], [{name: "ALICEBLUE"}, 1]);
});

test("PINNED: a flow between two private addresses is drawn as a zero-length arc at Los Angeles; a missing address throws", async () => {
  const t = await up(setup());
  t.emit(activity("ndpi_flow_aggregate", {src_ip: "10.0.0.1", dst_ip: "192.168.0.1", bytes: 1, protocol_hist: {SSH: 1}}));
  const [, spec] = drawn(t)[0];
  assert.deepEqual(spec.from, spec.to);
  assert.throws(() => t.emit(activity("ndpi_flow_aggregate", {dst_ip: "10.0.0.1", bytes: 1})), TypeError);
  assert.throws(() => t.emit(activity("ndpi_flow_aggregate", {src_ip: "10.0.0.1", dst_ip: "10.0.0.2", protocol_hist: {}})), TypeError);
});

test("a resolved IP draws a point with its address (and city) for 60 s, and is remembered for flows", async () => {
  const t = await up(setup());
  t.emit(activity("geoip_resolved", {ip: "8.8.8.8", geo: {lat: 37.4, lon: -122.1, city: "Mountain View"}}));
  const [name, spec] = drawn(t)[0];
  assert.equal(name, "drawGeoNode");
  assert.deepEqual(spec.position, {lat: 37.4, lon: -122.1, alt: 0});
  assert.equal(spec.label, "8.8.8.8\nMountain View");
  assert.equal(spec.ttlMs, GEO_NODE_MS);
  assert.equal(GEO_NODE_MS, 60000);
  t.emit(activity("geoip_resolved", {ip: "1.1.1.1", geo: {lat: 1, lon: 2}}));
  assert.equal(drawn(t)[1][1].label, "1.1.1.1");
  t.emit(activity("ndpi_flow_aggregate", {src_ip: "8.8.8.8", dst_ip: "1.1.1.1", bytes: 100, protocol_hist: {DNS: 1}}));
  assert.deepEqual(drawn(t)[2][1].from, {lat: 37.4, lon: -122.1, alt: 0});
});

test("a geo payload without an address or a position is ignored", async () => {
  const t = await up(setup());
  t.emit(activity("geoip_resolved", {geo: {lat: 1, lon: 2}}));
  t.emit(activity("geoip_resolved", {ip: "8.8.8.8"}));
  t.emit(activity("geoip_resolved", {}));
  assert.deepEqual(drawn(t), []);
  t.emit(activity("ndpi_flow_aggregate", {src_ip: "8.8.8.8", dst_ip: "10.0.0.1", bytes: 1, protocol_hist: {TLS: 1}}));
  assert.deepEqual(drawn(t), []);   // 8.8.8.8 was not remembered
});

test("events after deactivation are ignored, and a new activation starts with no remembered IPs", async () => {
  const t = await up(setup());
  t.emit(activity("geoip_resolved", {ip: "8.8.8.8", geo: {lat: 1, lon: 2}}));
  const handlers = [...t.state.stream.values()].flat();
  await t.workspace.deactivate(FUSION_ID);
  t.state.globe.length = 0;
  for (const h of handlers) h(activity("aoa_measured", {bearing_deg: 1}));
  assert.deepEqual(t.state.globe, []);
  await up(t);
  t.emit(activity("ndpi_flow_aggregate", {src_ip: "8.8.8.8", dst_ip: "10.0.0.1", bytes: 1, protocol_hist: {TLS: 1}}));
  assert.deepEqual(drawn(t), []);
});

// --- the controls the PCAP simulation calls ---

const demoGeo = (ip, lat, lon, city) => ({ip, geo: {lat, lon, city, country: "US"}, asn: {asn: 1, org: "x"}});
const demoFlow = (src, dst, bytes, proto) => ({src_ip: src, dst_ip: dst, bytes, protocol_hist: proto, timestamp: 1700000000});

test("the controls do nothing while the tool is inactive", async () => {
  const t = setupControls();
  assert.equal(t.controls.updateGeoNode(demoGeo("8.8.8.8", 1, 2, "X")), undefined);
  assert.equal(t.controls.drawFlow("s", demoFlow("10.0.0.1", "10.0.0.2", 1, {TLS: 1})), undefined);
  assert.deepEqual(t.state.globe, []);
});

test("updateGeoNode draws a point for the demo's payload and drawFlow then draws an arc from the demo's private source to it", async () => {
  const t = setupControls();
  await up(t);
  t.controls.updateGeoNode(demoGeo("142.250.190.46", 37.422, -122.084, "Mountain View"));
  const [name, spec] = drawn(t)[0];
  assert.equal(name, "drawGeoNode");
  assert.deepEqual(spec.position, {lat: 37.422, lon: -122.084, alt: 0});
  assert.equal(spec.label, "142.250.190.46\nMountain View");
  assert.equal(spec.ttlMs, GEO_NODE_MS);
  t.controls.drawFlow("SENSOR-VM-172-234-197-23", demoFlow("192.168.1.50", "142.250.190.46", 154000, {TLS: 100}));
  const [flowName, flow] = drawn(t)[1];
  assert.equal(flowName, "drawFlow");
  assert.deepEqual(flow.from, {lat: 34.0522, lon: -118.2437, alt: 0});
  assert.deepEqual(flow.to, {lat: 37.422, lon: -122.084, alt: 0});
  assert.deepEqual(flow.color, {name: "LIME"});
  near(flow.width, Math.log10(154000));
  assert.equal(flow.ttlMs, FLOW_MS);
});

test("a flow to an address the simulation has not located is not drawn; one to a public 172.x address it has located uses that position, not Los Angeles", async () => {
  const t = setupControls();
  await up(t);
  t.controls.drawFlow("s", demoFlow("192.168.1.50", "1.1.1.1", 10, {DNS: 1}));
  assert.deepEqual(drawn(t), []);
  t.controls.updateGeoNode(demoGeo("172.67.198.10", 37.7749, -122.4194, "San Francisco"));
  t.controls.drawFlow("s", demoFlow("192.168.1.50", "172.67.198.10", 850000, {QUIC: 900}));
  const flow = drawn(t)[1][1];
  assert.deepEqual(flow.to, {lat: 37.7749, lon: -122.4194, alt: 0});
  assert.deepEqual(flow.color, {name: "MAGENTA"});
});

test("the controls and the stream share what has been located", async () => {
  const t = setupControls();
  await up(t);
  t.emit(activity("geoip_resolved", {ip: "8.8.8.8", geo: {lat: 1, lon: 2}}));
  t.controls.drawFlow("s", demoFlow("10.0.0.1", "8.8.8.8", 10, {SSH: 1}));
  assert.equal(drawn(t).length, 2);
  t.controls.updateGeoNode(demoGeo("9.9.9.9", 3, 4, "Y"));
  t.emit(activity("ndpi_flow_aggregate", demoFlow("10.0.0.1", "9.9.9.9", 10, {SSH: 1})));
  assert.equal(drawn(t).length, 4);
});

test("a geo payload without a position is ignored by the control, and the controls stop when the tool is deactivated", async () => {
  const t = setupControls();
  await up(t);
  t.controls.updateGeoNode({ip: "8.8.8.8"});
  t.controls.updateGeoNode(undefined);
  assert.deepEqual(drawn(t), []);
  await t.workspace.deactivate(FUSION_ID);
  t.state.globe.length = 0;
  t.controls.updateGeoNode(demoGeo("8.8.8.8", 1, 2, "X"));
  assert.deepEqual(t.state.globe, []);
});
