/**
 * RouteForecastEngine.js — Predictive layer for evolutionary routing dynamics
 *
 * Transitions the architecture from descriptive (what happened?) to predictive
 * (what will happen next?). Forecasts RouteGenome divergence, extinction, and 
 * speciation events using historical stability, Motif ecology, and global Climate.
 */

class RouteForecastEngine {
  constructor() {
    this.forecasts = new Map(); // route_id -> Forecast
    this.evaluation_ledger = []; // Historic log to evaluate accuracy
    
    // Dynamic weights learned over time from forecast errors
    this.weights = {
      global_turbulence_penalty: 0.3,
      global_stability_bonus: 0.1,
      motif_fragility_penalty: 0.4,
      motif_resilience_bonus: 0.2,
      local_turbulence_penalty: 0.4,
      genome_instability_penalty: 0.3,
      ancestral_bonus: 0.2,
      learning_rate: 0.05
    };
  }

  /**
   * Generates a forecast for a given RouteGenome.
   * @param {RouteGenome} genome 
   * @param {TransitMotifGenome} parentMotif 
   * @param {RouteClimateField} climate 
   * @param {ResonanceLedger} ledger 
   * @param {LineageGenomeMemory} lineageMemory
   * @param {Object} selectionPressures (e.g. { congestion_pressure: 0.8, maintenance_pressure: 0.1 })
   */
  generateForecast(genome, parentMotif, climate, ledger, lineageMemory, selectionPressures = {}) {
    if (!genome || !genome.carrier_markers) return null;

    let divergenceProbability = 0;
    let extinctionProbability = 0;
    let speciationProbability = 0;
    let confidence = 0.5;
    const supportingFactors = [];
    const appliedWeights = {};
    const explanatoryPressures = [];

    // 0. Incorporate Lineage Memory (Historical tendencies)
    if (lineageMemory && genome.lineage) {
        const lineageProfile = lineageMemory.getLineageProfile(genome.lineage);
        if (lineageProfile) {
            if (lineageProfile.extinction_rate < 0.05 && lineageProfile.average_lifespan > 50000000) {
                extinctionProbability -= 0.1;
                supportingFactors.push("Lineage historically extinction-resistant");
            }
            if (lineageProfile.divergence_rate > 0.4) {
                divergenceProbability += 0.1;
                supportingFactors.push("Lineage historically prone to divergence");
            }
        }
    }

    // 0.5 Incorporate Environmental Selection Pressures
    if (selectionPressures.congestion_pressure > 0.5) {
        divergenceProbability += 0.2;
        explanatoryPressures.push(`Congestion pressure (${selectionPressures.congestion_pressure.toFixed(2)}) driving divergence.`);
    }
    if (selectionPressures.cable_outage_pressure > 0.3) {
        extinctionProbability += 0.3;
        explanatoryPressures.push(`Cable outage pressure (${selectionPressures.cable_outage_pressure.toFixed(2)}) driving extinction risk.`);
    }

    // 1. Incorporate Global/Regional Climate Pressure
    const zoneClimate = climate?.getClimateForZone ? climate.getClimateForZone(genome.phenotype || 'unknown_zone') : null;
    const localClimateTurbulence = zoneClimate?.turbulence_index || climate?.turbulence_index || 0;
    
    if (localClimateTurbulence > 0.6) {
        divergenceProbability += this.weights.global_turbulence_penalty;
        extinctionProbability += this.weights.global_turbulence_penalty * 0.7;
        supportingFactors.push("High regional turbulence (climate pressure)");
        appliedWeights.global_turbulence_penalty = true;
    } else if (localClimateTurbulence < 0.2) {
        divergenceProbability -= this.weights.global_stability_bonus;
        supportingFactors.push("Stable regional climate");
        appliedWeights.global_stability_bonus = true;
    }

    // 2. Incorporate Motif Ecology (Niche health)
    if (parentMotif) {
        if (parentMotif.climate?.lesion_resilience < 0.5) {
            extinctionProbability += this.weights.motif_fragility_penalty;
            supportingFactors.push("Parent motif structurally fragile");
            appliedWeights.motif_fragility_penalty = true;
        } else if (parentMotif.climate?.lesion_resilience > 0.8) {
            extinctionProbability -= this.weights.motif_resilience_bonus;
            supportingFactors.push("Parent motif highly resilient");
            appliedWeights.motif_resilience_bonus = true;
        }

        if (parentMotif.route_count > 100) {
            speciationProbability += 0.2;
            supportingFactors.push("Overcrowded transit motif (speciation likely)");
        }
    }

    // 3. Incorporate Genome-Specific Traits
    const localTurbulence = genome.carrier_markers.rdi_turbulence || 0;
    const stability = genome.stability_score || 0.1;
    const persistence = genome.route_persistence_score || 0;

    if (localTurbulence > 0.4) {
        divergenceProbability += this.weights.local_turbulence_penalty;
        speciationProbability += this.weights.local_turbulence_penalty * 0.75;
        supportingFactors.push("Severe local route turbulence");
        appliedWeights.local_turbulence_penalty = true;
    }

    if (stability < 0.3) {
        divergenceProbability += this.weights.genome_instability_penalty;
        extinctionProbability += this.weights.genome_instability_penalty;
        supportingFactors.push("Historically unstable genome");
        appliedWeights.genome_instability_penalty = true;
    } else if (stability > 0.8 && persistence > 1000) {
        divergenceProbability -= this.weights.ancestral_bonus;
        extinctionProbability -= this.weights.ancestral_bonus;
        speciationProbability += 0.1; // Highly stable routes spawn variants
        supportingFactors.push("Ancestral persistence anchor");
        appliedWeights.ancestral_bonus = true;
    }

    // Bound probabilities
    divergenceProbability = Math.max(0.01, Math.min(0.99, divergenceProbability));
    extinctionProbability = Math.max(0.01, Math.min(0.99, extinctionProbability));
    speciationProbability = Math.max(0.01, Math.min(0.99, speciationProbability));

    confidence = Math.min(0.99, (genome.stability_score * 0.5) + (parentMotif ? 0.3 : 0) + 0.1);

    const forecast = {
      route_id: genome.route_id,
      simTime_generated: Date.now(),
      predicted_divergence_probability: divergenceProbability,
      predicted_extinction_probability: extinctionProbability,
      predicted_speciation_probability: speciationProbability,
      applied_weights: appliedWeights,
      confidence,
      supporting_factors: supportingFactors,
      explanatory_pressures: explanatoryPressures, // "Why" it's mutating
      status: "pending_validation"
    };

    this.forecasts.set(genome.route_id, forecast);
    return forecast;
  }

