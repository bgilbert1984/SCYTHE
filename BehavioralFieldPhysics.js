/**
 * BehavioralFieldPhysics.js — Host scalar fields → GPU emitter parameters
 *
 * high entropy + high ingress pressure → repulsive deformation
 * trusted stable hosts → attraction basins
 * synchronized anomalies → standing-wave phase coupling
 */

const BehavioralFieldPhysics = {
  /**
   * @param {Object} host - BehavioralHost
   * @param {HostIdentity} identity
   * @param {Object} profile - host projector profile
   * @param {number} simTimeSec
   * @param {Array} neighbors - other hosts for coupling (optional)
   */
  sample(host, identity, profile, simTimeSec, neighbors = []) {
    const metrics = host.computeAggregateMetrics();
    const risk = host.signature.computeRisk();
    const trust = host.trust.score;
    const volatility = host.signature.role_volatility;
    const entropy = metrics.entropy_avg || identity?.entropy_baseline || 0;
    const pressure = metrics.total_rx_mbps;

    const pressureNorm = Math.min(1, pressure / 400);
    const entropyNorm = Math.min(1, entropy);

    const repulsion = Math.min(1, pressureNorm * entropyNorm * (1.2 - trust));
    const cohesion = Math.min(1, trust * (1 - volatility) * (identity?.stabilityScore?.() ?? 0.5));
    const standingWave = Math.sin(simTimeSec * 0.4 + (identity?.host_hash?.length ?? 0)) * risk;

    let coupledAnomaly = 0;
    if (neighbors.length > 0) {
      const syncCount = neighbors.filter((n) => n.risk > 0.55 && Math.abs(n.phase - standingWave) < 0.5).length;
      coupledAnomaly = Math.min(1, syncCount / Math.max(3, neighbors.length));
    }

    const intensity = Math.min(1.5,
      pressureNorm * 0.45 +
      risk * 0.35 +
      repulsion * 0.25 +
      coupledAnomaly * 0.2
    );

    const radius = 6000 + cohesion * 18000 - repulsion * 6000 + pressureNorm * 4000;
    const phase =
      (identity?.host_hash ? identity.host_hash.charCodeAt(4) || 0 : 0) * 0.01 +
      simTimeSec * (0.25 + volatility * 0.5) +
      standingWave;

    return {
      intensity,
      anomaly: Math.min(1, risk + repulsion * 0.3 + coupledAnomaly * 0.25),
      radius: Math.max(4000, radius),
      phase,
      meta: {
        trust_mass: trust,
        entropy_charge: entropy,
        ingress_pressure: pressure,
        temporal_heat: pressureNorm * entropyNorm,
        volatility,
        adversarial_confidence: risk,
        repulsion,
        cohesion,
        coupled_anomaly: coupledAnomaly,
      },
    };
  },
};

if (typeof window !== 'undefined') {
  window.BehavioralFieldPhysics = BehavioralFieldPhysics;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = BehavioralFieldPhysics;
}
