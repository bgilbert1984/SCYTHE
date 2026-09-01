import assert from "node:assert/strict";
import test from "node:test";

import {
  COVERAGE, coverageRibbon, planSurvey, retainTileProduct, surveyProgress,
} from "./rfSurveyController.js";
import { PRESET_BOUNDARY, RECEIVE_PRESETS, REGION, presetCentres } from "./rfBandPresets.us.js";

const FM = {startHz: 88e6, endHz: 108e6, sampleRateHz: 2_048_000};

test("a wide span becomes several visits, never one capture", () => {
  const plan = planSurvey(FM);
  assert.equal(plan.valid, true);
  assert.ok(plan.tiles.length >= 9, "20 MHz at 2.048 MS/s needs ten visits");
  assert.match(plan.boundary, /NOT SIMULTANEOUSLY/);
  for (const tile of plan.tiles) assert.equal(tile.state, COVERAGE.NEVER);
});

test("an inverted or unobservable span refuses to plan", () => {
  assert.equal(planSurvey({startHz: 108e6, endHz: 88e6, sampleRateHz: 2e6}).valid, false);
  const noSpan = planSurvey({startHz: 88e6, endHz: 108e6, sampleRateHz: 0});
  assert.equal(noSpan.valid, false);
  assert.match(noSpan.reason, /NO OBSERVABLE SPAN/);
});

test("only the span being observed now may claim to be live", () => {
  const plan = planSurvey(FM);
  const ribbon = coverageRibbon(plan, {liveCenterHz: plan.tiles[0].centerHz});
  assert.equal(ribbon[0].state, COVERAGE.LIVE);
  assert.ok(ribbon.slice(1).every((tile) => tile.state !== COVERAGE.LIVE),
    "a single receiver cannot observe two spans at once");
});

test("an unvisited tile is NEVER OBSERVED, not merely stale", () => {
  const ribbon = coverageRibbon(planSurvey(FM), {liveCenterHz: null});
  assert.ok(ribbon.every((tile) => tile.state === COVERAGE.NEVER));
  assert.ok(ribbon.every((tile) => tile.ageSeconds === null));
});

test("tiles age out of RECENT into STALE rather than staying current", () => {
  const plan = planSurvey(FM);
  plan.tiles[0].observedAt = 1000;
  plan.tiles[1].observedAt = 500;
  const ribbon = coverageRibbon(plan, {now: 1060, freshnessSeconds: 120, liveCenterHz: null});
  assert.equal(ribbon[0].state, COVERAGE.RECENT);
  assert.equal(ribbon[1].state, COVERAGE.STALE);
});

test("progress counts visits, not the size of the plan", () => {
  const plan = planSurvey(FM);
  plan.tiles[0].observedAt = 1000;
  plan.tiles[1].observedAt = 1000;
  const progress = surveyProgress(coverageRibbon(plan, {now: 1010, liveCenterHz: null}));
  assert.equal(progress.visited, 2);
  assert.equal(progress.never, plan.tiles.length - 2);
  assert.equal(progress.complete, false);
});

test("a retained tile product carries bounded summaries and no samples", () => {
  const plan = planSurvey(FM);
  const product = retainTileProduct(plan.tiles[0], {
    frame: {available: true, noiseFloorDbfs: -92, peakFrequencyHz: 101.7e6, peakDbfs: -41,
            bins: new Array(512).fill(-90)},
    sparseOutcome: "NOISE_COMPATIBLE", signalChainHash: "81acd94e",
    config: {sample_rate_hz: 2_048_000, fft_size: 4096, max_bins: 512,
             sample_type: "int16", sensor_id: "NESDR-SMART-V5-14530058"},
    observedAt: 1000,
  });
  assert.equal(product.rawIqRetained, false);
  assert.equal(product.noiseFloorDbfs, -92);
  assert.equal(product.significantPeaks.length, 1);
  assert.equal(product.sparseOutcome, "NOISE_COMPATIBLE");
  assert.equal(product.hardware.fftSize, 4096);
  assert.match(product.boundary, /NOT A SIMULTANEOUS WIDEBAND CAPTURE/);
  const serialized = JSON.stringify(product);
  assert.ok(!/bins/.test(serialized), "bin arrays must not enter survey retention");
  assert.ok(!/iq/i.test(serialized.replace(/rawIqRetained/g, "")), "no IQ may be retained");
});

test("an unavailable frame retains nothing rather than an empty tile", () => {
  const plan = planSurvey(FM);
  assert.equal(retainTileProduct(plan.tiles[0], {frame: {available: false}, observedAt: 1}), null);
});

test("presets describe where the receiver can point, not what is authorized", () => {
  assert.equal(REGION, "US");
  assert.match(PRESET_BOUNDARY, /TUNABILITY IS NOT TRANSMISSION AUTHORIZATION/);
  const labels = RECEIVE_PRESETS.map((preset) => preset.label);
  assert.deepEqual(labels, ["FM BROADCAST", "NOAA WEATHER", "ISM 433", "ISM 915", "ADS-B"]);
  const rendered = JSON.stringify(RECEIVE_PRESETS);
  assert.ok(!/authoriz/i.test(rendered), "a preset must not imply authorization");
});

test("discrete channels stay discrete and a span is split into visits", () => {
  const noaa = RECEIVE_PRESETS.find((preset) => preset.label === "NOAA WEATHER");
  assert.deepEqual(presetCentres(noaa, 2_048_000), [...noaa.channelsHz],
    "seven channels must not be reinterpreted as a continuous sweep");
  const fm = RECEIVE_PRESETS.find((preset) => preset.label === "FM BROADCAST");
  assert.ok(presetCentres(fm, 2_048_000).length >= 9);
  const adsb = RECEIVE_PRESETS.find((preset) => preset.label === "ADS-B");
  assert.deepEqual(presetCentres(adsb, 2_048_000), [1090e6]);
});
