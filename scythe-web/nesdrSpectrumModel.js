/**
 * Bounded state derivation for the NESDR Spectrum instrument.
 *
 * Every value this module produces carries the authority that earned it. Three
 * authorities are kept apart on purpose:
 *
 *   MODEL_DECLARED               datasheet facts about the SMArt v5 product line.
 *                                True of the model, never measured from this unit.
 *   CONFIGURED_NOT_USB_ATTESTED  values SCYTHE was told, not values it verified.
 *   RF_BRIDGE_RUNTIME_STATUS     observed bridge/transform state.
 *
 * A field the bridge does not report is UNDECLARED — a metadata omission. It is
 * never "UNKNOWN", which would imply SCYTHE looked at the hardware and could not
 * tell. The receiver never yields an emitter location; see sensorAnchorNotice().
 */

import { antennaHealthRow } from "./rfAntennaDeclaration.js";

export const HEALTH = Object.freeze({
  LIVE: "live",         // observed and fresh
  DEGRADED: "degraded", // connected but stale
  FAILED: "failed",     // disconnected or invalid
  UNDECLARED: "undeclared", // never reported; not a fault
});

/** Datasheet facts for the Nooelec NESDR SMArt v5. True of the model, not this unit. */
export const NESDR_SMART_V5 = Object.freeze({
  vendor: "NOOELEC",
  model: "NESDR SMArt v5",
  usbBridge: "RTL2832U",
  tuner: "R820T2",
  tunerNote: "SOFTWARE MAY REPORT R820T",
  clock: "0.5 PPM TCXO",
  input: "50 Ω FEMALE SMA",
  biasTee: "NOT FITTED",
  tunerRangeHz: Object.freeze([25e6, 1750e6]),
  // [low, high, lowInclusive, highInclusive]; the tuner range above is their hull.
  tunerRanges: Object.freeze([Object.freeze([25e6, 1750e6, true, true])]),
  directSamplingRangeHz: Object.freeze([100e3, 25e6]),
  nominalBandwidthHz: 2.4e6,
  authority: "MODEL_DECLARED",
  key: "SMART_V5",
});

/**
 * The Nooelec NESDR SMArt XTR (E4000). Range and gap are what `rtl_test` reported
 * on SN 71874552 on 2026-11-14 (PLL-not-locked lines at 51, 1104, 1250 and
 * 2206 MHz mark the edges, so those exact frequencies are excluded). Nothing else
 * is claimed: the repo declares no direct-sampling range, input, bias tee or
 * identified reference for it, and its reference is only a 100 ppm conservative
 * bound (docs/521_AUTHORIZED_RUN_DECLARATION_XTR.json).
 */
export const NESDR_SMART_XTR = Object.freeze({
  vendor: "NOOELEC",
  model: "NESDR SMArt XTR",
  usbBridge: "RTL2832U",
  tuner: "E4000",
  tunerNote: "AS REPORTED BY librtlsdr",
  clock: "REFERENCE UNIDENTIFIED · 100 ppm CONSERVATIVE BOUND",
  input: "UNDECLARED",
  biasTee: "UNDECLARED",
  tunerRangeHz: Object.freeze([52e6, 2205e6]),
  tunerRanges: Object.freeze([Object.freeze([52e6, 1104e6, true, false]), Object.freeze([1250e6, 2205e6, false, true])]),
  gapHz: Object.freeze([1104e6, 1250e6]),
  directSamplingRangeHz: null,
  nominalBandwidthHz: 2.4e6,
  authority: "MODEL_DECLARED",
  key: "SMART_XTR",
});

export const RECEIVER_PROFILES = Object.freeze({SMART_V5: NESDR_SMART_V5, SMART_XTR: NESDR_SMART_XTR});

// librtlsdr names an R820T2 "R820T"; the E4000 names itself.
const PROFILE_BY_REPORTED_TUNER = Object.freeze({E4000: "SMART_XTR", R820T: "SMART_V5", R820T2: "SMART_V5"});

