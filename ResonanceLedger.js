/**
 * ResonanceLedger.js — Persistent resonance memory (frequency-band aware)
 *
 * Resonance classes:
 *   immediate, delayed, harmonic, suppressed, anti_correlated, inverted, phase_shifted
 */

const RESONANCE_TYPES = Object.freeze({
  IMMEDIATE: 'immediate',
  DELAYED: 'delayed',
  HARMONIC: 'harmonic',
  SUPPRESSED: 'suppressed',
  ANTI_CORRELATED: 'anti_correlated',
  INVERTED: 'inverted',
  PHASE_SHIFTED: 'phase_shifted',
  SHARED_TRANSIT_DEPENDENCY: 'shared_transit_dependency',
});

class ResonanceLedger {
  constructor(opts = {}) {
    this.decayHalfLifeMs = opts.decayHalfLifeMs ?? 900_000;
    this.affinityThreshold = opts.affinityThreshold ?? 0.55;
    this.maxPairs = opts.maxPairs ?? 2000;

    this._pairs = new Map();
    this._lesionSensitivity = new Map();
    this._bandHistory = [];
  }

  _pairKey(a, b) {
    return a < b ? `${a}|${b}` : `${b}|${a}`;
  }

  /**
   * Primary API — frequency-band aware resonance record.
   *
   * @param {Object} record
   * @param {string} record.host_a
   * @param {string} record.host_b
   * @param {string} record.type - RESONANCE_TYPES
   * @param {number} [record.lag_ms]
   * @param {number} [record.coherence] - [0,1]
   * @param {number} [record.simTime]
   * @param {string} [record.lesion_label]
   */
  record(entry = {}) {
    const {
      host_a: hostA,
      host_b: hostB,
      type = RESONANCE_TYPES.IMMEDIATE,
      lag_ms = 0,
      coherence = 0.5,
      simTime = 0,
      lesion_label = null,
    } = entry;

    if (!hostA || !hostB) return null;

    const key = this._pairKey(hostA, hostB);
    const rec = this._pairs.get(key) ?? {
      host_a: hostA < hostB ? hostA : hostB,
      host_b: hostA < hostB ? hostB : hostA,
      co_divergence_count: 0,
      bands: {},
      cumulative_confidence: 0,
      last_simTime: 0,
      lesion_labels: [],
      first_seen_simTime: simTime,
    };

    if (!rec.bands[type]) {
      rec.bands[type] = { hits: 0, coherence_sum: 0, lag_ema: null };
    }
    const band = rec.bands[type];
    band.hits++;
    band.coherence_sum += coherence;
    if (lag_ms > 0) {
      band.lag_ema = band.lag_ema == null
        ? lag_ms
        : band.lag_ema * 0.8 + lag_ms * 0.2;
    }

    rec.co_divergence_count++;
    rec.cumulative_confidence = Math.min(
      1,
      rec.cumulative_confidence * 0.85 + coherence * 0.15
    );
    rec.last_simTime = simTime || rec.last_simTime;
    if (lesion_label && !rec.lesion_labels.includes(lesion_label)) {
      rec.lesion_labels.push(lesion_label);
      if (rec.lesion_labels.length > 16) rec.lesion_labels.shift();
    }

    this._pairs.set(key, rec);
    this._bandHistory.push({ type, host_a: hostA, host_b: hostB, simTime, coherence });
    if (this._bandHistory.length > 500) this._bandHistory.shift();
    this._prune();
    return rec;
  }

  recordCoDivergence(hostA, hostB, evidence = {}) {
    return this.record({
      host_a: hostA,
      host_b: hostB,
      type: evidence.type ?? RESONANCE_TYPES.HARMONIC,
      lag_ms: evidence.lag_ms ?? 0,
      coherence: evidence.confidence ?? 0.5,
      simTime: evidence.simTime,
      lesion_label: evidence.lesion_label,
    });
  }

  recordLesionSensitivity(hostId, lesionLabel, simTime, magnitude = 0.5) {
    if (!this._lesionSensitivity.has(hostId)) {
      this._lesionSensitivity.set(hostId, {});
    }
    const m = this._lesionSensitivity.get(hostId);
    m[lesionLabel] = {
      count: (m[lesionLabel]?.count ?? 0) + 1,
      last_magnitude: magnitude,
      last_simTime: simTime,
    };
  }

