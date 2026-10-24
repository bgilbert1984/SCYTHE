/**
 * The LPI (low probability of intercept) panel as a workspace tool (the eleventh tool on the lifecycle contract in lifecycle.js; moved out of
 * command-ops-visualization.html as `LPIManager`).
 *
 * While active it listens to the entity stream for sensor activity (read by sensorEvents.js), keeps the latest 500 events per sensor, and shows
 * them: a feed of the 50 newest events across sensors, one status card per sensor, and a details box for a clicked event. A detection or a
 * classification also pulses on the globe at the sensor and, for a classification, floats a label. The "Run simulation" button asks the server
 * to simulate a detection and shows what comes back at once.
 *
 * What is shown is decided here as plain strings and data (`renderFeedRows`, `summaryOf`, `colorForKind`, the pulse functions); the page is touched
 * only through services: `lpiView` (the panel's DOM), `lpiGlobe` (the Cesium pulse and label), `lpiSensors` (the Sensors panel's sensors and its
 * camera), `frames` (the page's animation frames) and `clock`.
 *
 * Kept as it was, including things that look unintended (see the tests that pin them):
 *  - Clear empties the feed and the count but not the latest-per-sensor data, so a status card can come back from before the clear.
 *  - A zero SNR shows as `--` in the feed (the old code tested truthiness) and is not remembered as the sensor's latest.
 *  - A status card for a recent classification with no classes, or with no SNR, throws while rendering (`classes[0]`, `snr_db.toFixed`).
 *  - A sensor's position is read with `||`, so a latitude or longitude of exactly 0 gets no pulse.
 *  - Two pulses for one sensor in the same millisecond have the same entity id, which Cesium refuses.
 *  - The simulation's request carries no session token, and it picks its sensor, signal family and SNR at random.
 *
 * Deliberate changes:
 *  - Everything that came from an event or a sensor (sensor ids, kinds, class names) is escaped where it reaches HTML. Events now arrive from other
 *    operators, and the old code put them into `innerHTML` as they came.
 *  - The feed's rows are handled by one listener on the list (they were given an `onclick` each), and a row is found by its position in what is shown.
 *  - Deactivating removes the stream subscription, the buttons' listeners, pending frames, and every pulse and label (the old animation loops and
 *    removal timers could not be cancelled).
 *  - Gone: `config.pulseDuration` (never read; the pulse lasts 2 s) and `state.artifacts`, an index of evidence pointers that was written for every
 *    event and read by nothing, and so only grew. `window.LPIManager` is no longer assigned (nothing on the page read it).
 *
 * Services it needs (see `requires`):
 *   entityStream  {on(event, handler), off(event, handler)}
 *   httpFetch     (url, init?) => Promise<Response>
 *   entityConsole {info(text), alert(text)}
 *   lpiSensors    {ids() => sensor ids, sensor(id) => the sensor or undefined, flyTo(id)}
 *   lpiView       the panel's DOM: see `workspaceBoot.js`
 *   lpiGlobe      {available(), pulse(), label(), clear()}: see `workspaceBoot.js`
 *   frames        {request(fn) => id, cancel(id)}
 *   clock         {now() => ms}
 */

import {activityOf} from "./sensorEvents.js";
import {escapeHtml} from "./htmlEscape.js";

export const LPI_ID = "lpi";
export const MAX_FEED_ITEMS = 500;
export const SHOWN_EVENTS = 50;
export const PULSE_MS = 2000;
export const PULSE_MIN_RADIUS_M = 100;
export const PULSE_GROWTH_M = 5000;
export const LABEL_MS = 4000;
const ACTIVE_MS = 10000;
const SIGNAL_FAMILIES = ["fmcw", "phase_coded", "noise_like"];

export const WAITING = '<div style="padding: 10px; background: rgba(30,30,30,0.6); border: 1px dashed #444; border-radius: 4px; text-align: center; color: #666; font-size: 12px;">Waiting for signal activity...</div>';

/** The feed row's left border by event kind. */
export function colorForKind(kind) {
  if (kind.includes("window")) return "3px solid #666";
  if (kind.includes("tf")) return "3px solid #4a9eff";
  if (kind.includes("candidate")) return "3px solid #ffcc00";
  if (kind.includes("classified")) return "3px solid #ff4444";
  return "3px solid #ccc";
}

/** What the plugin keeps for an activity: its time (seconds), kind, SNR, confidence, algorithm, classes and evidence, and the activity itself as `raw`. */
export function eventRecord(activity, nowMs) {
  const payload = activity.payload || {};
  return {
    t: payload.timestamp || nowMs / 1000, kind: activity.activity_type || "unknown", snr_db: payload.snr_db, confidence: payload.confidence,
    algo: payload.algo, classes: payload.classes, evidence: payload.evidence, raw: activity,
  };
}

/** Put `record` first in `list` and drop the oldest if there are more than `max`; returns `list`. */
export function unshiftCapped(list, record, max = MAX_FEED_ITEMS) {
  list.unshift(record);
  if (list.length > max) list.pop();
  return list;
}