/**
 * Which receiver the instrument surface describes. The dongle's own tuner report
 * outranks everything: a selector cannot make an E4000 an R820T2. The selection is
 * used only while the bridge has reported no tuner, and with neither the legacy
 * v5 default stays but is marked unconfirmed. A tuner outside the catalogue yields
 * profile null, which the callers render as undeclared rather than as a v5.
 */
export function resolveReceiverProfile(status = {}, selectedKey = null) {
  const reported = String(status?.bridge?.device?.tuner_type ?? "").trim().toUpperCase();
  if (reported && reported !== "UNKNOWN") {
    const key = PROFILE_BY_REPORTED_TUNER[reported];
    if (key) return {profile: RECEIVER_PROFILES[key], source: "DEVICE_REPORTED", tuner: reported};
    return {profile: null, source: "DEVICE_REPORTED_UNCATALOGUED", tuner: reported};
  }
  if (selectedKey && RECEIVER_PROFILES[selectedKey]) {
    return {profile: RECEIVER_PROFILES[selectedKey], source: "OPERATOR_SELECTED", tuner: null};
  }
  return {profile: NESDR_SMART_V5, source: "DEFAULT_UNCONFIRMED", tuner: null};
}

const withinRanges = (hz, ranges) => ranges.some(([low, high, lowIn, highIn]) =>
  (lowIn ? hz >= low : hz > low) && (highIn ? hz <= high : hz < high));

const UNDECLARED = "UNDECLARED";

// Number(null), Number("") and Number([]) are all 0, so an absent value would
// otherwise arrive as a real measurement of zero. Absence must stay absent.
const finite = (value) => {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
};

const epochSeconds = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed / 1000 : null;
};

/**
 * A derived geometric reference, rounded to the kHz it is actually good for.
 *
 * formatHz prints six decimals because a tuned frequency is commanded to that
 * precision. A quarter wave computed from a mast extension is not: the input is
 * a ruler reading, and 203.663355 MHz claims sub-hertz knowledge of a number
 * that moves ~550 kHz per millimetre of mast.
 */
export const formatReferenceHz = (value) => {
  const hz = finite(value);
  if (hz === null) return UNDECLARED;
  const abs = Math.abs(hz);
  if (abs >= 1e9) return `${(hz / 1e9).toFixed(3)} GHz`;
  if (abs >= 1e6) return `${(hz / 1e6).toFixed(3)} MHz`;
  if (abs >= 1e3) return `${(hz / 1e3).toFixed(1)} kHz`;
  return `${hz.toFixed(0)} Hz`;
};

export const formatHz = (value) => {
  const hz = finite(value);
  if (hz === null) return UNDECLARED;
  const abs = Math.abs(hz);
  if (abs >= 1e9) return `${(hz / 1e9).toFixed(6)} GHz`;
  if (abs >= 1e6) return `${(hz / 1e6).toFixed(6)} MHz`;
  if (abs >= 1e3) return `${(hz / 1e3).toFixed(3)} kHz`;
  return `${hz.toFixed(1)} Hz`;
};

/**
 * Native transform resolution and published product resolution are different
 * numbers and are always reported as both. A 4096-point FFT at 2.048 MS/s has
 * 500 Hz native bins; the bounded 512-bin product has 4 kHz analysis bins.
 * Peak downsampling preserves peak amplitude, never native frequency resolution.
 */
export function deriveBinWidths(config = {}, frame = null) {
  const sampleRate = finite(frame?.sample_rate_hz ?? config.sample_rate_hz);
  const fftSize = finite(frame?.fft_size ?? config.fft_size);
  const publishedBins = finite(frame?.bin_count ?? config.max_bins);
  return {
    sampleRateHz: sampleRate,
    fftSize,
    publishedBins,
    nativeBinWidthHz: sampleRate && fftSize ? sampleRate / fftSize : null,
    analysisBinWidthHz: sampleRate && publishedBins ? sampleRate / publishedBins : null,
    reduction: "PEAK_DOWNSAMPLE",
    note: "DISPLAY BINS PRESERVE PEAK AMPLITUDE, NOT NATIVE FREQUENCY RESOLUTION",
  };
}

