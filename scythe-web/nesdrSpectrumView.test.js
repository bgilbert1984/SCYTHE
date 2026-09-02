import assert from "node:assert/strict";
import test from "node:test";

import { NesdrSpectrumView } from "./nesdrSpectrumView.js";

/** Minimal DOM good enough for the view's element and listener use. */
function stubDocument() {
  const make = (tag) => {
    const node = {
      tagName: tag, className: "", children: [], listeners: new Map(),
      value: "", type: "", title: "", width: 0, height: 0, hidden: false,
      attributes: new Map(),
      classList: {add() {}},
      ownerDocument: null,
      set textContent(text) { this._text = String(text); },
      get textContent() { return this._text ?? ""; },
      append(...items) { node.children.push(...items); },
      replaceChildren(...items) { node.children = [...items]; },
      addEventListener(name, handler) { node.listeners.set(name, handler); },
      setAttribute(name, value) { node.attributes.set(name, value); },
      getContext() { return null; },
      getBoundingClientRect() { return {left: 0, width: 512}; },
    };
    return node;
  };
  const doc = {createElement: (tag) => { const n = make(tag); n.ownerDocument = doc; return n; }};
  return doc;
}

function findAll(node, predicate, found = []) {
  for (const child of node.children ?? []) {
    if (predicate(child)) found.push(child);
    findAll(child, predicate, found);
  }
  return found;
}

const buttonNamed = (root, text) =>
  findAll(root, (node) => node.tagName === "button" && node.textContent === text)[0];

function makeView({tuneResponse, onTuneRequest = () => {},
                  antennaResponse, onAntennaRequest = () => {}} = {}) {
  const doc = stubDocument();
  const root = doc.createElement("div");
  const fetchImpl = async (url, options = {}) => {
    if (String(url).includes("rf-tune/propose")) {
      onTuneRequest({url, options, body: JSON.parse(options.body)});
      return {ok: tuneResponse?.ok ?? true, status: tuneResponse?.status ?? 201,
              json: async () => tuneResponse?.payload ?? {}};
    }
    if (String(url).includes("rf-antenna/declare")) {
      onAntennaRequest({url, options, body: JSON.parse(options.body)});
      return {ok: antennaResponse?.ok ?? true, status: antennaResponse?.status ?? 201,
              json: async () => antennaResponse?.payload ?? {}};
    }
    return {ok: true, status: 200, json: async () => ({})};
  };
  return {root, view: new NesdrSpectrumView({root, fetchImpl, now: () => 1000})};
}

const DECLARED = (overrides = {}) => ({
  ok: true, status: 201,
  payload: {status: "declared", declared: true, autoDetected: false,
            antenna: {antenna_id: "nesdr-smart-433-ism", feedline_id: "direct",
                      extension_mm: null, note: "", declared_at: 1000, ...overrides},
            receipt: {declarationHash: "d".repeat(64), retroactive: false,
                      signalChainChanged: false}},
});

const PROPOSED = {
  ok: true, status: 201,
  payload: {status: "proposed", proposed: true, executed: false, rigctlContacted: false,
            params: {frequency_hz: 101_700_000, mode: "WFM"}, regime: "TUNER",
            receipt: {proposalId: "p-1", status: "proposed", executed: false,
                      requestHash: "a".repeat(64)},
            boundary: ["TUNING IS PROPOSED, NOT EXECUTED"]},
};

test("the panel offers no gain control and says why", () => {
  const {root, view} = makeView();
  const notes = findAll(root, (node) => /GAIN \/\/ UNDECLARED/.test(node.textContent ?? ""));
  assert.equal(notes.length, 1);
  assert.match(notes[0].textContent, /NO CONTROL OFFERED/);
  const tuningInputs = findAll(view.tuningPanel, (node) => node.tagName === "input");
  assert.equal(tuningInputs.length, 1, "only the centre-frequency field may exist");
  assert.equal(tuningInputs[0].attributes.get("aria-label"), "Centre frequency in MHz");
  // No control anywhere may offer a gain the bridge never reported as supported.
  const labels = findAll(root, (node) => node.tagName === "input" || node.tagName === "select")
    .map((node) => node.attributes.get("aria-label") ?? "");
  assert.ok(!labels.some((label) => /gain/i.test(label)), "no gain control may exist");
});

