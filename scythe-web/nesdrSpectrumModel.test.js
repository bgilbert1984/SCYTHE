import assert from "node:assert/strict";
import test from "node:test";

import {
  HEALTH, NESDR_SMART_V5, deriveBinWidths, deriveClassificationSummary, deriveHardwareHealth,
  deriveIdentity, deriveSparseRail, deriveSpectrumFrame, deriveTuningRegime, formatHz,
  sensorAnchorNotice,
} from "./nesdrSpectrumModel.js";

const status = (overrides = {}) => ({
  bridge: {
    bridge_state: "running", iq_connected: true, latest_frame_at: "2026-08-31T01:00:00Z",
    capture_owner: "orchestrator", last_error: null,
    config: {
      sensor_id: "NESDR-SMART-V5-14530058", center_frequency_hz: 100e6, sample_rate_hz: 2_048_000,
      fft_size: 4096, max_bins: 512, frames_per_second: 10, sample_type: "int16",
      iq_host: "127.0.0.1", iq_port: 1234, rigctl_host: "127.0.0.1", rigctl_port: 4532,
    },
    products: {
      fft_frames: {state: "live", freshness_limit_seconds: 5},
      sparse_supports: {state: "live"},
    },
    ...overrides,
  },
  observations: {
    classification_scope: "bounded_retained_detection_events",
    signal_classifications: {digital: 2, analogue: 1, unclassified: 7, total: 10},
  },
});

test("native and published resolution are reported as separate numbers", () => {
  const widths = deriveBinWidths({sample_rate_hz: 2_048_000, fft_size: 4096, max_bins: 512});
  assert.equal(widths.nativeBinWidthHz, 500);
  assert.equal(widths.analysisBinWidthHz, 4000);
  assert.equal(widths.reduction, "PEAK_DOWNSAMPLE");
  assert.match(widths.note, /NOT NATIVE FREQUENCY RESOLUTION/);
});

test("identity separates datasheet facts from configured, unattested values", () => {
  const rows = deriveIdentity(status());
  const by = Object.fromEntries(rows.map((row) => [row.label, row]));
  assert.equal(by.SERIAL.value, "14530058");
  assert.equal(by.SERIAL.authority, "CONFIGURED_NOT_USB_ATTESTED",
    "a configured serial must never be presented as USB-attested");
  assert.equal(by.TUNER.authority, "MODEL_DECLARED");
  assert.equal(by["BIAS TEE"].value, "NOT FITTED");
  assert.equal(by.USB.value, "RTL2832U");
  assert.match(by.TUNER.value, /R820T2/);
});

test("an unset sensor id is UNDECLARED rather than invented", () => {
  const rows = deriveIdentity({bridge: {config: {}}});
  const by = Object.fromEntries(rows.map((row) => [row.label, row]));
  assert.equal(by["SENSOR ID"].value, "UNDECLARED");
  assert.equal(by.SERIAL.value, "UNDECLARED");
});

test("direct sampling is a distinct regime, not folded into the tuner range", () => {
  assert.equal(deriveTuningRegime(status()).regime, "TUNER");
  const low = status(); low.bridge.config.center_frequency_hz = 5e6;
  const regime = deriveTuningRegime(low);
  assert.equal(regime.regime, "DIRECT SAMPLING REQUIRED");
  assert.match(regime.note, /PERFORMANCE DIFFERS FROM TUNER MODE/);
});

test("unreported hardware is UNDECLARED, never UNKNOWN, and is not a fault", () => {
  const rows = deriveHardwareHealth(status(), {now: Date.parse("2026-08-31T01:00:01Z") / 1000});
  const by = Object.fromEntries(rows.map((row) => [row.label, row]));
  for (const label of ["GAIN", "TUNER PPM", "DIRECT SAMPLE", "ANTENNA"]) {
    assert.equal(by[label].value, "UNDECLARED", `${label} must be undeclared`);
    assert.equal(by[label].level, HEALTH.UNDECLARED, `${label} must not read as a fault`);
  }
  assert.match(by.ANTENNA.detail, /OPERATOR HAS NOT DECLARED/);
  const rendered = JSON.stringify(rows);
  assert.ok(!/UNKNOWN ANTENNA/.test(rendered), "an omission must not read as a puzzled inspection");
});

test("configured cadence is never presented as a measured arrival rate", () => {
  const rows = deriveHardwareHealth(status(), {now: Date.parse("2026-08-31T01:00:01Z") / 1000});
  const rate = rows.find((row) => row.label === "FRAME RATE");
  assert.match(rate.value, /CONFIGURED/);
  assert.match(rate.detail, /NOT A MEASURED ARRIVAL RATE/);
});

test("configured sample rate governs; the model nominal is only context", () => {
  const rows = deriveHardwareHealth(status(), {now: Date.parse("2026-08-31T01:00:01Z") / 1000});
  const rate = rows.find((row) => row.label === "SAMPLE RATE");
  assert.match(rate.value, /2\.048 MS\/s/, "the configured rate must be shown, not 2.4 MHz");
  assert.match(rate.detail, /CONFIGURED VALUE GOVERNS/);
});

