export class LiveGraphController {
  constructor({apiBase = "", fetchImpl = globalThis.fetch, refreshMilliseconds = 2000,
               nodeLimit = 200, edgeLimit = 300} = {}) {
    this.apiBase = apiBase; this.fetchImpl = fetchImpl;
    this.refreshMilliseconds = Math.max(500, Number(refreshMilliseconds) || 2000);
    this.nodeLimit = Math.min(Math.max(Number(nodeLimit) || 200, 1), 500);
    this.edgeLimit = Math.min(Math.max(Number(edgeLimit) || 300, 1), 1000);
    this.listeners = new Set(); this.timer = null; this.running = false;
    this.graphRevision = null; this.snapshot = null; this.status = null;
    this.liveness = new Map(); this.livenessCursor = 0; this.livenessRevision = 0;
    this.focusId = "";
  }

  subscribe(listener) {
    if (typeof listener !== "function") throw new TypeError("live graph listener must be a function");
    this.listeners.add(listener);
    if (this.status) listener(this.status);
    return () => this.listeners.delete(listener);
  }

  async start() {
    if (this.running) return this;
    this.running = true;
    await this.refresh();
    this.#schedule();
    return this;
  }

  #schedule() {
    clearTimeout(this.timer);
    if (this.running) this.timer = setTimeout(async () => {
      try { await this.refresh(); } finally { this.#schedule(); }
    }, this.refreshMilliseconds);
  }

