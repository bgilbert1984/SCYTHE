/**
 * RoutePaleontologyEngine.js — A permanent evolutionary database for extinct routes
 *
 * Maintains the "Natural History Museum" of the Internet. When a routing species
 * goes extinct, it is moved from the active phylogeny into the fossil record.
 * Tracks geological epochs of the Internet, extinction causes, and surviving clades.
 */

class RoutePaleontologyEngine {
  constructor() {
    this.fossil_record = [];
    this.epochs = [
      { name: "Ancient Backbone Era", start: 0, end: 1600000000000 },
      { name: "Anthropocene Internet", start: 1600000000000, end: null }
    ];
    this.current_epoch = "Anthropocene Internet";
  }

  /**
   * Archives a routing species that has gone extinct.
   * @param {Object} speciesNode The node from RoutePhylogeneticEngine
   * @param {string} cause The reason for extinction (e.g. "Competition collapse", "Climate Shift")
   * @param {number} simTime 
   */
  archiveSpecies(speciesNode, cause, simTime) {
      if (!speciesNode) return;

      const fossil = {
          name: speciesNode.phenotype,
          birth_time: speciesNode.birthTime,
          extinction_time: simTime || Date.now(),
          age_cycles: (simTime || Date.now()) - speciesNode.birthTime,
          cause: cause || "Unknown Environmental Pressure",
          last_habitat: "Global Transit", // Could be derived from geographic pressure
          survived_by: Array.from(speciesNode.descendants),
          genetic_signature: speciesNode.genetic_signature
      };

      this.fossil_record.push(fossil);
      
      // Sort by extinction time descending (newest fossils first)
      this.fossil_record.sort((a, b) => b.extinction_time - a.extinction_time);
  }

  getFossils() {
      return this.fossil_record;
  }
}

if (typeof window !== 'undefined') {
  window.RoutePaleontologyEngine = RoutePaleontologyEngine;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { RoutePaleontologyEngine };
}
