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

test("clicking a facility selects the Cesium entity and emits infrastructure evidence", async () => {
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
  listener({snapshot: {status: "ok", graphRevision: "graph-fac-2", domains: [], observedFlows: [],
    peeringdbEvidence: {facilityPresences: [{fac_id: 14445, asn: 64500}], facilities: [
      {id: 14445, name: "Facility", city: "La Courneuve", country: "FR", latitude: 48.927,
        longitude: 2.397, updated: "2026-08-28T00:00:00Z"}]},
    declaredSharedIxCandidates: [], controlPlaneEvidence: {controlPlanePaths: []},
    infrastructureContradictions: {findings: []}}});
  picked = layer.source.entities.values.find((entity) => entity.id === "scythe-infra:pdb-fac:14445");
  actions.click({position: {x: 1, y: 2}});
  assert.equal(viewer.selectedEntity, picked);
  assert.equal(events[0].type, "scythe-web:infrastructure-selection");
  assert.equal(events[0].detail.entityId, "peeringdb:facility:14445");
  assert.deepEqual(events[0].detail.facility.environmentAsns, [64500]);
  layer.destroy(); assert.equal(listener, null);
});
