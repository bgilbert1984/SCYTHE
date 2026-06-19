/**
 * NicheSuccessionEngine.js — Tracks ecological succession in routing niches
 *
 * Observes when a route or motif goes extinct and measures what replaces it.
 * Calculates the succession delay and fitness delta (improvement/degradation)
 * to evaluate the health of the routing ecosystem.
 */

class NicheSuccessionEngine {
  constructor() {
    this.pending_vacancies = new Map(); // vacancy_event_id -> Vacancy
    this.succession_ledger = [];
  }

  /**
   * Registers a vacancy event when a route or motif goes extinct.
   * @param {Object} event 
   */
  registerVacancy(event) {
    if (!event || !event.niche_id) return;
    
    // Create a unique ID for this vacancy
    const vacancy_id = `vac_${Date.now()}_${Math.floor(Math.random()*1000)}`;
    
    this.pending_vacancies.set(vacancy_id, {
        vacancy_event_id: vacancy_id,
        niche_id: event.niche_id,
        extinct_occupant: event.extinct_route_id || event.extinct_motif_id || 'unknown',
        extinct_fitness: event.extinct_fitness || 0,
        simTime_vacated: event.simTime || Date.now()
    });

    return vacancy_id;
  }

  /**
   * Evaluates if a new route/motif has filled a pending vacancy.
   * @param {Object} newOccupant (RouteGenome or TransitMotifGenome)
   * @param {string} niche_id 
   * @param {number} simTime 
   */
  observeColonization(newOccupant, niche_id, simTime) {
      if (!newOccupant) return null;

      // Find the oldest pending vacancy for this niche
      let targetVacancyId = null;
      let targetVacancy = null;

      for (const [v_id, vac] of this.pending_vacancies.entries()) {
          if (vac.niche_id === niche_id) {
              if (!targetVacancy || vac.simTime_vacated < targetVacancy.simTime_vacated) {
                  targetVacancyId = v_id;
                  targetVacancy = vac;
              }
          }
      }

      if (targetVacancy) {
          // Calculate succession metrics
          const succession_delay = simTime - targetVacancy.simTime_vacated;
          
          // Fitness calculation depends on whether it's a route or motif
          const new_fitness = newOccupant.route_persistence_score || newOccupant.lesion_survival || 0;
          const fitness_delta = new_fitness - targetVacancy.extinct_fitness;

          const successionEvent = {
              vacancy_event_id: targetVacancy.vacancy_event_id,
              niche_id: niche_id,
              extinct_occupant: targetVacancy.extinct_occupant,
              replacement_occupant: newOccupant.route_id || newOccupant.motif_id,
              succession_delay: succession_delay,
              fitness_delta: fitness_delta,
              simTime_resolved: simTime
          };

          this.succession_ledger.push(successionEvent);
          if (this.succession_ledger.length > 1000) this.succession_ledger.shift();

          this.pending_vacancies.delete(targetVacancyId);
          return successionEvent;
      }

      return null;
  }

  toJSON() {
      return {
          pending_vacancies_count: this.pending_vacancies.size,
          recent_successions: this.succession_ledger.slice(-10)
      };
  }
}

if (typeof window !== 'undefined') {
  window.NicheSuccessionEngine = NicheSuccessionEngine;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { NicheSuccessionEngine };
}
