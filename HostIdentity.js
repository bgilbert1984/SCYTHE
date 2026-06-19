/**
 * HostIdentity.js — Persistent behavioral identity fabric
 *
 * Identity accumulates memory from telemetry + events; it is not recreated each poll.
 * Events mutate identity; identity informs inference weighting and field physics.
 */

function _fnv1a32(str) {
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

class HostIdentity {
  /**
   * @param {string} host_id
   * @param {Object} seed
   */
  constructor(host_id, seed = {}) {
    this.host_id = host_id;
    this.host_hash = seed.host_hash ?? HostIdentity.computeHostHash(host_id);

    this.mac_lineage = [...(seed.mac_lineage || [])];
    this.transport_signature = [...(seed.transport_signature || [])];
    this.timing_fingerprint = { ...(seed.timing_fingerprint || {}) };
    this.entropy_baseline = seed.entropy_baseline ?? null;
    this.jitter_profile = { ...(seed.jitter_profile || { samples: [], variance: 0 }) };
    this.vpn_affinity = seed.vpn_affinity ?? 0;
    this.protocol_dna = [...(seed.protocol_dna || [])];
    const Pfp = typeof ProtocolFingerprintComponent !== 'undefined' ? ProtocolFingerprintComponent : null;
    this.protocol_fingerprints = Pfp
      ? (seed.protocol_fingerprints instanceof Pfp
        ? seed.protocol_fingerprints
        : new Pfp(seed.protocol_fingerprints || {}))
      : (seed.protocol_fingerprints || {});

    this.speciation_subtype = seed.speciation_subtype ?? null;

    this.first_seen_simTime = seed.first_seen_simTime ?? 0;
    this.last_mutated_simTime = seed.last_mutated_simTime ?? 0;
    this.mutation_count = seed.mutation_count ?? 0;
  }

  static computeHostHash(host_id) {
    return `hid_${_fnv1a32(String(host_id)).toString(16).padStart(8, '0')}`;
  }

  /**
   * Absorb telemetry and/or semantic events at simulation time.
   */
  mutateFromEvidence(evidence = {}) {
    const {
      simTime = 0,
      iface = null,
      telemetry = null,
      event = null,
      protocol = null,
    } = evidence;

    if (protocol && this.protocol_fingerprints) {
      this.protocol_fingerprints.absorb({ simTime, ...protocol });
    }

    if (simTime > 0) {
      if (!this.first_seen_simTime) this.first_seen_simTime = simTime;
      this.last_mutated_simTime = simTime;
    }

    if (iface) {
      const role = String(iface.role || '').toLowerCase();
      if (role && !this.transport_signature.includes(role)) {
        this.transport_signature.push(role);
        if (this.transport_signature.length > 32) this.transport_signature.shift();
      }

      const mac = iface.mac_address || iface.mac;
      if (mac && !this.mac_lineage.includes(mac)) {
        this.mac_lineage.push(mac);
        if (this.mac_lineage.length > 16) this.mac_lineage.shift();
      }

      if (['mesh_vpn', 'vpn', 'tun', 'wg', 'tailscale'].some((r) => role.includes(r))) {
        this.vpn_affinity = Math.min(1, this.vpn_affinity + 0.08);
      }
    }

    if (telemetry) {
      const rx = Number(telemetry.rx_mbps) || 0;
      const role = String(telemetry.role || '').toUpperCase();
      if (role && !this.protocol_dna.includes(role)) {
        this.protocol_dna.push(role);
        if (this.protocol_dna.length > 24) this.protocol_dna.shift();
      }

      const entropy = telemetry.spectral_entropy;
      if (entropy != null) {
        if (this.entropy_baseline == null) {
          this.entropy_baseline = entropy;
        } else {
          this.entropy_baseline = this.entropy_baseline * 0.92 + entropy * 0.08;
        }
      }

      const prevMean = this.timing_fingerprint.rx_mean ?? rx;
      this.timing_fingerprint.rx_mean = prevMean * 0.85 + rx * 0.15;
      this.timing_fingerprint.rx_peak = Math.max(this.timing_fingerprint.rx_peak ?? 0, rx);

      const delta = Math.abs(rx - prevMean);
      const samples = this.jitter_profile.samples || [];
      samples.push(delta);
      if (samples.length > 64) samples.shift();
      this.jitter_profile.samples = samples;
      if (samples.length > 2) {
        const mean = samples.reduce((a, b) => a + b, 0) / samples.length;
        const variance = samples.reduce((a, b) => a + (b - mean) ** 2, 0) / samples.length;
        this.jitter_profile.variance = variance;
      }
    }

    if (event) {
      this.mutation_count++;
      const tag = `${event.type}:${event.entity_id}`;
      if (!this.protocol_dna.includes(tag)) {
        this.protocol_dna.push(tag);
        if (this.protocol_dna.length > 48) this.protocol_dna.shift();
      }

      if (event.type === 'ROLE_CHANGE' && event.value?.to) {
        const to = String(event.value.to).toLowerCase();
        if (!this.transport_signature.includes(to)) {
          this.transport_signature.push(to);
        }
      }

      if (event.type === 'ENTROPY_SHIFT' && event.value?.curr_entropy != null) {
        const e = event.value.curr_entropy;
        this.entropy_baseline = this.entropy_baseline == null
          ? e
          : this.entropy_baseline * 0.9 + e * 0.1;
      }
    }

    return this;
  }

  /**
   * Stability score [0,1] — higher = more established identity.
   */
  stabilityScore() {
    const lineage = Math.min(1, this.mac_lineage.length / 4);
    const transport = Math.min(1, this.transport_signature.length / 6);
    const mutations = Math.min(1, this.mutation_count / 50);
    return Math.min(1, lineage * 0.3 + transport * 0.3 + mutations * 0.4);
  }

  toJSON() {
    return {
      host_id: this.host_id,
      host_hash: this.host_hash,
      mac_lineage: [...this.mac_lineage],
      transport_signature: [...this.transport_signature],
      timing_fingerprint: { ...this.timing_fingerprint },
      entropy_baseline: this.entropy_baseline,
      jitter_profile: {
        variance: this.jitter_profile.variance,
        sample_count: (this.jitter_profile.samples || []).length,
      },
      vpn_affinity: this.vpn_affinity,
      protocol_dna: [...this.protocol_dna],
      protocol_fingerprints: this.protocol_fingerprints?.toJSON?.(),
      speciation_subtype: this.speciation_subtype,
      first_seen_simTime: this.first_seen_simTime,
      last_mutated_simTime: this.last_mutated_simTime,
      mutation_count: this.mutation_count,
      stability: this.stabilityScore(),
    };
  }
}

if (typeof window !== 'undefined') {
  window.HostIdentity = HostIdentity;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { HostIdentity };
}
