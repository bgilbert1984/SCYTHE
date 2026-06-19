/**
 * EpistemicConfidenceGraph.js — Formal epistemics for beliefs
 *
 * Tracks why the system believes X, how reversible, what falsifies it.
 */

class EpistemicHypothesis {
  constructor(id, claim, opts = {}) {
    this.id = id;
    this.claim = claim;
    this.confidence = opts.confidence ?? 0.5;
    this.created_simTime = opts.created_simTime ?? 0;
    this.last_reinforced_simTime = this.created_simTime;
    this.lesion_survival_count = 0;
    this.lesion_falsification_count = 0;
    this.contradiction_exposure = 0;
    this.replay_consistency = 1;
    this.resonance_support = 0;
    this.narrative_entropy = opts.narrative_entropy ?? 0.5;
    this.alternate_explanations = opts.alternate_explanations ?? [];
    this.evidence_refs = [];
    this.falsified = false;
  }

  reinforce(delta = 0.05, ref = null) {
    this.confidence = Math.min(1, this.confidence + delta);
    if (ref) this.evidence_refs.push(ref);
    return this;
  }

  exposeContradiction(weight = 0.1) {
    this.contradiction_exposure += weight;
    this.confidence = Math.max(0, this.confidence - weight);
    return this;
  }

  recordLesionSurvival() {
    this.lesion_survival_count++;
    this.reinforce(0.03, { type: 'lesion_survival' });
    return this;
  }

  recordLesionFalsification() {
    this.lesion_falsification_count++;
    this.confidence = Math.max(0, this.confidence - 0.15);
    if (this.lesion_falsification_count >= 2) this.falsified = true;
    return this;
  }

  addAlternate(explanation, confidence) {
    this.alternate_explanations.push({ explanation, confidence });
    this.narrative_entropy = Math.min(1, this.narrative_entropy + 0.08);
    return this;
  }

  brittleness() {
    const survival = this.lesion_survival_count + 1;
    const falsify = this.lesion_falsification_count + 1;
    return Math.min(1, falsify / survival + this.contradiction_exposure + this.narrative_entropy * 0.3);
  }

  toJSON() {
    return {
      id: this.id,
      claim: this.claim,
      confidence: this.confidence,
      brittleness: this.brittleness(),
      lesion_survival_count: this.lesion_survival_count,
      lesion_falsification_count: this.lesion_falsification_count,
      falsified: this.falsified,
      narrative_entropy: this.narrative_entropy,
      alternate_count: this.alternate_explanations.length,
      evidence_count: this.evidence_refs.length,
    };
  }
}

class EpistemicConfidenceGraph {
  constructor() {
    this.hypotheses = new Map();
    this._nextId = 1;
  }

  assert(claim, opts = {}) {
    const id = opts.id ?? `hyp_${this._nextId++}`;
    let h = this.hypotheses.get(id);
    if (!h) {
      h = new EpistemicHypothesis(id, claim, opts);
      this.hypotheses.set(id, h);
    }
    h.reinforce(opts.delta ?? 0.05, opts.ref);
    h.last_reinforced_simTime = opts.simTime ?? h.last_reinforced_simTime;
    return h;
  }

  /**
   * Update epistemics from multi-branch comparison.
   */
  ingestBranchComparison(comparison, simTime) {
    for (const evt of comparison.stable_causal_core ?? []) {
      const h = this.assert(`load_bearing:${evt.type}:${evt.entity_id}`, {
        simTime,
        confidence: evt.load_bearing_score ?? 0.6,
      });
      h.recordLesionSurvival();
      h.resonance_support = Math.min(1, (evt.branches_where_sensitive?.length ?? 0) / 3);
    }

    for (const [hostId, info] of Object.entries(comparison.branch_sensitive_identities ?? {})) {
      const h = this.assert(`branch_sensitive_identity:${hostId}`, { simTime, confidence: 0.45 });
      h.addAlternate(`sensitive_only_to:${info.sensitive_to}`, 0.4);
      if (info.type === 'unique_bifurcation') {
        h.recordLesionFalsification();
      }
    }

    for (const motif of comparison.invariant_motifs ?? []) {
      this.assert(`invariant_motif:${motif.motif}`, { simTime, confidence: 0.75 }).recordLesionSurvival();
    }

    for (const div of comparison.divergent_narratives ?? []) {
      const h = this.assert(`narrative:${div.narrative}`, { simTime, confidence: 0.4 });
      h.narrative_entropy = 0.7;
    }
  }

  /**
   * Anti-narrative pressure: inject uncertainty when confidence too high with few alternates.
   */
  applyUncertaintyPressure(maxConfidenceWithoutAlternates = 0.85) {
    for (const h of this.hypotheses.values()) {
      if (
        h.confidence > maxConfidenceWithoutAlternates &&
        h.alternate_explanations.length < 2
      ) {
        h.addAlternate('uncertainty_injection:sparse_evidence', 0.35);
        h.confidence *= 0.92;
      }
    }
  }

  getBeliefs(minConfidence = 0.4) {
    return [...this.hypotheses.values()]
      .filter((h) => !h.falsified && h.confidence >= minConfidence)
      .sort((a, b) => b.confidence - a.confidence)
      .map((h) => h.toJSON());
  }

  export() {
    return {
      hypothesis_count: this.hypotheses.size,
      beliefs: this.getBeliefs(),
      falsified: [...this.hypotheses.values()].filter((h) => h.falsified).map((h) => h.toJSON()),
    };
  }
}

if (typeof window !== 'undefined') {
  window.EpistemicConfidenceGraph = EpistemicConfidenceGraph;
  window.EpistemicHypothesis = EpistemicHypothesis;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { EpistemicConfidenceGraph, EpistemicHypothesis };
}
