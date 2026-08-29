import assert from "node:assert/strict";
import test from "node:test";

import {InfrastructureGlobeLayer, infrastructureSelectionDetail,
  summarizeInfrastructureCluster} from "./infrastructureGlobeLayer.js";

test("InfraFlow screen clusters count unique hosts and preserve inferred-location boundary", () => {
  const entities = [
    {properties: {domainId: "asn:8075", organization: "Microsoft Corporation",
      hostIdsJson: JSON.stringify(["host:20.1.1.1", "host:20.1.1.2"])}},
    {properties: {domainId: "asn:15169", organization: "Google LLC",
      hostIdsJson: JSON.stringify(["host:20.1.1.2", "host:142.250.1.1"])}},
  ];
  const summary = summarizeInfrastructureCluster(entities);
  assert.equal(summary.hostCount, 3); assert.equal(summary.domainCount, 2); assert.equal(summary.markerCount, 3);
  assert.match(summary.text, /3 HOSTS \/\/ 2 NETWORK DOMAINS/);
  assert.match(summary.text, /host:142\.250\.1\.1/);
  assert.match(summary.text, /ASN OWNERSHIP AND GEOIP LOCATION REMAIN INFERRED/);
});

test("PeeringDB facility markers expose a bounded typed interactive selection", () => {
  const detail = infrastructureSelectionDetail({properties: {
    infrastructureKind: "peeringdb_facility", facilityId: "14445",
    facilityName: "Interxion PAR8", organizationId: 123,
    city: "La Courneuve", state: "Île-de-France", country: "FR",
    latitudeDegrees: 48.927, longitudeDegrees: 2.397, recordUpdated: "2026-08-28T00:00:00Z",
    environmentAsnsJson: JSON.stringify([64500, 64501, 64500]), graphRevision: "graph-fac-1",
  }});
  assert.equal(detail.kind, "peeringdb-facility");
  assert.equal(detail.entityId, "peeringdb:facility:14445");
  assert.equal(detail.graphRevision, "graph-fac-1");
  assert.equal(detail.facility.city, "La Courneuve");
  assert.deepEqual(detail.facility.environmentAsns, [64500, 64501]);
  assert.equal(detail.authority, "PEERINGDB_SELF_REPORTED");
  assert.match(detail.boundary, /DOES NOT PROVE TRAFFIC, PATH, OR DEVICE PRESENCE/);
  assert.equal(infrastructureSelectionDetail({properties: {infrastructureKind: "ris_path"}}), null);
});

test("ASN domain markers expose bounded inferred evidence and observed hosts", () => {
  const detail = infrastructureSelectionDetail({properties: {
    infrastructureKind: "network_domain", domainId: "asn:20940", asn: 20940,
    organization: "Akamai International B.V.",
    hostIdsJson: JSON.stringify(["host:23.48.99.72", "host:23.48.99.72", "host:23.48.99.73"]),
    prefixesJson: JSON.stringify(["23.32.0.0/11"]), latitudeDegrees: 42.1167,
    longitudeDegrees: -86.4542, uncertaintyRadiusKm: 50, graphRevision: "graph-asn-1",
    evidenceClass: "INFERRED", authority: "HOST_PREFIX_ENRICHMENT",
    placementAuthority: "GEOIP_ESTIMATE_CENTROID",
  }});
  assert.equal(detail.kind, "infrastructure-domain");
  assert.equal(detail.entityId, "asn:20940");
  assert.equal(detail.graphRevision, "graph-asn-1");
  assert.equal(detail.domain.asn, 20940);
  assert.equal(detail.domain.organization, "Akamai International B.V.");
  assert.deepEqual(detail.domain.hostIds, ["host:23.48.99.72", "host:23.48.99.73"]);
  assert.deepEqual(detail.domain.prefixes, ["23.32.0.0/11"]);
  assert.equal(detail.authority, "HOST_PREFIX_ENRICHMENT");
  assert.equal(detail.domain.placementAuthority, "GEOIP_ESTIMATE_CENTROID");
  assert.match(detail.boundary, /DOES NOT LOCATE A DEVICE OR PROVE A ROUTE/);
});

