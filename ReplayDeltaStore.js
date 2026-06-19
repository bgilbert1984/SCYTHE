/**
 * ReplayDeltaStore.js — Persistent differential replay trees (delta branches, not snapshots)
 *
 * Primary intelligence artifact: which perturbations mattered, what fractured, what held.
 */

class ReplayDeltaNode {
  constructor(opts = {}) {
    this.id = opts.id ?? `node_${Date.now()}`;
    this.parent_id = opts.parent_id ?? null;
    this.label = opts.label ?? 'root';
    this.lesion_family = opts.lesion_family ?? 'event';
    this.from_simTime = opts.from_simTime ?? 0;
    this.mutations = opts.mutations ?? {};
    this.divergence_score = opts.divergence_score ?? 0;
    this.shockwave_summary = opts.shockwave_summary ?? null;
    this.stable_core_count = opts.stable_core_count ?? 0;
    this.created_at = Date.now();
    this.children = [];
  }
}

class ReplayDeltaStore {
  constructor(opts = {}) {
    this.maxNodes = opts.maxNodes ?? 200;
    this.nodes = new Map();
    this.root_id = null;
    this._persistKey = opts.persistKey ?? 'scythe:replay:delta_tree';
  }

  /**
   * Record a branch result as a delta node (not full state snapshot).
   */
  recordBranch(branchResult, meta = {}) {
    const parentId = meta.parent_id ?? this.root_id;
    const node = new ReplayDeltaNode({
      parent_id: parentId,
      label: branchResult.label ?? meta.label,
      lesion_family: meta.lesion_family ?? 'event',
      from_simTime: branchResult.from ?? 0,
      mutations: branchResult.mutations ?? {},
      divergence_score: branchResult.divergence?.overall_score ?? 0,
      shockwave_summary: ReplayDeltaStore._summarizeShockwave(
        branchResult.divergence?.shockwave_topology
      ),
      stable_core_count: branchResult.divergence?.stable_causal_core?.length ?? 0,
    });

    this.nodes.set(node.id, node);
    if (!this.root_id && !parentId) this.root_id = node.id;
    if (parentId && this.nodes.has(parentId)) {
      this.nodes.get(parentId).children.push(node.id);
    }

    while (this.nodes.size > this.maxNodes) {
      this._pruneLeaf();
    }

    this._persistAsync();
    return node;
  }

  static _summarizeShockwave(topo) {
    if (!topo) return null;
    return {
      bifurcation_count: topo.identity_bifurcation?.length ?? 0,
      extinction_count: topo.identity_extinction?.length ?? 0,
      speciation_count: topo.identity_speciation?.length ?? 0,
      max_depth: topo.shockwave_front?.max_depth ?? 0,
      affinity_candidates: topo.hidden_affinity_candidates?.length ?? 0,
      edges_lost: topo.edge_topology?.disappeared?.length ?? 0,
    };
  }

  /**
   * Record multi-branch comparison as tree fan-out.
   */
  recordComparison(comparison, parentMeta = {}) {
    const parent = this.recordBranch(
      { label: 'baseline_compare', from: 0, divergence: { overall_score: 0 } },
      { ...parentMeta, lesion_family: 'multi' }
    );
    const childIds = [];
    for (const r of comparison.results ?? []) {
      const child = this.recordBranch(r.result, {
        parent_id: parent.id,
        label: r.label,
        lesion_family: parentMeta.lesion_family ?? 'event',
      });
      childIds.push(child.id);
    }
    return { parent_id: parent.id, child_ids: childIds };
  }

  getTree(rootId = null) {
    const rid = rootId ?? this.root_id;
    if (!rid || !this.nodes.has(rid)) return null;
    const build = (id) => {
      const n = this.nodes.get(id);
      return {
        ...n,
        children: n.children.map((cid) => build(cid)).filter(Boolean),
      };
    };
    return build(rid);
  }

  exportParquetReady() {
    const rows = [];
    for (const n of this.nodes.values()) {
      rows.push({
        id: n.id,
        parent_id: n.parent_id,
        label: n.label,
        lesion_family: n.lesion_family,
        from_simTime: n.from_simTime,
        divergence_score: n.divergence_score,
        mutations_removed: n.mutations?.removed ?? 0,
        stable_core_count: n.stable_core_count,
        ...n.shockwave_summary,
      });
    }
    return { schema: 'replay_delta_v1', rows };
  }

  _pruneLeaf() {
    for (const [id, n] of this.nodes) {
      if (n.children.length === 0 && id !== this.root_id) {
        this.nodes.delete(id);
        for (const other of this.nodes.values()) {
          other.children = other.children.filter((c) => c !== id);
        }
        return;
      }
    }
  }

  _persistAsync() {
    try {
      const payload = {
        root_id: this.root_id,
        nodes: Array.from(this.nodes.entries()).map(([id, n]) => [
          id,
          {
            id: n.id,
            parent_id: n.parent_id,
            label: n.label,
            lesion_family: n.lesion_family,
            from_simTime: n.from_simTime,
            mutations: n.mutations,
            divergence_score: n.divergence_score,
            shockwave_summary: n.shockwave_summary,
            stable_core_count: n.stable_core_count,
            children: n.children,
          },
        ]),
      };
      const key = typeof window !== 'undefined'
        ? `${this._persistKey}:${window.SCYTHE_INSTANCE_ID || 'local'}`
        : this._persistKey;
      localStorage.setItem(key, JSON.stringify(payload));
    } catch (_) { /* quota */ }
  }
}

/** Lesion family helpers for orthogonal causal tomography */
const LESION_FAMILIES = Object.freeze({
  EVENT: 'event',
  TEMPORAL: 'temporal',
  IDENTITY: 'identity',
  FIELD: 'field',
  PROTOCOL: 'protocol',
  RESONANCE: 'resonance',
  NARRATIVE: 'narrative',
});

function createLesionMutators() {
  return {
    suppressEventType(type) {
      return (event) => (event.type === type ? null : event);
    },
    temporalJitter(meanMs = 1200, stdMs = 300) {
      return (event) => {
        const jitter = meanMs + (Math.random() - 0.5) * 2 * stdMs;
        return { ...event, ts: (event.ts ?? 0) + jitter };
      };
    },
    suppressIdentitySubtype(subtype) {
      return (event, ctx = {}) => {
        if (ctx.identity?.speciation_subtype === subtype) return null;
        return event;
      };
    },
    suppressNarrative(narrativePrefix) {
      return (event, ctx = {}) => {
        if ((ctx.narrative || '').startsWith(narrativePrefix)) return null;
        return event;
      };
    },
  };
}

if (typeof window !== 'undefined') {
  window.ReplayDeltaStore = ReplayDeltaStore;
  window.ReplayDeltaNode = ReplayDeltaNode;
  window.LESION_FAMILIES = LESION_FAMILIES;
  window.createLesionMutators = createLesionMutators;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    ReplayDeltaStore,
    ReplayDeltaNode,
    LESION_FAMILIES,
    createLesionMutators,
  };
}