/** The newest `limit` events across sensors, each as {sid, event}, newest first. */
export function newestEvents(feedBySensor, limit = SHOWN_EVENTS) {
  const all = [];
  for (const [sid, events] of Object.entries(feedBySensor)) for (const event of events) all.push({sid, event});
  all.sort((a, b) => b.event.t - a.event.t);
  return all.slice(0, limit);
}

const clockTime = (seconds) => {
  const date = new Date(seconds * 1000);
  return `${date.toLocaleTimeString([], {hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit"})}.${date.getMilliseconds().toString().padStart(3, "0")}`;
};

/** The feed's rows as HTML. Each carries its position in `shown` for the click handler. */
export function renderFeedRows(shown) {
  return shown.map(({sid, event}, index) => {
    let details = `SNR: <span style="color:#fff">${event.snr_db ? `${event.snr_db.toFixed(1)} dB` : "--"}</span>`;
    if (event.classes && event.classes.length > 0) {
      const top = event.classes[0];
      details += ` | <span style="color:#f88; font-weight:bold;">${escapeHtml(top.class)}</span> (${(top.confidence * 100).toFixed(0)}%)`;
    } else if (event.kind === "lpi_candidate_detected") {
      details += ' | <span style="color:#fd0">Candidate</span>';
    } else if (event.kind === "tf_computed") {
      details += " | Time-Freq";
    }
    return `<li class="list-item" data-index="${index}" style="border-left: ${colorForKind(event.kind)}; cursor: pointer;">
                        <div class="list-item-header">
                            <span class="list-item-title" style="font-size:11px;">${escapeHtml(sid)} / ${escapeHtml(event.kind.replace(/_/g, " ").toUpperCase())}</span>
                            <span style="font-size:10px; color:#666;">${clockTime(event.t)}</span>
                        </div>
                        <div class="list-item-subtitle">
                             ${details}
                        </div>
                     </li>`;
  }).join("");
}

/**
 * A sensor's status card: its border colour and HTML. A classification in the last 10 s wins, then a candidate in the last 10 s, else idle.
 * Throws for a recent classification with no classes or no SNR (as the old code did).
 */
export function summaryOf(sensorId, latest, nowMs) {
  const id = escapeHtml(sensorId);
  const lastClass = latest.lastClass, lastCand = latest.lastCandidate;
  if (lastClass && nowMs - lastClass.t * 1000 < ACTIVE_MS) {
    const cls = lastClass.classes[0];
    return {borderColor: "#f44", html: `
                        <div style="color:#f66; font-weight:bold; font-size:13px;">${escapeHtml(cls.class.toUpperCase())} DETECTED</div>
                        <div style="font-size:11px; color:#aaa;">Conf: ${(cls.confidence * 100).toFixed(1)}% | SNR: ${lastClass.snr_db.toFixed(1)} dB</div>
                        <div style="font-size:10px; color:#888; margin-top:2px;">Target: ${id}</div>
                    `};
  }
  if (lastCand && nowMs - lastCand.t * 1000 < ACTIVE_MS) {
    return {borderColor: "#fd0", html: `
                        <div style="color:#fd0; font-weight:bold; font-size:13px;">LPI SIGNAL DETECTED</div>
                        <div style="font-size:11px; color:#aaa;">Investigating... | SNR: ${lastCand.snr_db ? lastCand.snr_db.toFixed(1) : "?"} dB</div>
                        <div style="font-size:10px; color:#888; margin-top:2px;">Target: ${id}</div>
                     `};
  }
  return {borderColor: "#444", html: `
                        <div style="color:#aaa; font-weight:bold; font-size:13px;">${id}</div>
                        <div style="font-size:11px; color:#666;">Monitoring... | Last SNR: ${latest.snr_db ? latest.snr_db.toFixed(1) : "--"} dB</div>
                    `};
}

/** A sensor's {lat, lon} for a pulse, or null (`||`: a latitude or longitude of exactly 0 falls through to `latitude` / `longitude`). */
export function pulsePosition(sensor) {
  if (!sensor || !sensor.position) return null;
  const lat = parseFloat(sensor.position.lat || sensor.position.latitude);
  const lon = parseFloat(sensor.position.lon || sensor.position.longitude);
  return Number.isNaN(lat) || Number.isNaN(lon) ? null : {lat, lon};
}

/** The radius of an expanding pulse (meters) after `progress` (0 to 1) of its life, and its opacity at a radius. */
export const pulseRadius = (progress) => PULSE_MIN_RADIUS_M + PULSE_GROWTH_M * progress;
export const pulseAlpha = (radius) => Math.max(0, 1 - radius / PULSE_GROWTH_M);

/** The top class of a classification payload for the floating label. */
export const topClassOf = (payload) => (payload.classes && payload.classes[0] ? payload.classes[0].class : "UNKNOWN");

