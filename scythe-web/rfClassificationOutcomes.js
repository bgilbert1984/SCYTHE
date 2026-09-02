/**
 * Phase 0 rendering contract for RF signal-family outcomes.
 *
 * The panel used to show `DIGITAL 0 · ANALOGUE 0 · UNCLASSIFIED 0` and leave the
 * reader to guess which of two very different things it meant: that the band was
 * quiet, or that nothing had looked. This module makes the zeros state which.
 *
 * Two absences are rendered rather than hidden:
 *
 *   CLASSIFIER NOT IMPLEMENTED   no channelizer and no symbol-clock detector
 *                                are running, so every detection is unclassified
 *                                by construction and not by measurement.
 *   ANALOGUE DETECTOR MISSING    ANALOGUE is reserved, never inferred from the
 *                                absence of a symbol clock. Constant-envelope
 *                                digital modes look identical to FM voice under
 *                                an envelope-based test.
 *
 * Copy here mirrors rf_signal_family.py. The server owns the vocabulary; this
 * module owns only how it reads.
 */

const finite = (value) => (Number.isFinite(Number(value)) ? Number(value) : null);

/** Shown when the bridge publishes no classifier block at all. */
export const CLASSIFIER_UNDECLARED = Object.freeze({
  state: "UNDECLARED",
  headline: "CLASSIFIER STATE // UNDECLARED",
  note: "THE BRIDGE PUBLISHED NO CLASSIFIER BLOCK · THIS IS A MISSING FIELD, "
    + "NOT A CLASSIFIER THAT RAN AND FOUND NOTHING",
});

/** Human-readable labels for the reason codes. Falls back to the server's text. */
const REASON_LABELS = Object.freeze({
  NOT_ATTEMPTED: "NO CLASSIFIER RAN",
  INSUFFICIENT_WINDOW: "WINDOW TOO SHORT",
  CHANNELIZATION_FAILED: "CHANNEL NOT ISOLATED",
  NO_SYMBOL_CLOCK: "NO SYMBOL CLOCK FOUND",
  CONSTANT_ENVELOPE: "CONSTANT ENVELOPE · BLIND SPOT",
  NOISE_COMPATIBLE: "NOISE COMPATIBLE",
  STALE_WINDOW: "VERDICT WINDOW DID NOT COVER THE DETECTION",
  ANALOGUE_DETECTOR_NOT_IMPLEMENTED: "ANALOGUE CLAIM REFUSED · NO DETECTOR",
  METHOD_NOT_REGISTERED: "CLAIM REFUSED · METHOD NOT REGISTERED",
  METHOD_NOT_VALIDATED: "CLAIM REFUSED · METHOD NOT VALIDATED",
  DECISION_RULE_NOT_MET: "CLAIM REFUSED · DID NOT PASS ITS DECISION RULE",
  UNQUALIFIED_CLAIM: "CLAIM REFUSED · EVIDENCE INCOMPLETE",
  SYMBOL_CLOCK_LIKE_FEATURE: "SYMBOL-CLOCK-LIKE FEATURE · DIGITAL STRUCTURE SUPPORTED",
});

export function reasonLabel(code, serverText = null) {
  const key = String(code ?? "").trim().toUpperCase();
  return REASON_LABELS[key] ?? (serverText ? String(serverText) : key || "UNSPECIFIED");
}

/**
 * Reads the classifier block beside the counters.
 *
 * `implemented` is deliberately false-by-default: a build that forgets to
 * declare its classifier must not read as one that has a working detector.
 */
