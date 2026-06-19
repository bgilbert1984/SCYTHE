/**
 * FieldInferenceCoupling.js — Fields influence inference confidence (not just visualization)
 *
 * Spatial coherence and synchronized anomaly waves alter host affinity and
 * event confidence multipliers.
 */

const FieldInferenceCoupling = {
  /**
   * @param {Object} host
   * @param {HostIdentity} identity
   * @param {Object} fieldSample - from BehavioralFieldPhysics
   * @param {Array} neighbors - { host_id, risk, phase, field }
   * @returns {{ confidenceMultiplier, affinityDelta, inferred_edges }}
   */
  couple(host, identity, fieldSample, neighbors = []) {
    const meta = fieldSample?.meta ?? {};
    const repulsion = meta.repulsion ?? 0;
    const cohesion = meta.cohesion ?? 0;
    const coupled = meta.coupled_anomaly ?? 0;

    let confidenceMultiplier = 1;
    confidenceMultiplier *= 1 - repulsion * 0.25;
    confidenceMultiplier *= 1 + cohesion * 0.15;
    confidenceMultiplier *= 1 + coupled * 0.2;
    confidenceMultiplier = Math.max(0.35, Math.min(1.5, confidenceMultiplier));

    const affinityDelta = cohesion * 0.1 - repulsion * 0.15;

    const inferred_edges = [];
    if (coupled > 0.4) {
      for (const n of neighbors) {
        if (n.risk > 0.5) {
          inferred_edges.push({
            target: n.host_id,
            relationship: 'field_resonance',
            confidence: Math.min(1, coupled * n.risk),
          });
        }
      }
    }

    return {
      confidenceMultiplier,
      affinityDelta,
      inferred_edges,
      standing_wave: coupled > 0.55,
    };
  },

  /**
   * Apply coupling to an event before host projection.
   */
  adjustEventConfidence(event, coupling) {
    if (!event || !coupling) return event;
    const adjusted = { ...event, confidence: Math.min(1, (event.confidence ?? 1) * coupling.confidenceMultiplier) };
    return adjusted;
  },
};

if (typeof window !== 'undefined') {
  window.FieldInferenceCoupling = FieldInferenceCoupling;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = FieldInferenceCoupling;
}
