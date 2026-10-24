/**
 * The fusion overlays as a workspace tool (the ninth tool on the lifecycle contract in lifecycle.js; moved out of
 * command-ops-visualization.html as `FusionVisualization`).
 *
 * While active it listens to the entity stream for sensor activity (read by sensorEvents.js) and draws what a measurement says on the
 * globe for a few seconds: an angle-of-arrival ray from a sensor, a time-difference line between two sensors, a traffic-flow arc between
 * two resolved IPs, and a geo-resolved IP as a point. The page is touched only through two services: `fusionData` (the sensors, from
 * the Sensors panel) and `fusionGlobe` (the Cesium entities, which also removes each one after its lifetime). What is drawn is decided
 * here as plain data (`aoaEndpoint`, `protocolColor`, `flowWidth`, `ipPosition`, ...).
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - A sensor at latitude or longitude exactly 0 gets no AoA ray (`!lat || !lon`), and the sensor's position is read from `lat` / `lon`
 *    or from `location`, not from the `position` the Sensors panel normalizes to.
 *  - `drawTdoa` reads `location` without checking it and throws for a sensor that has none; an empty protocol histogram throws
 *    (`reduce` of nothing); an activity that carries no payload is drawn with an empty one (an AoA ray at bearing 0).
 *  - Any IP starting `192.168.`, `10.` or `172.` is "private" and drawn at Los Angeles (a demo placement), which includes public `172.x` addresses.
 *  - The TDoA label carries its own `position`, which Cesium ignores, so the label cannot be seen.
 *  - Nothing on the page calls the PCAP demo's `window.FusionVisualization.updateGeoNode` / `drawFlow`, because nothing ever assigned
 *    `window.FusionVisualization`; they stay dormant. This tool does not set it either.
 *
 * Deliberate changes:
 *  - The stream subscription is removed on deactivate (the old one never was), and every overlay is removed with it. Each overlay's lifetime
 *    is owned by the globe service, so it is cleared with the tool (the old timers could not be cancelled).
 *  - Without a globe the draw calls do nothing, instead of throwing a TypeError from the stream handler.
 *  - The old code's retry loop (every second, for ever, until the entity stream existed) is gone.
 *  - The raw edge-event branch of `handleActivity` is gone: the stream handler only ever passed it the flat form from `activityOf`.
 *
 * Services it needs (see `requires`):
 *   entityStream  {on(event, handler), off(event, handler)}: the entity stream's handlers
 *   fusionData    {sensor(id) => the sensor the Sensors panel has under that id, or undefined}
 *   fusionGlobe   {available(), drawAoa(), drawTdoa(), drawFlow(), drawGeoNode(), clear()}: see `workspaceBoot.js`
 */

import {activityOf} from "./sensorEvents.js";

export const FUSION_ID = "fusion";
export const AOA_MS = 3000;
export const TDOA_MS = 3000;
export const FLOW_MS = 10000;
export const GEO_NODE_MS = 60000;
const AOA_RAY_M = 5000;
const EARTH_RADIUS_M = 6378137;
const PRIVATE_IP_PREFIXES = ["192.168.", "10.", "172."];
const DEMO_VM_POSITION = {lat: 34.0522, lon: -118.2437, alt: 0};   // where a "private" IP is drawn
const PROTOCOL_COLORS = {TLS: "LIME", QUIC: "MAGENTA", DNS: "CYAN", HTTP: "YELLOW", SSH: "ORANGE"};

/** The colour for a flow's busiest protocol: `{name}` of a Cesium colour, with `alpha` for the fallback; ALICEBLUE with no histogram. */
export function protocolColor(histogram) {
  if (!histogram) return {name: "ALICEBLUE"};
  const top = Object.keys(histogram).reduce((a, b) => (histogram[a] > histogram[b] ? a : b));
  return PROTOCOL_COLORS[top] ? {name: PROTOCOL_COLORS[top]} : {name: "WHITE", alpha: 0.5};
}

/** The line width for a flow: the order of magnitude of its bytes, from 1 to 8. */
export const flowWidth = (bytes) => Math.min(Math.max(Math.log10(bytes || 1), 1), 8);

export const isPrivateIp = (ip) => PRIVATE_IP_PREFIXES.some((prefix) => ip.startsWith(prefix));

/** Where an IP is drawn: its resolved position, else the demo placement for a "private" address, else nowhere. */
export function ipPosition(ip, cache) {
  if (cache[ip]) return cache[ip];
  return isPrivateIp(ip) ? {...DEMO_VM_POSITION} : null;
}

