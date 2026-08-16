import assert from "node:assert/strict";
import test from "node:test";

import { LiveHypergraphView } from "./liveHypergraphView.js";

class Element {
  constructor() { this.children = []; this.attributes = {}; this.dataset = {}; this.listeners = {};
    this.classList = {add: (...names) => { this.classes = names; }}; this.clientWidth = 420; this.clientHeight = 260; }
  get firstChild() { return this.children[0] ?? null; }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}

test("live hypergraph polls bounded graph and Eve status without inventing geography", async () => {
  const previousDocument = globalThis.document; const previousEvent = globalThis.CustomEvent;
  globalThis.document = {createElementNS: () => new Element()};
  globalThis.CustomEvent = class { constructor(type, init) { this.type = type; this.detail = init.detail; } };
  try {
    const status = {textContent: ""}; const svg = new Element(); const events = [];
    const root = {querySelector: (selector) => selector === "svg" ? svg : status,
      dispatchEvent: (event) => events.push(event)};
    const graph = {status: "ok", graphRevision: "graph-live-1", nodeCount: 2, edgeCount: 1,
      nodes: [{id: "host:a", kind: "network_host", evidenceClass: "OBSERVED"},
              {id: "host:b", kind: "network_host", evidenceClass: "OBSERVED"}],
      edges: [{id: "flow:a", kind: "network_flow", nodes: ["host:a", "host:b"], evidenceClass: "OBSERVED",
        labels:{operational_direction:"OUTBOUND",direction_basis:"DISCOVERED_SENSOR_INTERFACE",
          motion_basis:"OBSERVED_SURICATA_COUNTER_DELTA",motion_interval_ms:"1000",
          motion_forward_delta_packets:"4",motion_reverse_delta_packets:"2"}}]};
    const fetchImpl = async (url) => new Response(JSON.stringify(url.includes("eve/status")
      ? {status: "ok", committed: 4} : graph), {status: 200});
    const view = new LiveHypergraphView({root, fetchImpl, refreshMilliseconds: 60_000});
    await view.start();
    assert.match(status.textContent, /4 COMMITTED/);
    assert.ok(svg.children.length >= 3);
    const renderedEdge = svg.children.find((child) => child.dataset.entityId === "flow:a");
    assert.equal(renderedEdge.dataset.flowType, "OTHER");
    assert.equal(renderedEdge.attributes.stroke, "#7890a8");
    assert.ok(Number(renderedEdge.attributes["stroke-width"]) > 1.4);
    const arrow = svg.children.find((child) => child.dataset.operationalDirection === "OUTBOUND");
    assert.equal(arrow.attributes.fill, "#00d4ff");
    assert.match(arrow.children[0].textContent, /VISUAL SCALE/);
    assert.equal(svg.children.filter((child) => child.classes?.includes("live-hypergraph__flow-particle")).length, 2);
    assert.equal(events[0].detail.graphRevision, "graph-live-1");
    svg.children.find((child) => child.dataset.entityId === "host:a").listeners.click();
    assert.equal(events.at(-1).detail.entityType, "network_host");
    assert.equal(graph.nodes.some((node) => "position" in node), false);
    view.destroy();
  } finally { globalThis.document = previousDocument; globalThis.CustomEvent = previousEvent; }
});
