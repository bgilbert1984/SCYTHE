export class LiveGraphController {
  constructor({apiBase = "", fetchImpl = globalThis.fetch, refreshMilliseconds = 2000,
               nodeLimit = 200, edgeLimit = 300} = {}) {
    this.apiBase = apiBase; this.fetchImpl = fetchImpl;
    this.refreshMilliseconds = Math.max(500, Number(refreshMilliseconds) || 2000);
    this.nodeLimit = Math.min(Math.max(Number(nodeLimit) || 200, 1), 500);
    this.edgeLimit = Math.min(Math.max(Number(edgeLimit) || 300, 1), 1000);
    this.listeners = new Set(); this.timer = null; this.running = false;
    this.graphRevision = null; this.snapshot = null; this.status = null;
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
    const graphUrl = `${this.apiBase}/api/graphops/selection/graph?node_limit=${this.nodeLimit}&edge_limit=${this.edgeLimit}`;
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
      if (changed || !this.snapshot) this.snapshot = graph;
      return this.#publish({kind: "snapshot", graph: this.snapshot, eve, changed, available: true,
        message: `LIVE HYPERGRAPH // ${graph.status.toUpperCase()} // ${graph.nodeCount ?? graph.nodes?.length ?? 0} NODES // ${graph.edgeCount ?? graph.edges?.length ?? 0} EDGES\nEVE // ${eve.status?.toUpperCase() ?? "UNKNOWN"} // ${eve.committed ?? 0} COMMITTED // RAW PACKETS NOT EXPOSED`});
    } catch (error) {
      return this.#publish({kind: "status", graph: this.snapshot, eve: null, available: false,
        error, message: `LIVE HYPERGRAPH // UNAVAILABLE // ${error.message}`});
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

  destroy() {
    this.running = false; clearTimeout(this.timer); this.timer = null;
    this.listeners.clear();
  }
}
