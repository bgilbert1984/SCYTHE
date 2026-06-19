/**
 * FitnessLandscapeEngine.js — Dynamic environmental fitness evaluation
 *
 * Separates intrinsic route survival traits from environmental viability.
 * A route's fitness is constantly shifting based on niche demand, climate, 
 * and exogenous market/policy pressures, even if the route itself never changes.
 */

class FitnessLandscapeEngine {
  constructor() {
    this.landscape_history = [];
    this.current_pressures = {
        economic_pressure: 1.0,
        policy_pressure: 1.0,
        congestion_pressure: 1.0
    };
  }

  /**
   * Update the exogenous global/regional pressures acting on the landscape.
   */
  updateMarketPressures(pressures = {}) {
      this.current_pressures = { ...this.current_pressures, ...pressures };
  }

  /**
   * Evaluates the Darwinian fitness of a RouteGenome within its current environment.
   * @param {RouteGenome} genome 
   * @param {Object} nicheState (from RouteNicheRegistry)
   * @param {Object} climateZone (from RouteClimateField)
   */
  evaluateFitness(genome, nicheState, climateZone) {
    if (!genome) return null;

    // Initialize fitness history array on the genome if it doesn't exist
    if (!genome.fitness_history) genome.fitness_history = [];

    // 1. Survivability (Intrinsic baseline from Genome)
    // How well has this route survived its own historical lesions and time?
    const survivability = Math.max(0.1, genome.route_persistence_score || 0.1);

    // 2. Niche Demand (Environmental Capacity)
    let niche_demand = 1.0;
    if (nicheState && nicheState.capacity > 0) {
        // If occupancy is well below capacity, demand for routes is high.
        // If overcrowded, fitness suffers a severe penalty (competition).
        const saturation = nicheState.occupancy / nicheState.capacity;
        niche_demand = Math.max(0.1, 1.5 - saturation); 
    }

    // 3. Climate Compatibility
    let climate_compatibility = 1.0;
    if (climateZone) {
        const state = climateZone.climate_state;
        const stability = genome.stability_score || 0;
        
        if (state === 'carrier_storm' && stability < 0.5) {
            climate_compatibility = 0.4; // Weak routes die in storms
        } else if (state === 'carrier_storm' && stability >= 0.8) {
            climate_compatibility = 1.3; // Highly stable routes become hyper-fit during storms (safe havens)
        } else if (state === 'stable_backbone' && stability > 0.8) {
            climate_compatibility = 1.1; 
        } else if (state === 'anycast_migration' && genome.anycast_affinity?.length > 0) {
            climate_compatibility = 1.4; // Anycast routes thrive during migrations
        }
    }

    // 4. Exogenous Pressures (Market / Policy / Economics)
    // In a mature system, these are fed from SelectionMarket.js
    const economic = this.current_pressures.economic_pressure;
    const policy = this.current_pressures.policy_pressure;

    // The grand unified fitness equation
    const environmental_fitness = survivability * niche_demand * climate_compatibility * economic * policy;

    // 5. Ecological Derivatives
    let fitness_velocity = 0;
    let fitness_acceleration = 0;

    if (genome.fitness_history.length > 0) {
        const lastFit = genome.fitness_history[genome.fitness_history.length - 1];
        fitness_velocity = environmental_fitness - lastFit.fitness;
        
        if (genome.fitness_history.length > 1) {
            const prevFit = genome.fitness_history[genome.fitness_history.length - 2];
            const prevVelocity = lastFit.fitness - prevFit.fitness;
            fitness_acceleration = fitness_velocity - prevVelocity;
        }
    }

    genome.fitness_history.push({
        fitness: environmental_fitness,
        velocity: fitness_velocity,
        acceleration: fitness_acceleration,
        simTime: Date.now()
    });

    if (genome.fitness_history.length > 50) genome.fitness_history.shift();

    const evaluation = {
        route_id: genome.route_id,
        environmental_fitness,
        fitness_velocity,
        fitness_acceleration,
        components: {
            survivability,
            niche_demand,
            climate_compatibility,
            economic_pressure: economic,
            policy_pressure: policy
        },
        simTime_evaluated: Date.now()
    };

    return evaluation;
  }
}

if (typeof window !== 'undefined') {
  window.FitnessLandscapeEngine = FitnessLandscapeEngine;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { FitnessLandscapeEngine };
}
