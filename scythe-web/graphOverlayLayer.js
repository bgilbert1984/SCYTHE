import { cesiumPolylineMaterial, evidenceStyle } from "./evidenceStyles.js";

function finitePosition(position) {
  return Array.isArray(position) && position.length >= 2 &&
    Number.isFinite(Number(position[0])) && Number.isFinite(Number(position[1]));
}

export class GraphOverlayLayer {
  constructor({ viewer, Cesium, apiBase = "", fetchImpl = globalThis.fetch,
                container = globalThis.document?.getElementById("globe-root"),
                nodeLimit = 200, edgeLimit = 300, refreshMilliseconds = 2500,
                controller = null }) {
    if (!viewer?.entities || !Cesium?.Cartesian3) throw new TypeError("Cesium viewer is required");
    this.viewer = viewer; this.Cesium = Cesium; this.apiBase = apiBase;
    this.fetchImpl = fetchImpl; this.container = container;
    this.controller = controller; this.unsubscribe = null;
    this.nodeLimit = Math.min(Math.max(nodeLimit, 1), 500);
    this.edgeLimit = Math.min(Math.max(edgeLimit, 1), 1000);
    this.entityIds = new Set(); this.nodes = new Map(); this.graphRevision = null;
    this.clickHandler = null; this.refreshMilliseconds = Math.max(500, refreshMilliseconds);
    this.refreshTimer = null; this.running = false;
  }

  async start() {
    this.running = true;
    if (this.controller) {
      this.unsubscribe = this.controller.subscribe((update) => {
        if (!this.running) return;
        if (update.kind === "snapshot" && update.graph) this.renderSnapshot(update.graph);
        else if (!update.available) this.#emitStatus({status: "unavailable", reason: update.message});
      });
      await this.controller.start();
    } else {
      await this.refresh();
    }
    if (this.Cesium.ScreenSpaceEventHandler) {
      this.clickHandler = new this.Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
      this.clickHandler.setInputAction((movement) => {
        const entity = this.viewer.scene.pick(movement.position)?.id;
        const isNode = entity?.id?.startsWith("scythe-web:graph-node:");
        const isEdge = entity?.id?.startsWith("scythe-web:graph-edge:");
        if (!isNode && !isEdge) return;
        const time = this.viewer.clock.currentTime;
        const read = (key) => entity.properties?.[key]?.getValue?.(time) ?? null;
        const EventClass = this.container?.ownerDocument?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
        this.container?.dispatchEvent(new EventClass("scythe-web:graph-selection", {bubbles: true, detail: {
          kind: isEdge ? "graph-edge" : (read("graphKind") === "event" ||
            String(read("graphKind") ?? "").toLowerCase().includes("burst") ? "event" : "graph-node"),
          entityId: read("graphEntityId"), graphRevision: read("graphRevision"),
          position: [read("latitudeDegrees"), read("longitudeDegrees"), read("heightMeters")],
          observedAt: read("observedAt"),
        }}));
      }, this.Cesium.ScreenSpaceEventType.LEFT_CLICK);
    }
    if (!this.controller) this.#scheduleRefresh();
    return this;
  }

  #scheduleRefresh() {
    clearTimeout(this.refreshTimer);
    if (this.running) this.refreshTimer = setTimeout(async () => {
      try { await this.refresh(); } finally { this.#scheduleRefresh(); }
    }, this.refreshMilliseconds);
  }

  async refresh() {
    const url = `${this.apiBase}/api/graphops/selection/graph?node_limit=${this.nodeLimit}&edge_limit=${this.edgeLimit}`;
    const response = await this.fetchImpl.call(globalThis, url, {
      credentials: "same-origin", cache: "no-store",
    });
    if (!response.ok) {
      this.#emitStatus({status: "unavailable", reason: `Graph endpoint HTTP ${response.status}`});
      return {status: "unavailable", nodes: [], edges: []};
    }
    const graph = await response.json();
    return this.renderSnapshot(graph);
  }

