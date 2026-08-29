import assert from "node:assert/strict";
import test from "node:test";

import {InfrastructureLensView} from "./infrastructureLensView.js";

class Element {
  constructor(tag = "div") { this.tag = tag; this.children = []; this.listeners = {}; this.attributes = {};
    this.className = ""; this.textContent = ""; this.disabled = false; }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this.children = [...children]; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
}

test("selected facility evidence exposes related observed hosts without claiming traffic", () => {
  const events = []; let publish;
  const EventClass = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
  const document = {defaultView: {CustomEvent: EventClass}, createElement: (tag) => new Element(tag)};
  const root = new Element(); root.ownerDocument = document; root.dispatchEvent = (event) => events.push(event);
  const controller = {subscribe(callback) { publish = callback; return () => { publish = null; }; }};
  const view = new InfrastructureLensView({root, controller}).start();
  publish({snapshot: {status: "ok", graphRevision: "graph-fac-3", summary: {},
    domains: [{id: "asn:64500", asn: 64500, organization: "Example Network", hostCount: 1,
      observedHostIds: ["host:192.0.2.10"]}], observedFlows: [],
    peeringdbEvidence: {status: "ok", networks: []}, controlPlaneEvidence: {controlPlanePaths: []},
    infrastructureContradictions: {summary: {}, findings: [], changes: [], withheld: []},
    boundary: "DISPLAY ONLY; NOT ROUTE"}});
  view.setInfrastructureSelection({kind: "peeringdb-facility", facility: {id: "14445",
    name: "Interxion PAR8", city: "La Courneuve", country: "FR", latitude: 48.927,
    longitude: 2.397, updated: "2026-08-28T00:00:00Z", environmentAsns: [64500]},
    boundary: "CO-LOCATION DOES NOT PROVE TRAFFIC, PATH, OR DEVICE PRESENCE"});
  view.setVisible(true);

  const panel = root.children.find((child) => child.className === "infra-lens__selection");
  assert.ok(panel); assert.match(panel.children[0].textContent, /FAC 14445/);
  assert.match(panel.children[1].textContent, /La Courneuve/);
  assert.match(panel.children.at(-1).textContent, /DOES NOT PROVE TRAFFIC/);
  const relatedHost = panel.children[2].children[0];
  assert.match(relatedHost.textContent, /host:192\.0\.2\.10/);
  relatedHost.listeners.click();
  assert.equal(events[0].type, "scythe-web:graph-selection");
  assert.deepEqual(events[0].detail, {kind: "graph-node", entityId: "host:192.0.2.10",
    graphRevision: "graph-fac-3"});
  view.destroy(); assert.equal(publish, null);
});

test("selected ASN evidence exposes every bounded observed host without claiming a route", () => {
  const events = []; let publish;
  const EventClass = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
  const document = {defaultView: {CustomEvent: EventClass}, createElement: (tag) => new Element(tag)};
  const root = new Element(); root.ownerDocument = document; root.dispatchEvent = (event) => events.push(event);
  const controller = {subscribe(callback) { publish = callback; return () => { publish = null; }; }};
  const view = new InfrastructureLensView({root, controller}).start();
  publish({snapshot: {status: "ok", graphRevision: "graph-asn-2", summary: {},
    domains: [], observedFlows: [], peeringdbEvidence: {status: "ok", networks: []},
    controlPlaneEvidence: {controlPlanePaths: []},
    infrastructureContradictions: {summary: {}, findings: [], changes: [], withheld: []},
    boundary: "DISPLAY ONLY; NOT ROUTE"}});
  view.setInfrastructureSelection({kind: "infrastructure-domain", domain: {id: "asn:20940", asn: 20940,
    organization: "Akamai International B.V.", hostIds: ["host:23.48.99.72", "host:23.48.99.73"],
    prefixes: ["23.32.0.0/11"], latitude: 42.1167, longitude: -86.4542, uncertaintyRadiusKm: 50,
    placementAuthority: "GEOIP_ESTIMATE_CENTROID"}, authority: "HOST_PREFIX_ENRICHMENT",
    boundary: "THE CENTROID DOES NOT LOCATE A DEVICE OR PROVE A ROUTE"});
  view.setVisible(true);

  const panel = root.children.find((child) => child.className === "infra-lens__selection");
  assert.ok(panel); assert.match(panel.children[0].textContent, /ASN 20940/);
  assert.match(panel.children[1].textContent, /Akamai International/);
  assert.match(panel.children[1].textContent, /23\.32\.0\.0\/11/);
  assert.match(panel.children[1].textContent, /HOST_PREFIX_ENRICHMENT/);
  assert.match(panel.children[1].textContent, /GEOIP_ESTIMATE_CENTROID/);
  assert.match(panel.children.at(-1).textContent, /DOES NOT LOCATE A DEVICE OR PROVE A ROUTE/);
  assert.equal(panel.children[2].children.length, 2);
  panel.children[2].children[1].listeners.click();
  assert.equal(events[0].type, "scythe-web:graph-selection");
  assert.deepEqual(events[0].detail, {kind: "graph-node", entityId: "host:23.48.99.73",
    graphRevision: "graph-asn-2"});
  view.destroy(); assert.equal(publish, null);
});