test("a zero detection count is never shown without the reason it is zero", () => {
  const {view} = makeView();
  view.state.status = {observations: {
    signal_classifications: {digital: 0, analogue: 0, unclassified: 4, total: 4},
    classification_reasons: {NOT_ATTEMPTED: 4},
    classifier: {state: "NOT_IMPLEMENTED", contract_phase: "0",
                 state_note: "PHASE 0 SHIPS THE EVIDENCE CONTRACT ONLY.",
                 analogue_detector: "NOT_IMPLEMENTED",
                 analogue_detector_note: "ANALOGUE REQUIRES A POSITIVE DETECTOR.",
                 reason_codes: {}, claims_withheld: ["analogue_family"]},
  }};
  view.render();
  const text = view.classificationLine.textContent;
  assert.match(text, /DIGITAL 0 · ANALOGUE 0 · UNCLASSIFIED 4/);
  assert.match(text, /CLASSIFIER STATE \/\/ NOT_IMPLEMENTED/);
  assert.match(text, /ANALOGUE DETECTOR \/\/ NOT_IMPLEMENTED/);
  assert.match(text, /UNCLASSIFIED BECAUSE \/\/ NO CLASSIFIER RAN 4/);
  assert.equal(view.classificationLine.attributes.get("data-classifier-state"), "NOT_IMPLEMENTED");
});

test("an undeclared classifier is stated, never rendered as a working one", () => {
  const {view} = makeView();
  view.state.status = {observations: {
    signal_classifications: {digital: 0, analogue: 0, unclassified: 0, total: 0}}};
  view.render();
  const text = view.classificationLine.textContent;
  assert.match(text, /CLASSIFIER STATE \/\/ UNDECLARED/);
  assert.match(text, /MISSING FIELD, NOT A CLASSIFIER THAT RAN/);
  assert.equal(view.classificationLine.attributes.get("data-classifier-state"), "UNDECLARED");
});

test("a tune click posts a proposal and never an execution", async () => {
  const seen = [];
  const {root, view} = makeView({tuneResponse: PROPOSED, onTuneRequest: (call) => seen.push(call)});
  view.frequencyInput.value = "101.700000";
  await buttonNamed(root, "PROPOSE TUNE").listeners.get("click")();
  assert.equal(seen.length, 1);
  assert.equal(seen[0].options.method, "POST");
  assert.match(seen[0].url, /rf-tune\/propose$/);
  assert.equal(seen[0].body.frequency_hz, 101_700_000);
  assert.ok(!/execute/i.test(seen[0].url), "the view must not reach an execute path");
});

test("the receipt states plainly that nothing was executed or tuned", async () => {
  const {root, view} = makeView({tuneResponse: PROPOSED});
  view.frequencyInput.value = "101.7";
  await buttonNamed(root, "PROPOSE TUNE").listeners.get("click")();
  const text = view.receiptLine.textContent;
  assert.match(text, /EXECUTED \/\/ NO/);
  assert.match(text, /RIGCTL CONTACTED \/\/ NO/);
  assert.match(text, /PROPOSAL \/\/ p-1/);
});

test("a refusal is reported and never rendered as a successful tune", async () => {
  const {root, view} = makeView({tuneResponse: {
    ok: false, status: 400, payload: {status: "refused", error: "outside declared coverage"}}});
  view.frequencyInput.value = "2400";
  await buttonNamed(root, "PROPOSE TUNE").listeners.get("click")();
  assert.match(view.receiptLine.textContent, /REFUSED \/\/ outside declared coverage/);
  assert.match(view.receiptLine.textContent, /EXECUTED \/\/ NO/);
});

test("a non-numeric frequency proposes nothing at all", async () => {
  const seen = [];
  const {root, view} = makeView({tuneResponse: PROPOSED, onTuneRequest: (call) => seen.push(call)});
  view.frequencyInput.value = "";
  await buttonNamed(root, "PROPOSE TUNE").listeners.get("click")();
  assert.equal(seen.length, 0, "an unparseable field must not reach the safety gate");
  assert.match(view.receiptLine.textContent, /NOTHING WAS PROPOSED/);
});

test("recentre with no cursor or peak proposes nothing", async () => {
  const seen = [];
  const {root, view} = makeView({tuneResponse: PROPOSED, onTuneRequest: (call) => seen.push(call)});
  await buttonNamed(root, "RECENTRE ON SELECTED PEAK").listeners.get("click")();
  assert.equal(seen.length, 0);
  assert.match(view.receiptLine.textContent, /NO CURSOR OR PEAK SELECTED/);
});

test("a step button changes the step without proposing a tune", async () => {
  const seen = [];
  const {root, view} = makeView({tuneResponse: PROPOSED, onTuneRequest: (call) => seen.push(call)});
  buttonNamed(root, "12.5 kHz").listeners.get("click")();
  assert.equal(view.stepHz, 12_500);
  assert.equal(seen.length, 0, "choosing a step size must not tune");
  assert.match(view.stepLine.textContent, /NONE IS EXECUTED HERE/);
});

