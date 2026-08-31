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
  directSamplingRangeHz: Object.freeze([100e3, 25e6]),
  nominalBandwidthHz: 2.4e6,
  authority: "MODEL_DECLARED",
});

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
export function deriveIdentity(status = {}) {
  const config = status?.bridge?.config ?? {};
  const sensorId = String(config.sensor_id ?? "").trim();
  const serial = sensorId.match(/(\d{6,})$/)?.[1] ?? null;
  const device = NESDR_SMART_V5;
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
export function deriveTuningRegime(status = {}) {
  const centre = finite(status?.bridge?.config?.center_frequency_hz);
  if (centre === null) return {regime: UNDECLARED, authority: UNDECLARED, note: "NO CONFIGURED CENTRE FREQUENCY"};
  const [low, high] = NESDR_SMART_V5.tunerRangeHz;
  const [directLow, directHigh] = NESDR_SMART_V5.directSamplingRangeHz;
  if (centre >= low && centre <= high) {
    return {regime: "TUNER", authority: NESDR_SMART_V5.authority,
            note: `WITHIN ORDINARY R820T2 RANGE ${formatHz(low)}–${formatHz(high)}`};
  }
  if (centre >= directLow && centre < directLow + (directHigh - directLow) + 1) {
    return {regime: "DIRECT SAMPLING REQUIRED", authority: NESDR_SMART_V5.authority,
            note: "BELOW ORDINARY TUNER RANGE · PERFORMANCE DIFFERS FROM TUNER MODE"};
  }
  return {regime: "OUTSIDE DECLARED RANGE", authority: NESDR_SMART_V5.authority,
          note: `CENTRE ${formatHz(centre)} IS OUTSIDE THE DECLARED MODEL COVERAGE`};
}

/**
 * The hardware-health rail. Fields the bridge does not publish (gain, PPM
 * correction, direct-sampling state, antenna) are UNDECLARED rather than
 * invented, and an undeclared row is not a fault.
 */
export function deriveHardwareHealth(status = {}, {now = Date.now() / 1000, sparse = null} = {}) {
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
     detail: `MODEL NOMINAL ${(NESDR_SMART_V5.nominalBandwidthHz / 1e6).toFixed(1)} MHz · CONFIGURED VALUE GOVERNS`},
    {label: "SAMPLE TYPE", value: String(config.sample_type ?? UNDECLARED).toUpperCase(),
     level: config.sample_type ? HEALTH.LIVE : HEALTH.UNDECLARED},
    {label: "RIGCTL", value: config.rigctl_host ? `${config.rigctl_host}:${config.rigctl_port}` : UNDECLARED,
     level: config.rigctl_host ? HEALTH.LIVE : HEALTH.UNDECLARED,
     detail: "ENDPOINT CONFIGURED; READINESS NOT PROBED"},
    // The bridge publishes none of the following. They are omissions, not faults.
    {label: "GAIN", value: UNDECLARED, level: HEALTH.UNDECLARED, detail: "BRIDGE PUBLISHES NO GAIN STATE"},
    {label: "TUNER PPM", value: UNDECLARED, level: HEALTH.UNDECLARED, detail: "NO CORRECTION REPORTED"},
    {label: "DIRECT SAMPLE", value: UNDECLARED, level: HEALTH.UNDECLARED, detail: "MODE NOT REPORTED BY BRIDGE"},
    {label: "BIAS TEE", value: NESDR_SMART_V5.biasTee, level: HEALTH.UNDECLARED,
     detail: "MODEL-DECLARED: NO BIAS TEE ON SMArt v5"},
    {label: "ANTENNA", value: UNDECLARED, level: HEALTH.UNDECLARED,
     detail: "OPERATOR HAS NOT DECLARED AN ANTENNA"},
    {label: "SIGNAL CHAIN", value: String(sparse?.chain?.signal_chain_hash ?? sparse?.signal_chain_hash ?? UNDECLARED),
     level: sparse?.chain?.signal_chain_hash || sparse?.signal_chain_hash ? HEALTH.LIVE : HEALTH.UNDECLARED},
  ];
  return rows;
}

/** Normalize one bounded spectrum frame into a plot-ready trace. Never interpolated. */
export function deriveSpectrumFrame(payload = {}, {config = {}, now = Date.now() / 1000} = {}) {
  if (!payload?.available || !payload?.spectrum) {
    return {available: false, reason: "NO FRAME RETAINED BY THE BRIDGE", bins: [],
            widths: deriveBinWidths(config, null)};
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