test("a disconnected socket and a stale frame are distinguishable states", () => {
  const offline = status({iq_connected: false, bridge_state: "reconnecting",
    latest_frame_at: null, last_error: "[Errno 111] Connection refused",
    products: {fft_frames: {state: "stale", freshness_limit_seconds: 5}, sparse_supports: {state: "stale"}}});
  const rows = deriveHardwareHealth(offline, {now: Date.parse("2026-08-31T01:00:01Z") / 1000});
  const by = Object.fromEntries(rows.map((row) => [row.label, row]));
  assert.equal(by["IQ SOCKET"].level, HEALTH.FAILED);
  assert.match(by["IQ SOCKET"].detail, /Connection refused/);
  assert.equal(by["LAST FFT"].value, "NO FRAME RECEIVED");
  assert.equal(by["FFT PRODUCT"].level, HEALTH.DEGRADED);
});

test("a frame older than its freshness limit degrades rather than reading live", () => {
  const rows = deriveHardwareHealth(status(), {now: Date.parse("2026-08-31T01:00:30Z") / 1000});
  assert.equal(rows.find((row) => row.label === "LAST FFT").level, HEALTH.DEGRADED);
});

test("a spectrum frame without bins is unavailable rather than drawn empty", () => {
  const frame = deriveSpectrumFrame({available: true, spectrum: {timestamp: 1}}, {config: {}});
  assert.equal(frame.available, false);
  assert.match(frame.reason, /WITHOUT A BOUNDED BIN PRODUCT/);
  assert.deepEqual(frame.bins, []);
});

test("a bounded frame keeps its own span and never claims raw IQ", () => {
  const frame = deriveSpectrumFrame({
    available: true, raw_iq_exposed: false,
    spectrum: {timestamp: 1000, bins_dbfs: [-90, -40, -85], fft_size: 4096, bin_count: 3,
               sample_rate_hz: 2_048_000, center_frequency_hz: 100e6,
               min_frequency_hz: 99e6, max_frequency_hz: 101e6, peak_dbfs: -40,
               peak_frequency_hz: 100e6, noise_floor_dbfs: -88, sequence: 7},
  }, {config: {}, now: 1002});
  assert.equal(frame.available, true);
  assert.equal(frame.ageSeconds, 2);
  assert.equal(frame.rawIqExposed, false);
  assert.equal(frame.evidenceClass, "MEASURED_SPECTRAL_SUMMARY");
  assert.equal(frame.widths.nativeBinWidthHz, 500);
});

test("a null estimator outcome is a result, distinct from no window at all", () => {
  const supported = deriveSparseRail({status: "ok", latest_outcome: "NOISE_COMPATIBLE",
    window_count: 4, dictionary_revision: "scythe.rf-sparse-dict.m1.v1"}, {supports: []});
  assert.equal(supported.state, "NO SUPPORTS");
  assert.equal(supported.outcome, "NOISE_COMPATIBLE");
  assert.match(supported.note, /THIS IS A RESULT, NOT AN ABSENCE OF DATA/);

  const never = deriveSparseRail({status: "ok", window_count: 0}, {supports: []});
  assert.equal(never.outcome, "NO WINDOW COMPLETED");
  assert.match(never.note, /THIS IS NOT A NULL RESULT/);
  assert.equal(never.level, HEALTH.DEGRADED);
});

test("an analyzer that is not running is not reported as a null result", () => {
  const rail = deriveSparseRail({status: "unavailable"}, {supports: []});
  assert.equal(rail.state, "UNAVAILABLE");
  assert.equal(rail.outcome, null);
  assert.match(rail.note, /NO WINDOW WAS ATTEMPTED/);
});

test("support cards carry estimator fit and never a family classification", () => {
  const rail = deriveSparseRail(
    {status: "ok", latest_outcome: "SUPPORT", window_count: 2,
     dictionary_revision: "scythe.rf-sparse-dict.m1.v1", published_bins: 512},
    {supports: [{support_id: "rfss-1", atom_family: "periodic_amplitude", sample_rate_hz: 2_048_000,
                 parameters: {carrier_hz: 101_702_000, modulation_rate_hz: 12.5},
                 fit: {snr_db: 18.4, persistence: 0.91, residual_reduction: 0.72},
                 observed_start: 1000, observed_end: 1013, evidence_class: "DERIVED_INFERENCE"}]});
  assert.equal(rail.cards.length, 1);
  const [card] = rail.cards;
  assert.equal(card.atomFamily, "PERIODIC AMPLITUDE");
  assert.equal(card.snrDb, 18.4);
  assert.equal(card.authority, "DERIVED INFERENCE");
  assert.equal(card.uncertaintyHz, 2000, "uncertainty is half the published analysis bin width");
  assert.ok(!("signalFamily" in card), "an estimator support must not carry a family verdict");
  assert.match(rail.note, /NOT A SIGNAL-FAMILY CLASSIFICATION/);
});

test("detection counts describe retained events, not unique emitters", () => {
  const summary = deriveClassificationSummary(status());
  assert.deepEqual(
    [summary.digital, summary.analogue, summary.unclassified, summary.total], [2, 1, 7, 10]);
  assert.match(summary.note, /NOT UNIQUE EMITTERS/);
  assert.equal(deriveClassificationSummary({}).available, false);
});

test("a single receiver never establishes an emitter location", () => {
  const anchor = sensorAnchorNotice();
  assert.equal(anchor.emitterLocation, "NOT ESTABLISHED");
  assert.match(anchor.reason, /MULTIPLE SPATIALLY SEPARATED RECEIVERS/);
});

test("frequencies format without implying resolution the product lacks", () => {
  assert.equal(formatHz(101_702_000), "101.702000 MHz");
  assert.equal(formatHz(2000), "2.000 kHz");
  assert.equal(formatHz(null), "UNDECLARED");
  assert.equal(NESDR_SMART_V5.authority, "MODEL_DECLARED");
});
