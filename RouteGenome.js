/**
 * RouteGenome.js — Persistent behavioral identity for network routes
 *
 * A route genome treats a path not as a literal physical coordinate, but as a
 * behavioral phenotype (a "latency shell" and a "core sequence"). It accumulates
 * memory from traceroutes and timing probes over time.
 */

function _fnv1a32(str) {
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

class RouteGenome {
  /**
   * @param {string} route_id
   * @param {Object} seed
   */
  constructor(route_id, seed = {}) {
    this.route_id = route_id;
    this.route_hash = seed.route_hash ?? RouteGenome.computeRouteHash(route_id);

    // Primary phenotype vectors
    this.core_sequence = [...(seed.core_sequence || [])];
    this.rdi_profile = [...(seed.rdi_profile || [])]; // Array of RDI observations
    
    // Component traits
    const CFp = typeof CarrierFingerprint !== 'undefined' ? CarrierFingerprint : null;
    this.carrier_markers = CFp 
        ? (seed.carrier_markers instanceof CFp ? seed.carrier_markers : new CFp(seed.carrier_markers || {})) 
        : (seed.carrier_markers || {});
        
    this.anycast_affinity = [...(seed.anycast_affinity || [])];
    this.divergence_history = [...(seed.divergence_history || [])];
    this.shadow_regions = [...(seed.shadow_regions || [])];
    this.phenotype_history = [...(seed.phenotype_history || [])];
    this.evolution_ledger = [...(seed.evolution_ledger || [])]; // Chronological mutation log
    this.ancestor_phenotypes = [...(seed.ancestor_phenotypes || [])];
    
    // Evolutionary tracking
    this.lineage = seed.lineage || null;
    this.stability_score = seed.stability_score ?? 1.0;
    this.lesion_survival = seed.lesion_survival ?? 1.0;
    this.recurrence_count = seed.recurrence_count ?? 1;

    // Temporal bounds
    this.first_seen_simTime = seed.first_seen_simTime ?? 0;
    this.last_seen_simTime = seed.last_seen_simTime ?? 0;
  }

  static computeRouteHash(route_id) {
    return `rt_${_fnv1a32(String(route_id)).toString(16).padStart(8, '0')}`;
  }

  /**
   * Generates a genetic signature for cladistics clustering.
   */
  get genetic_signature() {
      let carrier_entropy = 0;
      if (this.carrier_markers && this.carrier_markers.recurring_asns) {
          carrier_entropy = Math.min(1.0, this.carrier_markers.recurring_asns.length / 10);
      }
      
      const transit_entropy = Math.min(1.0, this.core_sequence.length / 20);
      const shadow_density = Math.min(1.0, this.shadow_regions.reduce((sum, r) => sum + r.duration, 0) / Math.max(1, this.core_sequence.length));
      
      return {
          carrier_entropy,
          transit_entropy,
          lesion_survival: this.lesion_survival,
          shadow_density,
          route_stability: this.stability_score
      };
  }

  /**
   * Generates an explicit "DNA" sequence string mapping the genetic signature.
   * Format: A-T-C-R-S (ASN, Transit, Carrier, Resilience, Shadow)
   */
  get genetic_sequence() {
      const sig = this.genetic_signature;
      const fmt = (val) => Math.floor((val || 0) * 9); // Scale 0.0-1.0 to 0-9
      
      const A = fmt(sig.carrier_entropy); // ASN Diversity
      const T = fmt(sig.transit_entropy); // Transit Redundancy
      const C = fmt(1.0 - sig.carrier_entropy); // Carrier specific (inverse entropy approximation)
      const R = fmt(sig.lesion_survival); // Resilience
      const S = fmt(sig.shadow_density);  // Shadow Density
      
      return `A${A}-T${T}-C${C}-R${R}-S${S}`;
  }

  /**
   * Evaluates how robust the route phenotype is by combining persistence,
   * recurrence, and its ability to survive causal lesions.
   * Returns a fitness score reflecting evolutionary strength with aging pressure.
   */
  get route_persistence_score() {
      if (!this.last_seen_simTime) return 0;
      
      const lastDivergenceTime = this.divergence_history.length > 0 
          ? this.divergence_history[this.divergence_history.length - 1].simTime 
          : this.first_seen_simTime;
          
      // Convert ms to rough days equivalent for log scale (or just use raw simTime diff safely)
      const timeSurvived = Math.max(1, this.last_seen_simTime - lastDivergenceTime);
      
      // Fitness = log(time) * sqrt(recurrence) * lesion_survival
      // This prevents ancient but rarely used routes from dominating actively used, highly resilient routes.
      return Math.log10(timeSurvived + 1) * Math.sqrt(this.recurrence_count) * this.lesion_survival;
  }

  /**
   * Identifies if a route has survived long enough to exert ancestral gravity
   * (e.g., acts as a stable foundational lineage).
   */
  get is_ancestral() {
      // Adjusted threshold based on log/sqrt fitness calculation.
      // E.g., ~120 days of ms log (~10) * ~1000 recurrences sqrt (~31) = ~310.
      return this.route_persistence_score > 250; 
  }

  /**
   * Absorb a traceroute or RTT probe into the genome.
   * Emits a divergence metric if this observation mutated the genome significantly.
   */
  absorb(evidence = {}) {
    const {
      simTime = 0,
      hops = [], // Array of { ip, rtt_ms, distance_km, ... }
      rtt = null,
      target = null
    } = evidence;

    if (simTime > 0) {
      if (!this.first_seen_simTime) this.first_seen_simTime = simTime;
      this.last_seen_simTime = simTime;
    }

    this.recurrence_count++;

    let divergence = {
      sequence_change: 0,
      latency_shift: 0,
      mutated: false
    };

    // Extract shadow regions (missing hops / * outputs)
    let inShadow = false;
    let currentShadow = null;
    hops.forEach((hop, idx) => {
       const isHidden = hop.ip === '*' || hop.ip == null;
       if (isHidden && !inShadow) {
           inShadow = true;
           currentShadow = { startHop: hop.hop || idx + 1, duration: 1 };
       } else if (isHidden && inShadow) {
           currentShadow.duration++;
       } else if (!isHidden && inShadow) {
           inShadow = false;
           currentShadow.endHop = (hop.hop || idx + 1) - 1;
           this._recordShadowRegion(currentShadow);
           currentShadow = null;
       }
    });
    if (inShadow && currentShadow) {
       currentShadow.endHop = hops.length;
       this._recordShadowRegion(currentShadow);
    }

    // 1. Absorb into CarrierFingerprint
    const sequence = hops.map(h => h.ip || '*');
    const effectiveRtt = rtt ?? (hops.length > 0 ? hops[hops.length - 1].rtt_ms : null);
    
    if (this.carrier_markers && typeof this.carrier_markers.absorb === 'function') {
        this.carrier_markers.absorb({
            simTime,
            sequence,
            rdi: effectiveRtt != null ? Math.round(effectiveRtt * 100) : null
        });
    }

    // 2. RDI Profile & Anycast Affinity
    if (effectiveRtt != null) {
      const rdi = Math.round(effectiveRtt * 100);
      this.rdi_profile.push(rdi);
      if (this.rdi_profile.length > 64) this.rdi_profile.shift();
      
      // Calculate shift from recent history
      if (this.rdi_profile.length > 5) {
          const recentMean = this.rdi_profile.slice(0, -1).reduce((a, b) => a + b, 0) / (this.rdi_profile.length - 1);
          divergence.latency_shift = Math.abs(rdi - recentMean) / Math.max(recentMean, 1);
          if (divergence.latency_shift > 0.3) divergence.mutated = true;
      }
    }

    // 3. Sequence Morphology
    if (hops.length > 0) {
      if (this.core_sequence.length === 0) {
        this.core_sequence = sequence;
      } else {
        // Jaccard similarity for sequence change
        const sa = new Set(this.core_sequence);
        const sb = new Set(sequence);
        let inter = 0;
        for (const x of sa) if (sb.has(x)) inter++;
        const union = sa.size + sb.size - inter;
        
        divergence.sequence_change = union ? 1 - (inter / union) : 0;

        if (divergence.sequence_change > 0.4) {
          divergence.mutated = true;
        }

        // Slowly adopt the new sequence if it's stable
        if (divergence.sequence_change <= 0.2) {
            this.core_sequence = sequence;
        }
      }
    }

    if (divergence.mutated) {
      this.divergence_history.push({
          simTime,
          ...divergence
      });
      if (this.divergence_history.length > 16) this.divergence_history.shift();
      this.stability_score = Math.max(0.1, this.stability_score * 0.8);
      
      this.addLedgerEntry(`Carrier divergence observed (Score: ${divergence.sequence_change.toFixed(2)})`, simTime);
    } else {
      this.stability_score = Math.min(1.0, this.stability_score + 0.05);
    }

    return divergence;
  }

  addLedgerEntry(message, simTime) {
      this.evolution_ledger.push({
          message,
          simTime: simTime || Date.now()
      });
      if (this.evolution_ledger.length > 50) this.evolution_ledger.shift();
  }

  updatePhenotype(newPhenotype, simTime) {
      if (this.phenotype !== newPhenotype) {
          const old = this.phenotype;
          this.phenotype = newPhenotype;
          if (old) {
              this.ancestor_phenotypes.push(old);
              if (this.ancestor_phenotypes.length > 10) this.ancestor_phenotypes.shift();
          }
          this.addLedgerEntry(`Entered ${newPhenotype} niche`, simTime);
      }
  }

  _recordShadowRegion(region) {
      // Find similar shadow region and update or push new
      const existing = this.shadow_regions.find(r => 
          Math.abs(r.startHop - region.startHop) <= 1 && 
          Math.abs(r.duration - region.duration) <= 1
      );
      if (existing) {
          existing.recurrence = (existing.recurrence || 1) + 1;
      } else {
          this.shadow_regions.push({ ...region, recurrence: 1 });
      }
      
      if (this.shadow_regions.length > 8) {
          // keep most recurrent shadows
          this.shadow_regions.sort((a,b) => b.recurrence - a.recurrence);
          this.shadow_regions = this.shadow_regions.slice(0, 8);
      }
  }

  toJSON() {
    return {
      route_id: this.route_id,
      route_hash: this.route_hash,
      core_sequence: [...this.core_sequence],
      rdi_profile: [...this.rdi_profile],
      carrier_markers: this.carrier_markers?.toJSON ? this.carrier_markers.toJSON() : this.carrier_markers,
      anycast_affinity: [...this.anycast_affinity],
      divergence_history: [...this.divergence_history],
      shadow_regions: [...this.shadow_regions],
      phenotype_history: [...this.phenotype_history],
      evolution_ledger: [...this.evolution_ledger],
      ancestor_phenotypes: [...this.ancestor_phenotypes],
      lineage: this.lineage,
      stability_score: this.stability_score,
      lesion_survival: this.lesion_survival,
      genetic_signature: this.genetic_signature,
      genetic_sequence: this.genetic_sequence,
      recurrence_count: this.recurrence_count,
      route_persistence_score: this.route_persistence_score,
      first_seen_simTime: this.first_seen_simTime,
      last_seen_simTime: this.last_seen_simTime,
    };
  }
}

if (typeof window !== 'undefined') {
  window.RouteGenome = RouteGenome;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { RouteGenome };
}
