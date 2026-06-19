/**
 * LineageGenomeMemory.js — Ancestral tracking for evolutionary forecasting
 *
 * Stores the historical behavior of a lineage (family of routes), allowing the
 * forecast engine to say "This lineage historically mutates toward anycast"
 * or "This lineage repeatedly colonizes oceanic niches."
 */

class LineageGenomeMemory {
  constructor() {
    this.lineages = new Map(); // lineage_id -> MemoryObject
  }

  _getOrCreateLineage(lineage_id) {
    if (!this.lineages.has(lineage_id)) {
      this.lineages.set(lineage_id, {
        lineage_id,
        divergence_count: 0,
        extinction_count: 0,
        route_count: 0,
        total_lifespan_sum: 0,
        preferred_mutation_vectors: new Map(), // vector -> count
        niche_history: new Map(), // niche_id -> count
        first_seen_simTime: Date.now()
      });
    }
    return this.lineages.get(lineage_id);
  }

  /**
   * Logs a route's entire lifespan into its lineage memory upon extinction.
   */
  recordExtinction(routeGenome, simTime) {
      if (!routeGenome || !routeGenome.lineage) return;
      
      const lineage = this._getOrCreateLineage(routeGenome.lineage);
      lineage.extinction_count++;
      
      const lifespan = simTime - routeGenome.first_seen_simTime;
      if (lifespan > 0) {
          lineage.total_lifespan_sum += lifespan;
          lineage.route_count++;
      }
  }

  /**
   * Logs a mutation event (divergence or speciation) to track lineage tendencies.
   */
  recordMutation(routeGenome, divergenceEvent) {
      if (!routeGenome || !routeGenome.lineage || !divergenceEvent) return;
      
      const lineage = this._getOrCreateLineage(routeGenome.lineage);
      lineage.divergence_count++;

      // Track mutation vectors
      if (divergenceEvent.divergence) {
          const div = divergenceEvent.divergence;
          if (div.anycast_shift) this._incrementMap(lineage.preferred_mutation_vectors, 'anycast_shift');
          if (div.transit_mutation) this._incrementMap(lineage.preferred_mutation_vectors, 'transit_mutation');
          if (div.latency_shift > 0.4) this._incrementMap(lineage.preferred_mutation_vectors, 'latency_shift');
      }
  }

  /**
   * Logs when a lineage colonizes a new niche.
   */
  recordNicheColonization(routeGenome, niche_id) {
      if (!routeGenome || !routeGenome.lineage || !niche_id) return;
      const lineage = this._getOrCreateLineage(routeGenome.lineage);
      this._incrementMap(lineage.niche_history, niche_id);
  }

  _incrementMap(map, key) {
      map.set(key, (map.get(key) || 0) + 1);
  }

  /**
   * Retrieves the historical behavioral profile of a lineage.
   */
  getLineageProfile(lineage_id) {
      const lineage = this.lineages.get(lineage_id);
      if (!lineage) return null;

      const avgLifespan = lineage.route_count > 0 ? lineage.total_lifespan_sum / lineage.route_count : 0;
      
      // Sort maps for output
      const preferredMutations = [...lineage.preferred_mutation_vectors.entries()].sort((a,b) => b[1]-a[1]).map(e => e[0]);
      const dominantNiches = [...lineage.niche_history.entries()].sort((a,b) => b[1]-a[1]).map(e => e[0]);

      return {
          lineage_id: lineage.lineage_id,
          divergence_rate: lineage.route_count > 0 ? lineage.divergence_count / lineage.route_count : 0,
          extinction_rate: lineage.route_count > 0 ? lineage.extinction_count / lineage.route_count : 0,
          average_lifespan: avgLifespan,
          preferred_mutation_vectors: preferredMutations,
          dominant_niches: dominantNiches
      };
  }
}

if (typeof window !== 'undefined') {
  window.LineageGenomeMemory = LineageGenomeMemory;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { LineageGenomeMemory };
}
