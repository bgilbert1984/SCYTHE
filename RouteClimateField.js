/**
 * RouteClimateField.js — Global tracking of routing weather and ecological state
 *
 * Integrates data from TransitMotifGenomes, ResonanceLedger, and RouteGenealogy
 * to characterize the current and historical environment (e.g. carrier_storm,
 * stable_backbone, anycast_migration).
 */

class RouteClimateField {
  constructor() {
    this.zones = new Map(); // zone_id -> ZoneClimate
    
    // Global aggregates
    this.global_turbulence_index = 0.0;
    this.global_transit_entropy = 0.0;
    this.active_extinctions = 0;
    this.active_speciations = 0;
    
    this.geographic_pressures = new Map(); // loc_id -> pressureValue
    
    // Internal tracking
    this.motifs = new Map(); // motif_id -> TransitMotifGenome summary
    this.event_window = [];  // sliding window of recent events
    this.last_update_simTime = 0;
  }

  _getOrCreateZone(zone_id) {
      if (!this.zones.has(zone_id)) {
          this.zones.set(zone_id, {
              zone_id,
              climate_state: "stable",
              climate_memory: [],
              turbulence_index: 0.0,
              motif_resilience_mean: 1.0,
              active_extinctions: 0,
              active_speciations: 0,
              dominant_mutation_vectors: []
          });
      }
      return this.zones.get(zone_id);
  }

  /**
   * Periodically poll the ecosystem and recompute regional and global climates.
   * @param {Array} motifs List of active TransitMotifGenomes
   * @param {number} simTime 
   */
  updateEcosystemState(motifs, simTime) {
    this.last_update_simTime = simTime;
    
    if (!motifs || motifs.length === 0) return;

    let globalTurbulenceSum = 0;
    
    this.motifs.clear();
    
    // Group motifs by zone (e.g., phenotype base like 'oceanic_crossing', 'regional_backbone')
    const zoneGroups = new Map();

    motifs.forEach(motif => {
       this.motifs.set(motif.motif_id, motif);
       globalTurbulenceSum += motif.climate?.seasonal_variance || 0;
       
       const zone_id = motif.phenotype || 'unknown_zone';
       if (!zoneGroups.has(zone_id)) zoneGroups.set(zone_id, []);
       zoneGroups.get(zone_id).push(motif);
    });

    this.global_turbulence_index = Math.min(1.0, globalTurbulenceSum / motifs.length);
    this.global_transit_entropy = this._calculateShannonEntropy(motifs.map(m => m.route_count));

    // Prune old events from window (e.g., last 500 events)
    while (this.event_window.length > 500) {
      this.event_window.shift();
    }

    this.active_speciations = this.event_window.filter(e => e.type === 'ROUTE_GENOME_SPECIATION').length;
    this.active_extinctions = this.event_window.filter(e => e.type === 'ROUTE_EXTINCTION').length;

    // Update individual zones
    for (const [zone_id, zoneMotifs] of zoneGroups.entries()) {
        const zone = this._getOrCreateZone(zone_id);
        
        let zTurbulence = 0;
        let zResilience = 0;
        zoneMotifs.forEach(m => {
            zTurbulence += m.climate?.seasonal_variance || 0;
            zResilience += m.lesion_survival || 1.0;
        });

        zone.turbulence_index = Math.min(1.0, zTurbulence / zoneMotifs.length);
        zone.motif_resilience_mean = zResilience / zoneMotifs.length;
        
        // Count events specific to this zone (approximated by event target if available, or proportion)
        zone.active_speciations = this.event_window.filter(e => e.type === 'ROUTE_GENOME_SPECIATION' && e.zone_id === zone_id).length;
        zone.active_extinctions = this.event_window.filter(e => e.type === 'ROUTE_EXTINCTION' && e.zone_id === zone_id).length;

        this._inferZoneClimateState(zone);
    }
  }

  /**
   * Absorb a significant event from the ecosystem.
   */
  absorbEvent(event) {
    this.event_window.push(event);
    
    // Tally mutation vectors in the specific zone
    if (event.type === 'ROUTE_GENOME_DIVERGENCE' && event.divergence && event.zone_id) {
        const zone = this._getOrCreateZone(event.zone_id);
        if (event.divergence.anycast_shift) this._recordZoneMutationVector(zone, 'anycast_shift');
        if (event.divergence.transit_mutation) this._recordZoneMutationVector(zone, 'tier1_rebalancing');
        if (event.divergence.latency_shift > 0.4) this._recordZoneMutationVector(zone, 'regional_congestion');
    }
  }

