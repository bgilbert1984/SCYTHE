import test from "node:test";
import assert from "node:assert/strict";
import {createWorkspace} from "./lifecycle.js";
import {
  createPropagationPlugin, PROPAGATION_ID, escapeHtml, conditionColor, renderError, renderResults, renderHistory, readPath,
  CALCULATING, NO_RESULT,
} from "./propagationPlugin.js";

const quiet = {warn() {}, debug() {}, log() {}, error() {}};
const settle = () => new Promise((resolve) => setImmediate(resolve));
const gate = () => { let open; const promise = new Promise((resolve) => { open = resolve; }); return {promise, open}; };

const analysisOf = (extra = {}) => ({
  geometry: {distance_km: 12.345}, losses: {total_db: 130.04, freeSpace_db: 110.1, diffraction_db: 0, atmospheric_db: 0.26},
  linkBudget: {linkViable: true, linkMargin_db: 8.46, receivedPower_dbm: -91.5, rxSensitivity_dbm: -100},
  terrain: {sampleCount: 64, obstructions: 2}, processingTime_ms: 3.21, ...extra,
});
const RAW = {
  tx: {lat: "36.1627", lon: "-86.7816", height: "50", frequency: "900", power: "30"},
  rx: {lat: "36.1527", lon: "-86.7916", height: "2", sensitivity: "-100"},
};

function setup({engine = {}, rawPath = RAW, activityMissing = false} = {}) {
  const calls = [], html = {}, text = {}, colors = {};
  const handlers = {};
  let unbound = 0;
  const fakeEngine = {
    present: true,
    conditions: {kFactor: 1.3333, surfaceRefractivity: 315}, condition: "standard",
    available() { return this.present; },
    atmosphere() { return {conditions: this.conditions, classification: {condition: this.condition}}; },
    setAtmosphere: (c) => calls.push(["setAtmosphere", c]),
    calculate: async (tx, rx) => { calls.push(["calculate", tx, rx]); return analysisOf(); },
    visualize: async (id, tx, rx) => { calls.push(["visualize", id, tx, rx]); return analysisOf(); },
    removePath: (id) => calls.push(["removePath", id]),
    clearAll: () => calls.push(["clearAll"]),
    ...engine,
  };
  const missing = new Set();
  const view = {
    setText: (id, value) => { text[id] = value; },
    setHtml: (id, value) => { if (missing.has(id)) return false; html[id] = value; return true; },
    setColor: (id, color) => { colors[id] = color; },
    atmosphereInputs: () => ({humidity: "60", temperature: "-5", gradient: "-40"}),
    pathInputs: () => rawPath,
    bind(h) { Object.assign(handlers, h); return () => { unbound++; }; },
  };
  const activity = [];
  const log = {
    metric: (...a) => activity.push(["metric", ...a]),
    interaction: (...a) => activity.push(["interaction", ...a]),
  };
  const workspace = createWorkspace({log: quiet});
  workspace.provide("propagationEngine", fakeEngine);
  workspace.provide("propagationView", view);
  workspace.provide("activityLog", activityMissing ? {metric() {}, interaction() {}} : log);
  const plugin = createPropagationPlugin({log: quiet});
  workspace.register(plugin.tool);
  return {workspace, plugin, calls, html, text, colors, handlers, activity, engine: fakeEngine, missing, unbound: () => unbound};
}

// --- pure rendering ---

test("escapeHtml and the condition colours: each known condition has its own, anything else is red", () => {
  assert.equal(escapeHtml(`<i a="b">&'`), "&lt;i a=&quot;b&quot;&gt;&amp;&#39;");
  assert.equal(conditionColor("super-refractive"), "#00aaff");
  assert.equal(conditionColor("standard"), "#00ff88");
  assert.equal(conditionColor("sub-refractive"), "#ffaa00");
  assert.equal(conditionColor("ducting"), "#ff4444");
  assert.equal(conditionColor(undefined), "#ff4444");
});

