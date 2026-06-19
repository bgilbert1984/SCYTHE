/**
 * SemanticGravity.js — Semantic mass bends future interpretation
 *
 * High-resonance lineages, lesion survivors, and stable causal cores attract
 * ambiguous future evidence. Counter-gravity injects uncertainty.
 */

class SemanticGravity {
  constructor(opts = {}) {
    this.wells = new Map();
    this.counterGravityStrength = opts.counterGravityStrength ?? 0.12;
    this.maxWells = opts.maxWells ?? 500;
  }

  /**
   * Update gravity well from host/lineage/resonance evidence.
   */
  updateWell(hostId, massDelta = 0, meta = {}) {
    const w = this.wells.get(hostId) ?? {
      host_id: hostId,
      mass: 0,
      lesion_survivals: 0,
      resonance_support: 0,
      causal_core_hits: 0,
      last_simTime: 0,
    };
    w.mass = Math.min(10, Math.max(0, w.mass + massDelta));
    if (meta.lesion_survival) w.lesion_survivals++;
    if (meta.resonance_support) w.resonance_support = Math.max(w.resonance_support, meta.resonance_support);
    if (meta.causal_core) w.causal_core_hits++;
    w.last_simTime = meta.simTime ?? w.last_simTime;
    this.wells.set(hostId, w);
    if (this.wells.size > this.maxWells) this._pruneWeakest();
    return w;
  }

  /**
   * Attraction [0,1] of ambiguous event/identity toward a well.
   */
  attraction(hostId, eventOrTelemetry = {}) {
    const w = this.wells.get(hostId);
    if (!w || w.mass < 0.1) return 0;
    let pull = Math.min(1, w.mass / 5);
    pull += w.lesion_survivals * 0.05;
    pull += w.causal_core_hits * 0.08;
    pull += w.resonance_support * 0.1;
    if (eventOrTelemetry.ambiguous) pull *= 1.2;
    return Math.min(1, pull);
  }

  /**
   * Counter-gravity: reduce over-confident attraction.
   */
  applyCounterGravity(hostId, baseConfidence) {
    const w = this.wells.get(hostId);
    if (!w) return baseConfidence;
    const damp = 1 - Math.min(0.25, w.mass * this.counterGravityStrength);
    return baseConfidence * damp;
  }

  ingestComparison(comparison, resonanceLedger) {
    for (const evt of comparison.stable_causal_core ?? []) {
      const host = evt.entity_id?.split?.('/')?.[0] ?? evt.entity_id;
      this.updateWell(host, 0.3, { causal_core: true, simTime: Date.now() });
    }
    for (const aff of resonanceLedger?.getOperationalAffinities?.(0.5) ?? []) {
      this.updateWell(aff.host_a, aff.affinity_score * 0.2, { resonance_support: aff.affinity_score });
      this.updateWell(aff.host_b, aff.affinity_score * 0.2, { resonance_support: aff.affinity_score });
    }
  }

  _pruneWeakest() {
    const sorted = [...this.wells.entries()].sort((a, b) => a[1].mass - b[1].mass);
    if (sorted.length) this.wells.delete(sorted[0][0]);
  }

  export() {
    return {
      well_count: this.wells.size,
      wells: [...this.wells.values()]
        .sort((a, b) => b.mass - a.mass)
        .slice(0, 50)
        .map((w) => ({ ...w })),
    };
  }
}

if (typeof window !== 'undefined') {
  window.SemanticGravity = SemanticGravity;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { SemanticGravity };
}