export function deriveClassifierState(status = {}) {
  const block = status?.observations?.classifier ?? null;
  if (!block || typeof block !== "object") {
    return {declared: false, implemented: false, ...CLASSIFIER_UNDECLARED,
            analogueDetector: "UNDECLARED", analogueNote: null,
            digitalReachable: false, digitalReachableNote: null, validatedMethods: [],
            claimsWithheld: [], claimableFamilies: [], reservedFamilies: []};
  }
  const state = String(block.state ?? "UNDECLARED").toUpperCase();
  const analogueDetector = String(block.analogue_detector ?? "UNDECLARED").toUpperCase();
  return {
    declared: true,
    implemented: state === "IMPLEMENTED",
    state,
    phase: String(block.contract_phase ?? ""),
    headline: `CLASSIFIER STATE // ${state}`,
    note: String(block.state_note ?? ""),
    analogueDetector,
    analogueNote: block.analogue_detector_note ? String(block.analogue_detector_note) : null,
    // A DIGITAL verdict needs a registered method that has passed Phase 3
    // validation. Until one has, the panel says the route is closed rather than
    // letting a zero imply the band was simply quiet.
    digitalReachable: block.digital_reachable === true,
    digitalReachableNote: block.digital_reachable_note
      ? String(block.digital_reachable_note) : null,
    validatedMethods: Array.isArray(block.validated_methods)
      ? block.validated_methods.map(String) : [],
    claimsWithheld: Array.isArray(block.claims_withheld) ? block.claims_withheld.map(String) : [],
    claimableFamilies: Array.isArray(block.claimable_families)
      ? block.claimable_families.map(String) : [],
    reservedFamilies: Array.isArray(block.reserved_families)
      ? block.reserved_families.map(String) : [],
    digitalEvidenceRequired: Array.isArray(block.digital_evidence_required)
      ? block.digital_evidence_required.map(String) : [],
  };
}

/** Reason breakdown behind the unclassified count, largest first. */
export function deriveOutcomeBreakdown(status = {}) {
  const counts = status?.observations?.classification_reasons ?? null;
  const descriptions = status?.observations?.classifier?.reason_codes ?? {};
  if (!counts || typeof counts !== "object") return [];
  return Object.entries(counts)
    .map(([code, value]) => ({
      code: String(code).toUpperCase(),
      count: Math.max(0, finite(value) ?? 0),
      label: reasonLabel(code, descriptions?.[code]),
    }))
    .filter((row) => row.count > 0)
    .sort((a, b) => b.count - a.count || a.code.localeCompare(b.code));
}

/**
 * The line the panel and the ticker both render.
 *
 * When nothing has been classified and no classifier is running, the reason
 * replaces the counters rather than sitting beside them — a zero that explains
 * itself is evidence, and a bare zero is an ambiguity.
 */
export function classificationOutcomeLines(summary, classifier, breakdown = []) {
  if (!summary?.available) return [`RF DETECTIONS // ${summary?.note ?? "COUNTS UNAVAILABLE"}`];
  const lines = [
    `RF DETECTIONS // DIGITAL ${summary.digital} · ANALOGUE ${summary.analogue} · `
      + `UNCLASSIFIED ${summary.unclassified} · RETAINED EVENTS ${summary.total}`,
  ];
  if (!classifier?.implemented) {
    lines.push(classifier?.declared
      ? `${classifier.headline} · ${classifier.note}`
      : `${CLASSIFIER_UNDECLARED.headline} · ${CLASSIFIER_UNDECLARED.note}`);
  }
  if (classifier?.declared && !classifier.digitalReachable) {
    lines.push(`DIGITAL VERDICT // UNREACHABLE · ${classifier.digitalReachableNote
      ?? "NO REGISTERED METHOD HAS PASSED VALIDATION"}`);
  }
  if (classifier?.analogueDetector !== "IMPLEMENTED") {
    lines.push(`ANALOGUE DETECTOR // ${classifier?.analogueDetector ?? "UNDECLARED"}`
      + (classifier?.analogueNote ? ` · ${classifier.analogueNote}` : ""));
  }
  if (breakdown.length) {
    lines.push(`UNCLASSIFIED BECAUSE // ${breakdown
      .map((row) => `${row.label} ${row.count}`).join(" · ")}`);
  }
  lines.push(`${summary.note} · A FAMILY INFERENCE DOES NOT REPLACE AN ESTIMATOR OUTCOME`);
  return lines;
}
