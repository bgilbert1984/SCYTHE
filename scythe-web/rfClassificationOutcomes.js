/**
 * Phase 0 rendering contract for RF signal characterisation outcomes.
 *
 * The panel used to show `DIGITAL 0 · ANALOGUE 0 · UNCLASSIFIED 0` and leave the
 * reader to guess which of two very different things it meant: that the band was
 * quiet, or that nothing had looked. This module makes the zeros state which.
 *
 * The server now publishes three independent axes rather than one label:
 *
 *   MODULATION             what the carrier is doing — no detector runs
 *   INFORMATION STRUCTURE  whether a symbol clock is present — the Phase 2 axis
 *   PROTOCOL               which protocol, if any — no decoder runs
 *
 * DIGITAL and ANALOGUE survive only as derived summaries of the first two, and
 * the panel labels them as derived so a reader does not mistake a summary for an
 * observation. Rendering the axes matters: `UNCLASSIFIED 4` now has a legible
 * decomposition instead of being a single opaque bucket.
 *
 * Three absences are rendered rather than hidden:
 *
 *   CLASSIFIER NOT IMPLEMENTED   no channelizer and no symbol-clock detector
 *                                are running, so every detection is unclassified
 *                                by construction and not by measurement.
 *   ANALOGUE DETECTOR MISSING    ANALOGUE is never inferred from the absence of
 *                                a symbol clock. Constant-envelope digital modes
 *                                look identical to FM voice under an
 *                                envelope-based test.
 *   MODULATION / PROTOCOL        two axes with no detector and no decoder, so
 *                                both are pinned at UNRESOLVED.
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
  NO_SYMBOL_CLOCK_DETECTED: "NO SYMBOL CLOCK FOUND",
  CONSTANT_ENVELOPE: "CONSTANT ENVELOPE · BLIND SPOT",
  NOISE_COMPATIBLE: "NOISE COMPATIBLE",
  STALE_WINDOW: "VERDICT WINDOW DID NOT COVER THE DETECTION",
  FAMILY_NOT_DIRECTLY_CLAIMABLE: "SUMMARY SUBMITTED AS AN OBSERVATION · REFUSED",
  ANALOGUE_DETECTOR_NOT_IMPLEMENTED: "ANALOGUE CLAIM REFUSED · NO DETECTOR",
  MODULATION_DETECTOR_NOT_IMPLEMENTED: "MODULATION CLAIM REFUSED · NO DETECTOR",
  PROTOCOL_HYPOTHESIS_NOT_IMPLEMENTED: "PROTOCOL CANDIDATE REFUSED · NO HYPOTHESIS SOURCE",
  DECODER_NOT_IMPLEMENTED: "PROTOCOL CONFIRMATION REFUSED · NO DECODER",
  METHOD_WRONG_AXIS: "CLAIM REFUSED · METHOD REGISTERED FOR ANOTHER AXIS",
  METHOD_NOT_REGISTERED: "CLAIM REFUSED · METHOD NOT REGISTERED",
  METHOD_NOT_VALIDATED: "CLAIM REFUSED · METHOD NOT VALIDATED",
  DECISION_RULE_NOT_MET: "CLAIM REFUSED · DID NOT PASS ITS DECISION RULE",
  UNQUALIFIED_CLAIM: "CLAIM REFUSED · EVIDENCE INCOMPLETE",
  SYMBOL_CLOCK_LIKE_FEATURE: "SYMBOL-CLOCK-LIKE FEATURE · DIGITAL STRUCTURE SUPPORTED",
});

/** Axis display names, in the order the panel reads them. */
export const AXIS_LABELS = Object.freeze({
  modulation: "MODULATION",
  information_structure: "INFORMATION STRUCTURE",
  protocol: "PROTOCOL",
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
 * Per-axis counts for the retained detections, with the two undetectable axes
 * marked as such.
 *
 * An axis whose detector does not exist is not reporting UNRESOLVED as a finding.
 * It is reporting that nothing looked, and the row says so rather than letting a
 * full UNRESOLVED count read as a measurement that came back inconclusive.
 */
export function deriveAxisBreakdown(status = {}) {
  const counts = status?.observations?.signal_axes ?? null;
  const declared = status?.observations?.classifier?.axes ?? {};
  if (!counts || typeof counts !== "object") return [];
  return Object.keys(AXIS_LABELS)
    .filter((axis) => counts[axis] && typeof counts[axis] === "object")
    .map((axis) => {
      const block = declared?.[axis] ?? {};
      const claimable = Array.isArray(block.claimable) ? block.claimable : [];
      const values = Object.entries(counts[axis])
        .map(([value, n]) => ({value: String(value), count: Math.max(0, finite(n) ?? 0)}))
        .filter((row) => row.count > 0)
        .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
      return {
        axis,
        label: AXIS_LABELS[axis],
        values,
        // False unless the server says otherwise: an axis with no declared
        // detector must not read as one that ran and found nothing.
        detectable: claimable.length > 0,
        detectorNote: block.detector_note ?? block.decoder_note ?? block.hypothesis_note ?? null,
      };
    });
}

export function axisBreakdownLines(axisBreakdown = []) {
  return axisBreakdown.map((row) => {
    const counts = row.values.length
      ? row.values.map((v) => `${v.value} ${v.count}`).join(" · ")
      : "NO RETAINED DETECTIONS";
    return `${row.label} // ${counts}`
      + (row.detectable ? "" : " · NO DETECTOR RUNS FOR THIS AXIS");
  });
}

/**
 * The line the panel and the ticker both render.
 *
 * When nothing has been classified and no classifier is running, the reason
 * replaces the counters rather than sitting beside them — a zero that explains
 * itself is evidence, and a bare zero is an ambiguity.
 */
export function classificationOutcomeLines(summary, classifier, breakdown = [],
                                           axisBreakdown = []) {
  if (!summary?.available) return [`RF DETECTIONS // ${summary?.note ?? "COUNTS UNAVAILABLE"}`];
  const lines = [
    `RF DETECTIONS // DIGITAL ${summary.digital} · ANALOGUE ${summary.analogue} · `
      + `UNCLASSIFIED ${summary.unclassified} · RETAINED EVENTS ${summary.total}`,
    // The counters above are a summary of the axes below, and saying so stops a
    // reader treating DIGITAL as something the receiver observed directly.
    "FAMILY COUNTS // DERIVED SUMMARY OF THE AXES BELOW · NOT PRIMARY OBSERVATIONS",
  ];
  lines.push(...axisBreakdownLines(axisBreakdown));
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