test("tension, observed-flow, and RIS panels route only to retained graph evidence", () => {
  const events = []; let publish;
  const EventClass = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
  const document = {defaultView: {CustomEvent: EventClass}, createElement: (tag) => new Element(tag)};
  const root = new Element(); root.ownerDocument = document; root.dispatchEvent = (event) => events.push(event);
  const controller = {subscribe(callback) { publish = callback; return () => { publish = null; }; }};
  const view = new InfrastructureLensView({root, controller}).start();
  publish({snapshot: {status: "ok", graphRevision: "graph-interactions-2", summary: {},
    domains: [], observedFlows: [], peeringdbEvidence: {status: "ok", networks: []},
    controlPlaneEvidence: {controlPlanePaths: []},
    infrastructureContradictions: {summary: {}, findings: [], changes: [], withheld: []},
    boundary: "DISPLAY ONLY; NOT ROUTE"}});
  view.setVisible(true);
  const selectedPanel = () => root.children.find((child) => child.className === "infra-lens__selection");

  view.setInfrastructureSelection({kind: "infrastructure-evidence-tension", authority: "PRESERVED_SOURCE_DISAGREEMENT",
    finding: {kind: "ORIGIN_DISAGREEMENT", status: "UNRESOLVED", severity: "REVIEW",
      subject: "asn:20940", prefix: "23.32.0.0/11",
      claims: [{value: "20940", authority: "HOST_PREFIX_ENRICHMENT"},
        {value: ["64500"], authority: "RIS_LIVE_COLLECTOR_VANTAGE", collectorId: "rrc21"}],
      alternatives: ["LOCAL ENRICHMENT IS STALE"], falsifier: "COMPARE MULTIPLE COLLECTORS",
      relatedHostIds: ["host:23.1.1.1"]}, boundary: "NOT A HIJACK DETERMINATION"});
  let panel = selectedPanel();
  assert.match(panel.children[0].textContent, /EVIDENCE TENSION/);
  assert.match(panel.children[1].textContent, /COMPARE MULTIPLE COLLECTORS/);
  assert.match(panel.children.at(-1).textContent, /NOT A HIJACK/);
  panel.children[2].children[0].listeners.click();
  assert.deepEqual(events[0].detail, {kind: "graph-node", entityId: "host:23.1.1.1",
    graphRevision: "graph-interactions-2"});

  view.setInfrastructureSelection({kind: "infrastructure-observed-flow", authority: "OBSERVED_GRAPH_EDGES",
    flow: {sourceDomain: "asn:20940", targetDomain: "asn:64500", protocol: "tcp", flowCount: 3,
      packets: 12, bytes: 1200, memberEdgeIds: ["flow:1", "flow:2"], firstSeen: "start", lastSeen: "end"},
    boundary: "ARC IS NOT A PHYSICAL OR BGP ROUTE"});
  panel = selectedPanel();
  assert.match(panel.children[0].textContent, /OBSERVED FLOW/);
  assert.match(panel.children[1].textContent, /3 FLOWS/);
  assert.match(panel.children.at(-1).textContent, /NOT A PHYSICAL OR BGP ROUTE/);
  panel.children[2].children[1].listeners.click();
  assert.deepEqual(events[1].detail, {kind: "graph-edge", entityId: "flow:2",
    graphRevision: "graph-interactions-2"});

  view.setInfrastructureSelection({kind: "infrastructure-ris-path", controlPlaneRevision: "ris-revision-2",
    observation: {messageType: "ANNOUNCE", prefix: "23.32.0.0/11", collectorId: "rrc21",
      collectorReceivedIso: "2026-08-29T12:00:00Z", peerAsn: 64496, originAsns: [64500],
      asPath: [64496, 3356, [64500, 64501]], segmentIndex: 1,
      segmentSourceAsns: [3356], segmentTargetAsns: [64500], relatedHostIds: ["host:192.0.2.10"]},
    boundary: "NOT AN OBSERVED DATA-PLANE ROUTE"});
  panel = selectedPanel();
  assert.match(panel.children[0].textContent, /RIS ANNOUNCE/);
  assert.match(panel.children[1].textContent, /64496 → 3356 → \{64500,64501\}/);
  assert.match(panel.children[1].textContent, /ris-revision-2/);
  assert.match(panel.children.at(-1).textContent, /NOT AN OBSERVED DATA-PLANE ROUTE/);
  panel.children[2].children[0].listeners.click();
  assert.deepEqual(events[2].detail, {kind: "graph-node", entityId: "host:192.0.2.10",
    graphRevision: "graph-interactions-2"});
  view.destroy(); assert.equal(publish, null);
});
