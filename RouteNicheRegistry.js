/**
 * RouteNicheRegistry.js — Ecological Niches for Routing Infrastructure
 *
 * Models the functional roles (niches) that routes and motifs occupy, rather than
 * just their identities. Examples: 'tier1_backbone', 'anycast_edge', 'metro_transit'.
 * Tracks niche capacity, competition, and critical Niche Vacancy Events.
 */

class RouteNicheRegistry {
  constructor() {
    this.niches = new Map();
    this.vacancy_events = [];
    
    // Initialize default niches
    const defaultNiches = [
      'tier1_transit_backbone', 'oceanic_crossing', 'continental_core',
      'regional_backbone', 'metro_transit', 'hyperscaler_private_edge',
      'anycast_edge', 'turbulent_transit'
    ];
    
    defaultNiches.forEach(niche_id => {
      this.niches.set(niche_id, {
        niche_id,
        occupant_routes: new Set(),
        occupant_motifs: new Set(),
        capacity: 1000, // baseline environmental carrying capacity
        climate_sensitivity: 0.5,
        extinction_pressure: 0.1,
        competition_index: 0.0,
        historical_occupancy: 0
      });
    });
  }

  /**
   * Registers a RouteGenome into a specific niche based on its phenotype.
   */
  registerOccupant(genome, motif) {
    if (!genome || !genome.phenotype) return;
    
    // Strip version tag (e.g. "_v2") to get the base niche
    const baseNiche = genome.phenotype.split('_v')[0];
    
    if (!this.niches.has(baseNiche)) {
        this.niches.set(baseNiche, {
            niche_id: baseNiche,
            occupant_routes: new Set(),
            occupant_motifs: new Set(),
            capacity: 1000,
            climate_sensitivity: 0.5,
            extinction_pressure: 0.1,
            competition_index: 0.0,
            historical_occupancy: 0
        });
    }

    const niche = this.niches.get(baseNiche);
    niche.occupant_routes.add(genome.route_id);
    if (motif) niche.occupant_motifs.add(motif.motif_id);
    
    // Update competition index based on carrying capacity
    niche.competition_index = Math.min(1.0, niche.occupant_routes.size / Math.max(1, niche.capacity));
    niche.historical_occupancy = Math.max(niche.historical_occupancy, niche.occupant_routes.size);
  }

  /**
   * Externally alter a niche's carrying capacity (e.g., due to a new submarine cable or CDN explosion).
   * Emits NICHE_EXPANSION_EVENT or NICHE_CONTRACTION_EVENT.
   */
  shiftCapacity(niche_id, newCapacity, simTime = Date.now()) {
      const niche = this.niches.get(niche_id);
      if (!niche) return null;

      const oldCapacity = niche.capacity;
      niche.capacity = newCapacity;
      niche.competition_index = Math.min(1.0, niche.occupant_routes.size / Math.max(1, newCapacity));

      if (newCapacity > oldCapacity * 1.2) {
          return { type: 'NICHE_EXPANSION_EVENT', niche_id, old_capacity: oldCapacity, new_capacity: newCapacity, simTime };
      } else if (newCapacity < oldCapacity * 0.8) {
          return { type: 'NICHE_CONTRACTION_EVENT', niche_id, old_capacity: oldCapacity, new_capacity: newCapacity, simTime };
      }
      return null;
  }

  /**
   * Processes a route extinction, potentially triggering a Niche Vacancy Event.
   */
  recordExtinction(routeId, baseNiche, simTime) {
      if (!this.niches.has(baseNiche)) return null;
      
      const niche = this.niches.get(baseNiche);
      niche.occupant_routes.delete(routeId);
      
      // Update extinction pressure EMA
      niche.extinction_pressure = niche.extinction_pressure * 0.9 + 0.1;
      niche.competition_index = Math.min(1.0, niche.occupant_routes.size / Math.max(1, niche.capacity));

      // Niche Vacancy Logic
      if (niche.historical_occupancy > 10 && (niche.occupant_routes.size / niche.historical_occupancy) < 0.2) {
          const event = {
              type: 'NICHE_VACANCY_EVENT',
              niche_id: baseNiche,
              simTime: simTime || Date.now(),
              remaining_occupants: niche.occupant_routes.size,
              extinction_pressure: niche.extinction_pressure
          };
          this.vacancy_events.push(event);
          if (this.vacancy_events.length > 100) this.vacancy_events.shift();
          return event;
      }
      return null;
  }

  getNicheState(niche_id) {
      const niche = this.niches.get(niche_id);
      if (!niche) return null;
      return {
          niche_id: niche.niche_id,
          occupancy: niche.occupant_routes.size,
          capacity: niche.capacity,
          competition_index: niche.competition_index,
          extinction_pressure: niche.extinction_pressure,
          climate_sensitivity: niche.climate_sensitivity
      };
  }
}

if (typeof window !== 'undefined') {
  window.RouteNicheRegistry = RouteNicheRegistry;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { RouteNicheRegistry };
}
