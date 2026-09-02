import test from "node:test";
import assert from "node:assert/strict";

import {
  CLASSIFIER_UNDECLARED, classificationOutcomeLines, deriveClassifierState,
  deriveOutcomeBreakdown, reasonLabel,
} from "./rfClassificationOutcomes.js";
import { deriveClassificationSummary } from "./nesdrSpectrumModel.js";

const CLASSIFIER = {
  schema: "scythe.rf-signal-family.v1",
  contract_phase: "0",
  state: "NOT_IMPLEMENTED",
  state_note: "PHASE 0 SHIPS THE EVIDENCE CONTRACT ONLY.",
  claimable_families: ["DIGITAL"],
  reserved_families: ["ANALOGUE"],
  reason_codes: {NO_SYMBOL_CLOCK: "THE DETECTOR RAN AND FOUND NO SIGNIFICANT CYCLIC FEATURE"},
  digital_evidence_required: ["symbol_rate_hz", "detection_statistic"],
  analogue_detector: "NOT_IMPLEMENTED",
  analogue_detector_note: "ANALOGUE REQUIRES A POSITIVE DETECTOR.",
  registered_methods: [{method_id: "squared-envelope-cyclic.v1",
                        validation_status: "REGISTERED_NOT_VALIDATED"}],
  validated_methods: [],
  digital_reachable: false,
  digital_reachable_note: "A DIGITAL VERDICT REQUIRES A REGISTERED METHOD THAT HAS "
    + "PASSED PHASE 3 VALIDATION. NONE HAS.",
  claims_withheld: ["analogue_family", "constant_envelope_digital"],
  raw_iq_exposed: false,
};

const status = (observations = {}) => ({
  observations: {
    classification_scope: "bounded_retained_detection_events",
    signal_classifications: {digital: 0, analogue: 0, unclassified: 0, total: 0},
    classifier: CLASSIFIER,
    ...observations,
  },
});

test("an undeclared classifier never reads as a working one", () => {
  const state = deriveClassifierState({});
  assert.equal(state.declared, false);
  assert.equal(state.implemented, false);
  assert.equal(state.state, CLASSIFIER_UNDECLARED.state);
  assert.match(state.note, /MISSING FIELD, NOT A CLASSIFIER THAT RAN/);
  assert.equal(state.analogueDetector, "UNDECLARED");
});

test("the declared classifier state carries its own explanation", () => {
  const state = deriveClassifierState(status());
  assert.equal(state.declared, true);
  assert.equal(state.implemented, false);
  assert.equal(state.state, "NOT_IMPLEMENTED");
  assert.equal(state.phase, "0");
  assert.deepEqual(state.reservedFamilies, ["ANALOGUE"]);
  assert.ok(!state.claimableFamilies.includes("ANALOGUE"));
  assert.equal(state.analogueDetector, "NOT_IMPLEMENTED");
});

test("the reason breakdown ranks by count and keeps zero rows out", () => {
  const rows = deriveOutcomeBreakdown(status({
    classification_reasons: {NOT_ATTEMPTED: 12, CONSTANT_ENVELOPE: 3, NO_SYMBOL_CLOCK: 0},
  }));
  assert.deepEqual(rows.map((row) => row.code), ["NOT_ATTEMPTED", "CONSTANT_ENVELOPE"]);
  assert.equal(rows[0].count, 12);
  assert.equal(rows[1].label, "CONSTANT ENVELOPE · BLIND SPOT");
  assert.deepEqual(deriveOutcomeBreakdown({}), []);
});

test("an unlabelled reason code falls back to the server's own text", () => {
  assert.equal(reasonLabel("NO_SYMBOL_CLOCK"), "NO SYMBOL CLOCK FOUND");
  assert.equal(reasonLabel("SOMETHING_NEW", "A NEWER SERVER SAID THIS"), "A NEWER SERVER SAID THIS");
  assert.equal(reasonLabel("SOMETHING_NEW"), "SOMETHING_NEW");
});

