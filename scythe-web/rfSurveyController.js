/**
 * Bounded regional survey coverage.
 *
 * A single receiver observes ONE instantaneous span at a time. A survey is a
 * stitched sequence of visits, and this module exists to keep that visible: a
 * stitched 20 MHz sweep must never render as a simultaneous 20 MHz capture.
 *
 * Every tile carries the time it was observed, so the ribbon can separate:
 *   LIVE      the span being observed right now
 *   RECENT    observed earlier, still inside the freshness window
 *   STALE     observed, but older than the freshness window
 *   NEVER     never visited — no claim of any kind
 *
 * Retained tile products are bounded summaries only. No raw IQ, ever.
 */

export const COVERAGE = Object.freeze({
  LIVE: "LIVE",
  RECENT: "RECENT",
  STALE: "STALE",
  NEVER: "NEVER OBSERVED",
});

const finite = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
};

/** Build the ordered visit plan for a survey across a span. */
export function planSurvey({startHz, endHz, sampleRateHz, dwellSeconds = 1.0, stepHz = null}) {
  const start = finite(startHz); const end = finite(endHz);
  const span = finite(sampleRateHz);
  if (start === null || end === null || end <= start) {
    return {valid: false, reason: "SURVEY SPAN IS EMPTY OR INVERTED", tiles: []};
  }
  if (span === null || span <= 0) {
    return {valid: false, reason: "NO OBSERVABLE SPAN REPORTED BY THE BRIDGE", tiles: []};
  }
  // Stepping by less than the observed span overlaps; stepping by more leaves
  // gaps that are NEVER OBSERVED rather than quietly interpolated.
  const step = finite(stepHz) ?? span;
  const tiles = [];
  for (let centre = start + span / 2; centre - span / 2 < end; centre += step) {
    tiles.push({
      centerHz: Math.round(centre),
      minHz: Math.round(centre - span / 2),
      maxHz: Math.round(centre + span / 2),
      observedAt: null,
      state: COVERAGE.NEVER,
    });
    if (tiles.length >= 512) break;   // bounded plan
  }
  return {
    valid: tiles.length > 0,
    reason: tiles.length ? null : "SPAN IS NARROWER THAN ONE OBSERVATION",
    startHz: start, endHz: end, spanHz: span, stepHz: step,
    dwellSeconds: Math.max(0.1, finite(dwellSeconds) ?? 1.0),
    tiles,
    boundary: "STITCHED SURVEY · TILES WERE OBSERVED AT DIFFERENT TIMES, NOT SIMULTANEOUSLY",
  };
}

/**
 * Classify each tile against the live span and the freshness window. The live
 * span is whatever the bridge is observing now — it is the only tile that can
 * claim to be current.
 */
export function coverageRibbon(plan, {liveCenterHz = null, now = Date.now() / 1000,
                                      freshnessSeconds = 120} = {}) {
  const live = finite(liveCenterHz);
  const span = finite(plan?.spanHz) ?? 0;
  return (plan?.tiles ?? []).map((tile) => {
    const observedAt = finite(tile.observedAt);
    const isLive = live !== null && span > 0 && Math.abs(live - tile.centerHz) < span / 2;
    if (isLive) return {...tile, state: COVERAGE.LIVE, ageSeconds: 0};
    if (observedAt === null) return {...tile, state: COVERAGE.NEVER, ageSeconds: null};
    const age = now - observedAt;
    return {...tile, ageSeconds: age,
            state: age <= freshnessSeconds ? COVERAGE.RECENT : COVERAGE.STALE};
  });
}

/** Progress counts what was actually visited, never what was planned. */
export function surveyProgress(ribbon) {
  const tiles = ribbon ?? [];
  const visited = tiles.filter((tile) => tile.state !== COVERAGE.NEVER).length;
  return {
    visited, total: tiles.length,
    live: tiles.filter((tile) => tile.state === COVERAGE.LIVE).length,
    recent: tiles.filter((tile) => tile.state === COVERAGE.RECENT).length,
    stale: tiles.filter((tile) => tile.state === COVERAGE.STALE).length,
    never: tiles.filter((tile) => tile.state === COVERAGE.NEVER).length,
    complete: tiles.length > 0 && visited === tiles.length,
  };
}

/**
 * Retain one bounded product for a visited tile. Fields are an explicit
 * allow-list so a caller cannot smuggle samples into survey retention.
 */
export function retainTileProduct(tile, {frame, sparseOutcome = null, config = {},
                                         signalChainHash = null, observedAt}) {
  if (!frame?.available) return null;
  const peaks = [];
  if (finite(frame.peakFrequencyHz) !== null) {
    peaks.push({frequencyHz: frame.peakFrequencyHz, dbfs: finite(frame.peakDbfs)});
  }
  return Object.freeze({
    centerHz: tile.centerHz, minHz: tile.minHz, maxHz: tile.maxHz,
    observedAt: finite(observedAt),
    noiseFloorDbfs: finite(frame.noiseFloorDbfs),
    significantPeaks: Object.freeze(peaks),
    sparseOutcome,
    hardware: Object.freeze({
      sampleRateHz: finite(config.sample_rate_hz), fftSize: finite(config.fft_size),
      publishedBins: finite(config.max_bins), sampleType: config.sample_type ?? null,
      sensorId: config.sensor_id ?? null,
    }),
    signalChainHash,
    rawIqRetained: false,
    evidenceClass: "MEASURED_SPECTRAL_SUMMARY",
    boundary: "OBSERVED DURING ONE VISIT · NOT A SIMULTANEOUS WIDEBAND CAPTURE",
  });
}
