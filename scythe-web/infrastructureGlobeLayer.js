function value(entity, key, time) {
  const property = entity?.properties?.[key];
  return property?.getValue?.(time) ?? property ?? null;
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
    this.hoverHandler = null; this.tooltip = null; this.removeClusterListener = null;
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
      this.source.entities.add({id: `scythe-infra:${domain.id}`, position,
        point: {pixelSize: 8, color: C.Color.CYAN, outlineColor: C.Color.WHITE, outlineWidth: 1},
        ellipse: {semiMajorAxis: point.uncertaintyRadiusKm * 1000, semiMinorAxis: point.uncertaintyRadiusKm * 1000,
          material: C.Color.CYAN.withAlpha(.06), outline: true, outlineColor: C.Color.CYAN.withAlpha(.35), height: 0},
        label: {text: domain.id, font: "10px monospace", fillColor: C.Color.CYAN,
          pixelOffset: new C.Cartesian2(0, -14), showBackground: true, backgroundColor: C.Color.BLACK.withAlpha(.65)},
        properties: {domainId: domain.id, organization: domain.organization,
          hostIdsJson: JSON.stringify(domain.observedHostIds ?? [])},
        description: `${domain.id} // ${domain.hostCount} OBSERVED HOSTS // ASN OWNERSHIP INFERRED // GEOIP CENTROID INFERRED ±${point.uncertaintyRadiusKm} km`});
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
      if (!presenceByFacility.has(row.fac_id)) presenceByFacility.set(row.fac_id, []);
      presenceByFacility.get(row.fac_id).push(row.asn);
    }
    for (const facility of pdb.facilities ?? []) {
      const lat = Number(facility.latitude), lon = Number(facility.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
      const asns = presenceByFacility.get(facility.id) ?? [];
      this.source.entities.add({id: `scythe-infra:pdb-fac:${facility.id}`,
        position: C.Cartesian3.fromDegrees(lon, lat, 600),
        ellipse: {semiMajorAxis: 18000, semiMinorAxis: 18000,
          material: C.Color.ORANGE.withAlpha(.035), outline: true, outlineColor: C.Color.ORANGE.withAlpha(.7), height: 0},
        label: {text: `FAC ${facility.id}`, font: "9px monospace", fillColor: C.Color.ORANGE,
          pixelOffset: new C.Cartesian2(0, -12), showBackground: true, backgroundColor: C.Color.BLACK.withAlpha(.65)},
        description: `PEERINGDB FACILITY // SELF-REPORTED // ${facility.name ?? "UNNAMED"} // ENVIRONMENT ASNs ${asns.join(", ") || "NONE"} // CO-LOCATION DOES NOT PROVE TRAFFIC`});
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
    if (!this.Cesium.ScreenSpaceEventHandler || !this.Cesium.ScreenSpaceEventType?.MOUSE_MOVE) return;
    this.hoverHandler = new this.Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
    this.hoverHandler.setInputAction((movement) => {
      const picked = this.viewer.scene.pick(movement.endPosition)?.id;
      if (!Array.isArray(picked) || picked.length < 2 || !this.tooltip) {
        if (this.tooltip) this.tooltip.hidden = true; return;
      }
      const relevant = picked.filter((entity) => entity?.id?.startsWith("scythe-infra:"));
      if (relevant.length < 2) { this.tooltip.hidden = true; return; }
      this.tooltip.textContent = summarizeInfrastructureCluster(relevant, this.viewer.clock?.currentTime).text;
      this.tooltip.hidden = false; this.tooltip.style.left = `${Number(movement.endPosition?.x ?? 0) + 13}px`;
      this.tooltip.style.top = `${Number(movement.endPosition?.y ?? 0) + 13}px`;
    }, this.Cesium.ScreenSpaceEventType.MOUSE_MOVE);
  }
  destroy() { this.unsubscribe?.(); this.hoverHandler?.destroy(); this.hoverHandler = null;
    this.tooltip?.remove(); this.tooltip = null; this.removeClusterListener?.(); this.removeClusterListener = null;
    this.viewer.dataSources.remove(this.source, true); }
}