  _recordZoneMutationVector(zone, vector) {
      const existing = zone.dominant_mutation_vectors.find(v => v.name === vector);
      if (existing) {
          existing.count++;
      } else {
          zone.dominant_mutation_vectors.push({ name: vector, count: 1 });
      }
      zone.dominant_mutation_vectors.sort((a, b) => b.count - a.count);
      if (zone.dominant_mutation_vectors.length > 5) zone.dominant_mutation_vectors.pop();
  }

  _calculateShannonEntropy(counts) {
      const total = counts.reduce((a, b) => a + b, 0);
      if (total === 0) return 0;
      let entropy = 0;
      counts.forEach(count => {
          if (count > 0) {
              const p = count / total;
              entropy -= p * Math.log2(p);
          }
      });
      // Normalize assuming max entropy is log2(counts.length)
      const maxEntropy = Math.log2(Math.max(1, counts.length));
      return maxEntropy > 0 ? entropy / maxEntropy : 0;
  }

  _inferZoneClimateState(zone) {
      let proposed_state = "transitional_drift";

      if (zone.active_extinctions > 10 && zone.turbulence_index > 0.6) {
          proposed_state = "carrier_storm";
      } else if (zone.active_speciations > 15) {
          proposed_state = "rapid_speciation_phase";
      } else if (zone.dominant_mutation_vectors.length > 0 && zone.dominant_mutation_vectors[0].name === 'anycast_shift') {
          proposed_state = "anycast_migration";
      } else if (zone.turbulence_index < 0.2 && zone.motif_resilience_mean > 0.8) {
          proposed_state = "stable_backbone";
      }

      zone.climate_memory.push(proposed_state);
      if (zone.climate_memory.length > 5) zone.climate_memory.shift();

      const recent = zone.climate_memory.slice(-3);
      if (recent.length === 3 && recent.every(s => s === proposed_state)) {
          // Detect oscillation before applying
          if (zone.climate_state !== proposed_state) {
              this._detectOscillation(zone, proposed_state);
          }
          zone.climate_state = proposed_state;
      }
  }

  _detectOscillation(zone, new_state) {
      zone.state_history = zone.state_history || [];
      zone.state_history.push({ state: new_state, simTime: this.last_update_simTime });
      
      if (zone.state_history.length > 10) zone.state_history.shift();

      // Look for A -> B -> A -> B patterns
      if (zone.state_history.length >= 4) {
          const hist = zone.state_history;
          const len = hist.length;
          const isOscillating = 
              hist[len-1].state === hist[len-3].state &&
              hist[len-2].state === hist[len-4].state &&
              hist[len-1].state !== hist[len-2].state;

          if (isOscillating) {
              const period = hist[len-1].simTime - hist[len-3].simTime;
              zone.oscillation = {
                  pattern: `${hist[len-2].state} <-> ${hist[len-1].state}`,
                  period_ms: period,
                  confidence: 0.85
              };
          } else {
              zone.oscillation = null;
          }
      }
  }

  /**
   * Retrieves the climate state for a specific zone, falling back to global heuristics if unknown.
   */
  getClimateForZone(zone_id) {
      return this.zones.get(zone_id) || {
          climate_state: "transitional_drift",
          turbulence_index: this.global_turbulence_index
      };
  }

  /**
   * Updates the accumulated pressure for a specific geographic location (e.g. DFW, ATL).
   */
  updateGeographicPressure(loc_id, val) {
      const current = this.geographic_pressures.get(loc_id) || 1.0;
      // EMA smoothing
      this.geographic_pressures.set(loc_id, current * 0.9 + val * 0.1);
  }

  toJSON() {
      const exportedZones = {};
      for (const [id, zone] of this.zones.entries()) {
          exportedZones[id] = {
              climate_state: zone.climate_state,
              turbulence_index: zone.turbulence_index,
              motif_resilience_mean: zone.motif_resilience_mean,
              active_extinctions: zone.active_extinctions,
              active_speciations: zone.active_speciations,
              dominant_mutation_vectors: zone.dominant_mutation_vectors.map(v => v.name),
              oscillation: zone.oscillation || null
          };
      }

      return {
          global_turbulence_index: this.global_turbulence_index,
          global_transit_entropy: this.global_transit_entropy,
          global_active_extinctions: this.active_extinctions,
          global_active_speciations: this.active_speciations,
          geographic_pressures: Object.fromEntries(this.geographic_pressures),
          zones: exportedZones,
          last_update_simTime: this.last_update_simTime
      };
  }
}

if (typeof window !== 'undefined') {
  window.RouteClimateField = RouteClimateField;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { RouteClimateField };
}
