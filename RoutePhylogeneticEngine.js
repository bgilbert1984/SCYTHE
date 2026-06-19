/**
 * RoutePhylogeneticEngine.js — Speciation, Cladistics, and Taxonomic modeling
 *
 * Transitions SCYTHE from tracking individual routes to tracking species.
 * A "species" in this model is a unique phenotype. We track populations,
 * speciation events (mutations), genetic signatures, extinction, and 
 * the Internet Food Web (trophic hierarchies).
 */

const SPECIATION_CAUSES = {
    allopatric: "geographic isolation",
    adaptive: "optimization pressure",
    hybrid: "merged carrier ecosystems",
    catastrophic: "survival after major outage",
    artificial: "operator engineered topology"
};

class RoutePhylogeneticEngine {
  constructor(paleontologyEngine = null) {
    this.species = new Map(); // phenotype -> species_data
    this.speciationEvents = [];
    this.extinctSpecies = [];
    this.paleontologyEngine = paleontologyEngine;
  }

  /**
   * Absorb a route genome and map it into the species population.
   */
  observeGenome(genome, simTime) {
      if (!genome || !genome.phenotype) return;
      
      const phenotype = genome.phenotype;
      
      if (!this.species.has(phenotype)) {
          this.species.set(phenotype, {
              phenotype,
              genomes: new Set(),
              parent: genome.ancestor_phenotypes?.length > 0 ? genome.ancestor_phenotypes[genome.ancestor_phenotypes.length-1] : null,
              descendants: new Set(),
              birthTime: simTime || Date.now(),
              deathTime: null,
              extinct: false,
              populationHistory: [],
              genetic_signature: genome.genetic_signature
          });
      }
      
      const sp = this.species.get(phenotype);
      sp.genomes.add(genome.route_id);
      
      // Update genetic signature (moving average)
      if (genome.genetic_signature && sp.genetic_signature) {
          sp.genetic_signature.carrier_entropy = sp.genetic_signature.carrier_entropy * 0.9 + genome.genetic_signature.carrier_entropy * 0.1;
          sp.genetic_signature.transit_entropy = sp.genetic_signature.transit_entropy * 0.9 + genome.genetic_signature.transit_entropy * 0.1;
          sp.genetic_signature.lesion_survival = sp.genetic_signature.lesion_survival * 0.9 + genome.genetic_signature.lesion_survival * 0.1;
          sp.genetic_signature.shadow_density = sp.genetic_signature.shadow_density * 0.9 + genome.genetic_signature.shadow_density * 0.1;
          sp.genetic_signature.route_stability = sp.genetic_signature.route_stability * 0.9 + genome.genetic_signature.route_stability * 0.1;
      }
  }

  /**
   * Check for speciation events. Triggered when a genome diverges heavily.
   */
  checkSpeciation(genome, divergence, oldPhenotype, newPhenotype, simTime) {
      if (divergence.sequence_change > 0.45 && oldPhenotype !== newPhenotype && genome.route_persistence_score > 1000) {
          
          let causeType = "adaptive"; // default
          if (divergence.cause && divergence.cause.includes('climate')) causeType = "catastrophic";
          else if (divergence.cause && divergence.cause.includes('carrier')) causeType = "hybrid";
          else if (divergence.latency_shift > 0.4) causeType = "allopatric";
          
          this.speciationEvents.push({
              parent: oldPhenotype,
              child: newPhenotype,
              simTime: simTime || Date.now(),
              divergenceScore: divergence.sequence_change,
              cause: SPECIATION_CAUSES[causeType]
          });
          
          if (this.species.has(oldPhenotype)) {
              this.species.get(oldPhenotype).descendants.add(newPhenotype);
          }
      }
  }

  /**
   * Calculate evolutionary distance between two genetic signatures.
   */
  evolutionaryDistance(a, b) {
      if (!a || !b) return 1.0;
      return (
          Math.abs(a.carrier_entropy - b.carrier_entropy) +
          Math.abs(a.transit_entropy - b.transit_entropy) +
          Math.abs(a.shadow_density - b.shadow_density) +
          Math.abs(a.route_stability - b.route_stability)
      );
  }

