/**
 * HostCognitionGraph.js — In-memory temporal cognition graph
 *
 * HostNode {
 *   identity, state, events[], edges[], embeddings[]
 * }
 */

class HostNode {
  constructor(host_id, identity) {
    this.host_id = host_id;
    this.identity = identity;
    this.state = {
      trust: null,
      pressure: 0,
      entropy: 0,
      volatility: 0,
      last_simTime: 0,
    };
    this.events = [];
    this.edges = [];
    this.embeddings = [];
    this.maxEvents = 500;
    this.maxEdges = 200;
  }

  recordEvent(event, simTime) {
    this.events.push({
      event_id: event.event_id,
      type: event.type,
      ts: event.ts ?? simTime,
      entity_id: event.entity_id,
      confidence: event.confidence,
    });
    if (this.events.length > this.maxEvents) {
      this.events.shift();
    }
    this.state.last_simTime = simTime;
  }

  addEdge(edge) {
    this.edges.push(edge);
    if (this.edges.length > this.maxEdges) {
      this.edges.shift();
    }
  }

  setBehavioralState(partial) {
    Object.assign(this.state, partial);
  }

  toJSON() {
    return {
      host_id: this.host_id,
      identity: this.identity?.toJSON?.() ?? this.identity,
      state: { ...this.state },
      events: [...this.events],
      edges: [...this.edges],
      embeddings: [...this.embeddings],
    };
  }
}

class HostCognitionGraph {
  constructor(opts = {}) {
    this.nodes = new Map();
    this.crossHostEdges = [];
    this.maxCrossHostEdges = opts.maxCrossHostEdges ?? 5000;
  }

  getOrCreate(host_id, identityFactory) {
    let node = this.nodes.get(host_id);
    if (!node) {
      const identity = identityFactory(host_id);
      node = new HostNode(host_id, identity);
      this.nodes.set(host_id, node);
    }
    return node;
  }

  getNode(host_id) {
    return this.nodes.get(host_id) ?? null;
  }

  recordHostEvent(host_id, identity, event, simTime, behavioralState = {}) {
    const node = this.getOrCreate(host_id, () => identity);
    if (node.identity !== identity) node.identity = identity;
    node.recordEvent(event, simTime);
    node.setBehavioralState(behavioralState);
    return node;
  }

  /**
   * Cross-host causal / correlation edge (host-level, not event-level).
   */
  linkHosts(sourceHostId, targetHostId, relationship, confidence, simTime, meta = {}) {
    const edge = {
      source: sourceHostId,
      target: targetHostId,
      relationship,
      confidence: Math.min(1, Math.max(0, confidence)),
      simTime,
      ...meta,
    };
    this.crossHostEdges.push(edge);
    if (this.crossHostEdges.length > this.maxCrossHostEdges) {
      this.crossHostEdges.shift();
    }

    const src = this.nodes.get(sourceHostId);
    const dst = this.nodes.get(targetHostId);
    if (src) src.addEdge({ ...edge, direction: 'out' });
    if (dst) dst.addEdge({ ...edge, direction: 'in' });
    return edge;
  }

  getEventsUpTo(simTime) {
    const out = [];
    for (const node of this.nodes.values()) {
      for (const e of node.events) {
        if (e.ts <= simTime) out.push({ host_id: node.host_id, ...e });
      }
    }
    out.sort((a, b) => a.ts - b.ts);
    return out;
  }

  exportGraph() {
    return {
      nodes: Array.from(this.nodes.values()).map((n) => n.toJSON()),
      cross_host_edges: [...this.crossHostEdges],
      node_count: this.nodes.size,
      edge_count: this.crossHostEdges.length,
    };
  }
}

if (typeof window !== 'undefined') {
  window.HostNode = HostNode;
  window.HostCognitionGraph = HostCognitionGraph;
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { HostNode, HostCognitionGraph };
}
