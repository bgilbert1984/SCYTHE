/**
 * CounterfactualUniverseEngine.js — Monte Carlo evolutionary forecasting
 *
 * Runs thousands of simulated futures by projecting the current ecosystem state
 * forward under probabilistic combinations of shocks, climate drift, and 
 * selection pressures. Outputs a probability distribution of future routing worlds.
 */

class CounterfactualUniverseEngine {
  constructor(forecastEngine, shockEngine, climateField, nicheRegistry) {
    this.forecastEngine = forecastEngine;
    this.shockEngine = shockEngine;
    this.climateField = climateField;
    this.nicheRegistry = nicheRegistry;
  }

  /**
   * Run a Monte Carlo simulation of the ecosystem's future.
   * @param {Object} options 
   * @param {number} [options.iterations=1000] - Number of simulated futures
   * @param {Array} [options.active_motifs] - Base state
   * @param {Array} [options.active_genomes] - Base state
   * @param {Array} [options.shock_candidates] - Potential events to sample from
   */
  simulateFutures(options = {}) {
    const iterations = options.iterations || 1000;
    const motifs = options.active_motifs || [];
    const genomes = options.active_genomes || [];
    const shockCandidates = options.shock_candidates || [
        { type: "submarine_cable_cut", severity: 0.8, probability: 0.05, target: "oceanic_crossing" },
        { type: "bgp_leak", severity: 0.6, probability: 0.1, target: "tier1_transit_backbone" },
        { type: "cdn_expansion", severity: 0.4, probability: 0.2, target: "anycast_edge" },
        { type: "regional_congestion", severity: 0.5, probability: 0.3, target: "regional_backbone" }
    ];

    const distribution = {
        extinctions: [],
        speciations: [],
        climate_states: new Map(),
        niche_collapses: new Map(),
        emergent_motifs: new Map()
    };

    const currentClimateState = this.climateField.climate_state || "stable";

    for (let i = 0; i < iterations; i++) {
        // 1. Sample probabilistic shocks for this timeline
        const timelineShocks = shockCandidates.filter(s => Math.random() <= s.probability);
        
        // 2. Clone basic ecosystem stats to mutate
        let simExtinctions = 0;
        let simSpeciations = 0;
        let simClimate = currentClimateState;
        
        timelineShocks.forEach(shock => {
            // Very simplified mock of shock impact for Monte Carlo speed
            if (shock.severity > 0.7) {
                simExtinctions += Math.floor(Math.random() * 40);
                simClimate = "carrier_storm";
                
                const collapseKey = `${shock.target}_collapse`;
                distribution.niche_collapses.set(collapseKey, (distribution.niche_collapses.get(collapseKey) || 0) + 1);
            } else if (shock.severity > 0.4) {
                simExtinctions += Math.floor(Math.random() * 15);
                simSpeciations += Math.floor(Math.random() * 20);
                if (shock.type === "cdn_expansion") simClimate = "anycast_migration";
            }
        });

        // 3. Extrapolate background evolutionary pressure (baseline drift)
        genomes.forEach(g => {
            // Simplified approximation: if genome stability is low, it might speciate or die
            if (g.stability_score < 0.4) {
                if (Math.random() < 0.3) simExtinctions++;
                else if (Math.random() < 0.4) simSpeciations++;
            }
        });

        // 4. Record Timeline Outcome
        distribution.extinctions.push(simExtinctions);
        distribution.speciations.push(simSpeciations);
        distribution.climate_states.set(simClimate, (distribution.climate_states.get(simClimate) || 0) + 1);
    }

    return this._compileDistribution(distribution, iterations);
  }

  _compileDistribution(dist, iterations) {
      // Helper to calculate P50, P90, etc.
      const calcPercentiles = (arr) => {
          if (!arr.length) return { p50: 0, p90: 0, p99: 0 };
          arr.sort((a, b) => a - b);
          return {
              mean: arr.reduce((a,b) => a+b, 0) / arr.length,
              p50: arr[Math.floor(arr.length * 0.50)],
              p90: arr[Math.floor(arr.length * 0.90)],
              p99: arr[Math.floor(arr.length * 0.99)],
          };
      };

      const normalizeMap = (map) => {
          const res = {};
          for (const [k, v] of map.entries()) {
              res[k] = parseFloat((v / iterations).toFixed(3));
          }
          return res;
      };

      return {
          iterations_run: iterations,
          predicted_extinction_distribution: calcPercentiles(dist.extinctions),
          predicted_speciation_distribution: calcPercentiles(dist.speciations),
          probable_climate_states: normalizeMap(dist.climate_states),
          probable_niche_collapses: normalizeMap(dist.niche_collapses),
          simTime_generated: Date.now()
      };
  }
}

if (typeof window !== 'undefined') {
  window.CounterfactualUniverseEngine = CounterfactualUniverseEngine;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CounterfactualUniverseEngine };
}