  /**
   * Track extinction. If a species drops below threshold for a duration, it goes extinct.
   */
  evaluateExtinctions(simTime) {
      for (const [p, sp] of this.species.entries()) {
          if (sp.extinct) continue;
          
          sp.populationHistory.push(sp.genomes.size);
          if (sp.populationHistory.length > 50) sp.populationHistory.shift();
          
          const recentPops = sp.populationHistory.slice(-5);
          if (recentPops.length === 5 && recentPops.every(pop => pop === 0)) {
              sp.extinct = true;
              sp.deathTime = simTime || Date.now();
              this.extinctSpecies.push(p);
              
              if (this.paleontologyEngine) {
                  const cause = sp.populationHistory[0] > 0 ? "Competition collapse" : "Environmental shift";
                  this.paleontologyEngine.archiveSpecies(sp, cause, simTime);
              }
          }
      }
  }

  /**
   * Calculates the extinction risk of a species.
   */
  calculateSurvivalOdds(phenotype, currentClimate) {
      const sp = this.species.get(phenotype);
      if (!sp) return 0;
      
      const pop = sp.genomes.size;
      const stability = sp.genetic_signature?.route_stability || 0.1;
      const lesionSurv = sp.genetic_signature?.lesion_survival || 0.1;
      
      const climate_exposure = currentClimate?.turbulence_index || 0;
      
      let odds = (stability * 0.4) + (lesionSurv * 0.4) + (Math.min(1.0, pop / 100) * 0.2) - (climate_exposure * 0.3);
      return Math.max(0.01, Math.min(0.99, odds));
  }

  /**
   * Returns the Internet Food Web (Trophic Layers).
   * Treats route dependencies as predator-prey interactions.
   */
  getFoodWeb() {
      return [
          { level: 4, type: 'Apex Predator', name: 'tier1_transit_backbone', feeds_on: ['regional_backbone', 'oceanic_crossing'] },
          { level: 3, type: 'Mesopredator', name: 'regional_backbone', feeds_on: ['anycast_edge', 'metro_transit'] },
          { level: 3, type: 'Mesopredator', name: 'oceanic_crossing', feeds_on: ['anycast_edge'] },
          { level: 2, type: 'Primary Consumer', name: 'anycast_edge', feeds_on: ['wireless_last_mile', 'hyperscaler_private_backbone'] },
          { level: 1, type: 'Symbiotic Overlay', name: 'encrypted_overlay_relay', feeds_on: ['anycast_edge', 'regional_backbone'] },
          { level: 0, type: 'Producer', name: 'wireless_last_mile', feeds_on: [] },
          { level: 0, type: 'Producer', name: 'hyperscaler_private_backbone', feeds_on: [] },
          { level: -1, type: 'Cryptid Species', name: 'shadow_anycast_cluster', feeds_on: [] }
      ];
  }

  /**
   * Generates an ASCII phylogenetic tree based on observed speciation.
   */
  buildPhylogeneticTree() {
      const roots = [];
      const childrenMap = new Map();
      
      for (const [p, sp] of this.species.entries()) {
          if (!sp.parent) roots.push(p);
          else {
              if (!childrenMap.has(sp.parent)) childrenMap.set(sp.parent, []);
              childrenMap.get(sp.parent).push(p);
          }
      }
      
      let output = "ROOT\n";
      
      const buildBranch = (node, prefix, isLast) => {
          const sp = this.species.get(node);
          const status = sp && sp.extinct ? " [EXTINCT]" : "";
          const branch = isLast ? "└── " : "├── ";
          output += `${prefix}${branch}${node}${status}\n`;
          
          const children = childrenMap.get(node) || [];
          for (let i = 0; i < children.length; i++) {
              const childPrefix = prefix + (isLast ? "    " : "│   ");
              buildBranch(children[i], childPrefix, i === children.length - 1);
          }
      };

      for (let i = 0; i < roots.length; i++) {
          buildBranch(roots[i], "", i === roots.length - 1);
      }
      
      if (roots.length === 0) return "Awaiting speciation events...";
      
      return output;
  }
}

if (typeof window !== 'undefined') {
  window.RoutePhylogeneticEngine = RoutePhylogeneticEngine;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { RoutePhylogeneticEngine };
}