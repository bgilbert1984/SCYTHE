import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTHORITY, BUNDLE_ANTENNAS, CORROBORATION, NO_AUTODETECT, antennaHealthRow,
  corroborateAntenna, declareAntenna,
} from "./rfAntennaDeclaration.js";

const LIVE = {available: true, noiseFloorDbfs: -92, peakDbfs: -41};
const QUIET = {available: true, noiseFloorDbfs: -92, peakDbfs: -88};

test("auto-detection is refused as a hardware fact, with the reasons stated", () => {
  assert.equal(NO_AUTODETECT.possible, false);
  const reasons = NO_AUTODETECT.reasons.join(" ");
  assert.match(reasons, /NO BIAS TEE/);
  assert.match(reasons, /DIRECTIONAL COUPLER OR VSWR BRIDGE/);
  assert.match(reasons, /NO IDENTITY PIN|CARRIES NO/);
  assert.match(NO_AUTODETECT.instead, /DECLARE THE ANTENNA/);
});

test("the bundle catalogue keeps undeclared resonances undeclared", () => {
  const uhf = BUNDLE_ANTENNAS.find((entry) => entry.id === "nesdr-smart-uhf");
  assert.equal(uhf.resonanceHz, null, "the vendor names the band but not the frequency");
  assert.equal(uhf.resonanceAuthority, AUTHORITY.UNDECLARED);
  const telescopic = BUNDLE_ANTENNAS.find((entry) => entry.id === "nesdr-smart-telescopic");
  assert.equal(telescopic.resonanceHz, null);
  const ism = BUNDLE_ANTENNAS.find((entry) => entry.id === "nesdr-smart-433-ism");
  assert.equal(ism.resonanceHz, 433e6);
  assert.equal(ism.resonanceAuthority, AUTHORITY.VENDOR, "a label is not a measurement");
});

test("a declaration is operator authority and never claims detection", () => {
  const {valid, declaration} = declareAntenna({antennaId: "nesdr-smart-433-ism"});
  assert.equal(valid, true);
  assert.equal(declaration.authority, AUTHORITY.OPERATOR);
  assert.equal(declaration.autoDetected, false);
  assert.match(declaration.detectionNote, /NOT PHYSICALLY AVAILABLE/);
});

test("an unselected or unknown antenna refuses rather than defaulting", () => {
  assert.equal(declareAntenna({}).valid, false);
  assert.equal(declareAntenna({antennaId: "nesdr-smart-uhf", feedlineId: "fibre"}).valid, false);

  // A default is configuration convenience, not physical evidence: nothing in a
  // receive-only path can tell a direct connection from 2 m of RG58.
  const undeclaredFeedline = declareAntenna({antennaId: "nesdr-smart-uhf"});
  assert.equal(undeclaredFeedline.valid, true);
  assert.equal(undeclaredFeedline.declaration.feedlineId, "undeclared");
  assert.equal(undeclaredFeedline.declaration.feedlineLabel, "FEEDLINE UNDECLARED");
  assert.equal(undeclaredFeedline.declaration.feedlineLengthM, null);
  assert.match(declareAntenna({}).reason, /DECLARATION REFUSED/);
});

test("a telescopic extension derives a quarter wave and labels it as derived", () => {
  const {declaration} = declareAntenna({antennaId: "nesdr-smart-telescopic", extensionMm: 750});
  assert.equal(declaration.quarterWaveHz, Math.round(299_792_458 / 3));
  assert.equal(declaration.quarterWaveAuthority, "DERIVED_INFERENCE");
  assert.match(declaration.quarterWaveNote, /IGNORES GROUND PLANE/);
});

test("a fixed mast derives no resonance from an extension it cannot have", () => {
  const {declaration} = declareAntenna({antennaId: "nesdr-smart-uhf", extensionMm: 750});
  assert.equal(declaration.quarterWaveHz, null);
  assert.equal(declaration.quarterWaveAuthority, AUTHORITY.UNDECLARED);
});

test("corroboration never identifies an antenna, only a responsive port", () => {
  const {declaration} = declareAntenna({antennaId: "nesdr-smart-telescopic"});
  const result = corroborateAntenna(declaration, {frame: LIVE, centerHz: 100e6});
  assert.equal(result.outcome, CORROBORATION.RESPONSIVE);
  assert.equal(result.identifiesAntenna, false);
  assert.match(result.agreement, /CONSISTENT WITH A CONNECTED ANTENNA/);
  assert.match(result.reason, /DOES NOT IDENTIFY WHICH ANTENNA/);
  assert.match(result.discrimination, /NO REFLECTOMETER/);
});

test("a quiet port accuses nothing, because a quiet band looks the same", () => {
  const {declaration} = declareAntenna({antennaId: "nesdr-smart-telescopic"});
  const result = corroborateAntenna(declaration, {frame: QUIET});
  assert.equal(result.outcome, CORROBORATION.QUIET);
  assert.equal(result.agreement, null, "absence of signal is not evidence of disconnection");
  assert.match(result.reason, /COMPATIBLE WITH A DISCONNECTED ANTENNA AND EQUALLY WITH A QUIET BAND/);
});

test("a terminated declaration is the one claim observation can contradict", () => {
  const {declaration} = declareAntenna({antennaId: "no-antenna"});
  const hot = corroborateAntenna(declaration, {frame: LIVE});
  assert.match(hot.agreement, /CONTRADICTS DECLARATION/);
  const cold = corroborateAntenna(declaration, {frame: QUIET});
  assert.match(cold.agreement, /CONSISTENT WITH DECLARATION/);
});

test("with no frame or no declaration corroboration withholds a verdict", () => {
  const {declaration} = declareAntenna({antennaId: "nesdr-smart-uhf"});
  assert.equal(corroborateAntenna(declaration, {frame: {available: false}}).outcome,
               CORROBORATION.INSUFFICIENT);
  assert.equal(corroborateAntenna(null, {frame: LIVE}).outcome, CORROBORATION.INSUFFICIENT);
  const partial = corroborateAntenna(declaration, {frame: {available: true, noiseFloorDbfs: null}});
  assert.equal(partial.outcome, CORROBORATION.INSUFFICIENT);
});

test("the health row states the omission is not a failed inspection", () => {
  const undeclared = antennaHealthRow(null);
  assert.equal(undeclared.value, "UNDECLARED");
  assert.match(undeclared.detail, /NOT AUTO-DETECTABLE/);
  assert.ok(!/UNKNOWN ANTENNA/.test(JSON.stringify(undeclared)));
  const {declaration} = declareAntenna({
    antennaId: "nesdr-smart-433-ism", feedlineId: "nesdr-magnetic-base-rg58-2m"});
  const row = antennaHealthRow(declaration);
  assert.match(row.value, /433 MHz ISM MAST · MAGNETIC BASE/);
  assert.match(row.detail, /NOT MEASURED BY THE RECEIVER/);
});