  async refresh() {
    const graphQuery = new URLSearchParams({node_limit: String(this.nodeLimit), edge_limit: String(this.edgeLimit)});
    if (this.focusId) graphQuery.set("focus_id", this.focusId);
    const graphUrl = `${this.apiBase}/api/graphops/selection/graph?${graphQuery}`;
    const statusUrl = `${this.apiBase}/api/graphops/eve/status`;
    try {
      const [graphResponse, eveResponse] = await Promise.all([
        this.fetchImpl.call(globalThis, graphUrl, {credentials: "same-origin", cache: "no-store"}),
        this.fetchImpl.call(globalThis, statusUrl, {credentials: "same-origin", cache: "no-store"}),
      ]);
      const graph = await graphResponse.json();
      const eve = eveResponse.ok ? await eveResponse.json() : {status: "unavailable", committed: 0};
      if (!graphResponse.ok || !["ok", "empty"].includes(graph.status)) {
        return this.#publish({kind: "status", graph, eve, available: false,
          message: `LIVE HYPERGRAPH // UNAVAILABLE // HTTP ${graphResponse.status}`});
      }
      const changed = graph.graphRevision !== this.graphRevision;
      this.graphRevision = graph.graphRevision;
      const livenessChanged = await this.#probeNextHost(graph);
      const currentIds = new Set((graph.nodes ?? []).map((node) => node.id));
      for (const id of this.liveness.keys()) if (!currentIds.has(id)) this.liveness.delete(id);
      this.snapshot = {...graph, livenessRevision: this.livenessRevision,
        nodes: (graph.nodes ?? []).map((node) => ({...node,
          ...(this.liveness.has(node.id) ? {liveness: this.liveness.get(node.id)} : {})}))};
      const counts = {active: 0, inactive: 0};
      for (const node of this.snapshot.nodes) if (node.liveness?.state in counts) counts[node.liveness.state] += 1;
      const hostCount = this.snapshot.nodes.filter((node) => this.#isHost(node)).length;
      const unknown = Math.max(0, hostCount - counts.active - counts.inactive);
      const detectedNodes = graph.detectedNodeCount ?? graph.nodeCount ?? graph.nodes?.length ?? 0;
      const detectedEdges = graph.detectedEdgeCount ?? graph.edgeCount ?? graph.edges?.length ?? 0;
      const displayedNodes = graph.displayedNodeCount ?? graph.nodes?.length ?? 0;
      const displayedEdges = graph.displayedEdgeCount ?? graph.edges?.length ?? 0;
      const ranking = graph.ranking ?? {};
      const lens = ranking.lens ?? "SOURCE ORDER";
      const suppressedNodes = ranking.suppressedNodes ?? Math.max(0, detectedNodes - displayedNodes);
      const suppressedEdges = ranking.suppressedEdges ?? Math.max(0, detectedEdges - displayedEdges);
      return this.#publish({kind: "snapshot", graph: this.snapshot, eve,
        changed: changed || livenessChanged, graphChanged: changed, livenessChanged, available: true,
        message: `LIVE HYPERGRAPH // ${graph.status.toUpperCase()}\nDETECTED // ${detectedNodes} NODES // ${detectedEdges} EDGES\nDISPLAYED // ${displayedNodes} / ${detectedNodes} NODES // ${displayedEdges} / ${detectedEdges} EDGES // BOUNDED ${this.nodeLimit}N·${this.edgeLimit}E\nLENS // ${lens} // SUPPRESSED ${suppressedNodes}N·${suppressedEdges}E${ranking.focusId ? ` // PINNED ${ranking.focusId}` : ""}\nHOST PING // ${counts.active} ACTIVE // ${counts.inactive} INACTIVE // ${unknown} UNKNOWN // ROUND ROBIN\nEVE // ${eve.status?.toUpperCase() ?? "UNKNOWN"} // ${eve.committed ?? 0} COMMITTED // ${eve.replayed ?? 0} BOOTSTRAP REPLAYED // ${eve.deduplicated ?? 0} DEDUPLICATED // RAW PACKETS NOT EXPOSED`});
    } catch (error) {
      return this.#publish({kind: "status", graph: this.snapshot, eve: null, available: false,
        error, message: `LIVE HYPERGRAPH // UNAVAILABLE // ${error.message}`});
    }
  }

  #isHost(node) {
    return String(node?.kind ?? "").toLowerCase() === "network_host" || String(node?.id ?? "").startsWith("host:");
  }

  async #probeNextHost(graph) {
    const hosts = (graph.nodes ?? []).filter((node) => this.#isHost(node));
    if (!hosts.length || !graph.graphRevision) return false;
    const node = hosts[this.livenessCursor % hosts.length];
    this.livenessCursor = (this.livenessCursor + 1) % hosts.length;
    try {
      const response = await this.fetchImpl.call(globalThis, `${this.apiBase}/api/graphops/host-liveness`, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({entityId: node.id, graphRevision: graph.graphRevision}),
      });
      if (!response.ok) return false;
      const observation = await response.json();
      if (!["active", "inactive", "unknown"].includes(observation.state)) return false;
      const previous = this.liveness.get(node.id) ?? {state: "unknown", consecutiveFailures: 0};
      const failures = observation.state === "inactive" ? previous.consecutiveFailures + 1 : 0;
      // A single lost echo is not enough to paint a host red. Active is
      // immediate; inactive requires two consecutive round-robin observations.
      const state = observation.state === "inactive" && failures < 2 ? previous.state : observation.state;
      const next = {state, consecutiveFailures: failures, rttMs: observation.rttMs ?? null,
        tool: observation.tool ?? null, observedAt: observation.observedAt ?? null,
        evidenceClass: observation.evidenceClass ?? "UNAVAILABLE"};
      const changed = JSON.stringify(previous) !== JSON.stringify(next);
      this.liveness.set(node.id, next);
      if (changed) this.livenessRevision += 1;
      return changed;
    } catch {
      return false;
    }
  }

  #publish(update) {
    this.status = update;
    for (const listener of this.listeners) {
      try { listener(update); }
      catch (error) { globalThis.console?.error?.("[SCYTHE] Live graph listener failed", error); }
    }
    return update.graph;
  }

  setFocus(entityId) {
    const next = String(entityId ?? "").slice(0, 256);
    if (next === this.focusId) return false;
    this.focusId = next;
    if (this.running) void this.refresh();
    return true;
  }

  destroy() {
    this.running = false; clearTimeout(this.timer); this.timer = null;
    this.listeners.clear(); this.liveness.clear();
  }
}
