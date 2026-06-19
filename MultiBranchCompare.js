/**
 * MultiBranchCompare.js — Comparative counterfactual analysis
 *
 * Identifies robust semantic structure vs fragile artifacts across branch trees.
 */

class MultiBranchCompare {
  /**
   * @param {ScytheCognitionRuntime} runtime
   */
  constructor(runtime) {
    this.runtime = runtime;
  }

  /**
   * Run and compare multiple counterfactual branches.
   *
   * @param {Array<Object>} branchSpecs - each passed to branchReplay()
   * @returns comparative topology
   */
  compareBranches(branchSpecs = []) {
    if (!branchSpecs.length) {
      return { error: 'no branches specified' };
    }

    const results = branchSpecs.map((spec, idx) => {
      const label = spec.label ?? `branch_${idx}`;
      const result = this.runtime.branchReplay({ ...spec, label });
      return { label, result };
    });

    const baseline = this.runtime.exportCausalityForReplay();
    const baselineEventIds = new Set((baseline.events || []).map((e) => e.event_id));

    const stable_causal_core = [];
    const branch_sensitive_identities = new Map();
    const invariant_motifs = new Map();
    const divergent_narratives = [];
    const overlap_topology = {
      shared_bifurcation_hosts: [],
      shared_extinction_hosts: [],
      shared_affinity_pairs: [],
    };

    const shockwaves = results.map((r) => ({
      label: r.label,
      topo: r.result?.divergence?.shockwave_topology,
      overall: r.result?.divergence?.overall_score ?? 0,
    }));

    const bifurcationByHost = new Map();
    const extinctionByHost = new Map();
    const affinityPairCounts = new Map();

    for (const { label, topo } of shockwaves) {
      if (!topo) continue;

      for (const b of topo.identity_bifurcation ?? []) {
        if (!bifurcationByHost.has(b.host_id)) bifurcationByHost.set(b.host_id, []);
        bifurcationByHost.get(b.host_id).push(label);
      }
      for (const e of topo.identity_extinction ?? []) {
        if (!extinctionByHost.has(e.host_id)) extinctionByHost.set(e.host_id, []);
        extinctionByHost.get(e.host_id).push(label);
      }
      for (const c of topo.hidden_affinity_candidates ?? []) {
        const pk = c.host_a < c.host_b ? `${c.host_a}|${c.host_b}` : `${c.host_b}|${c.host_a}`;
        affinityPairCounts.set(pk, (affinityPairCounts.get(pk) || 0) + 1);
      }
    }

    const branchCount = results.length;

    for (const [hostId, labels] of bifurcationByHost) {
      if (labels.length === branchCount) {
        overlap_topology.shared_bifurcation_hosts.push({ host_id: hostId, branches: labels });
      } else if (labels.length === 1) {
        branch_sensitive_identities.set(hostId, {
          sensitive_to: labels[0],
          type: 'unique_bifurcation',
        });
      } else {
        branch_sensitive_identities.set(hostId, {
          sensitive_to: labels,
          type: 'partial_bifurcation',
        });
      }
    }

    for (const [hostId, labels] of extinctionByHost) {
      if (labels.length === branchCount) {
        overlap_topology.shared_extinction_hosts.push({ host_id: hostId, branches: labels });
      }
    }

    for (const [pair, count] of affinityPairCounts) {
      if (count >= Math.max(2, Math.ceil(branchCount * 0.5))) {
        const [host_a, host_b] = pair.split('|');
        overlap_topology.shared_affinity_pairs.push({ host_a, host_b, branch_overlap: count });
      }
    }

    for (const { label, result } of results) {
      const removed = (result?.mutations?.removed ?? 0) > 0;
      const sensitive = result?.divergence?.sensitive_events ?? [];
      for (const evt of sensitive) {
        if (baselineEventIds.has(evt.event_id)) {
          const existing = stable_causal_core.find((x) => x.event_id === evt.event_id);
          if (!existing) {
            stable_causal_core.push({
              ...evt,
              load_bearing_score: 0,
              branches_where_sensitive: [],
            });
          }
          const entry = stable_causal_core.find((x) => x.event_id === evt.event_id);
          if (entry && !entry.branches_where_sensitive.includes(label)) {
            entry.branches_where_sensitive.push(label);
            entry.load_bearing_score = entry.branches_where_sensitive.length / branchCount;
          }
        }
      }

      const narratives = this.runtime.getOperationalNarratives?.() ?? [];
      for (const n of narratives) {
        const key = n.narrative ?? 'unknown';
        if (!invariant_motifs.has(key)) invariant_motifs.set(key, []);
        invariant_motifs.get(key).push(label);
      }
    }

    const invariant_motifs_out = [];
    for (const [motif, branches] of invariant_motifs) {
      if (branches.length >= branchCount) {
        invariant_motifs_out.push({ motif, stable_across: branches });
      } else {
        divergent_narratives.push({ narrative: motif, branches });
      }
    }

    const load_bearing_events = stable_causal_core
      .filter((e) => e.load_bearing_score >= 0.5)
      .sort((a, b) => b.load_bearing_score - a.load_bearing_score);

    const propagation_profiles = shockwaves.map(({ label, topo }) => ({
      label,
      max_depth: topo?.shockwave_front?.max_depth ?? 0,
      affected_hosts: topo?.shockwave_front?.affected_hosts?.length ?? 0,
      wave_count: topo?.shockwave_front?.waves?.length ?? 0,
      velocity_proxy: (topo?.shockwave_front?.waves?.length ?? 0) /
        Math.max(1, topo?.shockwave_front?.max_depth ?? 1),
    }));

    return {
      branch_count: branchCount,
      branch_labels: results.map((r) => r.label),
      overlap_topology,
      stable_causal_core: load_bearing_events,
      branch_sensitive_identities: Object.fromEntries(branch_sensitive_identities),
      invariant_motifs: invariant_motifs_out,
      divergent_narratives,
      propagation_profiles,
      branch_scores: results.map((r) => ({
        label: r.label,
        overall_divergence: r.result?.divergence?.overall_score,
      })),
      results,
    };
  }
}

if (typeof window !== 'undefined') {
  window.MultiBranchCompare = MultiBranchCompare;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { MultiBranchCompare };
}