/** Identity rows for the header strip, each tagged with the authority that earned it. */
export function deriveIdentity(status = {}, device = NESDR_SMART_V5) {
  const config = status?.bridge?.config ?? {};
  const sensorId = String(config.sensor_id ?? "").trim();
  const serial = sensorId.match(/(\d{6,})$/)?.[1] ?? null;
  if (device === null) {
    const tuner = String(status?.bridge?.device?.tuner_type ?? UNDECLARED);
    return [{label: "RECEIVER", value: `UNCATALOGUED // TUNER ${tuner}`, authority: "DEVICE_REPORTED"},
            {label: "SENSOR ID", value: sensorId || UNDECLARED, authority: sensorId ? "CONFIGURED_NOT_USB_ATTESTED" : UNDECLARED},
            {label: "CAPTURE OWNER", value: String(status?.bridge?.capture_owner ?? UNDECLARED).toUpperCase(),
             authority: "RF_BRIDGE_RUNTIME_STATUS"}];
  }
  return [
    {label: "RECEIVER", value: `${device.vendor} ${device.model}`.toUpperCase(), authority: device.authority},
    {label: "SENSOR ID", value: sensorId || UNDECLARED,
     authority: sensorId ? "CONFIGURED_NOT_USB_ATTESTED" : UNDECLARED},
    {label: "SERIAL", value: serial ?? UNDECLARED,
     authority: serial ? "CONFIGURED_NOT_USB_ATTESTED" : UNDECLARED},
    {label: "USB", value: device.usbBridge, authority: device.authority},
    {label: "TUNER", value: `${device.tuner} [${device.tunerNote}]`, authority: device.authority},
    {label: "CLOCK", value: device.clock, authority: device.authority},
    {label: "INPUT", value: device.input, authority: device.authority},
    {label: "BIAS TEE", value: device.biasTee, authority: device.authority},
    {label: "CAPTURE OWNER", value: String(status?.bridge?.capture_owner ?? UNDECLARED).toUpperCase(),
     authority: "RF_BRIDGE_RUNTIME_STATUS"},
  ];
}

/**
 * Where the configured span sits relative to the tuner's ordinary range. Direct
 * sampling is a distinct mode with distinct performance and is never folded into
 * the normal tuner range.
 */
export function deriveTuningRegime(status = {}, device = NESDR_SMART_V5) {
  const centre = finite(status?.bridge?.config?.center_frequency_hz);
  if (centre === null) return {regime: UNDECLARED, authority: UNDECLARED, note: "NO CONFIGURED CENTRE FREQUENCY"};
  if (device === null) return {regime: UNDECLARED, authority: UNDECLARED, note: "NO COVERAGE DECLARED FOR THE REPORTED TUNER"};
  const [low, high] = device.tunerRangeHz;
  if (withinRanges(centre, device.tunerRanges)) {
    return {regime: "TUNER", authority: device.authority,
            note: `WITHIN ORDINARY ${device.tuner} RANGE ${formatHz(low)}–${formatHz(high)}`
              + (device.gapHz ? ` EXCEPT ${formatHz(device.gapHz[0])}–${formatHz(device.gapHz[1])}` : "")};
  }
  if (device.gapHz && centre >= device.gapHz[0] && centre <= device.gapHz[1]) {
    return {regime: "OUTSIDE DECLARED RANGE", authority: device.authority,
            note: `CENTRE ${formatHz(centre)} IS IN THE ${device.tuner} GAP ${formatHz(device.gapHz[0])}–${formatHz(device.gapHz[1])}`};
  }
  const direct = device.directSamplingRangeHz;
  if (direct && centre >= direct[0] && centre < direct[1] + 1) {
    return {regime: "DIRECT SAMPLING REQUIRED", authority: device.authority,
            note: "BELOW ORDINARY TUNER RANGE · PERFORMANCE DIFFERS FROM TUNER MODE"};
  }
  return {regime: "OUTSIDE DECLARED RANGE", authority: device.authority,
          note: `CENTRE ${formatHz(centre)} IS OUTSIDE THE DECLARED MODEL COVERAGE`};
}

/**
 * The hardware-health rail. Fields the bridge does not publish (gain, PPM
 * correction, direct-sampling state, antenna) are UNDECLARED rather than
 * invented, and an undeclared row is not a fault.
 */
