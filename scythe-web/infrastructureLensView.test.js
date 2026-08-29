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
