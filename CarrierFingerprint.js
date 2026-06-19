/**
 * CarrierFingerprint.js — Persistent behavioral transit traits
 *
 * Models carrier influence fields, routing policies, and anycast affinities.
 * Equivalent to ProtocolFingerprintComponent but for transit infrastructure.
 */

function _jaccard(a, b) {
  if (!a?.length || !b?.length) return 0;
  const sa = new Set(a);
  const sb = new Set(b);
  let inter = 0;
  for (const x of sa) if (sb.has(x)) inter++;
  return inter / (sa.size + sb.size - inter);
}

class CarrierFingerprint {
  constructor(seed = {}) {
    this.transit_sequence = [...(seed.transit_sequence || [])];
    this.recurring_asns = [...(seed.recurring_asns || [])];
    this.rdi_samples = [...(seed.rdi_samples || [])];
    this.rdi_shell = { ...(seed.rdi_shell || { p25: null, p50: null, p75: null, p95: null }) };
    this.anycast_behavior = [...(seed.anycast_behavior || [])];
    this.recurrence_score = seed.recurrence_score ?? 0;
    this.last_updated_simTime = 0;
  }

  /**
   * Absorb routing evidence to build the carrier fingerprint.
   * @param {Object} evidence
   * @param {number} evidence.simTime
   * @param {Array} [evidence.sequence] - Array of transit nodes/IPs
   * @param {Array} [evidence.asns] - Array of ASN strings
   * @param {number} [evidence.rdi] - Routing Distance Index
   * @param {boolean} [evidence.is_anycast] - Flag indicating anycast migration
   */
  absorb(evidence = {}) {
    const { simTime = 0, sequence, asns, rdi, is_anycast } = evidence;
    if (simTime) this.last_updated_simTime = simTime;

    if (sequence && sequence.length > 0) {
        // Track unique transit structures (e.g. twelve99 backbone)
        const seqStr = sequence.join('->');
        if (!this.transit_sequence.includes(seqStr)) {
            this.transit_sequence.push(seqStr);
            if (this.transit_sequence.length > 8) this.transit_sequence.shift();
        }
    }

    if (asns && asns.length > 0) {
        asns.forEach(asn => {
            if (!this.recurring_asns.includes(asn)) {
                this.recurring_asns.push(asn);
                if (this.recurring_asns.length > 16) this.recurring_asns.shift();
            }
        });
    }

    if (rdi != null) {
        this.rdi_samples.push(rdi);
        if (this.rdi_samples.length > 64) this.rdi_samples.shift();

        // Calculate RDI Shell Percentiles
        if (this.rdi_samples.length > 3) {
            const sorted = [...this.rdi_samples].sort((a, b) => a - b);
            const getP = (p) => sorted[Math.floor(sorted.length * p)];
            this.rdi_shell = {
                p25: getP(0.25),
                p50: getP(0.50),
                p75: getP(0.75),
                p95: getP(0.95)
            };
        }
    }

    if (is_anycast) {
        const tag = sequence && sequence.length > 0 ? sequence[sequence.length - 1] : 'unknown_edge';
        if (!this.anycast_behavior.includes(tag)) {
            this.anycast_behavior.push(tag);
            if (this.anycast_behavior.length > 8) this.anycast_behavior.shift();
        }
    }

    this.recurrence_score = Math.min(1.0, this.recurrence_score + 0.05);

    return this;
  }

  /**
   * Computes the "route weather" or turbulence based on latency shell width.
   */
  get rdi_turbulence() {
    if (this.rdi_shell.p50 == null || this.rdi_shell.p50 === 0) return 0;
    return (this.rdi_shell.p95 - this.rdi_shell.p25) / this.rdi_shell.p50;
  }

  /**
   * Probabilistic affinity [0,1] with another carrier fingerprint.
   */
  affinity(other) {
    if (!other) return 0;
    
    const seqOverlap = _jaccard(this.transit_sequence, other.transit_sequence);
    const asnOverlap = _jaccard(this.recurring_asns, other.recurring_asns);
    
    let rdiScore = 0;
    if (this.rdi_shell.p50 != null && other.rdi_shell?.p50 != null) {
        rdiScore = 1 - Math.min(1, Math.abs(this.rdi_shell.p50 - other.rdi_shell.p50) / 100);
    }
    
    return Math.min(1.0, seqOverlap * 0.4 + asnOverlap * 0.4 + rdiScore * 0.2);
  }

  toJSON() {
    return {
      transit_sequence: [...this.transit_sequence],
      recurring_asns: [...this.recurring_asns],
      rdi_shell: { ...this.rdi_shell },
      anycast_behavior: [...this.anycast_behavior],
      recurrence_score: this.recurrence_score,
      last_updated_simTime: this.last_updated_simTime
    };
  }
}

if (typeof window !== 'undefined') {
  window.CarrierFingerprint = CarrierFingerprint;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CarrierFingerprint };
}
