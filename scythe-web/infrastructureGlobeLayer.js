function value(entity, key, time) {
  const property = entity?.properties?.[key];
  return property?.getValue?.(time) ?? property ?? null;
}

function boundedJsonList(raw, limit = 64) {
  try {
    const values = JSON.parse(String(raw ?? "[]"));
    return Array.isArray(values) ? [...new Set(values.map(Number).filter(Number.isFinite))].slice(0, limit) : [];
  } catch { return []; }
}

function boundedStringJsonList(raw, limit = 64) {
  try {
    const values = JSON.parse(String(raw ?? "[]"));
    return Array.isArray(values) ? [...new Set(values.map((item) => String(item).slice(0, 256))
      .filter(Boolean))].slice(0, limit) : [];
  } catch { return []; }
}

function escapeHtml(input) {
  return String(input ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

export function infrastructureSelectionDetail(entity, time, graphRevision = null) {
  const infrastructureKind = String(value(entity, "infrastructureKind", time) ?? "");
  if (infrastructureKind === "network_domain") {
    const domainId = String(value(entity, "domainId", time) ?? "").slice(0, 128);
    if (!domainId) return null;
    const asnValue = Number(value(entity, "asn", time));
    return {
      kind: "infrastructure-domain", entityId: domainId,
      graphRevision: String(value(entity, "graphRevision", time) ?? graphRevision ?? "").slice(0, 128) || null,
      evidenceClass: String(value(entity, "evidenceClass", time) ?? "INFERRED").slice(0, 64),
      authority: String(value(entity, "authority", time) ?? "HOST_PREFIX_ENRICHMENT").slice(0, 128),
      domain: {
        id: domainId, asn: Number.isFinite(asnValue) && asnValue > 0 ? asnValue : null,
        organization: String(value(entity, "organization", time) ?? "OWNERSHIP UNRESOLVED").slice(0, 256),
        hostIds: boundedStringJsonList(value(entity, "hostIdsJson", time)),
        prefixes: boundedStringJsonList(value(entity, "prefixesJson", time), 16),
        latitude: Number(value(entity, "latitudeDegrees", time)),
        longitude: Number(value(entity, "longitudeDegrees", time)),
        uncertaintyRadiusKm: Math.max(0, Number(value(entity, "uncertaintyRadiusKm", time)) || 0),
        placementAuthority: String(value(entity, "placementAuthority", time) ?? "GEOIP_ESTIMATE_CENTROID")
          .slice(0, 128),
      },
      boundary: "ASN OWNERSHIP COMES FROM LOCAL PREFIX ENRICHMENT AND GEOGRAPHY FROM GEOIP; BOTH ARE INFERRED; THE CENTROID DOES NOT LOCATE A DEVICE OR PROVE A ROUTE",
    };
  }
  if (infrastructureKind !== "peeringdb_facility") return null;
  const facilityId = String(value(entity, "facilityId", time) ?? "").slice(0, 64);
  if (!facilityId) return null;
  return {
    kind: "peeringdb-facility", entityId: `peeringdb:facility:${facilityId}`,
    graphRevision: String(value(entity, "graphRevision", time) ?? graphRevision ?? "").slice(0, 128) || null,
    evidenceClass: "INFRASTRUCTURE_EVIDENCE", authority: "PEERINGDB_SELF_REPORTED",
    facility: {
      id: facilityId, name: String(value(entity, "facilityName", time) ?? "UNNAMED").slice(0, 256),
      organizationId: String(value(entity, "organizationId", time) ?? "").slice(0, 64) || null,
      city: String(value(entity, "city", time) ?? "").slice(0, 128),
      state: String(value(entity, "state", time) ?? "").slice(0, 128),
      country: String(value(entity, "country", time) ?? "").slice(0, 32),
      latitude: Number(value(entity, "latitudeDegrees", time)),
      longitude: Number(value(entity, "longitudeDegrees", time)),
      updated: String(value(entity, "recordUpdated", time) ?? "").slice(0, 64) || null,
      environmentAsns: boundedJsonList(value(entity, "environmentAsnsJson", time)),
    },
    boundary: "PEERINGDB IS SELF-REPORTED DECLARED INFRASTRUCTURE; CO-LOCATION DOES NOT PROVE TRAFFIC, PATH, OR DEVICE PRESENCE",
  };
}

export function summarizeInfrastructureCluster(entities, time, limit = 24) {
  const domains = []; const hostIds = new Set();
  for (const entity of Array.isArray(entities) ? entities : []) {
    const domainId = String(value(entity, "domainId", time) ?? "");
    const organization = String(value(entity, "organization", time) ?? "");
    if (domainId) domains.push({domainId, organization});
    try {
      for (const hostId of JSON.parse(String(value(entity, "hostIdsJson", time) ?? "[]")))
        if (hostId) hostIds.add(String(hostId).slice(0, 256));
    } catch { /* fail closed on malformed display metadata */ }
  }
  const hosts = [...hostIds].sort(); const listed = hosts.slice(0, Math.max(1, limit));
  return {hostCount: hosts.length, domainCount: domains.length, markerCount: hosts.length || entities.length,
    text: [
      `INFRAFLOW SCREEN CLUSTER // ${hosts.length} HOSTS // ${domains.length} NETWORK DOMAINS`,
      ...domains.slice(0, 12).map((row) => `${row.domainId} // ${row.organization || "OWNERSHIP UNRESOLVED"}`),
      "", "HOSTS IN DISPLAY CLUSTER",
      ...listed,
      ...(hosts.length > listed.length ? [`+ ${hosts.length - listed.length} MORE`] : []),
      "BOUNDARY // SCREEN-SPACE PROXIMITY; ASN OWNERSHIP AND GEOIP LOCATION REMAIN INFERRED",
    ].join("\n")};
}

export class InfrastructureGlobeLayer {
  constructor({viewer, Cesium, controller}) {
    if (!viewer || !Cesium || !controller) throw new TypeError("viewer, Cesium, and controller are required");
    this.viewer = viewer; this.Cesium = Cesium; this.controller = controller; this.visible = false;
    this.overlays = {declared: true, controlPlane: true, contradictions: true}; this.snapshot = null;
    this.source = new Cesium.CustomDataSource("SCYTHE InfraFlow // DISPLAY ONLY");
    this.interactionHandler = null; this.tooltip = null; this.removeClusterListener = null;
    this.interactiveHovered = false;
  }
  async start() {
    await this.viewer.dataSources.add(this.source);
    const clustering = this.source.clustering;
    clustering.enabled = true; clustering.pixelRange = 45; clustering.minimumClusterSize = 2;
    clustering.clusterPoints = true; clustering.clusterLabels = true; clustering.clusterBillboards = true;
    this.removeClusterListener = clustering.clusterEvent.addEventListener((entities, cluster) => {
      const summary = summarizeInfrastructureCluster(entities, this.viewer.clock?.currentTime);
      cluster.billboard.show = false; cluster.point.show = true;
      cluster.point.pixelSize = Math.min(50, 27 + Math.sqrt(entities.length) * 2);
      cluster.point.color = this.Cesium.Color.fromCssColorString("#154d61").withAlpha(.94);
      cluster.point.outlineColor = this.Cesium.Color.CYAN; cluster.point.outlineWidth = 2;
      cluster.label.show = true; cluster.label.text = String(summary.markerCount);
      cluster.label.font = "bold 13px ui-monospace,monospace";
      cluster.label.fillColor = this.Cesium.Color.WHITE; cluster.label.outlineColor = this.Cesium.Color.BLACK;
      cluster.label.outlineWidth = 2; cluster.label.style = this.Cesium.LabelStyle.FILL_AND_OUTLINE;
      cluster.label.horizontalOrigin = this.Cesium.HorizontalOrigin.CENTER;
      cluster.label.verticalOrigin = this.Cesium.VerticalOrigin.CENTER;
      cluster.label.pixelOffset = new this.Cesium.Cartesian2(0, 0);
    });
    this.#installHover();
    this.unsubscribe = this.controller.subscribe((update) => { if (update.snapshot) this.render(update.snapshot); });
    return this;
  }
  setVisible(value) { this.visible = Boolean(value); this.source.show = this.visible; }
  setOverlayVisibility(value = {}) { this.overlays = {...this.overlays, ...value}; if (this.snapshot) this.render(this.snapshot); }
  render(snapshot) {
    this.snapshot = snapshot;
    const C = this.Cesium; this.source.entities.removeAll();
    const domains = new Map((snapshot.domains ?? []).map((item) => [item.id, item]));
    for (const domain of domains.values()) {
      const point = domain.centroid; if (!point) continue;
      const position = C.Cartesian3.fromDegrees(point.longitude, point.latitude, 1500);
      const hostIds = (domain.observedHostIds ?? []).slice(0, 64);
      const prefixes = (domain.prefixes ?? []).slice(0, 16);
      const asnLabel = Number.isFinite(Number(domain.asn)) ? `ASN ${Number(domain.asn)}` : domain.id;
      const hoverText = [
        `${asnLabel} // OWNERSHIP + GEOIP INFERRED`,
        domain.organization ?? "OWNERSHIP UNRESOLVED",
        `${domain.hostCount ?? hostIds.length} OBSERVED HOSTS // ${prefixes.length} PREFIXES`,
        `CENTROID // ${Number(point.latitude).toFixed(5)}°, ${Number(point.longitude).toFixed(5)}° ±${point.uncertaintyRadiusKm} km`,
        "CLICK // OPEN INFERRED DOMAIN EVIDENCE",
        "BOUNDARY // CENTROID DOES NOT LOCATE A DEVICE OR PROVE A ROUTE",
      ].join("\n");
      this.source.entities.add({id: `scythe-infra:${domain.id}`, position,
        point: {pixelSize: 8, color: C.Color.CYAN, outlineColor: C.Color.WHITE, outlineWidth: 1},
        ellipse: {semiMajorAxis: point.uncertaintyRadiusKm * 1000, semiMinorAxis: point.uncertaintyRadiusKm * 1000,
          material: C.Color.CYAN.withAlpha(.06), outline: true, outlineColor: C.Color.CYAN.withAlpha(.35), height: 0},
        label: {text: domain.id, font: "10px monospace", fillColor: C.Color.CYAN,
          pixelOffset: new C.Cartesian2(0, -14), showBackground: true, backgroundColor: C.Color.BLACK.withAlpha(.65)},
        properties: {infrastructureKind: "network_domain", domainId: domain.id, asn: domain.asn,
          organization: domain.organization, hostIdsJson: JSON.stringify(hostIds), prefixesJson: JSON.stringify(prefixes),
          latitudeDegrees: point.latitude, longitudeDegrees: point.longitude,
          uncertaintyRadiusKm: point.uncertaintyRadiusKm, graphRevision: snapshot.graphRevision,
          evidenceClass: domain.evidenceClass ?? "INFERRED", authority: domain.authority ?? "HOST_PREFIX_ENRICHMENT",
          placementAuthority: point.authority ?? "GEOIP_ESTIMATE_CENTROID", hoverText},
        description: escapeHtml(hoverText).replaceAll("\n", "<br>")});
    }
    for (const flow of snapshot.observedFlows ?? []) {
      const source = domains.get(flow.sourceDomain)?.centroid; const target = domains.get(flow.targetDomain)?.centroid;
      if (!source || !target || flow.sourceDomain === flow.targetDomain) continue;
      this.source.entities.add({id: `scythe-infra:${flow.id}`,
        polyline: {positions: C.Cartesian3.fromDegreesArray([source.longitude, source.latitude, target.longitude, target.latitude]),
          width: Math.min(6, 1.5 + Math.log2(1 + flow.flowCount)), arcType: C.ArcType.GEODESIC,
          material: new C.PolylineGlowMaterialProperty({glowPower: .16, color: C.Color.CYAN.withAlpha(.72)})},
        description: `OBSERVED FLOW // ENDPOINT ASN AND REGIONS INFERRED // PATH DISPLAY ONLY // NOT ROUTE`});
    }
    if (this.overlays.declared) this.#renderPeeringDb(snapshot, domains);
    if (this.overlays.controlPlane) this.#renderControlPlane(snapshot, domains);
    if (this.overlays.contradictions) this.#renderContradictions(snapshot, domains);
    this.source.show = this.visible;
  }
  #renderPeeringDb(snapshot, domains) {
    const C = this.Cesium; const pdb = snapshot.peeringdbEvidence ?? {};
    const presenceByFacility = new Map();
    for (const row of pdb.facilityPresences ?? []) {
      const key = String(row.fac_id ?? ""); if (!key) continue;
      if (!presenceByFacility.has(key)) presenceByFacility.set(key, new Set());
      if (Number.isFinite(Number(row.asn))) presenceByFacility.get(key).add(Number(row.asn));
    }
    for (const facility of pdb.facilities ?? []) {
      const lat = Number(facility.latitude), lon = Number(facility.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const asns = [...(presenceByFacility.get(String(facility.id)) ?? [])];
      const hoverText = [
        `PEERINGDB FACILITY ${facility.id} // SELF-REPORTED`,
        facility.name ?? "UNNAMED",
        [facility.city, facility.state, facility.country].filter(Boolean).join(", ") || "LOCATION UNDECLARED",
        `ENVIRONMENT ASNs // ${asns.join(", ") || "NONE IN CURRENT SCOPE"}`,
        `UPDATED // ${facility.updated ?? "UNKNOWN"}`,
        "CLICK // OPEN DECLARED FACILITY EVIDENCE",
        "BOUNDARY // CO-LOCATION DOES NOT PROVE TRAFFIC, PATH, OR DEVICE PRESENCE",
      ].join("\n");
      this.source.entities.add({id: `scythe-infra:pdb-fac:${facility.id}`,
        position: C.Cartesian3.fromDegrees(lon, lat, 600),
        ellipse: {semiMajorAxis: 18000, semiMinorAxis: 18000,
          material: C.Color.ORANGE.withAlpha(.035), outline: true, outlineColor: C.Color.ORANGE.withAlpha(.7), height: 0},
        label: {text: `FAC ${facility.id}`, font: "9px monospace", fillColor: C.Color.ORANGE,
          pixelOffset: new C.Cartesian2(0, -12), showBackground: true, backgroundColor: C.Color.BLACK.withAlpha(.65)},
        properties: {infrastructureKind: "peeringdb_facility", facilityId: String(facility.id),
          facilityName: facility.name ?? "UNNAMED", organizationId: facility.org_id ?? "",
          city: facility.city ?? "", state: facility.state ?? "", country: facility.country ?? "",
          latitudeDegrees: lat, longitudeDegrees: lon, recordUpdated: facility.updated ?? null,
          environmentAsnsJson: JSON.stringify(asns.slice(0, 64)), graphRevision: snapshot.graphRevision,
          evidenceClass: "INFRASTRUCTURE_EVIDENCE", authority: "PEERINGDB_SELF_REPORTED", hoverText},
        description: escapeHtml(hoverText).replaceAll("\n", "<br>")});
    }
    for (const row of snapshot.declaredSharedIxCandidates ?? []) {
      const source = domains.get(`asn:${row.sourceAsn}`)?.centroid; const target = domains.get(`asn:${row.targetAsn}`)?.centroid;
      if (!source || !target) continue;
      this.source.entities.add({id: `scythe-infra:pdb:${row.id}`,
        polyline: {positions: C.Cartesian3.fromDegreesArray([source.longitude, source.latitude, target.longitude, target.latitude]),
          width: 1.5, arcType: C.ArcType.GEODESIC,
          material: new C.PolylineDashMaterialProperty({color: C.Color.ORANGE.withAlpha(.8), dashLength: 14})},
        description: `PEERINGDB SHARED IX ${row.ixId} // SELF-REPORTED DECLARED PRESENCE // NO TRAFFIC OR ROUTE CLAIM`});
    }
  }
  #renderControlPlane(snapshot, domains) {
    const C = this.Cesium;
    for (const row of (snapshot.controlPlaneEvidence?.controlPlanePaths ?? []).slice(-64)) {
      const flattened = (row.asPath ?? []).flatMap((hop) => Array.isArray(hop) ? hop : [hop]);
      for (let index = 0; index < flattened.length - 1; index += 1) {
        const source = domains.get(`asn:${flattened[index]}`)?.centroid;
        const target = domains.get(`asn:${flattened[index + 1]}`)?.centroid;
        if (!source || !target) continue;
        this.source.entities.add({id: `scythe-infra:ris:${row.id}:${index}`,
          polyline: {positions: C.Cartesian3.fromDegreesArray([source.longitude, source.latitude, target.longitude, target.latitude]),
            width: 2, arcType: C.ArcType.GEODESIC,
            material: new C.PolylineDashMaterialProperty({color: C.Color.MAGENTA.withAlpha(.85), dashLength: 8})},
          description: `RIS LIVE ${row.messageType} ${row.prefix} // ${row.collectorId} COLLECTOR VANTAGE // CONTROL PLANE ONLY // NOT DATA-PLANE ROUTE`});
      }
    }
  }
  #renderContradictions(snapshot, domains) {
    const C = this.Cesium;
    for (const finding of snapshot.infrastructureContradictions?.findings ?? []) {
      const domain = domains.get(finding.subject); const point = domain?.centroid;
      if (!point) continue;
      this.source.entities.add({id: `scythe-infra:tension:${finding.id}`,
        position: C.Cartesian3.fromDegrees(point.longitude, point.latitude, 2200),
        point: {pixelSize: 14, color: C.Color.TRANSPARENT, outlineColor: C.Color.RED.withAlpha(.95), outlineWidth: 3},
        ellipse: {semiMajorAxis: Math.max(30000, point.uncertaintyRadiusKm * 1100),
          semiMinorAxis: Math.max(30000, point.uncertaintyRadiusKm * 1100),
          material: C.Color.RED.withAlpha(.025), outline: true, outlineColor: C.Color.RED.withAlpha(.85), height: 0},
        label: {text: "EVIDENCE TENSION", font: "9px monospace", fillColor: C.Color.RED,
          pixelOffset: new C.Cartesian2(0, -18), showBackground: true, backgroundColor: C.Color.BLACK.withAlpha(.72)},
        description: `${finding.kind} // ${finding.status} // ${finding.boundary} // FALSIFIER: ${finding.falsifier}`});
    }
  }
  #installHover() {
    const document = this.viewer.container?.ownerDocument ?? globalThis.document;
    if (document?.createElement) {
      this.tooltip = document.createElement("div"); this.tooltip.className = "graph-globe-cluster-tooltip";
      this.tooltip.hidden = true; this.tooltip.setAttribute("role", "tooltip"); this.viewer.container.append(this.tooltip);
    }
    if (!this.Cesium.ScreenSpaceEventHandler) return;
    this.interactionHandler = new this.Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
    if (this.Cesium.ScreenSpaceEventType?.MOUSE_MOVE) this.interactionHandler.setInputAction((movement) => {
      const picked = this.viewer.scene.pick(movement.endPosition)?.id;
      const detail = infrastructureSelectionDetail(picked, this.viewer.clock?.currentTime, this.snapshot?.graphRevision);
      if (detail && this.tooltip) {
        this.tooltip.textContent = String(value(picked, "hoverText", this.viewer.clock?.currentTime) ?? detail.boundary);
        this.tooltip.hidden = false; this.tooltip.style.left = `${Number(movement.endPosition?.x ?? 0) + 13}px`;
        this.tooltip.style.top = `${Number(movement.endPosition?.y ?? 0) + 13}px`;
        if (this.viewer.scene.canvas?.style) this.viewer.scene.canvas.style.cursor = "pointer";
        this.interactiveHovered = true; return;
      }
      if (this.interactiveHovered && this.viewer.scene.canvas?.style) this.viewer.scene.canvas.style.cursor = "default";
      this.interactiveHovered = false;
      if (!Array.isArray(picked) || picked.length < 2 || !this.tooltip) {
        if (this.tooltip) this.tooltip.hidden = true; return;
      }
      const relevant = picked.filter((entity) => entity?.id?.startsWith("scythe-infra:"));
      if (relevant.length < 2) { this.tooltip.hidden = true; return; }
      this.tooltip.textContent = summarizeInfrastructureCluster(relevant, this.viewer.clock?.currentTime).text;
      this.tooltip.hidden = false; this.tooltip.style.left = `${Number(movement.endPosition?.x ?? 0) + 13}px`;
      this.tooltip.style.top = `${Number(movement.endPosition?.y ?? 0) + 13}px`;
    }, this.Cesium.ScreenSpaceEventType.MOUSE_MOVE);
    if (this.Cesium.ScreenSpaceEventType?.LEFT_CLICK) this.interactionHandler.setInputAction((movement) => {
      const picked = this.viewer.scene.pick(movement.position)?.id;
      const detail = infrastructureSelectionDetail(picked, this.viewer.clock?.currentTime, this.snapshot?.graphRevision);
      if (!detail) return;
      this.viewer.selectedEntity = picked;
      const EventClass = this.viewer.container?.ownerDocument?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
      this.viewer.container?.dispatchEvent(new EventClass("scythe-web:infrastructure-selection",
        {bubbles: true, detail}));
    }, this.Cesium.ScreenSpaceEventType.LEFT_CLICK);
  }
  destroy() { this.unsubscribe?.(); this.interactionHandler?.destroy(); this.interactionHandler = null;
    this.tooltip?.remove(); this.tooltip = null; this.removeClusterListener?.(); this.removeClusterListener = null;
    this.viewer.dataSources.remove(this.source, true); }
}