  renderSnapshot(graph) {
    if (graph.status === "empty") { this.#clearEntities(); this.graphRevision = graph.graphRevision; this.#emitStatus(graph); return graph; }
    if (graph.status !== "ok") { this.#emitStatus(graph); return graph; }
    if (graph.graphRevision === this.graphRevision) { this.#emitStatus({status: "ok", graphRevision: graph.graphRevision,
      nodeCount: graph.nodeCount, edgeCount: graph.edgeCount}); return graph; }
    this.#clearEntities(); this.graphRevision = graph.graphRevision;
    for (const node of graph.nodes.slice(0, this.nodeLimit)) {
      if (!node.id || !finitePosition(node.position)) continue;
      const [lat, lon, height = 0] = node.position.map(Number);
      const evidenceClass = ["OBSERVED", "MEASURED", "SYNTHETIC", "INFERRED"].includes(node.evidenceClass)
        ? node.evidenceClass : "INFERRED";
      const style = evidenceStyle(evidenceClass);
      const entityId = `scythe-web:graph-node:${encodeURIComponent(node.id)}`;
      this.viewer.entities.add({id: entityId,
        position: this.Cesium.Cartesian3.fromDegrees(lon, lat, Math.max(height, 500)),
        point: {pixelSize: 9, color: this.Cesium.Color.fromCssColorString(style.color).withAlpha(style.alpha),
          outlineColor: this.Cesium.Color.BLACK, outlineWidth: 1},
        label: {text: String(node.id).slice(0, 32), font: "10px ui-monospace,monospace",
          fillColor: this.Cesium.Color.fromCssColorString(style.color),
          pixelOffset: new this.Cesium.Cartesian2(0, 16),
          distanceDisplayCondition: new this.Cesium.DistanceDisplayCondition(0, 2_000_000)},
        properties: {graphEntityId: node.id, graphRevision: graph.graphRevision, graphKind: node.kind,
          latitudeDegrees: lat, longitudeDegrees: lon, heightMeters: height,
          observedAt: node.observedAt ?? null, evidenceClass},
      });
      this.entityIds.add(entityId); this.nodes.set(node.id, {lat, lon, height, evidenceClass});
    }
    for (const edge of graph.edges.slice(0, this.edgeLimit)) {
      if (!Array.isArray(edge.nodes) || edge.nodes.length < 2) continue;
      const endpoints = edge.nodes.slice(0, 2).map((id) => this.nodes.get(id));
      if (endpoints.some((value) => !value)) continue;
      const id = `scythe-web:graph-edge:${encodeURIComponent(edge.id)}`;
      const edgeClass = endpoints.every((value) => value.evidenceClass === "SYNTHETIC")
        ? "SYNTHETIC" : "INFERRED";
      this.viewer.entities.add({id, polyline: {positions: endpoints.map((p) =>
        this.Cesium.Cartesian3.fromDegrees(p.lon, p.lat, Math.max(p.height, 300))), width: 1.5,
        material: cesiumPolylineMaterial(this.Cesium, edgeClass)},
        properties: {graphEntityId: edge.id, graphRevision: graph.graphRevision,
          graphKind: edge.kind, observedAt: edge.observedAt ?? edge.timestamp ?? null,
          latitudeDegrees: (endpoints[0].lat + endpoints[1].lat) / 2,
          longitudeDegrees: (endpoints[0].lon + endpoints[1].lon) / 2,
          heightMeters: (endpoints[0].height + endpoints[1].height) / 2,
          evidenceClass: edge.evidenceClass ?? edgeClass,
          scytheSemantics: "GRAPH RELATIONSHIP; NOT CAUSAL PROOF"}});
      this.entityIds.add(id);
    }
    this.#emitStatus({status: "ok", graphRevision: graph.graphRevision,
      nodeCount: this.nodes.size, edgeCount: graph.edges.length});
    return graph;
  }

  #emitStatus(detail) {
    const EventClass = this.container?.ownerDocument?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
    this.container?.dispatchEvent(new EventClass("scythe-web:graph-status", {bubbles: true, detail}));
  }

  #clearEntities() { for (const id of this.entityIds) this.viewer.entities.removeById(id); this.entityIds.clear(); this.nodes.clear(); }
  destroy() { this.running = false; clearTimeout(this.refreshTimer); this.refreshTimer = null;
    this.unsubscribe?.(); this.unsubscribe = null;
    this.clickHandler?.destroy(); this.clickHandler = null; this.#clearEntities(); }
}
