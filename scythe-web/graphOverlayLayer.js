import {cesiumPolylineMaterial, flowDirectionStyle, flowMotion, flowTypeStyle,
  graphPurposeStyle, hostLivenessStyle} from "./evidenceStyles.js";
import {geographicArcWaypoints, geographicGraphPlacement,
  geographicProjectionRevision} from "./geographicGraphProjection.js";
import {GRAPH_VISUAL_SCALE_BOUNDARY, graphFlowGroupScale, graphNodeScale} from "./graphVisualScale.js";

function readProperty(entity, key, time) {
  const value = entity?.properties?.[key];
  return value?.getValue?.(time) ?? value ?? null;
}

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

export function summarizeGraphCluster(entities, time, limit = 24) {
  const rows = (Array.isArray(entities) ? entities : []).map((entity) => ({
    id: String(readProperty(entity, "graphEntityId", time) ?? "").slice(0, 256),
    kind: String(readProperty(entity, "graphKind", time) ?? "entity").slice(0, 64),
    evidence: String(readProperty(entity, "evidenceClass", time) ?? "UNAVAILABLE").slice(0, 32),
    graphEvidence: String(readProperty(entity, "graphEvidenceClass", time) ?? "UNAVAILABLE").slice(0, 32),
    organization: String(readProperty(entity, "organization", time) ?? "").slice(0, 160),
    place: String(readProperty(entity, "placeLabel", time) ?? "").slice(0, 160),
    placement: String(readProperty(entity, "placementAuthority", time) ?? "UNAVAILABLE").slice(0, 64),
    uncertainty: Number(readProperty(entity, "uncertaintyRadiusKm", time)),
  })).filter((row) => row.id);
  const hosts = rows.filter((row) => /host/i.test(row.kind) || row.id.startsWith("host:"));
  const listed = (hosts.length ? hosts : rows).slice(0, Math.max(1, limit));
  const remainder = (hosts.length ? hosts.length : rows.length) - listed.length;
  return {
    entityCount: rows.length, hostCount: hosts.length,
    markerCount: hosts.length || rows.length,
    text: [
      `SCREEN CLUSTER // ${rows.length} ENTITIES // ${hosts.length} HOSTS`,
      ...listed.map((row) => `${row.id}${row.organization ? ` // ${row.organization}` : ""}` +
        `${row.place ? ` // ${row.place}` : ""} // GRAPH ${row.graphEvidence} · PLACEMENT ${row.evidence} // ${row.placement}` +
        `${Number.isFinite(row.uncertainty) && row.uncertainty > 0 ? ` ±${row.uncertainty} km` : ""}`),
      ...(remainder > 0 ? [`+ ${remainder} MORE`] : []),
      "BOUNDARY // SCREEN-SPACE PROXIMITY; GEOIP REMAINS INFERRED",
    ].join("\n"),
  };
}

export class GraphOverlayLayer {
  constructor({ viewer, Cesium, apiBase = "", fetchImpl = globalThis.fetch,
                container = globalThis.document?.getElementById("globe-root"),
                nodeLimit = 300, edgeLimit = 600, refreshMilliseconds = 2500,
                controller = null, sensorVantage = null, reducedMotion = null }) {
    if (!viewer?.entities || !Cesium?.Cartesian3) throw new TypeError("Cesium viewer is required");
    this.viewer = viewer; this.Cesium = Cesium; this.apiBase = apiBase;
    this.fetchImpl = fetchImpl; this.container = container;
    this.controller = controller; this.unsubscribe = null;
    this.nodeLimit = Math.min(Math.max(nodeLimit, 1), 500);
    this.edgeLimit = Math.min(Math.max(edgeLimit, 1), 1000);
    this.entityIds = new Set(); this.nodes = new Map(); this.graphRevision = null; this.renderKey = null;
    this.latestGraph = null; this.sensorVantage = sensorVantage;
    this.visible = true; this.overlays = {hosts: true, flows: true, uncertainty: true,
      direction: true, motion: true, localVantage: true, aggregateFlows: true};
    const window = container?.ownerDocument?.defaultView ?? globalThis;
    this.reducedMotion = reducedMotion ?? Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
    this.clickHandler = null; this.clusterSource = null; this.collection = viewer.entities;
    this.removeClusterListener = null; this.tooltip = null;
    this.refreshMilliseconds = Math.max(500, refreshMilliseconds);
    this.refreshTimer = null; this.running = false;
  }