test("tension, observed-flow, and RIS entities expose distinct bounded evidence contracts", () => {
  const tension = infrastructureSelectionDetail({properties: {infrastructureKind: "evidence_tension",
    findingId: "finding-1", findingKind: "ORIGIN_DISAGREEMENT", findingStatus: "UNRESOLVED",
    severity: "REVIEW", subject: "asn:20940", prefix: "23.32.0.0/11",
    claimsJson: JSON.stringify([{value: 20940, authority: "HOST_PREFIX_ENRICHMENT"},
      {value: [64500], authority: "RIS_LIVE_COLLECTOR_VANTAGE", collectorId: "rrc21"}]),
    alternativesJson: JSON.stringify(["LOCAL ENRICHMENT IS STALE"]), falsifier: "COMPARE SOURCES",
    relatedHostIdsJson: JSON.stringify(["host:23.1.1.1"]), evidenceBoundary: "NOT A HIJACK DETERMINATION",
    graphRevision: "graph-interactions-1"}});
  assert.equal(tension.kind, "infrastructure-evidence-tension");
  assert.equal(tension.finding.claims[1].collectorId, "rrc21");
  assert.deepEqual(tension.finding.relatedHostIds, ["host:23.1.1.1"]);
  assert.match(tension.boundary, /NOT A HIJACK/);

  const flow = infrastructureSelectionDetail({properties: {infrastructureKind: "observed_domain_flow",
    flowId: "infra-flow-1", sourceDomain: "asn:20940", targetDomain: "asn:64500", protocol: "tcp",
    flowCount: 3, bytes: 1200, packets: 12, memberEdgeIdsJson: JSON.stringify(["flow:1", "flow:2"]),
    graphRevision: "graph-interactions-1"}});
  assert.equal(flow.kind, "infrastructure-observed-flow");
  assert.equal(flow.authority, "OBSERVED_GRAPH_EDGES");
  assert.deepEqual(flow.flow.memberEdgeIds, ["flow:1", "flow:2"]);
  assert.match(flow.boundary, /NOT A PHYSICAL OR BGP ROUTE/);

  const ris = infrastructureSelectionDetail({properties: {infrastructureKind: "ris_control_plane_path",
    observationId: "ris-1", messageType: "ANNOUNCE", prefix: "23.32.0.0/11", collectorId: "rrc21",
    collectorReceivedIso: "2026-08-29T12:00:00Z", peerAsn: 64496,
    originAsnsJson: JSON.stringify([20940]), asPathJson: JSON.stringify([64496, 3356, [20940, 20941]]),
    segmentIndex: 1, segmentSourceAsnsJson: JSON.stringify([3356]),
    segmentTargetAsnsJson: JSON.stringify([20940]), relatedHostIdsJson: JSON.stringify(["host:23.1.1.1"]),
    controlPlaneRevision: "ris-revision-1", graphRevision: "graph-interactions-1"}});
  assert.equal(ris.kind, "infrastructure-ris-path");
  assert.deepEqual(ris.observation.asPath, [64496, 3356, [20940, 20941]]);
  assert.equal(ris.controlPlaneRevision, "ris-revision-1");
  assert.match(ris.boundary, /ONE COLLECTOR VANTAGE/);
});