test("a zero detection count is rendered with the reason it is zero", () => {
  const payload = status({classification_reasons: {NOT_ATTEMPTED: 9}});
  const lines = classificationOutcomeLines(
    deriveClassificationSummary(payload), deriveClassifierState(payload),
    deriveOutcomeBreakdown(payload));
  assert.match(lines[0], /DIGITAL 0 · ANALOGUE 0 · UNCLASSIFIED 0 · RETAINED EVENTS 0/);
  assert.match(lines.join("\n"), /CLASSIFIER STATE \/\/ NOT_IMPLEMENTED/);
  assert.match(lines.join("\n"), /UNCLASSIFIED BECAUSE \/\/ NO CLASSIFIER RAN 9/);
  assert.match(lines.at(-1), /A FAMILY INFERENCE DOES NOT REPLACE AN ESTIMATOR OUTCOME/);
});

test("the missing analogue detector is stated every time analogue is counted", () => {
  const payload = status();
  const lines = classificationOutcomeLines(
    deriveClassificationSummary(payload), deriveClassifierState(payload), []);
  const analogue = lines.find((line) => line.startsWith("ANALOGUE DETECTOR //"));
  assert.ok(analogue, "an ANALOGUE count of 0 must not stand without its detector state");
  assert.match(analogue, /NOT_IMPLEMENTED/);
  assert.match(analogue, /ANALOGUE REQUIRES A POSITIVE DETECTOR/);
});

test("a closed route to DIGITAL is stated, not left to look like a quiet band", () => {
  const payload = status();
  const state = deriveClassifierState(payload);
  assert.equal(state.digitalReachable, false);
  assert.deepEqual(state.validatedMethods, []);
  const lines = classificationOutcomeLines(
    deriveClassificationSummary(payload), state, []);
  const digital = lines.find((line) => line.startsWith("DIGITAL VERDICT //"));
  assert.ok(digital, "a DIGITAL count of 0 must say whether DIGITAL was reachable");
  assert.match(digital, /UNREACHABLE/);
  assert.match(digital, /PHASE 3 VALIDATION/);
});

test("a validated detector removes the unreachable notice", () => {
  const payload = status({classifier: {...CLASSIFIER, state: "IMPLEMENTED",
    validated_methods: ["squared-envelope-cyclic.v1"], digital_reachable: true,
    digital_reachable_note: null}});
  const state = deriveClassifierState(payload);
  assert.equal(state.digitalReachable, true);
  assert.equal(state.implemented, true);
  const lines = classificationOutcomeLines(deriveClassificationSummary(payload), state, []);
  assert.ok(!lines.some((line) => line.startsWith("DIGITAL VERDICT //")));
  assert.ok(!lines.some((line) => line.startsWith("CLASSIFIER STATE //")));
});

test("refusal reason codes read as refusals, not as measurements", () => {
  const rows = deriveOutcomeBreakdown(status({classification_reasons: {
    METHOD_NOT_VALIDATED: 5, DECISION_RULE_NOT_MET: 2, METHOD_NOT_REGISTERED: 1}}));
  assert.deepEqual(rows.map((row) => row.label), [
    "CLAIM REFUSED · METHOD NOT VALIDATED",
    "CLAIM REFUSED · DID NOT PASS ITS DECISION RULE",
    "CLAIM REFUSED · METHOD NOT REGISTERED"]);
  // The positive outcome states support, never proof.
  assert.match(reasonLabel("SYMBOL_CLOCK_LIKE_FEATURE"), /DIGITAL STRUCTURE SUPPORTED/);
});

test("unavailable counts are reported as unavailable, not as zero", () => {
  const lines = classificationOutcomeLines(deriveClassificationSummary({}),
    deriveClassifierState({}), []);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /NO RETAINED DETECTION COUNTS PUBLISHED/);
  assert.ok(!lines[0].includes(" 0 "), "an absent count must never render as a zero");
});
