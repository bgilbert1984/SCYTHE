/**
 * TransitMotifGenome.js — Persistent behavioral identity for recurring transit patterns
 *
 * A transit motif captures a multi-carrier routing structure that appears repeatedly
 * across independent target destinations (e.g. the "Twelve99 Affinity Cluster").
 * It represents the ecological niche or routing backbone that multiple RouteGenomes rely upon.
 */

function _fnv1a32(str) {
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

class TransitMotifGenome {
  constructor(motif_id, seed = {}) {
    this.motif_id = motif_id;
    this.motif_hash = seed.motif_hash ?? TransitMotifGenome.computeMotifHash(motif_id);

    // The sequence of ASNs or carriers that define this motif
    this.carriers = [...(seed.carriers || [])];
    
    this.route_count = seed.route_count ?? 0;
    this.recurrence_score = seed.recurrence_score ?? 1.0;
    this.lesion_survival = seed.lesion_survival ?? 1.0;
    
    // High-level behavioral classification of this structural cluster
    this.phenotype = seed.phenotype || "unknown";

    // Route Climate: Long-term ecological envelope
    this.climate = seed.climate || {
      median_rdi: null,
      seasonal_variance: 0,
      extinction_rate: 0,
      mutation_rate: 0,
      lesion_resilience: 1.0,
      divergence_events: 0,
      extinction_events: 0
    };

    // Track which individual routes depend on this motif
    this.dependent_routes = new Set(seed.dependent_routes || []);

    this.first_seen_simTime = seed.first_seen_simTime ?? 0;
    this.last_seen_simTime = seed.last_seen_simTime ?? 0;
  }

  static computeMotifHash(motif_id) {
    return `mtf_${_fnv1a32(String(motif_id)).toString(16).padStart(8, '0')}`;
  }

  /**
   * Absorb a RouteGenome that utilizes this transit motif.
   */
  absorbRoute(routeGenome, simTime) {
    if (simTime > 0) {
      if (!this.first_seen_simTime) this.first_seen_simTime = simTime;
      this.last_seen_simTime = simTime;
    }

    if (!this.dependent_routes.has(routeGenome.route_id)) {
      this.dependent_routes.add(routeGenome.route_id);
      this.route_count++;
      
      // A motif's recurrence score strengthens as independent routes rely on it.
      this.recurrence_score = Math.min(1.0, this.recurrence_score + 0.05);
    }
    
    // Update median RDI climate
    if (routeGenome.carrier_markers?.rdi_shell?.p50 != null) {
      const p50 = routeGenome.carrier_markers.rdi_shell.p50;
      if (this.climate.median_rdi == null) {
        this.climate.median_rdi = p50;
      } else {
        this.climate.median_rdi = this.climate.median_rdi * 0.95 + p50 * 0.05;
        this.climate.seasonal_variance = Math.abs(p50 - this.climate.median_rdi) / this.climate.median_rdi;
      }
    }
  }

  /**
   * Record a divergence or extinction event to update motif climate.
   */
  recordEvent(eventType) {
    const total = Math.max(1, this.route_count);
    if (eventType === 'ROUTE_GENOME_DIVERGENCE') {
      this.climate.divergence_events++;
      this.climate.mutation_rate = this.climate.divergence_events / total;
    } else if (eventType === 'ROUTE_EXTINCTION') {
      this.climate.extinction_events++;
      this.climate.extinction_rate = this.climate.extinction_events / total;
    }
  }

  /**
   * If a lesion is applied to this motif, what percentage of its dependent routes survive?
   * Allows the system to gauge the structural load-bearing nature of the motif.
   */
  updateLesionSurvival(survivalRate) {
    // EMA smoothing
    this.lesion_survival = this.lesion_survival * 0.8 + survivalRate * 0.2;
    this.climate.lesion_resilience = this.lesion_survival;
  }

  /**
   * Simulates the collapse of an internal carrier to see if the motif can structurally survive.
   * Enables ecological niche replacement analysis.
   */
  simulateMotifCollapse(lesionTarget) {
     if (!this.carriers.includes(lesionTarget)) return { survived: true, collapse_ratio: 0 };
     
     // In a full simulation engine, this would poll the dependent RouteGenomes.
     // For now, it relies on the motif's historic resilience.
     const collapse_ratio = 1.0 - this.lesion_survival;
     
     return {
         survived: collapse_ratio < 0.6,
         collapse_ratio,
         impacted_routes: this.dependent_routes.size
     };
  }

  /**
   * Determine the phenotype based on observed carrier composition and route dependencies.
   */
  inferPhenotype() {
    if (this.carriers.includes("Twelve99") || this.carriers.includes("Level3")) {
      this.phenotype = "tier1_transit_backbone";
    } else if (this.carriers.includes("GitHub") || this.carriers.includes("Azure") || this.carriers.includes("AWS")) {
      this.phenotype = "hyperscaler_egress";
    } else if (this.route_count > 50) {
      this.phenotype = "dense_affinity_cluster";
    } else {
      this.phenotype = "emerging_motif";
    }
  }

  toJSON() {
    return {
      motif_id: this.motif_id,
      motif_hash: this.motif_hash,
      carriers: [...this.carriers],
      route_count: this.route_count,
      recurrence_score: this.recurrence_score,
      lesion_survival: this.lesion_survival,
      climate: { ...this.climate },
      phenotype: this.phenotype,
      dependent_routes: Array.from(this.dependent_routes),
      first_seen_simTime: this.first_seen_simTime,
      last_seen_simTime: this.last_seen_simTime,
    };
  }
}

if (typeof window !== 'undefined') {
  window.TransitMotifGenome = TransitMotifGenome;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { TransitMotifGenome };
}