/** A sensor's {lat, lon, alt} as the AoA ray reads it: `lat` / `lon` / `alt`, else those of `location`, else 0. */
export function sensorPoint(sensor) {
  return {
    lat: sensor.lat || (sensor.location ? sensor.location.lat : 0),
    lon: sensor.lon || (sensor.location ? sensor.location.lon : 0),
    alt: sensor.alt || (sensor.location ? sensor.location.alt : 0),
  };
}

/** The end of a 5 km ray from `start` along `bearingDeg` (clockwise from north), on a spherical earth. */
export function aoaEndpoint(start, bearingDeg) {
  const bearing = (bearingDeg * Math.PI) / 180;
  const dy = AOA_RAY_M * Math.cos(bearing);
  const dx = AOA_RAY_M * Math.sin(bearing);
  return {
    lat: start.lat + (dy / EARTH_RADIUS_M) * (180 / Math.PI),
    lon: start.lon + (dx / (EARTH_RADIUS_M * Math.cos((Math.PI * start.lat) / 180))) * (180 / Math.PI),
    alt: start.alt,
  };
}

/** A sensor's {lat, lon, alt} as the TDoA line reads it. Throws for a sensor with no `location` and no `lat` / `lon` (as the old code did). */
export const tdoaPoint = (s) => ({lon: s.lon || s.location.lon, lat: s.lat || s.location.lat, alt: s.alt || s.location.alt || 0});

export function createFusionPlugin({log = console} = {}) {
  const tool = {
    id: FUSION_ID,
    title: "Fusion overlays",
    requires: ["entityStream", "fusionData", "fusionGlobe"],
    activate(ctx) {
      const stream = ctx.services.get("entityStream");
      const data = ctx.services.get("fusionData");
      const globe = ctx.services.get("fusionGlobe");
      const ipCache = {};   // ip -> {lat, lon, alt} from geoip activity

      function drawAoa(sensorId, payload) {
        if (!globe.available()) return;
        const sensor = data.sensor(sensorId);
        if (!sensor) return;
        const from = sensorPoint(sensor);
        if (!from.lat || !from.lon) return;
        globe.drawAoa({from, to: aoaEndpoint(from, payload.bearing_deg || 0), ttlMs: AOA_MS});
      }

      function drawTdoa(sensorId, payload) {
        if (!globe.available()) return;
        const s1 = data.sensor(sensorId);
        const s2 = data.sensor(payload.ref_sensor_id);
        if (!s1 || !s2) return;
        const a = tdoaPoint(s1), b = tdoaPoint(s2);
        globe.drawTdoa({a, b, label: `Δt: ${payload.tau_ns.toFixed(1)}ns`, ttlMs: TDOA_MS});
      }

      function drawFlow(sensorId, payload) {
        const from = ipPosition(payload.src_ip, ipCache);
        const to = ipPosition(payload.dst_ip, ipCache);
        if (from && to) globe.drawFlow({from, to, width: flowWidth(payload.bytes), color: protocolColor(payload.protocol_hist), ttlMs: FLOW_MS});
      }

      function updateGeoNode(payload) {
        if (!(payload && payload.ip && payload.geo)) return;
        const position = {lat: payload.geo.lat, lon: payload.geo.lon, alt: 0};
        ipCache[payload.ip] = position;
        globe.drawGeoNode({position, label: payload.ip + (payload.geo.city ? `\n${payload.geo.city}` : ""), ttlMs: GEO_NODE_MS});
      }

      function handleActivity(activity) {
        const kind = activity.activity_type;   // activityOf only returns an activity that names a sensor, and its payload is always an object
        const payload = activity.payload;
        const sensorId = activity.sensor_id;
        if (kind === "aoa_measured") drawAoa(sensorId, payload);
        else if (kind === "tdoa_measured") drawTdoa(sensorId, payload);
        else if (kind === "ndpi_flow_aggregate") drawFlow(sensorId, payload);
        else if (kind === "geoip_resolved") updateGeoNode(payload);
      }

      ctx.onCleanup(() => { globe.clear(); });   // registered first, so it runs last: after the subscription is gone
      const onEvent = ctx.guard((event) => {
        const activity = activityOf(event);
        if (activity) handleActivity(activity);
      });
      for (const type of ["create", "update"]) {
        stream.on(type, onEvent);
        ctx.onCleanup(() => stream.off(type, onEvent));
      }
      log.log?.("[FusionVisualization] Initialized & Subscribed");
    },
  };

  return {tool};
}