test("a wide preset plans a stitched survey rather than one wideband claim", async () => {
  const seen = [];
  const {root, view} = makeView({tuneResponse: PROPOSED, onTuneRequest: (call) => seen.push(call)});
  view.state.status = {bridge: {config: {sample_rate_hz: 2_048_000}}};
  await buttonNamed(root, "FM BROADCAST").listeners.get("click")();
  assert.ok(view.survey.valid);
  assert.ok(view.survey.tiles.length >= 9, "20 MHz needs multiple visits");
  assert.equal(seen.length, 1, "only the first centre is proposed");
  assert.match(view.surveySummary.textContent, /NEVER OBSERVED/);
  assert.match(view.surveySummary.textContent, /NOT SIMULTANEOUSLY/);
});

test("presets are labelled as receive targets, not authorized bands", () => {
  const {root} = makeView();
  const labels = findAll(root, (node) => /RECEIVE PRESETS/.test(node.textContent ?? ""));
  assert.ok(labels.length >= 1);
  const boundary = findAll(root,
    (node) => /TUNABILITY IS NOT TRANSMISSION AUTHORIZATION/.test(node.textContent ?? ""));
  assert.equal(boundary.length, 1);
});

test("with no survey planned the coverage ribbon claims nothing", () => {
  const {view} = makeView();
  assert.match(view.surveySummary.textContent, /SURVEY \/\/ NONE PLANNED/);
  assert.equal(view.coverageRibbonEl.children.length, 0);
});

test("the antenna panel states that detection is a hardware impossibility", () => {
  const {root} = makeView();
  const headline = findAll(root,
    (node) => /ANTENNA AUTO-DETECTION \/\/ NOT PHYSICALLY AVAILABLE/.test(node.textContent ?? ""));
  assert.equal(headline.length, 1);
  const reasons = findAll(root, (node) => node.tagName === "li")
    .map((node) => node.textContent).join(" ");
  assert.match(reasons, /NO BIAS TEE/);
  assert.match(reasons, /DIRECTIONAL COUPLER OR VSWR BRIDGE/);
});

test("declaring an antenna posts a declaration and never asks for a detection", async () => {
  const seen = [];
  const {root, view} = makeView({antennaResponse: DECLARED(), onAntennaRequest: (c) => seen.push(c)});
  view.antennaSelect.value = "nesdr-smart-433-ism";
  await buttonNamed(root, "DECLARE ANTENNA").listeners.get("click")();
  assert.equal(seen.length, 1);
  assert.equal(seen[0].options.method, "POST");
  assert.match(seen[0].url, /rf-antenna\/declare$/);
  assert.equal(seen[0].body.antenna_id, "nesdr-smart-433-ism");
  assert.ok(!/detect/i.test(seen[0].url), "there is no detection endpoint to call");
  assert.equal(view.antenna.autoDetected, false);
  assert.equal(view.antenna.authority, "OPERATOR_DECLARED");
});

test("declaring nothing declares nothing", async () => {
  const seen = [];
  const {root, view} = makeView({antennaResponse: DECLARED(), onAntennaRequest: (c) => seen.push(c)});
  view.antennaSelect.value = "";
  await buttonNamed(root, "DECLARE ANTENNA").listeners.get("click")();
  assert.equal(seen.length, 0);
  assert.equal(view.antenna, null);
  assert.match(view.antennaStateLine.textContent, /NOTHING WAS DECLARED/);
});

test("a fixed mast never sends an extension the contract would refuse", async () => {
  const seen = [];
  const {root, view} = makeView({antennaResponse: DECLARED(), onAntennaRequest: (c) => seen.push(c)});
  view.antennaSelect.value = "nesdr-smart-uhf";
  view.extensionInput.value = "750";
  await buttonNamed(root, "DECLARE ANTENNA").listeners.get("click")();
  assert.equal(seen[0].body.extension_mm, undefined);
  view.antennaSelect.value = "nesdr-smart-telescopic";
  await buttonNamed(root, "DECLARE ANTENNA").listeners.get("click")();
  assert.equal(seen[1].body.extension_mm, 750);
});

test("corroboration reports a responsive port without identifying the antenna", async () => {
  const {root, view} = makeView({antennaResponse: DECLARED()});
  view.antennaSelect.value = "nesdr-smart-433-ism";
  await buttonNamed(root, "DECLARE ANTENNA").listeners.get("click")();
  assert.match(view.corroborationLine.textContent, /PORT CORROBORATION \/\/ INSUFFICIENT/);
  view.state.spectrum = {available: true, spectrum: {
    bins_dbfs: [-92, -40, -92, -91], noise_floor_dbfs: -92, peak_dbfs: -40,
    min_frequency_hz: 99e6, max_frequency_hz: 101e6, center_frequency_hz: 100e6,
    peak_frequency_hz: 100.1e6, sequence: 5, timestamp: 1000}};
  view.render();
  assert.match(view.corroborationLine.textContent, /PORT RESPONSIVE/);
  assert.match(view.corroborationLine.textContent, /DOES NOT IDENTIFY WHICH ANTENNA/);
  assert.match(view.corroborationLine.textContent, /NO REFLECTOMETER/);
});
