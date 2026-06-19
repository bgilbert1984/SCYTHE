/**
 * ProtocolFingerprintComponent.js — High-persistence behavioral protocol traits
 *
 * Attackers rotate IPs/tunnels; they rarely rotate timing grammar, TLS ordering,
 * DNS lexical rhythm, QUIC pacing, or burst morphology.
 *
 * Fused probabilistically into HostIdentity (not deterministic keys).
 */

function _fnv(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

class ProtocolFingerprintComponent {
  constructor(seed = {}) {
    this.tls_ja3_lineage = [...(seed.tls_ja3_lineage || [])];
    this.tls_ja4_lineage = [...(seed.tls_ja4_lineage || [])];
    this.quic_cadence = { ...(seed.quic_cadence || { rtt_ema: null, pacing_ema: null }) };
    this.dns_grammar = [...(seed.dns_grammar || [])];
    this.burst_morphology = { ...(seed.burst_morphology || { burst_ema: null, gap_ema: null }) };
    this.rf_spectral_habits = [...(seed.rf_spectral_habits || [])];
    this.fusion_confidence = seed.fusion_confidence ?? 0.5;
    this.last_updated_simTime = 0;
  }

  /**
   * @param {Object} evidence
   * @param {number} evidence.simTime
   * @param {Object} [evidence.tls] - { ja3, ja4, alpn }
   * @param {Object} [evidence.quic] - { rtt_ms, pacing_rate }
   * @param {Object} [evidence.dns] - { qname_pattern, qtype }
   * @param {Object} [evidence.burst] - { bytes, duration_ms, inter_arrival_ms }
   * @param {Object} [evidence.rf] - { spectral_peak_hz, bandwidth_hz }
   * @param {Object} [evidence.telemetry]
   */
  absorb(evidence = {}) {
    const { simTime = 0, tls, quic, dns, burst, rf, telemetry } = evidence;
    if (simTime) this.last_updated_simTime = simTime;

    if (tls?.ja3) {
      const h = _fnv(tls.ja3);
      if (!this.tls_ja3_lineage.includes(h)) {
        this.tls_ja3_lineage.push(h);
        if (this.tls_ja3_lineage.length > 12) this.tls_ja3_lineage.shift();
      }
    }
    if (tls?.ja4) {
      const h = _fnv(tls.ja4);
      if (!this.tls_ja4_lineage.includes(h)) {
        this.tls_ja4_lineage.push(h);
        if (this.tls_ja4_lineage.length > 12) this.tls_ja4_lineage.shift();
      }
    }

    if (quic) {
      const alpha = 0.15;
      if (quic.rtt_ms != null) {
        this.quic_cadence.rtt_ema = this.quic_cadence.rtt_ema == null
          ? quic.rtt_ms
          : this.quic_cadence.rtt_ema * (1 - alpha) + quic.rtt_ms * alpha;
      }
      if (quic.pacing_rate != null) {
        this.quic_cadence.pacing_ema = this.quic_cadence.pacing_ema == null
          ? quic.pacing_rate
          : this.quic_cadence.pacing_ema * (1 - alpha) + quic.pacing_rate * alpha;
      }
    }

    if (dns?.qname_pattern) {
      const gram = String(dns.qname_pattern).slice(0, 64);
      if (!this.dns_grammar.includes(gram)) {
        this.dns_grammar.push(gram);
        if (this.dns_grammar.length > 24) this.dns_grammar.shift();
      }
    }

    if (burst) {
      const alpha = 0.12;
      const rate = burst.duration_ms > 0 ? burst.bytes / burst.duration_ms : 0;
      this.burst_morphology.burst_ema = this.burst_morphology.burst_ema == null
        ? rate
        : this.burst_morphology.burst_ema * (1 - alpha) + rate * alpha;
      if (burst.inter_arrival_ms != null) {
        this.burst_morphology.gap_ema = this.burst_morphology.gap_ema == null
          ? burst.inter_arrival_ms
          : this.burst_morphology.gap_ema * (1 - alpha) + burst.inter_arrival_ms * alpha;
      }
    }

    if (telemetry?.rx_mbps != null && telemetry.rx_mbps > 0) {
      this.absorb({
        simTime,
        burst: {
          bytes: telemetry.rx_mbps * 125000,
          duration_ms: 1000,
          inter_arrival_ms: telemetry.inter_arrival_ms,
        },
      });
    }

    if (rf?.spectral_peak_hz != null) {
      const tag = `rf:${Math.round(rf.spectral_peak_hz / 1000)}k`;
      if (!this.rf_spectral_habits.includes(tag)) {
        this.rf_spectral_habits.push(tag);
        if (this.rf_spectral_habits.length > 16) this.rf_spectral_habits.shift();
      }
    }

    this.fusion_confidence = Math.min(1, this._computeFusionConfidence());
    return this;
  }

  _computeFusionConfidence() {
    let score = 0.2;
    if (this.tls_ja3_lineage.length) score += 0.2;
    if (this.tls_ja4_lineage.length) score += 0.15;
    if (this.quic_cadence.rtt_ema != null) score += 0.15;
    if (this.dns_grammar.length) score += 0.1;
    if (this.burst_morphology.burst_ema != null) score += 0.15;
    if (this.rf_spectral_habits.length) score += 0.1;
    return score;
  }

  /**
   * Probabilistic affinity [0,1] with another fingerprint (not binary match).
   */
  affinity(other) {
    if (!other) return 0;
    const ja3 = ProtocolFingerprintComponent._jaccard(
      this.tls_ja3_lineage,
      other.tls_ja3_lineage
    );
    const dns = ProtocolFingerprintComponent._jaccard(this.dns_grammar, other.dns_grammar);
    const rf = ProtocolFingerprintComponent._jaccard(
      this.rf_spectral_habits,
      other.rf_spectral_habits
    );
    let quic = 0;
    if (this.quic_cadence.rtt_ema != null && other.quic_cadence?.rtt_ema != null) {
      quic = 1 - Math.min(1, Math.abs(this.quic_cadence.rtt_ema - other.quic_cadence.rtt_ema) / 200);
    }
    return Math.min(1, ja3 * 0.35 + dns * 0.25 + quic * 0.2 + rf * 0.2);
  }

  static _jaccard(a, b) {
    if (!a?.length || !b?.length) return 0;
    const sa = new Set(a);
    const sb = new Set(b);
    let inter = 0;
    for (const x of sa) if (sb.has(x)) inter++;
    return inter / (sa.size + sb.size - inter);
  }

  toJSON() {
    return {
      tls_ja3_lineage: [...this.tls_ja3_lineage],
      tls_ja4_lineage: [...this.tls_ja4_lineage],
      quic_cadence: { ...this.quic_cadence },
      dns_grammar: [...this.dns_grammar],
      burst_morphology: { ...this.burst_morphology },
      rf_spectral_habits: [...this.rf_spectral_habits],
      fusion_confidence: this.fusion_confidence,
      last_updated_simTime: this.last_updated_simTime,
    };
  }
}

if (typeof window !== 'undefined') {
  window.ProtocolFingerprintComponent = ProtocolFingerprintComponent;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { ProtocolFingerprintComponent };
}