test("clicking interactive infrastructure entities emits typed bounded evidence", async () => {
  const actions = {}; const events = []; let listener = null; let picked = null;
  class Color { withAlpha() { return this; } static fromCssColorString() { return new Color(); } }
  class Entities {
    constructor() { this.values = []; }
    add(entity) { this.values.push(entity); return entity; }
    removeAll() { this.values = []; }
  }
  class CustomDataSource {
    constructor() { this.entities = new Entities(); this.show = true; this.clustering = {clusterEvent: {
      addEventListener: () => () => undefined,
    }}; }
  }
  class ScreenSpaceEventHandler {
    setInputAction(callback, type) { actions[type] = callback; }
    destroy() { this.destroyed = true; }
  }
  const colors = {CYAN: new Color(), WHITE: new Color(), BLACK: new Color(), ORANGE: new Color(),
    MAGENTA: new Color(), RED: new Color(), TRANSPARENT: new Color()};
  const Cesium = {CustomDataSource, ScreenSpaceEventHandler,
    ScreenSpaceEventType: {MOUSE_MOVE: "move", LEFT_CLICK: "click"},
    Cartesian3: {fromDegrees: (longitude, latitude, height) => ({longitude, latitude, height}),
      fromDegreesArray: (values) => values},
    Cartesian2: class { constructor(x, y) { this.x = x; this.y = y; } },
    Color: Object.assign(Color, colors), ArcType: {GEODESIC: "geodesic"}, LabelStyle: {FILL_AND_OUTLINE: "fill"},
    PolylineGlowMaterialProperty: class {}, PolylineDashMaterialProperty: class {},
  };
  const tooltip = {hidden: true, style: {}, setAttribute() {}, remove() {}};
  const EventClass = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
  const container = {ownerDocument: {defaultView: {CustomEvent: EventClass}, createElement: () => tooltip},
    append() {}, dispatchEvent: (event) => events.push(event)};
  const viewer = {container, clock: {currentTime: 1}, selectedEntity: null,
    scene: {canvas: {style: {}}, pick: () => ({id: picked})},
    dataSources: {async add(source) { return source; }, remove() {}},
  };
  const controller = {subscribe(callback) { listener = callback; return () => { listener = null; }; }};
  const layer = new InfrastructureGlobeLayer({viewer, Cesium, controller}); await layer.start();
  listener({snapshot: {status: "ok", graphRevision: "graph-fac-2", domains: [
    {id: "asn:20940", asn: 20940, organization: "Akamai International B.V.", hostCount: 1,
      observedHostIds: ["host:23.48.99.72"], prefixes: ["23.32.0.0/11"],
      centroid: {latitude: 42.1167, longitude: -86.4542, uncertaintyRadiusKm: 50}},
    {id: "asn:64500", asn: 64500, organization: "Example Network", hostCount: 1,
      observedHostIds: ["host:192.0.2.10"], prefixes: ["192.0.2.0/24"],
      centroid: {latitude: 47.61, longitude: -122.33, uncertaintyRadiusKm: 20}},
  ], observedFlows: [{id: "infra-flow-1", sourceDomain: "asn:20940", targetDomain: "asn:64500",
    protocol: "tcp", flowCount: 3, bytes: 1200, packets: 12, firstSeen: "2026-08-29T11:59:00Z",
    lastSeen: "2026-08-29T12:00:00Z", memberEdgeIds: ["flow:1", "flow:2"]}],
    peeringdbEvidence: {facilityPresences: [{fac_id: 14445, asn: 64500}], facilities: [
      {id: 14445, name: "Facility", city: "La Courneuve", country: "FR", latitude: 48.927,
        longitude: 2.397, updated: "2026-08-28T00:00:00Z"}]},
    declaredSharedIxCandidates: [], controlPlaneEvidence: {snapshotRevision: "ris-revision-1",
      controlPlanePaths: [{id: "ris-1", messageType: "ANNOUNCE", prefix: "23.32.0.0/11",
        collectorId: "rrc21", collectorReceivedIso: "2026-08-29T12:00:00Z", peerAsn: 64496,
        originAsn: 64500, asPath: [20940, 64500]}]},
    infrastructureContradictions: {findings: [{id: "finding-1", kind: "ORIGIN_DISAGREEMENT",
      status: "UNRESOLVED", severity: "REVIEW", subject: "asn:20940", prefix: "23.32.0.0/11",
      claims: [{value: 20940, authority: "HOST_PREFIX_ENRICHMENT"},
        {value: [64500], authority: "RIS_LIVE_COLLECTOR_VANTAGE", collectorId: "rrc21"}],
      alternatives: ["LOCAL ENRICHMENT IS STALE"], falsifier: "COMPARE MULTIPLE SOURCES",
      boundary: "NOT A HIJACK DETERMINATION"}]}}});
  picked = layer.source.entities.values.find((entity) => entity.id === "scythe-infra:asn:20940");
  actions.move({endPosition: {x: 11, y: 12}});
  assert.equal(tooltip.hidden, false); assert.equal(viewer.scene.canvas.style.cursor, "pointer");
  assert.match(tooltip.textContent, /ASN 20940/); assert.match(tooltip.textContent, /CLICK/);
  actions.click({position: {x: 1, y: 2}});
  assert.equal(viewer.selectedEntity, picked);
  assert.equal(events[0].type, "scythe-web:infrastructure-selection");
  assert.equal(events[0].detail.kind, "infrastructure-domain");
  assert.equal(events[0].detail.domain.asn, 20940);
  assert.deepEqual(events[0].detail.domain.hostIds, ["host:23.48.99.72"]);

  picked = layer.source.entities.values.find((entity) => entity.id === "scythe-infra:pdb-fac:14445");
  actions.click({position: {x: 1, y: 2}});
  assert.equal(viewer.selectedEntity, picked);
  assert.equal(events[1].type, "scythe-web:infrastructure-selection");
  assert.equal(events[1].detail.entityId, "peeringdb:facility:14445");
  assert.deepEqual(events[1].detail.facility.environmentAsns, [64500]);

  picked = layer.source.entities.values.find((entity) => entity.id === "scythe-infra:tension:finding-1");
  actions.click({position: {x: 1, y: 2}});
  assert.equal(events[2].detail.kind, "infrastructure-evidence-tension");
  assert.equal(events[2].detail.finding.falsifier, "COMPARE MULTIPLE SOURCES");

  picked = layer.source.entities.values.find((entity) => entity.id === "scythe-infra:infra-flow-1");
  actions.click({position: {x: 1, y: 2}});
  assert.equal(events[3].detail.kind, "infrastructure-observed-flow");
  assert.deepEqual(events[3].detail.flow.memberEdgeIds, ["flow:1", "flow:2"]);

  picked = layer.source.entities.values.find((entity) => entity.id === "scythe-infra:ris:ris-1:0");
  actions.click({position: {x: 1, y: 2}});
  assert.equal(events[4].detail.kind, "infrastructure-ris-path");
  assert.equal(events[4].detail.observation.collectorId, "rrc21");
  assert.deepEqual(events[4].detail.observation.relatedHostIds, ["host:192.0.2.10"]);
  layer.destroy(); assert.equal(listener, null);
});
