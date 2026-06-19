/**
 * EcologicalShockEngine.js — Macro-perturbations for routing ecosystems
 *
 * Simulates systemic events (e.g., BGP leaks, cable cuts, policy shifts) that 
 * temporarily or permanently alter niche capacities and market pressures, forcing 
 * the ecosystem to adapt.
 */

class EcologicalShockEngine {
  constructor(nicheRegistry, fitnessEngine) {
    this.nicheRegistry = nicheRegistry;
    this.fitnessEngine = fitnessEngine;
    this.active_shocks = new Map(); // shock_id -> ShockEvent
    this.shock_history = [];
  }

  /**
   * Inject a macro-level shock into the ecosystem.
   * @param {Object} shockConfig 
   */
  injectShock(shockConfig) {
    const shock_id = `shock_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
    
    const shock = {
        shock_id,
        type: shockConfig.type || "unknown_shock",
        severity: Math.max(0.1, Math.min(1.0, shockConfig.severity || 0.5)),
        duration_ms: shockConfig.duration_ms || 3600000, // Default 1 hour
        propagation_vector: shockConfig.propagation_vector || "global",
        affected_niches: shockConfig.affected_niches || [],
        simTime_started: Date.now(),
        simTime_ends: Date.now() + (shockConfig.duration_ms || 3600000),
        status: "active",
        applied_deltas: []
    };

    this.active_shocks.set(shock_id, shock);
    this.shock_history.push(shock);
    if (this.shock_history.length > 200) this.shock_history.shift();

    // 1. Apply Capacity Shocks to Niches
    shock.affected_niches.forEach(niche_id => {
        const state = this.nicheRegistry.getNicheState(niche_id);
        if (state) {
            // e.g. A cable cut reduces capacity by up to 90% (based on severity)
            const capacityPenalty = 1.0 - (shock.severity * 0.9);
            const newCapacity = Math.floor(Math.max(10, state.capacity * capacityPenalty));
            
            shock.applied_deltas.push({
                target: 'niche_capacity',
                niche_id,
                old_val: state.capacity,
                new_val: newCapacity
            });
            
            this.nicheRegistry.shiftCapacity(niche_id, newCapacity, shock.simTime_started);
        }
    });

    // 2. Apply Exogenous Pressures to Fitness Landscape
    const pressureDeltas = {};
    if (shock.type.includes("policy") || shock.type.includes("filtering")) {
        pressureDeltas.policy_pressure = Math.max(0.1, 1.0 - shock.severity);
    }
    if (shock.type.includes("economic") || shock.type.includes("bankruptcy")) {
        pressureDeltas.economic_pressure = Math.max(0.1, 1.0 - shock.severity);
    }
    if (shock.type.includes("ddos") || shock.type.includes("congestion")) {
        pressureDeltas.congestion_pressure = 1.0 + shock.severity;
    }
    
    if (Object.keys(pressureDeltas).length > 0) {
        this.fitnessEngine.updateMarketPressures(pressureDeltas);
        shock.applied_deltas.push({ target: 'market_pressures', deltas: pressureDeltas });
    }

    return shock;
  }

  /**
   * Poll active shocks to revert expired ones.
   */
  decayShocks(simTime) {
      for (const [id, shock] of this.active_shocks.entries()) {
          if (simTime >= shock.simTime_ends) {
              shock.status = "expired";
              this.active_shocks.delete(id);
              
              // In a fully integrated system, this would reverse the capacity shifts
              // and normalize market pressures. For now, it marks them expired.
          }
      }
  }

  toJSON() {
      return {
          active_shocks_count: this.active_shocks.size,
          active_shocks: Array.from(this.active_shocks.values()),
          shock_history_summary: this.shock_history.slice(-5)
      };
  }
}

if (typeof window !== 'undefined') {
  window.EcologicalShockEngine = EcologicalShockEngine;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { EcologicalShockEngine };
}
