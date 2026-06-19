/**
 * RouteGenealogy.js — Evolutionary lineage of route genomes
 *
 * Tracks the speciation and mutation of route genomes (e.g., how a Verizon core
 * route forks into a GitHub lineage or a Tailscale lineage). Also bridges 
 * divergences into standard SCYTHE events (ROUTE_GENOME_DIVERGENCE).
 */

function _jaccard(a, b) {
  if (!a?.length || !b?.length) return 0;
  const sa = new Set(a);
  const sb = new Set(b);
  let inter = 0;
  for (const x of sa) if (sb.has(x)) inter++;
  const union = sa.size + sb.size - inter;
  return union ? inter / union : 0;
}

class RouteGenealogy {
  constructor(opts = {}) {
    this.mergeThreshold = opts.mergeThreshold ?? 0.85;
    this.forkThreshold = opts.forkThreshold ?? 0.40;
    this.decayHalfLifeMs = opts.decayHalfLifeMs ?? 600_000;

    /** @type {Map<string, Object>} */
    this.lineage = new Map();
    this.canonical = new Map();
  }

  _node(routeId) {
    if (!this.lineage.has(routeId)) {
      this.lineage.set(routeId, {
        route_id: routeId,
        parent_id: null,
        children: [],
        forks: [], // Kept for compatibility, acts as speciation_events
        speciation_events: [],
        extinction_events: [],
        merged_from: [],
        merged_into: null,
        confidence: 1.0,
        last_seen_simTime: 0,
        created_simTime: 0,
      });
    }
    return this.lineage.get(routeId);
  }

  canonicalId(routeId) {
    let id = routeId;
    let guard = 0;
    while (this.canonical.has(id) && guard++ < 32) {
      id = this.canonical.get(id);
    }
    return id;
  }

  register(routeId, genome, simTime) {
    const canon = this.canonicalId(routeId);
    const node = this._node(canon);
    node.last_seen_simTime = simTime;
    if (!node.created_simTime) node.created_simTime = simTime;
    node.genome_ref = genome;
    return canon;
  }

  /**
   * Compare two genomes to see if they are essentially the same routing identity.
   */
  scoreMerge(idA, idB, genomeA, genomeB) {
    const sequenceOverlap = _jaccard(genomeA?.core_sequence, genomeB?.core_sequence);
    
    // Closer latency = higher score
    let latencySimilarity = 1.0;
    if (genomeA?.latency_profile?.mean != null && genomeB?.latency_profile?.mean != null) {
      const a = genomeA.latency_profile.mean;
      const b = genomeB.latency_profile.mean;
      latencySimilarity = 1 - Math.min(1, Math.abs(a - b) / Math.max(a, 0.1));
    }

    return Math.min(1, sequenceOverlap * 0.7 + latencySimilarity * 0.3);
  }

  /**
   * Merge b into a (a becomes canonical).
   */
  merge(routeA, routeB, confidence = 0.8) {
    const canonA = this.canonicalId(routeA);
    const canonB = this.canonicalId(routeB);
    if (canonA === canonB) return canonA;

    const nodeA = this._node(canonA);
    const nodeB = this._node(canonB);

    nodeA.merged_from.push(canonB);
    nodeA.confidence = Math.min(1, nodeA.confidence * 0.5 + confidence * 0.5);
    nodeB.merged_into = canonA;
    nodeB.confidence *= 0.3;

    this.canonical.set(canonB, canonA);
    return canonA;
  }

  /**
   * Create a new evolutionary branch from an ancestor route.
   */
  fork(parentId, newRouteId, reason, simTime) {
    const parent = this._node(this.canonicalId(parentId));
    const child = this._node(newRouteId);
    child.parent_id = parent.route_id;
    child.created_simTime = simTime;
    parent.children.push(newRouteId);
    
    const event = { child_id: newRouteId, reason, simTime };
    parent.forks.push(event);
    parent.speciation_events.push(event);
    
    return newRouteId;
  }

  /**
   * Mark a route as extinct (disappeared from routing tables/observations).
   */
  extinct(routeId, reason, simTime) {
    const canon = this.canonicalId(routeId);
    const node = this._node(canon);
    
    node.extinction_events.push({
      reason,
      simTime,
      last_confidence: node.confidence
    });
    
    // Extinction heavily decays confidence
    node.confidence *= 0.1;
    
    return canon;
  }
  
  /**
   * Emits a ROUTE_GENOME_DIVERGENCE event structure.
   */
  createDivergenceEvent(routeId, divergenceMetrics, simTime) {
    // Determine severity based on how much it shifted
    const sequenceSeverity = divergenceMetrics.sequence_change || 0;
    const latencySeverity = Math.min(1.0, divergenceMetrics.latency_shift || 0);
    const severity = Math.max(sequenceSeverity, latencySeverity);

    return {
      type: 'ROUTE_GENOME_DIVERGENCE',
      timestamp: simTime || Date.now(),
      entity_id: routeId,
      divergence: {
        sequence_change: sequenceSeverity,
        latency_shift: latencySeverity,
        transit_mutation: sequenceSeverity > 0.3
      },
      severity: severity
    };
  }

  decay(simTime) {
    for (const node of this.lineage.values()) {
      if (!node.last_seen_simTime) continue;
      const age = simTime - node.last_seen_simTime;
      if (age <= 0) continue;
      const decay = Math.pow(0.5, age / this.decayHalfLifeMs);
      node.confidence = Math.max(0.05, node.confidence * decay);
    }
  }

  getDescendants(routeId) {
    const descendants = [];
    const node = this._node(this.canonicalId(routeId));
    
    // Breadth-first collect
    const queue = [...node.children];
    while (queue.length > 0) {
        const currId = queue.shift();
        descendants.push(currId);
        const currNode = this._node(currId);
        if (currNode.children.length > 0) {
            queue.push(...currNode.children);
        }
    }
    
    return descendants;
  }

  exportGenealogy() {
    return {
      nodes: Array.from(this.lineage.values()),
      canonical_map: Object.fromEntries(this.canonical),
    };
  }
}

if (typeof window !== 'undefined') {
  window.RouteGenealogy = RouteGenealogy;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { RouteGenealogy };
}