test("the error card shows the message, escaped", () => {
  assert.match(renderError("boom"), /⚠️ Error: boom/);
  const html = renderError("<img src=x onerror=alert(1)>");
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test("the result card: distance to 2 places, losses to 1, a viable link in green with its margin", () => {
  const html = renderResults(analysisOf());
  assert.match(html, /12\.35 km/);
  assert.match(html, /130\.0 dB/);
  assert.match(html, /110\.1 dB/);
  assert.match(html, /✓ LINK VIABLE/);
  assert.match(html, /8\.5 dB/);
  assert.match(html, /RX Power: -91\.5 dBm/);
  assert.match(html, /Sensitivity: -100 dBm/);
  assert.match(html, /Terrain samples: 64 \|\s*Obstructions: 2 \|\s*Processing: 3\.2 ms/);
  assert.match(html, /rgba\(0, 255, 136, 0\.1\)/);
});

test("a failed link is red; diffraction is amber only when above zero", () => {
  const failed = renderResults(analysisOf({linkBudget: {linkViable: false, linkMargin_db: -3.04, receivedPower_dbm: -103, rxSensitivity_dbm: -100}}));
  assert.match(failed, /✗ LINK FAILED/);
  assert.match(failed, /rgba\(255, 68, 68, 0\.1\)/);
  assert.match(failed, /border: 1px solid #ff4444/);
  assert.match(failed, /-3\.0 dB/);
  const loss = (d) => renderResults(analysisOf({losses: {total_db: 1, freeSpace_db: 1, diffraction_db: d, atmospheric_db: 0}}));
  assert.match(loss(0.1), /#ffaa00;">0\.1 dB/);
  assert.match(loss(0), /#00ff88;">0\.0 dB/);
});

test("server-supplied text in the result card is escaped", () => {
  const html = renderResults(analysisOf({
    linkBudget: {linkViable: true, linkMargin_db: 1, receivedPower_dbm: 1, rxSensitivity_dbm: "<b>"},
    terrain: {sampleCount: "<i>", obstructions: "<u>"},
  }));
  assert.doesNotMatch(html, /<b>|<i>|<u>/);
  assert.match(html, /&lt;b&gt;.*&lt;i&gt;.*&lt;u&gt;/s);
});

test("a result missing a field it shows throws, as the old display did", () => {
  assert.throws(() => renderResults({...analysisOf(), geometry: undefined}), TypeError);
});

test("the history shows the newest ten, newest first; empty shows the placeholder", () => {
  assert.match(renderHistory([]), /No calculations yet/);
  const items = Array.from({length: 12}, (_, i) => ({timestamp: Date.UTC(2026, 0, 1, 0, 0, i), analysis: analysisOf({geometry: {distance_km: i}})}));
  const html = renderHistory(items);
  const rows = html.match(/<tr>/g).length;
  assert.equal(rows, 10);
  assert.ok(html.indexOf("11.0 km") < html.indexOf("2.0 km"));
  assert.doesNotMatch(html, /[^\d.]1\.0 km|[^\d.]0\.0 km/);
  assert.match(html, /metric-time-cell">\d\d:\d\d:\d\d</);
});

test("history rows colour the margin by viability", () => {
  const bad = analysisOf({linkBudget: {linkViable: false, linkMargin_db: -1, receivedPower_dbm: 0, rxSensitivity_dbm: 0}});
  assert.match(renderHistory([{timestamp: 0, analysis: bad}]), /color: #ff4444; font-weight: bold;">-1\.0 dB/);
  assert.match(renderHistory([{timestamp: 0, analysis: analysisOf()}]), /color: #00ff88; font-weight: bold;">8\.5 dB/);
});

test("PINNED: path boxes are read with parseFloat and not validated; an empty box is NaN", () => {
  const path = readPath(RAW);
  assert.deepEqual(path.tx, {lat: 36.1627, lon: -86.7816, height_m: 50, frequency_mhz: 900, power_dbm: 30});
  assert.deepEqual(path.rx, {lat: 36.1527, lon: -86.7916, height_m: 2, sensitivity_dbm: -100});
  const blank = readPath({tx: {...RAW.tx, lat: ""}, rx: {...RAW.rx, sensitivity: "12abc"}});
  assert.ok(Number.isNaN(blank.tx.lat));
  assert.equal(blank.rx.sensitivity_dbm, 12);
});

// --- lifecycle and atmosphere ---

test("activating shows the atmosphere and binds the buttons; deactivating unbinds them", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  assert.equal(t.text["atm-k-factor"], "1.333");
  assert.equal(t.text["atm-refractivity"], "315 N");
  assert.equal(t.text["atm-condition"], "standard");
  assert.equal(t.colors["atm-condition"], "#00ff88");
  assert.deepEqual(Object.keys(t.handlers).sort(), ["apply", "calculate", "clear", "visualize"]);
  assert.deepEqual(await t.workspace.deactivate(PROPAGATION_ID), []);
  assert.equal(t.unbound(), 1);
});

test("without the engine there is no atmosphere display and nothing throws", async () => {
  const t = setup();
  t.engine.present = false;
  await t.workspace.activate(PROPAGATION_ID);
  assert.deepEqual(t.text, {});
  t.plugin.controls.updateAtmosphericDisplay();
  assert.deepEqual(t.text, {});
});

test("an engine that fails to describe the atmosphere is reported, not thrown", async () => {
  const t = setup({engine: {atmosphere() { throw new Error("no conditions"); }}});
  await t.workspace.activate(PROPAGATION_ID);   // must not reject
  assert.equal(t.workspace.state(PROPAGATION_ID), "active");
  assert.deepEqual(t.text, {});
});

test("showPanel's hook refreshes the display while active, and does nothing when inactive", async () => {
  const t = setup();
  t.plugin.controls.updateAtmosphericDisplay();
  assert.deepEqual(t.text, {});   // inactive
  await t.workspace.activate(PROPAGATION_ID);
  t.engine.conditions = {kFactor: 1.5, surfaceRefractivity: 400};
  t.engine.condition = "super-refractive";
  t.plugin.controls.updateAtmosphericDisplay();
  assert.equal(t.text["atm-k-factor"], "1.500");
  assert.equal(t.colors["atm-condition"], "#00aaff");
  await t.workspace.deactivate(PROPAGATION_ID);
  t.engine.condition = "sub-refractive";
  t.plugin.controls.updateAtmosphericDisplay();
  assert.equal(t.text["atm-condition"], "super-refractive");   // unchanged
});

test("applying the atmosphere sends the three numbers, redisplays, and logs a metric", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  t.engine.conditions = {kFactor: 1.1, surfaceRefractivity: 280};
  t.handlers.apply();
  assert.deepEqual(t.calls, [["setAtmosphere", {humidity: 60, temperature: -5, refractivityGradient: -40}]]);
  assert.equal(t.text["atm-k-factor"], "1.100");
  assert.deepEqual(t.activity, [["metric", "propagation", "atmosphere_updated", 1]]);
});

test("applying the atmosphere without the engine warns and sends nothing", async () => {
  const warnings = [];
  const t = setup();
  const plugin = createPropagationPlugin({log: {...quiet, warn: (...a) => warnings.push(a)}});
  const w = createWorkspace({log: quiet});
  w.provide("propagationEngine", {...t.engine, present: false, available: () => false});
  w.provide("propagationView", {setText() {}, setHtml: () => true, setColor() {}, atmosphereInputs: () => ({}), pathInputs: () => RAW, bind(h) { t.handlers.apply = h.apply; return () => {}; }});
  w.provide("activityLog", {metric: (...a) => t.activity.push(a), interaction() {}});
  w.register(plugin.tool);
  await w.activate(PROPAGATION_ID);
  t.handlers.apply();
  assert.equal(warnings.length, 1);
  assert.deepEqual(t.calls, []);
  assert.deepEqual(t.activity, []);
});

// --- calculate ---

test("calculating shows the spinner, then the result and the history, and logs the distance", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  const done = t.handlers.calculate();
  assert.equal(t.html["path-analysis-results"], CALCULATING);
  await done;
  assert.deepEqual(t.calls[0], ["calculate",
    {lat: 36.1627, lon: -86.7816, height_m: 50, frequency_mhz: 900, power_dbm: 30},
    {lat: 36.1527, lon: -86.7916, height_m: 2, sensitivity_dbm: -100}]);
  assert.match(t.html["path-analysis-results"], /12\.35 km/);
  assert.match(t.html["propagation-history-body"], /12\.3 km/);
  assert.deepEqual(t.activity, [["interaction", "calculate_path_loss", "12.3 km"]]);
});

test("an analysis with an error field shows that error and is not kept", async () => {
  const t = setup({engine: {calculate: async () => ({error: "no terrain <data>"})}});
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.calculate();
  assert.match(t.html["path-analysis-results"], /no terrain &lt;data&gt;/);
  assert.equal(t.html["propagation-history-body"], undefined);
  assert.deepEqual(t.activity, []);
});

test("an engine that throws shows its message; the history is untouched", async () => {
  const t = setup({engine: {calculate: async () => { throw new Error("terrain service down"); }}});
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.calculate();
  assert.match(t.html["path-analysis-results"], /Error: terrain service down/);
  assert.equal(t.html["propagation-history-body"], undefined);
});

test("PINNED: a result that cannot be displayed is already in the history, and the panel shows the error", async () => {
  const broken = {...analysisOf(), terrain: undefined};
  const t = setup({engine: {calculate: async () => broken}});
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.calculate();
  assert.match(t.html["path-analysis-results"], /Error: /);
  assert.equal(t.html["propagation-history-body"], undefined);   // the table is not redrawn...
  t.engine.calculate = async () => analysisOf();
  await t.handlers.calculate();
  assert.equal(t.html["propagation-history-body"].match(/<tr>/g).length, 2);   // ...but the broken one is in it
});

test("without the engine, calculating and visualizing say so and call nothing", async () => {
  const t = setup();
  t.engine.present = false;
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.calculate();
  assert.match(t.html["path-analysis-results"], /RFPropagationManager not initialized/);
  t.html["path-analysis-results"] = "";
  await t.handlers.visualize();
  assert.match(t.html["path-analysis-results"], /RFPropagationManager not initialized/);
  assert.deepEqual(t.calls, []);
});

test("PINNED: NaN from empty boxes reaches the engine", async () => {
  const blank = {tx: {...RAW.tx, lat: ""}, rx: RAW.rx};
  const t = setup({rawPath: blank});
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.calculate();
  assert.ok(Number.isNaN(t.calls[0][1].lat));
});

test("a calculation that lands after deactivation changes nothing", async () => {
  const hold = gate();
  const t = setup({engine: {calculate: async () => { await hold.promise; return analysisOf(); }}});
  await t.workspace.activate(PROPAGATION_ID);
  const pending = t.handlers.calculate();
  await t.workspace.deactivate(PROPAGATION_ID);
  const before = {...t.html};
  hold.open();
  await pending;
  assert.deepEqual(t.html, before);
  assert.deepEqual(t.activity, []);
});

test("an engine failure that lands after deactivation is not shown", async () => {
  const hold = gate();
  const t = setup({engine: {calculate: async () => { await hold.promise; throw new Error("late"); }}});
  await t.workspace.activate(PROPAGATION_ID);
  const pending = t.handlers.calculate();
  await t.workspace.deactivate(PROPAGATION_ID);
  const before = {...t.html};
  hold.open();
  await pending;
  assert.deepEqual(t.html, before);
});

test("a late analysis error is not shown either", async () => {
  const hold = gate();
  const t = setup({engine: {calculate: async () => { await hold.promise; return {error: "late"}; }}});
  await t.workspace.activate(PROPAGATION_ID);
  const pending = t.handlers.calculate();
  await t.workspace.deactivate(PROPAGATION_ID);
  const before = {...t.html};
  hold.open();
  await pending;
  assert.deepEqual(t.html, before);
});

// --- visualize ---

test("visualizing numbers the paths, shows and records the result, and logs the id", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.visualize();
  await t.handlers.visualize();
  assert.deepEqual(t.calls.map((c) => c[1]), ["path_1", "path_2"]);
  assert.match(t.html["path-analysis-results"], /12\.35 km/);
  assert.equal(t.html["propagation-history-body"].match(/<tr>/g).length, 2);
  assert.deepEqual(t.activity, [["interaction", "visualize_path", "path_1"], ["interaction", "visualize_path", "path_2"]]);
});

test("PINNED: an empty visualization shows nothing but still uses up a path id", async () => {
  let n = 0;
  const t = setup({engine: {visualize: async (id) => { t.calls.push(["visualize", id]); return n++ === 0 ? null : analysisOf(); }}});
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.visualize();
  assert.equal(t.html["path-analysis-results"], undefined);
  assert.deepEqual(t.activity, []);
  await t.handlers.visualize();
  assert.deepEqual(t.calls.map((c) => c[1]), ["path_1", "path_2"]);
});

test("a visualization that throws shows its message", async () => {
  const t = setup({engine: {visualize: async () => { throw new Error("no viewer"); }}});
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.visualize();
  assert.match(t.html["path-analysis-results"], /Error: no viewer/);
});

test("a visualization that lands after deactivation is taken back down and changes nothing on the page", async () => {
  const hold = gate();
  const t = setup({engine: {visualize: async (id) => { await hold.promise; return analysisOf(); }}});
  await t.workspace.activate(PROPAGATION_ID);
  const pending = t.handlers.visualize();
  await t.workspace.deactivate(PROPAGATION_ID);
  hold.open();
  await pending;
  assert.deepEqual(t.calls, [["removePath", "path_1"]]);
  assert.deepEqual(t.html, {});
  assert.deepEqual(t.activity, []);
});

test("a late empty visualization removes nothing", async () => {
  const hold = gate();
  const t = setup({engine: {visualize: async () => { await hold.promise; return null; }}});
  await t.workspace.activate(PROPAGATION_ID);
  const pending = t.handlers.visualize();
  await t.workspace.deactivate(PROPAGATION_ID);
  hold.open();
  await pending;
  assert.deepEqual(t.calls, []);
});

test("a visualization that throws after deactivation is not shown", async () => {
  const hold = gate();
  const t = setup({engine: {visualize: async () => { await hold.promise; throw new Error("late"); }}});
  await t.workspace.activate(PROPAGATION_ID);
  const pending = t.handlers.visualize();
  await t.workspace.deactivate(PROPAGATION_ID);
  hold.open();
  await pending;
  assert.deepEqual(t.html, {});
});

test("deactivating removes the paths this activation drew, and ids stay unique across activations", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.visualize();
  await t.handlers.visualize();
  await t.workspace.deactivate(PROPAGATION_ID);
  assert.deepEqual(t.calls.filter((c) => c[0] === "removePath"), [["removePath", "path_1"], ["removePath", "path_2"]]);
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.visualize();
  assert.equal(t.calls.filter((c) => c[0] === "visualize").pop()[1], "path_3");
});

// --- clear ---

test("clearing asks the engine, empties the history, restores the prompt and logs", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.calculate();
  t.calls.length = 0;
  t.activity.length = 0;
  t.handlers.clear();
  assert.deepEqual(t.calls, [["clearAll"]]);
  assert.match(t.html["propagation-history-body"], /No calculations yet/);
  assert.equal(t.html["path-analysis-results"], NO_RESULT);
  assert.deepEqual(t.activity, [["interaction", "clear_paths", "all"]]);
});

test("clearing forgets the drawn paths, so deactivating removes none of them again", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.visualize();
  t.handlers.clear();
  t.calls.length = 0;
  await t.workspace.deactivate(PROPAGATION_ID);
  assert.deepEqual(t.calls, []);
});

test("clearing without the engine still clears the panel", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.calculate();
  t.engine.present = false;
  t.calls.length = 0;
  t.handlers.clear();
  assert.deepEqual(t.calls, []);
  assert.match(t.html["propagation-history-body"], /No calculations yet/);
});

test("PINNED: the history keeps everything while the table shows ten", async () => {
  const t = setup();
  await t.workspace.activate(PROPAGATION_ID);
  for (let i = 0; i < 12; i++) await t.handlers.calculate();
  assert.equal(t.html["propagation-history-body"].match(/<tr>/g).length, 10);
});

test("a panel without the results element is skipped, not thrown on", async () => {
  const t = setup();
  t.missing.add("path-analysis-results");
  await t.workspace.activate(PROPAGATION_ID);
  await t.handlers.calculate();
  t.handlers.clear();
  assert.equal(t.html["path-analysis-results"], undefined);
  assert.match(t.html["propagation-history-body"], /No calculations yet/);
});