  /**
   * Evaluates a previously made forecast against reality, and updates internal weights via backpropagation.
   * @param {string} route_id 
   * @param {Object} event (e.g. ROUTE_GENOME_DIVERGENCE or ROUTE_EXTINCTION)
   */
  evaluateForecast(route_id, event) {
      const forecast = this.forecasts.get(route_id);
      if (!forecast || forecast.status !== "pending_validation") return;

      let score = 0; // 0 to 1 accuracy
      let outcome = "unknown";
      let error = 0; // Directional error for weight adjustment

      if (event.type === 'ROUTE_GENOME_DIVERGENCE') {
          outcome = "diverged";
          score = forecast.predicted_divergence_probability;
          error = 1.0 - score; // If it diverged, we wanted a 1.0 prediction
      } else if (event.type === 'ROUTE_EXTINCTION') {
          outcome = "extinct";
          score = forecast.predicted_extinction_probability;
          error = 1.0 - score;
      } else if (event.type === 'ROUTE_GENOME_SPECIATION') {
          outcome = "speciated";
          score = forecast.predicted_speciation_probability;
          error = 1.0 - score;
      } else if (event.type === 'ROUTE_STABLE_OBSERVATION') {
          // If the route was just observed and is stable, our predictions of divergence should have been low
          outcome = "survived";
          score = 1.0 - Math.max(forecast.predicted_divergence_probability, forecast.predicted_extinction_probability);
          error = - (Math.max(forecast.predicted_divergence_probability, forecast.predicted_extinction_probability));
      }

      // Backpropagate error to adjust weights
      if (forecast.applied_weights) {
          const lr = this.weights.learning_rate;
          for (const [weightKey, applied] of Object.entries(forecast.applied_weights)) {
              if (applied && this.weights[weightKey] !== undefined) {
                  // If we underestimated the event (positive error), increase the penalties that contributed
                  // If we overestimated (negative error), decrease those penalties
                  this.weights[weightKey] += (error * lr);
                  // Bound weights to reasonable limits
                  this.weights[weightKey] = Math.max(0.01, Math.min(1.0, this.weights[weightKey]));
              }
          }
      }

      // Record in ledger
      forecast.status = "evaluated";
      forecast.actual_outcome = outcome;
      forecast.accuracy_score = score;
      forecast.post_evaluation_weights = { ...this.weights };

      this.evaluation_ledger.push(forecast);
      
      // Keep ledger bounded
      if (this.evaluation_ledger.length > 1000) {
          this.evaluation_ledger.shift();
      }

      this.forecasts.delete(route_id);
      return score;
  }

  /**
   * Counterfactual Forecast: What happens if a specific lesion is applied to the ecosystem?
   * Evaluates cascading motif collapses and predicts ecosystem shifts.
   */
  counterfactual(lesion, activeMotifs, currentClimate) {
      if (!lesion || !lesion.target) return null;

      let predicted_extinctions = 0;
      let predicted_speciations = 0;
      let impacted_motifs = [];
      let emergent_motifs = [];

      activeMotifs.forEach(motif => {
          const sim = motif.simulateMotifCollapse(lesion.target);
          if (!sim.survived) {
              impacted_motifs.push(motif.motif_id);
              // Motif collapse forces extinction/speciation of dependent routes
              predicted_extinctions += Math.floor(sim.impacted_routes * 0.7);
              predicted_speciations += Math.ceil(sim.impacted_routes * 0.3);
              emergent_motifs.push(`${lesion.target}_Replacement_Niche_${motif.phenotype}`);
          }
      });

      let expected_climate_state = currentClimate?.climate_state || "stable";
      if (predicted_extinctions > 50) {
          expected_climate_state = "carrier_storm";
      } else if (predicted_speciations > 30) {
          expected_climate_state = "rapid_speciation_phase";
      }

      return {
          lesion_simulated: lesion,
          impacted_motifs,
          predicted_extinctions,
          predicted_speciations,
          expected_climate_state,
          emergent_motifs,
          confidence: Math.min(0.9, 0.5 + (this.evaluation_ledger.length / 2000)) // Confidence grows as engine learns
      };
  }

  getGlobalAccuracy() {
      if (this.evaluation_ledger.length === 0) return 0;
      const total = this.evaluation_ledger.reduce((acc, f) => acc + (f.accuracy_score || 0), 0);
      return total / this.evaluation_ledger.length;
  }
}

if (typeof window !== 'undefined') {
  window.RouteForecastEngine = RouteForecastEngine;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { RouteForecastEngine };
}