export function createLpiPlugin({log = console, random = () => Math.random()} = {}) {
  const tool = {
    id: LPI_ID,
    title: "LPI",
    requires: ["entityStream", "httpFetch", "entityConsole", "lpiSensors", "lpiView", "lpiGlobe", "frames", "clock"],
    activate(ctx) {
      const stream = ctx.services.get("entityStream");
      const http = ctx.services.get("httpFetch");
      const out = ctx.services.get("entityConsole");
      const sensors = ctx.services.get("lpiSensors");
      const view = ctx.services.get("lpiView");
      const globe = ctx.services.get("lpiGlobe");
      const frames = ctx.services.get("frames");
      const clock = ctx.services.get("clock");

      let state = {feedBySensor: {}, latestBySensor: {}, eventCount: 0};
      let shown = [];   // what the feed shows now, in order: a clicked row is found here
      const pendingFrames = new Set();

      function drawFeed(clear = false) {
        if (!view.hasFeed()) return;
        if (clear) { shown = []; view.clearFeed(); return; }
        shown = newestEvents(state.feedBySensor);
        view.setFeed(renderFeedRows(shown), state.eventCount);
      }

      function drawSummary(sensorId) {   // a sensor has its latest data by the time it is drawn
        view.ensureSummaryCard(sensorId);
        const card = summaryOf(sensorId, state.latestBySensor[sensorId], clock.now());
        view.setSummaryCard(sensorId, card.borderColor, card.html);
      }

      function pulse(sensorId, kind, payload) {
        if (!globe.available()) return;
        const where = pulsePosition(sensors.sensor(sensorId));
        if (!where) return;
        const stamp = clock.now();
        globe.pulse({id: `lpi-pulse-${sensorId}-${stamp}`, ...where, color: kind === "waveform_classified" ? "RED" : "YELLOW", durationMs: PULSE_MS});
        if (kind === "waveform_classified") {
          globe.label({id: `lpi-label-${sensorId}-${clock.now()}`, ...where, text: `⚠ ${topClassOf(payload)}`, offsetY: -10 + (-1 * (clock.now() % 50)), ttlMs: LABEL_MS});
        }
      }

      function handleActivityEvent(activity) {
        const sensorId = activity.sensor_id;
        const payload = activity.payload;   // an object: activityOf supplies {}, and the simulation passes its trace items
        const record = eventRecord(activity, clock.now());
        const kind = record.kind;
        unshiftCapped(state.feedBySensor[sensorId] ||= [], record);
        state.eventCount++;
        const latest = (state.latestBySensor[sensorId] ||= {});
        latest.lastSeen = clock.now();
        if (payload.snr_db) latest.snr_db = payload.snr_db;
        if (kind === "lpi_candidate_detected") latest.lastCandidate = record;
        if (kind === "waveform_classified") latest.lastClass = record;
        const frame = frames.request(ctx.guard(() => {
          pendingFrames.delete(frame);
          drawFeed();
          drawSummary(sensorId);
        }));
        pendingFrames.add(frame);
        if (kind === "lpi_candidate_detected" || kind === "waveform_classified") pulse(sensorId, kind, payload);
      }

      function clearAll() {
        state.feedBySensor = {};
        state.eventCount = 0;
        drawFeed(true);
        view.resetSummary(WAITING);
      }

      async function runSimulation() {
        out.info("Starting LPI Simulation...");
        const family = SIGNAL_FAMILIES[Math.floor(random() * SIGNAL_FAMILIES.length)];
        const snr = 5.0 + random() * 20.0;
        let sensorId = "SENSOR-TEST";
        const ids = sensors.ids();
        if (ids.length > 0) sensorId = ids[Math.floor(random() * ids.length)];
        try {
          const response = await http(`/api/sensors/${sensorId}/process/lpi`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({window: {center_freq_hz: 915000000 + Math.floor(random() * 1000000)}, simulate_detection: true, snr_db: snr, signal_family: family}),
          });
          if (response.ok) {
            const data = await response.json();
            if (ctx.signal.aborted) return;
            if (data.pipeline_trace) {
              data.pipeline_trace.forEach((item) => handleActivityEvent({sensor_id: sensorId, activity_type: item.kind, payload: item, timestamp: item.timestamp}));
            }
            sensors.flyTo(sensorId);
          } else {
            log.error?.("Sim error", response);
          }
        } catch (error) {
          log.error?.(error);
          if (!ctx.signal.aborted) out.alert(`Simulation failed: ${error.message}`);
        }
      }

      const onEvent = ctx.guard((event) => {
        const activity = activityOf(event);
        if (activity) handleActivityEvent(activity);
      });
      ctx.onCleanup(() => { globe.clear(); for (const frame of pendingFrames) frames.cancel(frame); pendingFrames.clear(); });   // registered first, so it runs last
      for (const type of ["create", "update"]) {
        stream.on(type, onEvent);
        ctx.onCleanup(() => stream.off(type, onEvent));
      }
      ctx.onCleanup(view.bind({clear: clearAll, runSimulation: () => runSimulation(), select: (index) => { if (shown[index]) view.showDetails(JSON.stringify(shown[index].event.raw, null, 2)); }}));
      log.log?.("[LPIManager] Initialized");
    },
  };

  return {tool};
}
