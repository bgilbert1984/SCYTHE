/**
 * CausalShockwave.js — Divergence topology / causal shockwave mapping
 *
 * Maps where counterfactual perturbation propagated:
 *   identity bifurcation, edge loss, cluster decoherence, newly causal events
 */

class CausalShockwave {
  /**
   * @param {Object} baseline - _simulateCognitionState output
   * @param {Object} branch - _simulateCognitionState output
   * @param {Array} sourceEvents
   * @param {Array} branchedEvents
   * @param {Object} causalExport - exportCausalityGraph() { events, edges }
   * @param {Array} lesionEvents - removed/suppressed events from branch
   */
  static map(baseline, branch, sourceEvents, branchedEvents, causalExport = {}, lesionEvents = []) {
    const baseIds = new Map(baseline.identities.map((i) => [i.host_id, i.snapshot]));
    const branchIds = new Map(branch.identities.map((i) => [i.host_id, i.snapshot]));

    const identity_bifurcation = [];
    const identity_extinction = [];
    const identity_speciation = [];

    for (const [hostId, baseSnap] of baseIds) {
      const br = branchIds.get(hostId);
      if (!br) {
        identity_extinction.push({ host_id: hostId, base_stability: baseSnap.stability });
        continue;
      }
      const delta = CausalShockwave._identityDelta(baseSnap, br);
      if (delta.magnitude > 0.12) {
        identity_bifurcation.push({ host_id: hostId, ...delta });
      }
    }

    for (const [hostId, brSnap] of branchIds) {
      if (!baseIds.has(hostId)) {
        identity_speciation.push({
          host_id: hostId,
          branch_stability: brSnap.stability,
          subtype_hint: CausalShockwave._inferSubtype(brSnap),
        });
      }
    }

    const edgeTopology = CausalShockwave._diffEdges(
      baseline.graph?.cross_host_edges ?? [],
      branch.graph?.cross_host_edges ?? []
    );

    const cluster_decoherence = CausalShockwave._clusterCoherence(
      baseline.graph,
      branch.graph
    );

    const newly_causal = CausalShockwave._newlyCausalEvents(sourceEvents, branchedEvents);
    const shockwave_front = CausalShockwave._propagateShockwave(
      lesionEvents,
      causalExport,
      sourceEvents
    );

    const field_harmonic_collapse = CausalShockwave._fieldHarmonicDelta(
      baseline,
      branch,
      identity_bifurcation
    );

    const propagation_depth = shockwave_front.max_depth;
    const affected_hosts = new Set([
      ...identity_bifurcation.map((x) => x.host_id),
      ...identity_extinction.map((x) => x.host_id),
      ...identity_speciation.map((x) => x.host_id),
      ...shockwave_front.affected_hosts,
    ]);

    return {
      identity_bifurcation,
      identity_extinction,
      identity_speciation,
      edge_topology: edgeTopology,
      cluster_decoherence,
      field_harmonic_collapse,
      newly_causal_events: newly_causal,
      shockwave_front,
      propagation_depth,
      affected_host_count: affected_hosts.size,
      hidden_affinity_candidates: CausalShockwave._hiddenAffinityCandidates(
        identity_bifurcation,
        edgeTopology
      ),
    };
  }

  static _identityDelta(base, branch) {
    const dMut = (branch.mutation_count ?? 0) - (base.mutation_count ?? 0);
    const dVpn = (branch.vpn_affinity ?? 0) - (base.vpn_affinity ?? 0);
    const dStab = (branch.stability ?? 0) - (base.stability ?? 0);
    const dProto = CausalShockwave._setDelta(base.protocol_dna, branch.protocol_dna);
    const magnitude = Math.min(
      1,
      Math.abs(dMut) * 0.05 + Math.abs(dVpn) * 0.35 + Math.abs(dStab) * 0.25 + dProto * 0.2
    );
    return {
      magnitude,
      mutation_delta: dMut,
      vpn_delta: dVpn,
      stability_delta: dStab,
      protocol_dna_delta: dProto,
    };
  }

  static _setDelta(a = [], b = []) {
    const sa = new Set(a);
    const sb = new Set(b);
    let sym = 0;
    for (const x of sa) if (!sb.has(x)) sym++;
    for (const x of sb) if (!sa.has(x)) sym++;
    return Math.min(1, sym / Math.max(1, sa.size + sb.size));
  }

  static _inferSubtype(snap) {
    const sig = (snap.transport_signature || []).join(' ');
    if (sig.includes('mesh') || sig.includes('vpn')) return 'mesh_vpn_node';
    if ((snap.protocol_dna || []).some((p) => String(p).includes('INGRESS_SPIKE'))) {
      return 'burst_exfil_subtype';
    }
    if ((snap.jitter_profile?.variance ?? 0) > 2) return 'beaconing_subtype';
    return 'general_operational';
  }

  static _diffEdges(baseEdges, branchEdges) {
    const key = (e) => `${e.source}:${e.target}:${e.relationship}`;
    const bSet = new Map(baseEdges.map((e) => [key(e), e]));
    const brSet = new Map(branchEdges.map((e) => [key(e), e]));

    const disappeared = [];
    const emerged = [];
    const weakened = [];

    for (const [k, e] of bSet) {
      if (!brSet.has(k)) {
        disappeared.push(e);
      } else {
        const br = brSet.get(k);
        if ((br.confidence ?? 1) < (e.confidence ?? 1) - 0.15) {
          weakened.push({ ...e, confidence_delta: (br.confidence ?? 0) - (e.confidence ?? 0) });
        }
      }
    }
    for (const [k, e] of brSet) {
      if (!bSet.has(k)) emerged.push(e);
    }

    return {
      disappeared,
      emerged,
      weakened,
      net_delta: branchEdges.length - baseEdges.length,
      field_resonance_lost: disappeared.filter((e) => e.relationship === 'field_resonance'),
      field_resonance_gained: emerged.filter((e) => e.relationship === 'field_resonance'),
    };
  }