export function deriveHardwareHealth(status = {}, {now = Date.now() / 1000, sparse = null, antenna = null,
    profile = NESDR_SMART_V5} = {}) {
  const device = profile ?? NESDR_SMART_V5;   // nominal bandwidth is an RTL2832U fact, shared by both
  const bridge = status?.bridge ?? {};
  const config = bridge.config ?? {};
  const products = bridge.products ?? {};
  const frameAt = epochSeconds(bridge.latest_frame_at);
  const frameAge = frameAt === null ? null : now - frameAt;
  const fftLimit = finite(products.fft_frames?.freshness_limit_seconds) ?? 5;
  const connected = Boolean(bridge.iq_connected);
  const fftState = String(products.fft_frames?.state ?? "").toLowerCase();
  const sparseState = String(products.sparse_supports?.state ?? "").toLowerCase();

  const socketLevel = connected ? HEALTH.LIVE : HEALTH.FAILED;
  const rows = [
    {label: "IQ SOCKET", value: connected ? "CONNECTED" : "DISCONNECTED", level: socketLevel,
     detail: connected ? `${config.iq_host ?? "?"}:${config.iq_port ?? "?"}` : String(bridge.last_error ?? "")},
    {label: "BRIDGE", value: String(bridge.bridge_state ?? UNDECLARED).toUpperCase(),
     level: bridge.bridge_state === "running" ? HEALTH.LIVE
       : bridge.bridge_state ? HEALTH.DEGRADED : HEALTH.UNDECLARED},
    {label: "LAST FFT", value: frameAge === null ? "NO FRAME RECEIVED" : `${frameAge.toFixed(2)}s AGO`,
     level: frameAge === null ? HEALTH.FAILED : frameAge <= fftLimit ? HEALTH.LIVE : HEALTH.DEGRADED},
    {label: "FFT PRODUCT", value: fftState.toUpperCase() || UNDECLARED,
     level: fftState === "live" ? HEALTH.LIVE : fftState === "stale" ? HEALTH.DEGRADED : HEALTH.UNDECLARED},
    {label: "SPARSE PRODUCT", value: sparseState.toUpperCase() || UNDECLARED,
     level: sparseState === "live" ? HEALTH.LIVE : sparseState === "stale" ? HEALTH.DEGRADED : HEALTH.UNDECLARED},
    {label: "FRAME RATE", value: finite(config.frames_per_second) === null ? UNDECLARED
       : `${finite(config.frames_per_second).toFixed(1)} /s CONFIGURED`,
     level: finite(config.frames_per_second) === null ? HEALTH.UNDECLARED : HEALTH.LIVE,
     detail: "CONFIGURED CADENCE, NOT A MEASURED ARRIVAL RATE"},
    {label: "SAMPLE RATE", value: finite(config.sample_rate_hz) === null ? UNDECLARED
       : `${(finite(config.sample_rate_hz) / 1e6).toFixed(3)} MS/s`,
     level: finite(config.sample_rate_hz) === null ? HEALTH.UNDECLARED : HEALTH.LIVE,
     detail: `MODEL NOMINAL ${(device.nominalBandwidthHz / 1e6).toFixed(1)} MHz · CONFIGURED VALUE GOVERNS`},
    {label: "SAMPLE TYPE", value: String(config.sample_type ?? UNDECLARED).toUpperCase(),
     level: config.sample_type ? HEALTH.LIVE : HEALTH.UNDECLARED},
    {label: "RIGCTL", value: config.rigctl_host ? `${config.rigctl_host}:${config.rigctl_port}` : UNDECLARED,
     level: config.rigctl_host ? HEALTH.LIVE : HEALTH.UNDECLARED,
     detail: "ENDPOINT CONFIGURED; READINESS NOT PROBED"},
    // The bridge publishes none of the following. They are omissions, not faults.
    {label: "GAIN", value: UNDECLARED, level: HEALTH.UNDECLARED, detail: "BRIDGE PUBLISHES NO GAIN STATE"},
    {label: "TUNER PPM", value: UNDECLARED, level: HEALTH.UNDECLARED, detail: "NO CORRECTION REPORTED"},
    {label: "DIRECT SAMPLE", value: UNDECLARED, level: HEALTH.UNDECLARED, detail: "MODE NOT REPORTED BY BRIDGE"},
    {label: "BIAS TEE", value: profile === null ? UNDECLARED : device.biasTee, level: HEALTH.UNDECLARED,
     detail: profile === null ? "NO COVERAGE DECLARED FOR THE REPORTED TUNER"
       : device.biasTee === "NOT FITTED" ? `MODEL-DECLARED: NO BIAS TEE ON ${device.model}`
       : `NOT DECLARED FOR ${device.model}`},
    // The antenna is not readable by any receiver measurement — see
    // rfAntennaDeclaration.js. An undeclared antenna is an omission, and a
    // declared one is the operator's word, never the instrument's.
    {...antennaHealthRow(antenna),
     level: antenna ? HEALTH.LIVE : HEALTH.UNDECLARED},
    {label: "SIGNAL CHAIN", value: String(sparse?.chain?.signal_chain_hash ?? sparse?.signal_chain_hash ?? UNDECLARED),
     level: sparse?.chain?.signal_chain_hash || sparse?.signal_chain_hash ? HEALTH.LIVE : HEALTH.UNDECLARED},
  ];
  return rows;
}

