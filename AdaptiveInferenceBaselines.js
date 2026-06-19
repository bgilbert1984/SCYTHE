/**
 * AdaptiveInferenceBaselines.js — EMA baselines + probabilistic event confidence
 *
 * Replaces static spike/entropy thresholds with per-entity adaptive baselines.
 */

class AdaptiveInferenceBaselines {
  constructor(opts = {}) {
    this.emaAlpha = opts.emaAlpha ?? 0.12;
    this.minSamples = opts.minSamples ?? 4;
    this.defaultSpikeRatio = opts.defaultSpikeRatio ?? 0.5;
    this.defaultEntropyShift = opts.defaultEntropyShift ?? 0.25;
    this._entities = new Map();
    /** entity_id → cohort_id */
    this._entityCohort = new Map();
    /** cohort_id → running stats */
    this._cohorts = new Map();
    /** role archetype → aggregate stats */
    this._archetypes = new Map();
  }

  registerCohort(cohortId, entityIds = []) {
    if (!this._cohorts.has(cohortId)) {
      this._cohorts.set(cohortId, { rx_sum: 0, rx_n: 0, entropy_sum: 0, entropy_n: 0, members: new Set() });
    }
    const c = this._cohorts.get(cohortId);
    for (const id of entityIds) {
      this._entityCohort.set(id, cohortId);
      c.members.add(id);
    }
  }

  registerArchetype(role, telemetry = {}) {
    const key = String(role || 'unknown').toLowerCase();
    if (!this._archetypes.has(key)) {
      this._archetypes.set(key, { rx_ema: null, entropy_ema: null, n: 0 });
    }
    const a = this._archetypes.get(key);
    const rx = Number(telemetry.rx_mbps) || 0;
    const entropy = telemetry.spectral_entropy;
    a.rx_ema = a.rx_ema == null ? rx : a.rx_ema * (1 - this.emaAlpha) + rx * this.emaAlpha;
    if (entropy != null) {
      a.entropy_ema = a.entropy_ema == null
        ? entropy
        : a.entropy_ema * (1 - this.emaAlpha) + entropy * this.emaAlpha;
    }
    a.n++;
  }

  _get(entity_id) {
    if (!this._entities.has(entity_id)) {
      this._entities.set(entity_id, {
        rx_ema: null,
        rx_var_ema: 0,
        entropy_ema: null,
        sample_count: 0,
      });
    }
    return this._entities.get(entity_id);
  }

  /**
   * Ingest observation; returns adaptive thresholds for inference.
   */
  observe(entity_id, telemetry = {}) {
    const st = this._get(entity_id);
    const rx = Number(telemetry.rx_mbps) || 0;
    const entropy = telemetry.spectral_entropy;

    if (st.rx_ema == null) {
      st.rx_ema = rx;
    } else {
      const delta = Math.abs(rx - st.rx_ema);
      st.rx_var_ema = st.rx_var_ema * (1 - this.emaAlpha) + delta * this.emaAlpha;
      st.rx_ema = st.rx_ema * (1 - this.emaAlpha) + rx * this.emaAlpha;
    }

    if (entropy != null) {
      st.entropy_ema = st.entropy_ema == null
        ? entropy
        : st.entropy_ema * (1 - this.emaAlpha) + entropy * this.emaAlpha;
    }

    st.sample_count++;

    const cohortId = this._entityCohort.get(entity_id);
    if (cohortId && this._cohorts.has(cohortId)) {
      const c = this._cohorts.get(cohortId);
      c.rx_sum += rx;
      c.rx_n++;
      if (entropy != null) {
        c.entropy_sum += entropy;
        c.entropy_n++;
      }
    }

    if (telemetry.role) {
      this.registerArchetype(telemetry.role, telemetry);
    }

    return st;
  }

  /**
   * Baseline families: self vs cohort vs role archetype.
   * @returns {{ self, cohort, archetype, blended_score, interpretation }}
   */
  assessDeviation(entity_id, telemetry = {}) {
    const st = this._entities.get(entity_id);
    const rx = Number(telemetry.rx_mbps) || 0;
    const entropy = telemetry.spectral_entropy;
    const role = String(telemetry.role || 'unknown').toLowerCase();

    const selfRx = st?.rx_ema ?? rx;
    const selfDev = Math.abs(rx - selfRx) / Math.max(selfRx, 0.1);

    let cohortDev = 0;
    const cohortId = this._entityCohort.get(entity_id);
    if (cohortId && this._cohorts.has(cohortId)) {
      const c = this._cohorts.get(cohortId);
      const cohortRx = c.rx_n ? c.rx_sum / c.rx_n : rx;
      cohortDev = Math.abs(rx - cohortRx) / Math.max(cohortRx, 0.1);
    }

    let archetypeDev = 0;
    const arch = this._archetypes.get(role);
    if (arch?.rx_ema != null) {
      archetypeDev = Math.abs(rx - arch.rx_ema) / Math.max(arch.rx_ema, 0.1);
    }

    const blended = Math.min(1, selfDev * 0.5 + cohortDev * 0.3 + archetypeDev * 0.2);

    let interpretation = 'normal_for_self';
    if (selfDev < 0.3 && cohortDev > 0.8) {
      interpretation = 'normal_for_self_abnormal_for_cohort';
    } else if (selfDev > 0.8 && cohortDev < 0.4) {
      interpretation = 'abnormal_for_self_normal_for_cohort';
    } else if (archetypeDev > 0.9) {
      interpretation = 'abnormal_for_role_archetype';
    }

    return {
      self: selfDev,
      cohort: cohortDev,
      archetype: archetypeDev,
      blended_score: blended,
      interpretation,
      cohort_id: cohortId ?? null,
      role,
    };
  }

  getSpikeThreshold(entity_id) {
    const st = this._entities.get(entity_id);
    if (!st || st.sample_count < this.minSamples) {
      return this.defaultSpikeRatio;
    }
    const volatility = st.rx_var_ema / Math.max(st.rx_ema, 0.1);
    return Math.min(2.5, Math.max(0.25, this.defaultSpikeRatio + volatility * 0.35));
  }

  getEntropyShiftThreshold(entity_id) {
    const st = this._entities.get(entity_id);
    if (!st || st.sample_count < this.minSamples) {
      return this.defaultEntropyShift;
    }
    return Math.min(0.6, Math.max(0.1, this.defaultEntropyShift * 0.85));
  }

  /**
   * P(event is significant | observation) — simple logistic-style score.
   */
  spikeConfidence(entity_id, prevRx, currRx) {
    const st = this._entities.get(entity_id);
    const baseline = st?.rx_ema ?? prevRx ?? 1;
    const delta = (currRx - (prevRx ?? baseline)) / Math.max(baseline, 0.01);
    const threshold = this.getSpikeThreshold(entity_id);
    if (delta <= threshold) return 0;

    const excess = (delta - threshold) / (threshold + 0.5);
    return Math.min(1, 0.55 + excess * 0.35 + (st?.sample_count > 10 ? 0.1 : 0));
  }

  entropyShiftConfidence(entity_id, prevE, currE) {
    const delta = Math.abs(currE - prevE);
    const threshold = this.getEntropyShiftThreshold(entity_id);
    if (delta <= threshold) return 0;
    return Math.min(1, 0.5 + (delta - threshold) * 1.2);
  }
}

if (typeof window !== 'undefined') {
  window.AdaptiveInferenceBaselines = AdaptiveInferenceBaselines;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { AdaptiveInferenceBaselines };
}