  static _clusterCoherence(baseGraph, branchGraph) {
    const byRel = (edges) => {
      const m = {};
      for (const e of edges || []) {
        const r = e.relationship || 'unknown';
        m[r] = (m[r] || 0) + 1;
      }
      return m;
    };
    const base = byRel(baseGraph?.cross_host_edges);
    const br = byRel(branchGraph?.cross_host_edges);
    const decohered = [];
    for (const rel of new Set([...Object.keys(base), ...Object.keys(br)])) {
      const d = (br[rel] || 0) - (base[rel] || 0);
      if (d < 0) decohered.push({ relationship: rel, cluster_loss: -d });
    }
    return { decohered_clusters: decohered, baseline_counts: base, branch_counts: br };
  }

  static _newlyCausalEvents(source, branched) {
    const sourceKeys = new Set(source.map((e) => `${e.ts}:${e.type}:${e.entity_id}`));
    return branched
      .filter((e) => !sourceKeys.has(`${e.ts}:${e.type}:${e.entity_id}`))
      .slice(0, 20)
      .map((e) => ({
        event_id: e.event_id,
        type: e.type,
        ts: e.ts,
        entity_id: e.entity_id,
        role: 'newly_causal_in_branch',
      }));
  }

  /**
   * BFS from lesion events along causal_parents / causal graph.
   */
  static _propagateShockwave(lesionEvents, causalExport, allEvents) {
    const eventById = new Map((causalExport.events || []).map((e) => [e.event_id, e]));
    const childrenOf = new Map();
    for (const e of causalExport.events || []) {
      for (const pid of e.causal_parents || []) {
        if (!childrenOf.has(pid)) childrenOf.set(pid, []);
        childrenOf.get(pid).push(e.event_id);
      }
    }
    for (const e of causalExport.edges || []) {
      if (!childrenOf.has(e.source_id)) childrenOf.set(e.source_id, []);
      childrenOf.get(e.source_id).push(e.target_id);
    }

    const visited = new Set();
    const waves = [];
    const affected_hosts = new Set();
    let max_depth = 0;

    const seeds = lesionEvents.map((e) => e.event_id).filter(Boolean);
    const queue = seeds.map((id) => ({ id, depth: 0 }));

    while (queue.length > 0) {
      const { id, depth } = queue.shift();
      if (visited.has(id)) continue;
      visited.add(id);
      max_depth = Math.max(max_depth, depth);

      const evt = eventById.get(id);
      if (evt) {
        waves.push({
          event_id: id,
          depth,
          type: evt.type,
          entity_id: evt.entity_id,
          ts: evt.ts,
        });
        const hostHint = evt.value?.host_id || evt.entity_id?.split?.('/')?.[0];
        if (hostHint) affected_hosts.add(hostHint);
      }

      for (const childId of childrenOf.get(id) || []) {
        if (!visited.has(childId)) {
          queue.push({ id: childId, descendent_of_lesion: true, depth: depth + 1 });
        }
      }
    }

    return {
      waves: waves.slice(0, 100),
      max_depth,
      affected_hosts: [...affected_hosts],
      seed_count: seeds.length,
    };
  }

  static _fieldHarmonicDelta(baseline, branch, bifurcation) {
    const collapsed = bifurcation
      .filter((b) => Math.abs(b.vpn_delta ?? 0) > 0.15 || (b.magnitude ?? 0) > 0.25)
      .map((b) => ({
        host_id: b.host_id,
        harmonic_collapse_score: b.magnitude,
        reason: 'identity_field_decoupling',
      }));
    return {
      collapsed_hosts: collapsed,
      branch_edge_resonance_net: (branch.graph?.cross_host_edges || [])
        .filter((e) => e.relationship === 'field_resonance').length,
      baseline_edge_resonance_net: (baseline.graph?.cross_host_edges || [])
        .filter((e) => e.relationship === 'field_resonance').length,
    };
  }

  /**
   * Hosts that bifurcated similarly → hidden operational affinity candidate.
   */
  static _hiddenAffinityCandidates(bifurcation, edgeTopology) {
    const candidates = [];
    for (let i = 0; i < bifurcation.length; i++) {
      for (let j = i + 1; j < bifurcation.length; j++) {
        const a = bifurcation[i];
        const b = bifurcation[j];
        const sync =
          Math.abs((a.vpn_delta ?? 0) - (b.vpn_delta ?? 0)) < 0.08 &&
          Math.abs((a.mutation_delta ?? 0) - (b.mutation_delta ?? 0)) <= 1;
        if (sync) {
          candidates.push({
            host_a: a.host_id,
            host_b: b.host_id,
            affinity: 'co_divergence_under_lesion',
            confidence: 0.5 + (1 - Math.abs(a.magnitude - b.magnitude)) * 0.3,
          });
        }
      }
    }
    return candidates.slice(0, 20);
  }
}

if (typeof window !== 'undefined') {
  window.CausalShockwave = CausalShockwave;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { CausalShockwave };
}