/** Normalize one bounded spectrum frame into a plot-ready trace. Never interpolated. */
export function deriveSpectrumFrame(payload = {}, {config = {}, now = Date.now() / 1000} = {}) {
  const source = payload?.capture_source ?? {};
  // Two axes, never collapsed: an established socket is not a moving stream,
  // and rtl_tcp surviving USB removal is the case where they disagree.
  const flow = {
    transportState: String(source.transport_state ?? UNDECLARED),
    sampleFlowState: String(source.sample_flow_state ?? UNDECLARED),
    sourceAvailability: String(source.availability ?? UNDECLARED),
    lastSampleAgeMs: finite(source.last_sample_age_ms),
    lastFrameAgeMs: finite(source.last_frame_age_ms),
    starvationThresholdMs: finite(source.starvation_threshold_ms),
  };
  if (!payload?.available || !payload?.spectrum) {
    return {available: false, reason: "NO FRAME RETAINED BY THE BRIDGE", bins: [],
            widths: deriveBinWidths(config, null), stale: false, staleReason: null, ...flow};
  }
  const frame = payload.spectrum;
  const bins = Array.isArray(frame.bins_dbfs)
    ? frame.bins_dbfs.map((value) => finite(value)).filter((value) => value !== null)
    : [];
  const observedAt = epochSeconds(frame.timestamp);
  return {
    available: bins.length > 0,
    reason: bins.length ? null : "FRAME RETAINED WITHOUT A BOUNDED BIN PRODUCT",
    bins,
    minFrequencyHz: finite(frame.min_frequency_hz),
    maxFrequencyHz: finite(frame.max_frequency_hz),
    centerFrequencyHz: finite(frame.center_frequency_hz),
    peakFrequencyHz: finite(frame.peak_frequency_hz),
    peakDbfs: finite(frame.peak_dbfs),
    noiseFloorDbfs: finite(frame.noise_floor_dbfs),
    sequence: finite(frame.sequence),
    observedAt,
    ageSeconds: observedAt === null ? null : now - observedAt,
    truncated: Boolean(frame.bins_truncated),
    widths: deriveBinWidths(config, frame),
    rawIqExposed: Boolean(payload.raw_iq_exposed),
    // The server's own judgement that this frame is no longer current. It was
    // measured, so it is not wrong; it has simply stopped describing now.
    stale: Boolean(payload.stale),
    staleReason: payload.stale ? String(payload.stale_reason ?? "SOURCE_NOT_STREAMING") : null,
    ...flow,
    evidenceClass: "MEASURED_SPECTRAL_SUMMARY",
  };
}

/**
 * Sparse-estimator rail. A null outcome is rendered, never blanked: "no support"
 * and "the analyzer never ran" are different realities and must look different.
 */
