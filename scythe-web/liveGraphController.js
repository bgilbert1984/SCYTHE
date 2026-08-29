export const LIVE_GRAPH_DETAIL_TIERS = Object.freeze([
  Object.freeze({id: "overview", label: "OVERVIEW", nodeLimit: 300, edgeLimit: 600}),
  Object.freeze({id: "focused", label: "FOCUSED", nodeLimit: 400, edgeLimit: 800}),
  Object.freeze({id: "max", label: "MAX", nodeLimit: 500, edgeLimit: 1000}),
]);

export class LiveGraphController {
  constructor({apiBase = "", fetchImpl = globalThis.fetch, refreshMilliseconds = 2000,
               nodeLimit = 300, edgeLimit = 600, slowFrameMilliseconds = 28,
               slowFrameBudget = 45} = {}) {
    this.apiBase = apiBase; this.fetchImpl = fetchImpl;
    this.refreshMilliseconds = Math.max(500, Number(refreshMilliseconds) || 2000);
    this.detailTiers = LIVE_GRAPH_DETAIL_TIERS.map((tier, index) => index ? tier : Object.freeze({...tier,
      nodeLimit: Math.min(Math.max(Number(nodeLimit) || 300, 1), 400),
      edgeLimit: Math.min(Math.max(Number(edgeLimit) || 600, 1), 800)}));
    this.operatorMaxDetail = false; this.performanceTierCap = this.detailTiers.length - 1;
    this.slowFrameMilliseconds = Math.max(16, Number(slowFrameMilliseconds) || 28);
    this.slowFrameBudget = Math.max(3, Number(slowFrameBudget) || 45); this.slowFrameCount = 0;
    this.detailTierIndex = 0; this.nodeLimit = 300; this.edgeLimit = 600;
    this.listeners = new Set(); this.timer = null; this.running = false;
    this.graphRevision = null; this.presentationKey = null; this.snapshot = null; this.status = null;
    this.liveness = new Map(); this.livenessCursor = 0; this.livenessRevision = 0;
    this.focusId = ""; this.rfSensorContext = null; this.#applyDetailPolicy();
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
    const requestedNodeLimit = this.nodeLimit; const requestedEdgeLimit = this.edgeLimit;
    const requestedFocusId = this.focusId;
    const graphQuery = new URLSearchParams({node_limit: String(requestedNodeLimit),
      edge_limit: String(requestedEdgeLimit)});
    if (requestedFocusId) graphQuery.set("focus_id", requestedFocusId);
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
        const retained = this.snapshot;
        return this.#publish({kind: "status", graph: retained ?? graph, eve, available: false,
          retained: Boolean(retained), transportGraph: graph,
          message: retained
            ? `LIVE HYPERGRAPH // DEGRADED // RETAINING LAST SNAPSHOT // HTTP ${graphResponse.status}`
            : `LIVE HYPERGRAPH // UNAVAILABLE // HTTP ${graphResponse.status}`});
      }
      const graphChanged = graph.graphRevision !== this.graphRevision;
      const presentationKey = `${graph.graphRevision}:${requestedNodeLimit}:${requestedEdgeLimit}:${requestedFocusId}`;
      const changed = presentationKey !== this.presentationKey;
      this.graphRevision = graph.graphRevision;
      this.presentationKey = presentationKey;
      const livenessChanged = await this.#probeNextHost(graph);
      const currentIds = new Set((graph.nodes ?? []).map((node) => node.id));
      for (const id of this.liveness.keys()) if (!currentIds.has(id)) this.liveness.delete(id);
      this.snapshot = {...graph, livenessRevision: this.livenessRevision,
        ...(this.rfSensorContext ? {rfSensorContext: this.rfSensorContext} : {}),
        nodes: (graph.nodes ?? []).map((node) => ({...node,
          ...(this.liveness.has(node.id) ? {liveness: this.liveness.get(node.id)} : {})}))};
      const counts = {active: 0, inactive: 0};
      for (const node of this.snapshot.nodes) if (node.liveness?.state in counts) counts[node.liveness.state] += 1;
      const hostCount = this.snapshot.nodes.filter((node) => this.#isProbeableHost(node)).length;
      const unknown = Math.max(0, hostCount - counts.active - counts.inactive);
      const detectedNodes = graph.detectedNodeCount ?? graph.nodeCount ?? graph.nodes?.length ?? 0;
      const detectedEdges = graph.detectedEdgeCount ?? graph.edgeCount ?? graph.edges?.length ?? 0;
      const displayedNodes = graph.displayedNodeCount ?? graph.nodes?.length ?? 0;
      const displayedEdges = graph.displayedEdgeCount ?? graph.edges?.length ?? 0;
      const ranking = graph.ranking ?? {};
      const lens = ranking.lens ?? "SOURCE ORDER";
      const suppressedNodes = ranking.suppressedNodes ?? Math.max(0, detectedNodes - displayedNodes);
      const suppressedEdges = ranking.suppressedEdges ?? Math.max(0, detectedEdges - displayedEdges);
      const detail = this.detailState();
      return this.#publish({kind: "snapshot", graph: this.snapshot, eve, detail,
        changed: changed || livenessChanged, graphChanged, livenessChanged, available: true,
        message: `LIVE HYPERGRAPH // ${graph.status.toUpperCase()}\nDETECTED // ${detectedNodes} NODES // ${detectedEdges} EDGES\nDISPLAYED // ${displayedNodes} / ${detectedNodes} NODES // ${displayedEdges} / ${detectedEdges} EDGES // BOUNDED ${requestedNodeLimit}N·${requestedEdgeLimit}E\nDETAIL // ${detail.tier}${detail.performanceLimited ? ` // FRAME GUARD STEPPED DOWN FROM ${detail.requestedTier}` : ""}\nLENS // ${lens} // SUPPRESSED ${suppressedNodes}N·${suppressedEdges}E${ranking.focusId ? ` // PINNED ${ranking.focusId}` : ""}\nHOST PING // ${counts.active} ACTIVE // ${counts.inactive} INACTIVE // ${unknown} UNKNOWN // ROUND ROBIN\nEVE // ${eve.status?.toUpperCase() ?? "UNKNOWN"} // ${eve.committed ?? 0} COMMITTED // ${eve.replayed ?? 0} BOOTSTRAP REPLAYED // ${eve.deduplicated ?? 0} DEDUPLICATED // RAW PACKETS NOT EXPOSED`});
    } catch (error) {
      return this.#publish({kind: "status", graph: this.snapshot, eve: null, available: false,
        retained: Boolean(this.snapshot), error,
        message: this.snapshot
          ? `LIVE HYPERGRAPH // DEGRADED // RETAINING LAST SNAPSHOT // ${error.message}`
          : `LIVE HYPERGRAPH // UNAVAILABLE // ${error.message}`});
    }
  }

  #isHost(node) {
    return String(node?.kind ?? "").toLowerCase() === "network_host" || String(node?.id ?? "").startsWith("host:");
  }

  #isProbeableHost(node) {
    const kind = String(node?.kind ?? "").toLowerCase();
    const scope = String(node?.enrichment?.scope ?? "").toUpperCase();
    if (["network_multicast_group", "network_unspecified_address"].includes(kind)) return false;
    if (["MULTICAST", "RESERVED", "LOOPBACK"].includes(scope)) return false;
    return this.#isHost(node);
  }

  async #probeNextHost(graph) {
    const hosts = (graph.nodes ?? []).filter((node) => this.#isProbeableHost(node));
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

  #desiredTierIndex() {
    if (this.operatorMaxDetail) return 2;
    return this.focusId ? 1 : 0;
  }

  #applyDetailPolicy() {
    const desired = this.#desiredTierIndex();
    const effective = Math.min(desired, this.performanceTierCap);
    const tier = this.detailTiers[effective];
    const changed = effective !== this.detailTierIndex || tier.nodeLimit !== this.nodeLimit ||
      tier.edgeLimit !== this.edgeLimit;
    this.detailTierIndex = effective; this.nodeLimit = tier.nodeLimit; this.edgeLimit = tier.edgeLimit;
    return changed;
  }

  detailState() {
    const desired = this.#desiredTierIndex(); const tier = this.detailTiers[this.detailTierIndex];
    return {tier: tier.label, tierId: tier.id, requestedTier: this.detailTiers[desired].label,
      nodeLimit: tier.nodeLimit, edgeLimit: tier.edgeLimit,
      maxDetailRequested: this.operatorMaxDetail, performanceLimited: this.detailTierIndex < desired};
  }

  setMaxDetail(enabled) {
    const next = Boolean(enabled);
    if (next === this.operatorMaxDetail) return false;
    this.operatorMaxDetail = next; this.slowFrameCount = 0;
    if (next) this.performanceTierCap = this.detailTiers.length - 1;
    const changed = this.#applyDetailPolicy();
    if (changed && this.running) void this.refresh();
    return changed;
  }

  reportFrameTime(milliseconds) {
    const elapsed = Number(milliseconds);
    if (!Number.isFinite(elapsed) || elapsed <= 0 || this.detailTierIndex === 0) return false;
    this.slowFrameCount = elapsed > this.slowFrameMilliseconds ? this.slowFrameCount + 1 : 0;
    if (this.slowFrameCount < this.slowFrameBudget) return false;
    this.slowFrameCount = 0; this.performanceTierCap = Math.max(0, this.detailTierIndex - 1);
    const changed = this.#applyDetailPolicy();
    if (changed && this.running) void this.refresh();
    return changed;
  }

  setFocus(entityId) {
    const next = String(entityId ?? "").slice(0, 256);
    if (next === this.focusId) return false;
    this.focusId = next; this.slowFrameCount = 0; this.#applyDetailPolicy();
    if (this.running) void this.refresh();
    return true;
  }

  setRfSensorContext(context) {
    const next = context ? Object.freeze({...context}) : null;
    if (JSON.stringify(next) === JSON.stringify(this.rfSensorContext)) return false;
    this.rfSensorContext = next;
    if (this.snapshot) {
      this.snapshot = {...this.snapshot, ...(next ? {rfSensorContext: next} : {})};
      if (!next) delete this.snapshot.rfSensorContext;
      this.#publish({...this.status, kind:"snapshot",graph:this.snapshot,changed:true,
        rfSensorChanged:true,available:true});
    }
    return true;
  }

  destroy() {
    this.running = false; clearTimeout(this.timer); this.timer = null;
    this.listeners.clear(); this.liveness.clear();
  }
}