  async start() {
    this.running = true;
    if (this.Cesium.CustomDataSource && this.viewer.dataSources?.add) {
      this.clusterSource = new this.Cesium.CustomDataSource("SCYTHE Graph Hosts // CLUSTERED DISPLAY");
      await this.viewer.dataSources.add(this.clusterSource); this.collection = this.clusterSource.entities;
      const clustering = this.clusterSource.clustering;
      clustering.enabled = this.overlays.aggregateFlows; clustering.pixelRange = 45; clustering.minimumClusterSize = 2;
      clustering.clusterBillboards = true; clustering.clusterLabels = true; clustering.clusterPoints = true;
      this.removeClusterListener = clustering.clusterEvent.addEventListener((entities, cluster) => {
        const summary = summarizeGraphCluster(entities, this.viewer.clock?.currentTime);
        cluster.billboard.show = false;
        cluster.point.show = true; cluster.point.pixelSize = Math.min(46, 25 + Math.sqrt(summary.entityCount) * 2);
        cluster.point.color = this.Cesium.Color.fromCssColorString("#0b6079").withAlpha(.92);
        cluster.point.outlineColor = this.Cesium.Color.fromCssColorString("#66ddff"); cluster.point.outlineWidth = 2;
        cluster.label.show = true; cluster.label.text = String(summary.markerCount);
        cluster.label.font = "bold 13px ui-monospace,monospace";
        cluster.label.fillColor = this.Cesium.Color.WHITE; cluster.label.outlineColor = this.Cesium.Color.BLACK;
        cluster.label.outlineWidth = 2; cluster.label.style = this.Cesium.LabelStyle?.FILL_AND_OUTLINE;
        cluster.label.horizontalOrigin = this.Cesium.HorizontalOrigin?.CENTER;
        cluster.label.verticalOrigin = this.Cesium.VerticalOrigin?.CENTER;
        cluster.label.pixelOffset = new this.Cesium.Cartesian2(0, 0);
      });
    }
    this.#createTooltip();
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
        if (Array.isArray(entity)) return;
        if (entity?.id === "scythe-web:sensor-vantage") {
          const raw = readProperty(entity, "sensorDetailJson", this.viewer.clock?.currentTime);
          let detail = null; try { detail = JSON.parse(String(raw ?? "null")); } catch { /* fail closed */ }
          if (!detail) return;
          const EventClass = this.container?.ownerDocument?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
          this.container?.dispatchEvent(new EventClass("scythe-web:rf-sensor-selection",
            {bubbles:true,detail:{kind:"rf-sensor",entityId:`sensor:${detail.sensorId}`,sensor:detail}}));
          return;
        }
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
      if (this.Cesium.ScreenSpaceEventType.MOUSE_MOVE) this.clickHandler.setInputAction((movement) => {
        const picked = this.viewer.scene.pick(movement.endPosition)?.id;
        if (picked?.id === "scythe-web:sensor-vantage") {
          const description = readProperty(picked, "hoverText", this.viewer.clock?.currentTime);
          if (!description) return this.#hideTooltip();
          this.tooltip.textContent = description; this.tooltip.hidden = false;
          this.tooltip.style.left = `${Number(movement.endPosition?.x ?? 0) + 13}px`;
          this.tooltip.style.top = `${Number(movement.endPosition?.y ?? 0) + 13}px`; return;
        }
        if (picked?.id?.startsWith?.("scythe-web:graph-flow-cluster:")) {
          const description = readProperty(picked, "hoverText", this.viewer.clock?.currentTime);
          if (!description) return this.#hideTooltip();
          this.tooltip.textContent = description; this.tooltip.hidden = false;
          this.tooltip.style.left = `${Number(movement.endPosition?.x ?? 0) + 13}px`;
          this.tooltip.style.top = `${Number(movement.endPosition?.y ?? 0) + 13}px`; return;
        }
        if (!Array.isArray(picked) || picked.length < 2) return this.#hideTooltip();
        const graphEntities = picked.filter((entity) => entity?.id?.startsWith("scythe-web:graph-node:"));
        if (graphEntities.length < 2) return this.#hideTooltip();
        const summary = summarizeGraphCluster(graphEntities, this.viewer.clock?.currentTime);
        this.tooltip.textContent = summary.text; this.tooltip.hidden = false;
        this.tooltip.style.left = `${Number(movement.endPosition?.x ?? 0) + 13}px`;
        this.tooltip.style.top = `${Number(movement.endPosition?.y ?? 0) + 13}px`;
      }, this.Cesium.ScreenSpaceEventType.MOUSE_MOVE);
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

  setVisible(value) {
    this.visible = Boolean(value);
    if (this.clusterSource) this.clusterSource.show = this.visible;
    else for (const id of this.entityIds) { const entity = this.collection.getById?.(id); if (entity) entity.show = this.visible; }
  }

  setOverlayVisibility(value = {}) {
    this.overlays = {...this.overlays, ...value};
    if (this.clusterSource) this.clusterSource.clustering.enabled = Boolean(this.overlays.aggregateFlows);
    this.renderKey = null; if (this.latestGraph) this.renderSnapshot(this.latestGraph);
  }

  setSensorVantage(value) {
    this.sensorVantage = value || null; this.renderKey = null;
    if (this.latestGraph) this.renderSnapshot(this.latestGraph);
  }

  renderSnapshot(graph) {
    this.latestGraph = graph;
    if (graph.status === "empty") { this.#clearEntities(); this.graphRevision = graph.graphRevision; this.#emitStatus(graph); return graph; }
    if (graph.status !== "ok") { this.#emitStatus(graph); return graph; }
    const nodeLimit = this.controller?.nodeLimit ?? this.nodeLimit;
    const edgeLimit = this.controller?.edgeLimit ?? this.edgeLimit;
    const projectionRevision = geographicProjectionRevision(graph.nodes, this.sensorVantage);
    const renderKey = `${graph.graphRevision}:${graph.livenessRevision ?? 0}:${projectionRevision}:${nodeLimit}:${edgeLimit}:${JSON.stringify(this.overlays)}`;
    if (renderKey === this.renderKey) { this.#emitStatus({status: "ok", graphRevision: graph.graphRevision,
      nodeCount: graph.nodeCount, edgeCount: graph.edgeCount}); return graph; }
    this.#clearEntities(); this.graphRevision = graph.graphRevision; this.renderKey = renderKey;
    for (const node of graph.nodes.slice(0, nodeLimit)) {
      if (!node.id) continue;
      const placement = geographicGraphPlacement(node, this.sensorVantage); if (!placement) continue;
      if (placement.coLocatedAtSensor && !this.overlays.localVantage) continue;
      const lat = placement.latitude; const lon = placement.longitude; const height = placement.heightMeters;
      const evidenceClass = ["OBSERVED", "MEASURED", "SYNTHETIC", "INFERRED", "ILLUSTRATIVE"]
        .includes(node.evidenceClass) ? node.evidenceClass : "INFERRED";
      const style = graphPurposeStyle({...node, evidenceClass}); const liveness = hostLivenessStyle(node);
      const scale = graphNodeScale(node);
      const network = node.enrichment?.network ?? {}; const geo = node.enrichment?.geo ?? {};
      const placeLabel = [geo.city, geo.region, geo.country].filter(Boolean).join(", ");
      const entityId = `scythe-web:graph-node:${encodeURIComponent(node.id)}`;
      if (this.overlays.hosts) {
        const uncertainty = Math.max(0, Number(placement.uncertaintyRadiusKm) || 0);
        this.collection.add({id: entityId,
          position: this.Cesium.Cartesian3.fromDegrees(lon, lat, Math.max(height, 500)),
          point: {pixelSize: scale.cesiumPixels, color: this.Cesium.Color.fromCssColorString(style.color).withAlpha(style.alpha),
            outlineColor: liveness ? this.Cesium.Color.fromCssColorString(liveness.color) : this.Cesium.Color.BLACK,
            outlineWidth: liveness ? 3 : 1},
          ...(this.overlays.uncertainty && uncertainty > 0 && this.Cesium.Color ? {ellipse: {
            semiMajorAxis: uncertainty * 1000, semiMinorAxis: uncertainty * 1000,
            material: this.Cesium.Color.fromCssColorString(style.color).withAlpha(.025), outline: true,
            outlineColor: this.Cesium.Color.fromCssColorString(style.color).withAlpha(.32), height: 0}} : {}),
          label: {text: String(node.id).slice(0, 32), font: "10px ui-monospace,monospace",
            fillColor: this.Cesium.Color.fromCssColorString(style.color),
            pixelOffset: new this.Cesium.Cartesian2(0, 16),
            distanceDisplayCondition: new this.Cesium.DistanceDisplayCondition(0, 2_000_000)},
          description: `${node.id} // ${placement.placementAuthority}` +
            `${uncertainty ? ` ±${uncertainty} km` : ""} // ${placement.coLocatedAtSensor ? "CO-LOCATED AT SENSOR FOR DISPLAY; NOT DEVICE LOCATION" : "IP NETWORK LOCATION ESTIMATE; NOT DEVICE LOCATION"}` +
            `<br>VISUAL SCALE // ${scale.basis.replaceAll("_", " ")} // ${scale.cesiumPixels.toFixed(2)} PX` +
            `<br>BOUNDARY // SIZE IS PRESENTATION METADATA; IT DOES NOT CHANGE EVIDENCE AUTHORITY`,
          properties: {graphEntityId: node.id, graphRevision: graph.graphRevision, graphKind: node.kind,
            latitudeDegrees: lat, longitudeDegrees: lon, heightMeters: height,
            observedAt: node.observedAt ?? null, evidenceClass: placement.placementEvidenceClass,
            graphEvidenceClass: evidenceClass,
            placementAuthority: placement.placementAuthority, uncertaintyRadiusKm: uncertainty,
            organization: network.organization ?? "", placeLabel},
        });
        this.entityIds.add(entityId);
      }
      this.nodes.set(node.id, {lat, lon, height, evidenceClass, placement, node});
    }
    if (this.sensorVantage && this.overlays.localVantage && this.overlays.hosts) this.#addSensorVantage();
    const drawable = [];
    for (const edge of graph.edges.slice(0, edgeLimit)) {
      if (!Array.isArray(edge.nodes) || edge.nodes.length < 2) continue;
      const endpoints = edge.nodes.slice(0, 2).map((id) => this.nodes.get(id));
      if (endpoints.some((value) => !value)) continue;
      drawable.push({edge, endpoints});
    }
    if (this.overlays.flows) this.#renderFlowGroups(drawable, graph);
    if (this.clusterSource) this.clusterSource.show = this.visible;
    this.#emitStatus({status: "ok", graphRevision: graph.graphRevision,
      nodeCount: this.nodes.size, edgeCount: drawable.length,
      inferredGeoCount: [...this.nodes.values()].filter((item) =>
        item.placement.placementAuthority === "GEOIP_ESTIMATE").length,
      sensorColocatedCount: [...this.nodes.values()].filter((item) => item.placement.coLocatedAtSensor).length});
    return graph;
  }

  #addSensorVantage() {
    const placement = geographicGraphPlacement({kind: "network_multicast_group",
      enrichment: {scope: "PRIVATE"}}, this.sensorVantage);
    if (!placement) return;
    const C = this.Cesium; const id = "scythe-web:sensor-vantage";
    const uncertainty = Math.max(.005, placement.uncertaintyRadiusKm || 0);
    const receiver = this.sensorVantage.receiver ?? {};
    const sensorId = receiver.sensorId ?? this.sensorVantage.sensorId ?? "browser-capture-vantage";
    const detail = {...receiver, sensorId, latitude:placement.latitude,longitude:placement.longitude,
      accuracyMeters:uncertainty*1000,locationAuthority:this.sensorVantage.authority ?? "OPERATOR PROVIDED",
      locationEvidenceClass:this.sensorVantage.evidenceClass ?? "MEASURED"};
    const hoverText = ["RF RECEIVER SENSOR // DISPLAY CONTEXT", sensorId,
      `BRIDGE // ${String(receiver.bridgeState ?? "UNKNOWN").toUpperCase()} // IQ ${receiver.iqConnected ? "CONNECTED" : "DISCONNECTED"}`,
      `CENTER // ${receiver.centerFrequencyHz ?? "UNKNOWN"} Hz // SAMPLE RATE ${receiver.sampleRateHz ?? "UNKNOWN"} Hz`,
      `LOCATION // ${placement.latitude.toFixed(5)}°, ${placement.longitude.toFixed(5)}° ±${Math.round(uncertainty*1000)} m`,
      `AUTHORITY // ${detail.locationAuthority}`, "CLICK // OPEN RF FIELD INSPECTOR",
      "BOUNDARY // CONFIGURED RECEIVER; USB ATTACHMENT NOT ATTESTED; RAW IQ NOT EXPOSED"].join("\n");
    this.collection.add({id, position: C.Cartesian3.fromDegrees(placement.longitude, placement.latitude, 750),
      point: {pixelSize: 13, color: C.Color.fromCssColorString("#ffffff"),
        outlineColor: C.Color.fromCssColorString("#00d4ff"), outlineWidth: 3},
      ...(this.overlays.uncertainty ? {ellipse: {semiMajorAxis: uncertainty * 1000,
        semiMinorAxis: uncertainty * 1000, material: C.Color.CYAN.withAlpha(.045), outline: true,
        outlineColor: C.Color.CYAN.withAlpha(.7), height: 0}} : {}),
      label: {text: `RF RX // ${sensorId}`, font: "bold 10px ui-monospace,monospace",
        fillColor: C.Color.WHITE, pixelOffset: new C.Cartesian2(0, -18), showBackground: true,
        backgroundColor: C.Color.BLACK.withAlpha(.72)},
      properties:{sensorDetailJson:JSON.stringify(detail),hoverText},
      description: hoverText.replaceAll("\n","<br>")});
    this.entityIds.add(id);
  }

  #renderFlowGroups(drawable, graph) {
    const groups = new Map(); const focusId = graph.ranking?.focusId;
    for (const item of drawable) {
      const visual = flowTypeStyle(item.edge); const direction = flowDirectionStyle(item.edge);
      const endpoints = item.endpoints;
      const sourceKey = endpoints[0].node?.enrichment?.network?.asn ??
        `${endpoints[0].lat.toFixed(1)},${endpoints[0].lon.toFixed(1)}`;
      const targetKey = endpoints[1].node?.enrichment?.network?.asn ??
        `${endpoints[1].lat.toFixed(1)},${endpoints[1].lon.toFixed(1)}`;
      const aggregate = this.overlays.aggregateFlows && item.edge.id !== focusId;
      const key = aggregate ? `${sourceKey}>${targetKey}:${visual.type}:${direction.direction}` : item.edge.id;
      if (!groups.has(key)) groups.set(key, []); groups.get(key).push(item);
    }
    for (const [key, items] of groups) this.#addFlowArc(key, items, graph);
  }

  #addFlowArc(key, items, graph) {
    const C = this.Cesium; const representative = items[0]; const edge = representative.edge;
    const source = representative.endpoints[0]; const target = representative.endpoints[1];
    const waypoints = geographicArcWaypoints({latitude: source.lat, longitude: source.lon, heightMeters: source.height},
      {latitude: target.lat, longitude: target.lon, heightMeters: target.height});
    const positions = waypoints.map((point) => C.Cartesian3.fromDegrees(
      point.longitude, point.latitude, point.heightMeters));
    const visual = flowTypeStyle(edge); const direction = flowDirectionStyle(edge); const motion = flowMotion(edge);
    const scale = graphFlowGroupScale(items.map((item) => item.edge));
    const aggregate = items.length > 1;
    const evidenceClass = ["OBSERVED", "MEASURED", "SOLVER_OUTPUT", "REDUCED_ORDER", "SYNTHETIC",
      "ILLUSTRATIVE", "INFERRED", "COUNTERFACTUAL"].includes(edge.evidenceClass) ? edge.evidenceClass : "INFERRED";
    const id = aggregate ? `scythe-web:graph-flow-cluster:${encodeURIComponent(key)}` :
      `scythe-web:graph-edge:${encodeURIComponent(edge.id)}`;
    const flowIds = items.map((item) => item.edge.id); const hoverText = [
      `${aggregate ? "GEOGRAPHIC FLOW AGGREGATE" : "GEOGRAPHIC FLOW"} // ${items.length} FLOW${items.length === 1 ? "" : "S"}`,
      `TYPE // ${visual.label} // DIRECTION // ${direction.label}`,
      `VISUAL SCALE // ${scale.basis.replaceAll("_", " ")} // WIDTH ${scale.cesiumWidth.toFixed(2)} // ARROW ${scale.arrowPixels.toFixed(2)} PX`,
      ...flowIds.slice(0, 20), ...(flowIds.length > 20 ? [`+ ${flowIds.length - 20} MORE`] : []),
      "BOUNDARY // ENDPOINT PLACEMENTS ARE INFERRED OR VANTAGE-COLOCATED; ARC IS NOT A PHYSICAL ROUTE",
      `BOUNDARY // ${GRAPH_VISUAL_SCALE_BOUNDARY}`,
    ].join("\n");
    this.collection.add({id, polyline: {positions, width: scale.cesiumWidth,
      arcType: C.ArcType?.NONE, material: cesiumPolylineMaterial(C, evidenceClass, visual.color, visual.alpha)},
      description: escapeHtml(hoverText).replaceAll("\n", "<br>"), properties: {
        graphEntityId: aggregate ? "" : edge.id, graphEntityIdsJson: JSON.stringify(flowIds.slice(0, 64)),
        graphRevision: graph.graphRevision, graphKind: aggregate ? "network_flow_aggregate" : edge.kind,
        observedAt: edge.observedAt ?? edge.timestamp ?? null,
        latitudeDegrees: waypoints[Math.floor(waypoints.length / 2)].latitude,
        longitudeDegrees: waypoints[Math.floor(waypoints.length / 2)].longitude,
        heightMeters: waypoints[Math.floor(waypoints.length / 2)].heightMeters,
        evidenceClass, placementAuthority: "DISPLAY_ARC_NOT_ROUTE", hoverText,
        scytheSemantics: "OBSERVED COMMUNICATION BETWEEN INFERRED OR VANTAGE-COLOCATED ENDPOINTS; NOT PHYSICAL ROUTE"}});
    this.entityIds.add(id);
    if (this.overlays.direction) {
      const start = Math.floor(waypoints.length * .54); const arrowPositions = positions.slice(start, start + 3);
      const arrowId = `scythe-web:graph-direction:${encodeURIComponent(key)}`;
      const arrowMaterial = C.PolylineArrowMaterialProperty ?
        new C.PolylineArrowMaterialProperty(C.Color.fromCssColorString(direction.color).withAlpha(.96)) :
        C.Color.fromCssColorString(direction.color).withAlpha(.96);
      this.collection.add({id: arrowId, polyline: {positions: arrowPositions,
        width: Math.max(4, scale.arrowPixels * .65),
        arcType: C.ArcType?.NONE, material: arrowMaterial}, description: hoverText}); this.entityIds.add(arrowId);
    }
    if (items.length === 1 && this.overlays.motion && motion.measured && !this.reducedMotion)
      this.#addFlowParticles(key, positions, motion, visual.color);
  }

  #addFlowParticles(key, positions, motion, color) {
    const C = this.Cesium; if (!C.CallbackProperty || !C.Cartesian3?.lerp) return;
    const add = (reverse, count) => {
      if (!(count > 0)) return;
      const path = reverse ? [...positions].reverse() : positions; const duration = motion.durationSeconds * 1000;
      const phase = reverse ? .5 : 0;
      const position = new C.CallbackProperty((_time, result) => {
        const progress = ((Date.now() / duration) + phase) % 1;
        const scaled = progress * (path.length - 1); const index = Math.min(path.length - 2, Math.floor(scaled));
        return C.Cartesian3.lerp(path[index], path[index + 1], scaled - index, result);
      }, false);
      const id = `scythe-web:graph-motion:${reverse ? "reverse" : "forward"}:${encodeURIComponent(key)}`;
      this.collection.add({id, position, point: {pixelSize: Math.min(8, 4 + Math.log2(count + 1)),
        color: C.Color.fromCssColorString(reverse ? "#ff6fb7" : color),
        outlineColor: C.Color.WHITE, outlineWidth: 1}}); this.entityIds.add(id);
    };
    add(false, motion.forwardPackets); add(true, motion.reversePackets);
  }

  #emitStatus(detail) {
    const EventClass = this.container?.ownerDocument?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
    this.container?.dispatchEvent(new EventClass("scythe-web:graph-status", {bubbles: true, detail}));
  }

  #createTooltip() {
    const document = this.container?.ownerDocument;
    if (!document?.createElement || this.tooltip) return;
    this.tooltip = document.createElement("div"); this.tooltip.className = "graph-globe-cluster-tooltip";
    this.tooltip.hidden = true; this.tooltip.setAttribute("role", "tooltip"); this.container.append(this.tooltip);
  }
  #hideTooltip() { if (this.tooltip) this.tooltip.hidden = true; }

  #clearEntities() { for (const id of this.entityIds) this.collection.removeById(id); this.entityIds.clear(); this.nodes.clear(); this.#hideTooltip(); }
  destroy() { this.running = false; clearTimeout(this.refreshTimer); this.refreshTimer = null;
    this.unsubscribe?.(); this.unsubscribe = null;
    this.clickHandler?.destroy(); this.clickHandler = null; this.#clearEntities();
    this.removeClusterListener?.(); this.removeClusterListener = null;
    if (this.clusterSource) this.viewer.dataSources.remove(this.clusterSource, true);
    this.clusterSource = null; this.tooltip?.remove(); this.tooltip = null; }
}
