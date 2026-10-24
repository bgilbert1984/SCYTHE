import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {
  createLpiPlugin, LPI_ID, WAITING, MAX_FEED_ITEMS, SHOWN_EVENTS, PULSE_MS, LABEL_MS, colorForKind, eventRecord, newestEvents, renderFeedRows, summaryOf,
  pulsePosition, pulseRadius, pulseAlpha, topClassOf, unshiftCapped,
} from "./lpiPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };
const near = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} is not within ${tolerance} of ${expected}`);

// --- pure pieces ---

test("colorForKind: the border by kind, first match wins", () => {
  assert.equal(colorForKind("window_defined"), "3px solid #666");
  assert.equal(colorForKind("tf_computed"), "3px solid #4a9eff");
  assert.equal(colorForKind("lpi_candidate_detected"), "3px solid #ffcc00");
  assert.equal(colorForKind("waveform_classified"), "3px solid #ff4444");
  assert.equal(colorForKind("something_else"), "3px solid #ccc");
  assert.equal(colorForKind("window_tf"), "3px solid #666");   // "window" is checked before "tf"
});

test("eventRecord: the payload's time or the clock's, the kind or 'unknown', and the activity as raw", () => {
  const activity = {sensor_id: "s", activity_type: "tf_computed", payload: {timestamp: 100, snr_db: 7, confidence: 0.5, algo: {name: "a"}, classes: [{class: "x"}], evidence: {iq_hash: "h"}}};
  assert.deepEqual(eventRecord(activity, 5000), {t: 100, kind: "tf_computed", snr_db: 7, confidence: 0.5, algo: {name: "a"}, classes: [{class: "x"}], evidence: {iq_hash: "h"}, raw: activity});
  const bare = eventRecord({sensor_id: "s"}, 5000);
  assert.deepEqual([bare.t, bare.kind, bare.snr_db], [5, "unknown", undefined]);
  assert.equal(eventRecord({sensor_id: "s", payload: {timestamp: 0}}, 5000).t, 5);   // a zero timestamp counts as missing
});

test("unshiftCapped puts the newest first and drops the oldest past the cap", () => {
  const list = [];
  for (let i = 1; i <= 5; i++) unshiftCapped(list, i, 3);
  assert.deepEqual(list, [5, 4, 3]);
  const given = [9];
  assert.equal(unshiftCapped(given, 8), given);
  const big = [];
  for (let i = 0; i < MAX_FEED_ITEMS + 10; i++) unshiftCapped(big, i);
  assert.equal(big.length, 500);
  assert.equal(big[0], 509);
  assert.equal(big.at(-1), 10);
});

test("newestEvents: newest first across sensors, at most `limit`; equal times keep the sensors' order", () => {
  const feed = {a: [{t: 3}, {t: 1}], b: [{t: 2}, {t: 3}]};
  assert.deepEqual(newestEvents(feed).map((e) => [e.sid, e.event.t]), [["a", 3], ["b", 3], ["b", 2], ["a", 1]]);
  assert.equal(newestEvents(feed, 2).length, 2);
  assert.deepEqual(newestEvents({}), []);
  const many = {a: Array.from({length: 80}, (_, i) => ({t: i}))};
  assert.equal(newestEvents(many).length, SHOWN_EVENTS);
  assert.equal(SHOWN_EVENTS, 50);
  assert.equal(newestEvents(many)[0].event.t, 79);
});

const row = (sid, event) => ({sid, event});
const rec = (extra = {}) => ({t: 1700000000, kind: "tf_computed", snr_db: undefined, classes: undefined, ...extra});

test("renderFeedRows: a row per event with its position, border, title, time and details", () => {
  const html = renderFeedRows([row("s1", rec({kind: "lpi_candidate_detected", snr_db: 12.345})), row("s2", rec())]);
  assert.equal((html.match(/<li /g) || []).length, 2);
  assert.match(html, /data-index="0"/);
  assert.match(html, /data-index="1"/);
  assert.match(html, /border-left: 3px solid #ffcc00; cursor: pointer;/);
  assert.match(html, /s1 \/ LPI CANDIDATE DETECTED/);
  assert.match(html, /SNR: <span style="color:#fff">12\.3 dB<\/span> \| <span style="color:#fd0">Candidate<\/span>/);
  assert.match(html, /SNR: <span style="color:#fff">--<\/span> \| Time-Freq/);
  assert.match(html, /color:#666;">\d\d:\d\d:\d\d\.\d{3}</);
  const expected = `${new Date(1700000000 * 1000).toLocaleTimeString([], {hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit"})}.000`;
  assert.ok(html.includes(`color:#666;">${expected}<`), expected);
  const ms = renderFeedRows([row("s", rec({t: 1700000000.007}))]);
  assert.match(ms, /\.00[67]</);   // milliseconds are padded to three digits
});

test("renderFeedRows: a classification shows its top class and confidence; a candidate with classes shows the class", () => {
  const html = renderFeedRows([row("s", rec({kind: "waveform_classified", snr_db: 9, classes: [{class: "fmcw", confidence: 0.876}, {class: "x", confidence: 0.1}]}))]);
  assert.match(html, /<span style="color:#f88; font-weight:bold;">fmcw<\/span> \(88%\)/);
  assert.doesNotMatch(html, />x</);
  assert.doesNotMatch(renderFeedRows([row("s", rec({kind: "lpi_candidate_detected", classes: []}))]), /Time-Freq/);
  assert.match(renderFeedRows([row("s", rec({kind: "lpi_candidate_detected", classes: []}))]), /Candidate/);
  assert.equal(renderFeedRows([]), "");
});

test("PINNED: a zero SNR shows as '--'", () => {
  assert.match(renderFeedRows([row("s", rec({snr_db: 0}))]), /color:#fff">--</);
});

test("renderFeedRows escapes the sensor id, the kind and the class name", () => {
  const evil = "<img src=x onerror=alert(1)>";
  const html = renderFeedRows([row(evil, rec({kind: evil, classes: [{class: evil, confidence: 1}]}))]);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;IMG SRC=X ONERROR=ALERT\(1\)&gt;/);
});

const NOW = 1_000_000_000_000;
const cls = (secondsAgo, extra = {}) => ({t: NOW / 1000 - secondsAgo, kind: "waveform_classified", snr_db: 14.04, classes: [{class: "fmcw", confidence: 0.912}], ...extra});
const cand = (secondsAgo, extra = {}) => ({t: NOW / 1000 - secondsAgo, kind: "lpi_candidate_detected", snr_db: 6.56, ...extra});

test("summaryOf: a classification in the last 10 s wins, then a candidate in the last 10 s, else idle", () => {
  const classified = summaryOf("s1", {lastClass: cls(2), lastCandidate: cand(1), snr_db: 3}, NOW);
  assert.equal(classified.borderColor, "#f44");
  assert.match(classified.html, /FMCW DETECTED/);
  assert.match(classified.html, /Conf: 91\.2% \| SNR: 14\.0 dB/);
  assert.match(classified.html, /Target: s1/);
  const candidate = summaryOf("s1", {lastClass: cls(30), lastCandidate: cand(1), snr_db: 3}, NOW);
  assert.equal(candidate.borderColor, "#fd0");
  assert.match(candidate.html, /LPI SIGNAL DETECTED/);
  assert.match(candidate.html, /SNR: 6\.6 dB/);
  const idle = summaryOf("s1", {lastClass: cls(30), lastCandidate: cand(30), snr_db: 3.14}, NOW);
  assert.equal(idle.borderColor, "#444");
  assert.match(idle.html, />s1</);
  assert.match(idle.html, /Monitoring\.\.\. \| Last SNR: 3\.1 dB/);
  assert.match(summaryOf("s1", {}, NOW).html, /Last SNR: -- dB/);
  assert.match(summaryOf("s1", {lastCandidate: cand(1, {snr_db: 0})}, NOW).html, /SNR: \? dB/);
});

test("summaryOf: exactly 10 s old is no longer active", () => {
  assert.equal(summaryOf("s", {lastClass: cls(9.999)}, NOW).borderColor, "#f44");
  assert.equal(summaryOf("s", {lastClass: cls(10)}, NOW).borderColor, "#444");
  assert.equal(summaryOf("s", {lastCandidate: cand(9.999)}, NOW).borderColor, "#fd0");
  assert.equal(summaryOf("s", {lastCandidate: cand(10)}, NOW).borderColor, "#444");
});

test("PINNED: a recent classification with no classes, or with no SNR, throws", () => {
  assert.throws(() => summaryOf("s", {lastClass: cls(1, {classes: []})}, NOW), TypeError);
  assert.throws(() => summaryOf("s", {lastClass: cls(1, {snr_db: undefined})}, NOW), TypeError);
});

test("summaryOf escapes the sensor id and the class name", () => {
  const evil = "<b>x</b>";
  for (const latest of [{lastClass: cls(1, {classes: [{class: evil, confidence: 1}]})}, {lastCandidate: cand(1)}, {}]) {
    const html = summaryOf(evil, latest, NOW).html;
    assert.doesNotMatch(html, /<b>/);
  }
  assert.match(summaryOf("s", {lastClass: cls(1, {classes: [{class: evil, confidence: 1}]})}, NOW).html, /&lt;B&gt;X&lt;\/B&gt; DETECTED/);
});

test("PINNED: pulsePosition uses ||, so a latitude or longitude of exactly 0 is no position", () => {
  assert.deepEqual(pulsePosition({position: {lat: "1.5", lon: "2.5"}}), {lat: 1.5, lon: 2.5});
  assert.deepEqual(pulsePosition({position: {latitude: 3, longitude: 4}}), {lat: 3, lon: 4});
  assert.equal(pulsePosition({position: {lat: 0, lon: 5}}), null);
  assert.equal(pulsePosition({position: {lat: 5, lon: 0}}), null);
  assert.equal(pulsePosition({position: {lat: "x", lon: 1}}), null);
  assert.equal(pulsePosition({}), null);
  assert.equal(pulsePosition(undefined), null);
});

test("the pulse grows from 100 m to 5.1 km and fades as it grows; the label names the top class", () => {
  assert.equal(pulseRadius(0), 100);
  assert.equal(pulseRadius(1), 5100);
  assert.equal(pulseRadius(0.5), 2600);
  assert.equal(pulseAlpha(0), 1);
  near(pulseAlpha(2500), 0.5);
  assert.equal(pulseAlpha(5000), 0);
  assert.equal(pulseAlpha(6000), 0);   // not negative
  assert.equal(topClassOf({classes: [{class: "fmcw"}, {class: "x"}]}), "fmcw");
  assert.equal(topClassOf({classes: []}), "UNKNOWN");
  assert.equal(topClassOf({}), "UNKNOWN");
  assert.equal(PULSE_MS, 2000);
  assert.equal(LABEL_MS, 4000);
  assert.equal(MAX_FEED_ITEMS, 500);
});

// --- the tool ---

function setup({sensors = {s1: {position: {lat: 36, lon: -86}}}, globeAvailable = true, hasFeed = true, responses, random} = {}) {
  const state = {
    stream: new Map(), view: [], globe: [], out: [], requests: [], flights: [], clock: NOW, nextFrame: 1, frames: new Map(), cancelled: [],
    sensors, globeAvailable, hasFeed, bound: null, unbound: 0,
  };
  const view = {
    hasFeed: () => state.hasFeed,
    clearFeed: () => state.view.push(["clearFeed"]),
    setFeed: (html, count) => state.view.push(["setFeed", html, count]),
    ensureSummaryCard: (id) => state.view.push(["ensureCard", id]),
    setSummaryCard: (id, border, html) => state.view.push(["card", id, border, html]),
    resetSummary: (html) => state.view.push(["reset", html]),
    showDetails: (text) => state.view.push(["details", text]),
    bind(handlers) { state.bound = handlers; return () => { state.unbound++; }; },
  };
  const globe = {
    available: () => state.globeAvailable,
    pulse: (spec) => state.globe.push(["pulse", spec]),
    label: (spec) => state.globe.push(["label", spec]),
    clear: () => state.globe.push(["clear"]),
  };
  const http = async (url, init) => { state.requests.push({url, init}); return responses ? responses(url, init) : {ok: true, json: async () => ({})}; };
  const workspace = createWorkspace({log: quiet});
  workspace.provide("entityStream", {
    on: (event, handler) => state.stream.set(event, [...(state.stream.get(event) || []), handler]),
    off: (event, handler) => state.stream.set(event, (state.stream.get(event) || []).filter((h) => h !== handler)),
  });
  workspace.provide("httpFetch", http);
  workspace.provide("entityConsole", {info: (t) => state.out.push(["info", t]), alert: (t) => state.out.push(["alert", t])});
  workspace.provide("lpiSensors", {ids: () => Object.keys(state.sensors), sensor: (id) => state.sensors[id], flyTo: (id) => state.flights.push(id)});
  workspace.provide("lpiView", view);
  workspace.provide("lpiGlobe", globe);
  workspace.provide("frames", {request: (fn) => { const id = state.nextFrame++; state.frames.set(id, fn); return id; }, cancel: (id) => { state.cancelled.push(id); state.frames.delete(id); }});
  workspace.provide("clock", {now: () => state.clock});
  workspace.register(createLpiPlugin({log: quiet, random}).tool);
  const emit = (event) => { for (const h of state.stream.get(event.event_type.toLowerCase()) || []) h(event); };
  const runFrames = () => { const pending = [...state.frames.entries()]; state.frames.clear(); for (const [, fn] of pending) fn(); };
  return {workspace, state, emit, runFrames};
}
const up = async (t) => { await t.workspace.activate(LPI_ID); return t; };
const activity = (kind, payload = {}, sensorId = "s1", eventType = "CREATE") => ({
  event_type: eventType, entity_id: `a:${sensorId}`, entity_type: "SENSOR_ACTIVITY", entity_data: {sensor_id: sensorId, kind, payload}, sequence_id: 1,
});
const views = (t, kind) => t.state.view.filter((v) => v[0] === kind);

test("activating subscribes to create and update and binds the panel; deactivating undoes both, stops the frames and clears the globe", async () => {
  const t = await up(setup());
  assert.deepEqual([...t.state.stream.keys()].sort(), ["create", "update"]);
  assert.equal(typeof t.state.bound.clear, "function");
  t.emit(activity("tf_computed", {snr_db: 5}));
  assert.equal(t.state.frames.size, 1);
  assert.deepEqual(await t.workspace.deactivate(LPI_ID), []);
  for (const handlers of t.state.stream.values()) assert.deepEqual(handlers, []);
  assert.equal(t.state.unbound, 1);
  assert.equal(t.state.cancelled.length, 1);
  assert.deepEqual(t.state.globe.at(-1), ["clear"]);
});

test("events that are not sensor activity are ignored; so are events after deactivation", async () => {
  const t = await up(setup());
  t.emit({event_type: "CREATE", entity_id: "e", entity_type: "RECON_ENTITY", entity_data: {}});
  t.emit({event_type: "CREATE", entity_id: "e", entity_type: "SENSOR_ACTIVITY", entity_data: {kind: "k"}});   // no sensor
  assert.equal(t.state.frames.size, 0);
  const handlers = [...t.state.stream.values()].flat();
  await t.workspace.deactivate(LPI_ID);
  for (const h of handlers) h(activity("tf_computed"));
  assert.equal(t.state.frames.size, 0);
});

test("an activity is recorded newest first per sensor, counted, and drawn on the next frame, not before", async () => {
  const t = await up(setup());
  t.emit(activity("tf_computed", {snr_db: 5, timestamp: 100}));
  t.emit(activity("window_defined", {timestamp: 200}));
  assert.deepEqual(t.state.view, []);   // nothing yet
  t.runFrames();
  const feed = views(t, "setFeed");
  assert.equal(feed.length, 2);
  assert.equal(feed[0][2], 2);
  assert.ok(feed[0][1].indexOf("WINDOW DEFINED") < feed[0][1].indexOf("TF COMPUTED"));   // newest first
});

test("the feed draws the newest 50 across sensors; the count is every event", async () => {
  const t = await up(setup({sensors: {a: {}, b: {}}}));
  for (let i = 0; i < 60; i++) t.emit(activity("tf_computed", {timestamp: i + 1}, i % 2 ? "a" : "b"));
  t.runFrames();
  const [, html, count] = views(t, "setFeed").at(-1);
  assert.equal((html.match(/<li /g) || []).length, 50);
  assert.equal(count, 60);
});

test("each sensor keeps at most 500 events", async () => {
  const t = await up(setup());
  for (let i = 0; i < MAX_FEED_ITEMS + 5; i++) t.emit(activity("tf_computed", {timestamp: i + 1}));
  t.runFrames();
  t.state.bound.select(0);
  assert.match(views(t, "details").at(-1)[1], /"timestamp": 505/);
  assert.equal(views(t, "setFeed").at(-1)[2], 505);   // the count is every event, the list is capped
  const html = views(t, "setFeed").at(-1)[1];
  assert.equal((html.match(/<li /g) || []).length, 50);
});

test("the status card is ensured and set for the sensor on each frame", async () => {
  const t = await up(setup());
  t.emit(activity("lpi_candidate_detected", {snr_db: 9, timestamp: NOW / 1000}));
  t.runFrames();
  assert.deepEqual(views(t, "ensureCard"), [["ensureCard", "s1"]]);
  const [, id, border, html] = views(t, "card")[0];
  assert.deepEqual([id, border], ["s1", "#fd0"]);
  assert.match(html, /LPI SIGNAL DETECTED/);
});

test("the latest SNR is remembered only when it is truthy; the last candidate and classification are kept", async () => {
  const t = await up(setup());
  t.emit(activity("tf_computed", {snr_db: 8, timestamp: NOW / 1000}));
  t.emit(activity("tf_computed", {snr_db: 0, timestamp: NOW / 1000}));   // PINNED: zero is not remembered
  t.runFrames();
  assert.match(views(t, "card").at(-1)[3], /Last SNR: 8\.0 dB/);
  t.emit(activity("waveform_classified", {snr_db: 11, classes: [{class: "fmcw", confidence: 0.9}], timestamp: NOW / 1000}));
  t.runFrames();
  assert.equal(views(t, "card").at(-1)[2], "#f44");
});

test("without a feed on the page, no feed is drawn (the status card still is)", async () => {
  const none = await up(setup({hasFeed: false}));
  none.emit(activity("tf_computed", {snr_db: 8}));
  none.runFrames();
  assert.deepEqual(none.state.view.map((v) => v[0]), ["ensureCard", "card"]);
});

test("a frame that has fired is not cancelled at deactivation; one still pending is", async () => {
  const t = await up(setup());
  t.emit(activity("tf_computed"));
  t.runFrames();
  t.emit(activity("tf_computed"));
  await t.workspace.deactivate(LPI_ID);
  assert.equal(t.state.cancelled.length, 1);
});

test("a frame that fires after deactivation draws nothing", async () => {
  const t = await up(setup());
  t.emit(activity("tf_computed"));
  const pending = [...t.state.frames.values()];
  await t.workspace.deactivate(LPI_ID);
  for (const fn of pending) fn();
  assert.deepEqual(t.state.view, []);
});

test("a candidate pulses yellow at its sensor for 2 s; a classification pulses red and floats the top class for 4 s", async () => {
  const t = await up(setup());
  t.state.clock = 1234567;
  t.emit(activity("lpi_candidate_detected", {snr_db: 4}));
  assert.deepEqual(t.state.globe, [["pulse", {id: "lpi-pulse-s1-1234567", lat: 36, lon: -86, color: "YELLOW", durationMs: 2000}]]);
  t.state.globe.length = 0;
  t.state.clock = 1234567 + 123;
  t.emit(activity("waveform_classified", {classes: [{class: "fmcw", confidence: 0.9}]}));
  const [pulse, label] = t.state.globe;
  assert.deepEqual(pulse, ["pulse", {id: "lpi-pulse-s1-1234690", lat: 36, lon: -86, color: "RED", durationMs: 2000}]);
  assert.equal(label[0], "label");
  assert.deepEqual(label[1], {id: "lpi-label-s1-1234690", lat: 36, lon: -86, text: "⚠ fmcw", offsetY: -10 - (1234690 % 50), ttlMs: 4000});
  t.emit(activity("waveform_classified", {}));
  assert.equal(t.state.globe.at(-1)[1].text, "⚠ UNKNOWN");
});

test("other kinds do not pulse; nor does an event with no globe, an unknown sensor, or a sensor at latitude 0 (PINNED)", async () => {
  const t = await up(setup({sensors: {s1: {position: {lat: 36, lon: -86}}, eq: {position: {lat: 0, lon: -86}}, nopos: {}}}));
  t.emit(activity("tf_computed"));
  t.emit(activity("lpi_candidate_detected", {}, "nope"));
  t.emit(activity("lpi_candidate_detected", {}, "eq"));
  t.emit(activity("lpi_candidate_detected", {}, "nopos"));
  assert.deepEqual(t.state.globe, []);
  const none = await up(setup({globeAvailable: false}));
  none.emit(activity("lpi_candidate_detected"));
  assert.deepEqual(none.state.globe, []);
});

test("PINNED: Clear empties the feed and the count but keeps the latest-per-sensor data, so a status card can return", async () => {
  const t = await up(setup());
  t.emit(activity("lpi_candidate_detected", {snr_db: 9, timestamp: NOW / 1000}));
  t.runFrames();
  t.state.view.length = 0;
  t.state.bound.clear();
  assert.deepEqual(t.state.view, [["clearFeed"], ["reset", WAITING]]);
  t.state.bound.select(0);   // nothing is shown any more
  assert.deepEqual(views(t, "details"), []);
  t.emit(activity("tf_computed", {timestamp: NOW / 1000}));
  t.runFrames();
  assert.equal(views(t, "card").at(-1)[2], "#fd0");   // the candidate from before the clear is still the sensor's last
  assert.equal(views(t, "setFeed").at(-1)[2], 1);     // the count restarted
  assert.equal((views(t, "setFeed").at(-1)[1].match(/<li /g) || []).length, 1);   // and the feed holds only what came after
});

test("clearing with no feed on the page still resets the summary", async () => {
  const t = await up(setup({hasFeed: false}));
  t.state.bound.clear();
  assert.deepEqual(t.state.view, [["reset", WAITING]]);
});

test("clicking a row shows that event's raw activity as JSON; a position that is not shown does nothing", async () => {
  const t = await up(setup());
  t.emit(activity("tf_computed", {snr_db: 5, timestamp: 100}));
  t.emit(activity("window_defined", {timestamp: 200}));
  t.runFrames();
  t.state.bound.select(1);
  const shown = JSON.parse(views(t, "details").at(-1)[1]);
  assert.equal(shown.activity_type, "tf_computed");
  assert.equal(shown.payload.snr_db, 5);
  assert.match(views(t, "details").at(-1)[1], /\n  "sensor_id"/);   // indented
  t.state.bound.select(0);
  assert.equal(JSON.parse(views(t, "details").at(-1)[1]).activity_type, "window_defined");
  const before = views(t, "details").length;
  t.state.bound.select(7);
  t.state.bound.select(-1);
  assert.equal(views(t, "details").length, before);
});

// the simulation

const trace = [{kind: "lpi_window_defined", timestamp: 10, snr_db: 7}, {kind: "lpi_candidate_detected", timestamp: 11, snr_db: 7.5}];

test("the simulation picks a sensor, a family and an SNR at random, posts to the sensor's endpoint (with no token), shows the trace, and flies to the sensor", async () => {
  const draws = [0.5, 0.5, 0.9, 0.9];   // family, SNR, sensor, centre frequency
  const t = await up(setup({sensors: {s1: {position: {lat: 36, lon: -86}}, s2: {position: {lat: 37, lon: -87}}}, random: () => draws.shift(), responses: async () => ({ok: true, json: async () => ({pipeline_trace: trace})})}));
  await t.state.bound.runSimulation();
  assert.deepEqual(t.state.out[0], ["info", "Starting LPI Simulation..."]);
  const request = t.state.requests[0];
  assert.equal(request.url, "/api/sensors/s2/process/lpi");
  assert.equal(request.init.method, "POST");
  assert.deepEqual(request.init.headers, {"Content-Type": "application/json"});
  assert.deepEqual(JSON.parse(request.init.body), {window: {center_freq_hz: 915000000 + Math.floor(0.9 * 1000000)}, simulate_detection: true, snr_db: 5 + 0.5 * 20, signal_family: "phase_coded"});
  t.runFrames();
  const [, html, count] = views(t, "setFeed").at(-1);
  assert.equal(count, 2);
  assert.match(html, /s2 \/ LPI WINDOW DEFINED/);
  assert.match(html, /s2 \/ LPI CANDIDATE DETECTED/);
  assert.deepEqual(t.state.flights, ["s2"]);
  assert.ok(t.state.globe.some((g) => g[0] === "pulse" && g[1].id.startsWith("lpi-pulse-s2-")));   // the candidate pulsed
});

test("the simulation's signal family is one of three, by the draw", async () => {
  const families = [];
  for (const [draw, expected] of [[0, "fmcw"], [0.4, "phase_coded"], [0.99, "noise_like"]]) {
    const draws = [draw, 0, 0, 0];
    const t = await up(setup({random: () => draws.shift() ?? 0, responses: async () => ({ok: true, json: async () => ({})})}));
    await t.state.bound.runSimulation();
    families.push([JSON.parse(t.state.requests[0].init.body).signal_family, expected]);
  }
  for (const [actual, expected] of families) assert.equal(actual, expected);
});

test("the simulation's trace items become activities: their kind is the type, themselves the payload", async () => {
  const t = await up(setup({random: () => 0, responses: async () => ({ok: true, json: async () => ({pipeline_trace: trace})})}));
  await t.state.bound.runSimulation();
  t.runFrames();
  t.state.bound.select(0);
  const raw = JSON.parse(views(t, "details").at(-1)[1]);
  assert.deepEqual([raw.sensor_id, raw.activity_type, raw.payload, raw.timestamp], ["s1", "lpi_candidate_detected", trace[1], 11]);
});

test("with no sensors the simulation uses SENSOR-TEST; a response without a trace still flies to the sensor", async () => {
  const t = await up(setup({sensors: {}, random: () => 0, responses: async () => ({ok: true, json: async () => ({})})}));
  await t.state.bound.runSimulation();
  assert.equal(t.state.requests[0].url, "/api/sensors/SENSOR-TEST/process/lpi");
  assert.deepEqual(t.state.flights, ["SENSOR-TEST"]);
  assert.equal(t.state.frames.size, 0);
});

test("a failed simulation request is only logged; a request that throws reports the message", async () => {
  const failed = await up(setup({random: () => 0, responses: async () => ({ok: false, status: 500, json: async () => ({})})}));
  await failed.state.bound.runSimulation();
  assert.deepEqual(failed.state.out, [["info", "Starting LPI Simulation..."]]);
  assert.deepEqual(failed.state.flights, []);
  const thrown = await up(setup({random: () => 0, responses: async () => { throw new Error("offline"); }}));
  await thrown.state.bound.runSimulation();
  assert.deepEqual(thrown.state.out.at(-1), ["alert", "Simulation failed: offline"]);
});

test("a simulation that finishes after deactivation shows and reports nothing", async () => {
  const hold = gate();
  const late = await up(setup({random: () => 0, responses: async () => { await hold.promise; return {ok: true, json: async () => ({pipeline_trace: trace})}; }}));
  const pending = late.state.bound.runSimulation();
  await settle();
  await late.workspace.deactivate(LPI_ID);
  late.state.view.length = 0;
  hold.open();
  await pending;
  assert.equal(late.state.frames.size, 0);
  assert.deepEqual(late.state.flights, []);
  const hold2 = gate();
  const failing = await up(setup({random: () => 0, responses: async () => { await hold2.promise; throw new Error("late"); }}));
  const p2 = failing.state.bound.runSimulation();
  await settle();
  await failing.workspace.deactivate(LPI_ID);
  hold2.open();
  await p2;
  assert.deepEqual(failing.state.out, [["info", "Starting LPI Simulation..."]]);
});