export function deriveSparseRail(sparseStatus = {}, supportsPayload = {}) {
  const status = String(sparseStatus?.status ?? "").toLowerCase();
  if (status === "unavailable" || sparseStatus?.enabled === false) {
    return {state: "UNAVAILABLE", outcome: null, cards: [],
            note: "SPARSE ANALYZER IS NOT RUNNING · NO WINDOW WAS ATTEMPTED",
            level: HEALTH.UNDECLARED};
  }
  const supports = Array.isArray(supportsPayload?.supports) ? supportsPayload.supports : [];
  const outcome = sparseStatus?.latest_outcome ?? null;
  const cards = supports.map((support) => {
    const fit = support?.fit ?? {};
    const parameters = support?.parameters ?? {};
    return {
      supportId: String(support?.support_id ?? ""),
      carrierHz: finite(parameters.carrier_hz),
      atomFamily: String(support?.atom_family ?? UNDECLARED).replace(/_/g, " ").toUpperCase(),
      model: String(sparseStatus?.dictionary_revision ?? "").includes("m1") ? "M1" : UNDECLARED,
      snrDb: finite(fit.snr_db),
      persistence: finite(fit.persistence),
      residualReduction: finite(fit.residual_reduction),
      modulationRateHz: finite(parameters.modulation_rate_hz),
      observedStart: epochSeconds(support?.observed_start),
      observedEnd: epochSeconds(support?.observed_end),
      authority: String(support?.evidence_class ?? "DERIVED_INFERENCE").replace(/_/g, " "),
      // Frequency uncertainty is the published analysis bin width; the estimator
      // does not resolve a carrier more finely than the product it consumed.
      uncertaintyHz: finite(support?.sample_rate_hz) && finite(sparseStatus?.published_bins ?? 512)
        ? finite(support.sample_rate_hz) / (finite(sparseStatus?.published_bins) ?? 512) / 2
        : null,
    };
  });
  if (!cards.length) {
    return {
      state: "NO SUPPORTS", outcome: outcome ?? "NO WINDOW COMPLETED", cards: [],
      level: outcome ? HEALTH.LIVE : HEALTH.DEGRADED,
      note: outcome
        ? "THE ESTIMATOR OPERATED AND SUPPORTED NOTHING · THIS IS A RESULT, NOT AN ABSENCE OF DATA"
        : "NO ANALYSIS WINDOW HAS COMPLETED · THIS IS NOT A NULL RESULT",
      windowCount: finite(sparseStatus?.window_count) ?? 0,
    };
  }
  return {state: "SUPPORTED", outcome, cards, level: HEALTH.LIVE,
          note: "DERIVED ESTIMATOR OUTPUT · NOT A SIGNAL-FAMILY CLASSIFICATION",
          windowCount: finite(sparseStatus?.window_count) ?? 0};
}

/**
 * A sparse support and a signal-family classification answer different questions.
 * NO_SUPPORT is an estimator decision; DIGITAL is a family inference. Neither
 * replaces the other, so both are reported side by side.
 */
export function deriveClassificationSummary(status = {}) {
  const counts = status?.observations?.signal_classifications ?? null;
  if (!counts) return {available: false, note: "NO RETAINED DETECTION COUNTS PUBLISHED"};
  const read = (name) => Math.max(0, finite(counts[name]) ?? 0);
  return {
    available: true,
    digital: read("digital"), analogue: read("analogue"),
    unclassified: read("unclassified"), total: read("total"),
    scope: String(status?.observations?.classification_scope ?? "bounded_retained_detection_events"),
    note: "RETAINED DETECTION EVENTS, NOT UNIQUE EMITTERS",
  };
}

/** With one receiver, SCYTHE knows where the sensor is — never where an emitter is. */
export function sensorAnchorNotice() {
  return Object.freeze({
    sensorLocation: "OBSERVED OR OPERATOR DECLARED",
    emitterLocation: "NOT ESTABLISHED",
    reason: "A SINGLE RECEIVER CANNOT ESTABLISH EMITTER POSITION; TDOA/AOA REQUIRES "
      + "MULTIPLE SPATIALLY SEPARATED RECEIVERS AND TRUSTWORTHY CLOCKS",
  });
}
