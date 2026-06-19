/**
 * RoutePhenotypeClassifier.js — Infers high-level behavioral classes from RouteGenomes
 *
 * It transitions SCYTHE from asking "What path did packets take?" to asking
 * "What kind of routing organism am I observing?" and "How likely is it to mutate next?"
 * by assessing stability, carrier topology, anycast behaviors, and route weather.
 */

class RoutePhenotypeClassifier {
  /**
   * Evaluates a RouteGenome and assigns a primary operational phenotype, 
   * including evolutionary pressure forecasting.
   * @param {RouteGenome} genome 
   * @param {Object} [options]
   * @param {RouteClimateField} [options.climateField]
   * @param {number} [options.simTime]
   */
  static classify(genome, options = {}) {
    if (!genome || !genome.carrier_markers) {
      return { phenotype: 'insufficient_data', confidence: 0 };
    }

    const cm = genome.carrier_markers;
    const rdi_p50 = cm.rdi_shell?.p50 || 0;
    const turbulence = cm.rdi_turbulence || 0;
    const persistence = genome.route_persistence_score || 0;
    const isAncestral = genome.is_ancestral;
    const globalTurbulence = options.climateField?.turbulence_index || 0;
    const simTime = options.simTime || Date.now();

    let confidence = 0.5;
    let basePhenotype = 'unknown_transit';
    let lineageClass = isAncestral ? 'stable_backbone' : 'ephemeral_route';

    // 0. Evaluate Specific Ecological Niches based on sequence signatures
    const seqStr = genome.core_sequence.join(' ').toLowerCase();
    
    let signatureMatched = false;

    // Wireless Last Mile (Verizon mobile)
    if (seqStr.includes('.myvzw.com')) {
        basePhenotype = 'wireless_last_mile';
        confidence += 0.3;
        signatureMatched = true;
    }
    // Encrypted Overlay Mesh / Relay (Tailscale DERP)
    else if (seqStr.includes('tailscale.com')) {
        basePhenotype = 'encrypted_overlay_relay';
        confidence += 0.4;
        signatureMatched = true;
    }
    // Hyperscaler Private WAN (Microsoft MSN)
    else if (seqStr.includes('.msn.net')) {
        basePhenotype = 'hyperscaler_private_backbone';
        confidence += 0.3;
        signatureMatched = true;
    }
    // Tier-1 Transit Backbone (Twelve99, Alter.net)
    else if (seqStr.includes('twelve99') || seqStr.includes('alter.net')) {
        basePhenotype = 'tier1_transit_backbone';
        confidence += 0.3;
        signatureMatched = true;
    }
    
    // Probe Response Artifact (huge latency jump followed by normal latency)
    // Often seen as a massive outlier in the shell where p95 is wildly disconnected from p50
    if (cm.rdi_shell) {
        const p50 = cm.rdi_shell.p50 || 0;
        const p95 = cm.rdi_shell.p95 || 0;
        if (p95 > p50 * 4 && p50 < 5000 && p95 > 15000) {
            basePhenotype = 'probe_response_artifact';
            confidence += 0.3;
            signatureMatched = true;
        }
    }

    // 1. Evaluate Topology / Latency Shell (fallback if no signature matched)
    if (!signatureMatched) {
      if (rdi_p50 > 0 && rdi_p50 < 1500) {
        // Extremely low latency -> CDN, Edge, or Metro Anycast
        basePhenotype = genome.anycast_affinity.length > 0 ? 'anycast_edge' : 'metro_transit';
        confidence += 0.2;
      } else if (rdi_p50 > 8000) {
        // High latency -> Continental or Oceanic
        basePhenotype = cm.transit_sequence.length > 4 ? 'oceanic_crossing' : 'continental_core';
        confidence += 0.15;
      } else if (rdi_p50 >= 1500 && rdi_p50 <= 8000) {
        // Mid latency -> Regional or backbone
        basePhenotype = 'regional_backbone';
      }
    }

    // 2. Evaluate Route Weather (Turbulence)
    if (turbulence > 0.4) {
      basePhenotype = 'turbulent_transit';
      lineageClass = 'volatile_mutation';
      confidence += 0.1;
    }

    // 3. Evaluate Carrier Density (e.g. BGP peering diversity)
    if (cm.recurring_asns.length > 3 && rdi_p50 < 5000) {
      basePhenotype = 'hyperscaler_private_edge';
      confidence += 0.2;
    }

    // 4. Determine Phenotype Version (Drift tracking)
    if (genome.phenotype_history.length === 0 || genome.phenotype_history[genome.phenotype_history.length - 1].phenotype !== basePhenotype) {
        if (genome.phenotype_history.length > 0) {
            genome.phenotype_history[genome.phenotype_history.length - 1].exited = simTime;
        }
        genome.phenotype_history.push({
            phenotype: basePhenotype,
            entered: simTime,
            exited: null,
            version: genome.phenotype_history.filter(h => h.phenotype === basePhenotype).length + 1
        });
    }
    const currentHistory = genome.phenotype_history[genome.phenotype_history.length - 1];
    const version = currentHistory.version;
    const phenotype = `${basePhenotype}_v${version}`;

    // Normalizing confidence bounds
    confidence = Math.min(0.99, Math.max(0.1, confidence * genome.stability_score));

    // Calculate Divergence Risk (Inversely proportional to stability)
    let divergenceRisk = 1.0 - genome.stability_score;
    if (turbulence > 0.3) divergenceRisk += 0.2;
    divergenceRisk = Math.min(1.0, divergenceRisk);

    // 5. Evolutionary Forecasting
    // Extinction Risk: High if lesion survival is low and stability is failing
    const extinctionRisk = Math.min(1.0, divergenceRisk * (1.0 - genome.lesion_survival));
    
    // Speciation Probability: High if turbulence is high but it survives lesions well (highly adaptive)
    const speciationProbability = Math.min(1.0, (turbulence * 0.5) + (genome.lesion_survival * 0.5) * divergenceRisk);

    // Evolutionary Pressure: Aggregate force acting on the route (local + climate pressure)
    const localPressure = Math.min(1.0, extinctionRisk * 0.5 + speciationProbability * 0.5 + turbulence * 0.2);
    const evolutionaryPressure = Math.min(1.0, localPressure * 0.7 + globalTurbulence * 0.3);

    let expectedMutationWindow = 'unknown';
    if (evolutionaryPressure > 0.8) {
        expectedMutationWindow = '< 24 hours';
    } else if (evolutionaryPressure > 0.5) {
        expectedMutationWindow = '1-3 days';
    } else if (evolutionaryPressure > 0.2) {
        expectedMutationWindow = '7-14 days';
    } else {
        expectedMutationWindow = '30+ days';
    }

    return {
      phenotype,
      base_phenotype: basePhenotype,
      phenotype_version: version,
      confidence,
      lineage_class: lineageClass,
      route_persistence_score: persistence,
      rdi_turbulence: turbulence,
      divergence_risk: divergenceRisk,
      is_ancestral: isAncestral,
      
      // Forecasting
      evolutionary_pressure: evolutionaryPressure,
      expected_mutation_window: expectedMutationWindow,
      extinction_risk: extinctionRisk,
      speciation_probability: speciationProbability
    };
  }
}

if (typeof window !== 'undefined') {
  window.RoutePhenotypeClassifier = RoutePhenotypeClassifier;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { RoutePhenotypeClassifier };
}