  ingestShockwave(shockwave, meta = {}) {
    if (!shockwave) return;

    const label = meta.label ?? 'unnamed_lesion';
    const simTime = meta.simTime ?? 0;
    const lesionFamily = meta.lesion_family ?? 'event';

    for (const c of shockwave.hidden_affinity_candidates ?? []) {
      this.record({
        host_a: c.host_a,
        host_b: c.host_b,
        type: lesionFamily === 'suppressed' ? RESONANCE_TYPES.SUPPRESSED : RESONANCE_TYPES.HARMONIC,
        coherence: c.confidence,
        simTime,
        lesion_label: label,
      });
    }

    for (const b of shockwave.identity_bifurcation ?? []) {
      this.recordLesionSensitivity(b.host_id, label, simTime, b.magnitude ?? 0.5);
    }

    const waves = shockwave.shockwave_front?.waves ?? [];
    for (let i = 1; i < waves.length; i++) {
      const prev = waves[i - 1];
      const curr = waves[i];
      const lag = (curr.ts ?? 0) - (prev.ts ?? 0);
      if (lag > 50 && prev.entity_id && curr.entity_id && prev.entity_id !== curr.entity_id) {
        this.record({
          host_a: prev.entity_id.split('/')[0] || prev.entity_id,
          host_b: curr.entity_id.split('/')[0] || curr.entity_id,
          type: RESONANCE_TYPES.PHASE_SHIFTED,
          lag_ms: lag,
          coherence: 0.55,
          simTime,
          lesion_label: label,
        });
      }
    }
  }

  getAffinity(hostA, hostB) {
    const rec = this._pairs.get(this._pairKey(hostA, hostB));
    if (!rec) return 0;
    const countBoost = Math.min(1, rec.co_divergence_count / 8);
    let bandBoost = 0;
    for (const band of Object.values(rec.bands)) {
      bandBoost += Math.min(0.15, band.hits * 0.03);
    }
    return Math.min(1, rec.cumulative_confidence * countBoost + bandBoost);
  }

  getBandProfile(hostA, hostB) {
    const rec = this._pairs.get(this._pairKey(hostA, hostB));
    if (!rec) return null;
    const profile = {};
    for (const [type, band] of Object.entries(rec.bands)) {
      profile[type] = {
        hits: band.hits,
        avg_coherence: band.hits ? band.coherence_sum / band.hits : 0,
        lag_ema_ms: band.lag_ema,
      };
    }
    return profile;
  }

  getOperationalAffinities(minScore) {
    const threshold = minScore ?? this.affinityThreshold;
    const out = [];
    for (const rec of this._pairs.values()) {
      const score = this.getAffinity(rec.host_a, rec.host_b);
      if (score >= threshold) {
        const dominantBand = Object.entries(rec.bands).sort(
          (a, b) => b[1].hits - a[1].hits
        )[0]?.[0];
        out.push({
          host_a: rec.host_a,
          host_b: rec.host_b,
          affinity_score: score,
          dominant_resonance: dominantBand,
          band_profile: this.getBandProfile(rec.host_a, rec.host_b),
          lineage_hypothesis: score > 0.75 ? 'operational_kinship' : 'recurring_resonance',
        });
      }
    }
    out.sort((a, b) => b.affinity_score - a.affinity_score);
    return out;
  }

  decay(simTime) {
    for (const [key, rec] of this._pairs) {
      const age = simTime - rec.last_simTime;
      if (age <= 0) continue;
      const factor = Math.pow(0.5, age / this.decayHalfLifeMs);
      rec.cumulative_confidence *= factor;
      if (rec.cumulative_confidence < 0.05) this._pairs.delete(key);
    }
  }

  _prune() {
    if (this._pairs.size <= this.maxPairs) return;
    const sorted = [...this._pairs.entries()].sort(
      (a, b) => a[1].cumulative_confidence - b[1].cumulative_confidence
    );
    for (let i = 0; i < sorted.length - this.maxPairs; i++) {
      this._pairs.delete(sorted[i][0]);
    }
  }

  export() {
    return {
      pair_count: this._pairs.size,
      operational_affinities: this.getOperationalAffinities(),
      lesion_sensitivity: Object.fromEntries(this._lesionSensitivity),
      resonance_types: RESONANCE_TYPES,
    };
  }
}

if (typeof window !== 'undefined') {
  window.ResonanceLedger = ResonanceLedger;
  window.RESONANCE_TYPES = RESONANCE_TYPES;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ResonanceLedger, RESONANCE_TYPES };
}
