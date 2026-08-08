import { evidenceStyle } from "./evidenceStyles.js";

const SVG_NS = "http://www.w3.org/2000/svg";

function hash(value) {
  let result = 2166136261;
  for (const char of String(value)) { result ^= char.charCodeAt(0); result = Math.imul(result, 16777619); }
  return result >>> 0;
}

function graphKind(node) {
  const kind = String(node.kind ?? "").toLowerCase();
  return kind === "event" || kind.includes("burst") ? "event" : "graph-node";
}

function layout(nodes, width, height) {
  const centerX = width / 2; const centerY = height / 2;
  const radius = Math.max(40, Math.min(width, height) * 0.39);
  const positions = new Map();
  nodes.forEach((node, index) => {
    const seed = hash(node.id); const ring = 0.42 + ((seed >>> 8) % 58) / 100;
    const angle = (index / Math.max(nodes.length, 1)) * Math.PI * 2 + (seed % 360) * Math.PI / 180;
    positions.set(node.id, {x: centerX + Math.cos(angle) * radius * ring,
      y: centerY + Math.sin(angle) * radius * ring});
  });
  return positions;
}

export class LiveHypergraphView {
  constructor({root, apiBase = "", fetchImpl = globalThis.fetch, refreshMilliseconds = 2000,
               nodeLimit = 200, edgeLimit = 300}) {
    if (!root) throw new TypeError("live hypergraph root is required");
    this.root = root; this.apiBase = apiBase; this.fetchImpl = fetchImpl;
    this.refreshMilliseconds = Math.max(500, Number(refreshMilliseconds) || 2000);
    this.nodeLimit = Math.min(Math.max(nodeLimit, 1), 500);
    this.edgeLimit = Math.min(Math.max(edgeLimit, 1), 1000);
    this.timer = null; this.running = false; this.graphRevision = null;
    this.statusRoot = root.querySelector("[data-live-graph-status]");
    this.svg = root.querySelector("svg");
  }

  async start() {
    this.running = true; await this.refresh(); this.#schedule(); return this;
  }

  #schedule() {
    clearTimeout(this.timer);
    if (this.running) this.timer = setTimeout(async () => {
      try { await this.refresh(); } finally { this.#schedule(); }
    }, this.refreshMilliseconds);
  }

  async refresh() {
    const graphUrl = `${this.apiBase}/api/graphops/selection/graph?node_limit=${this.nodeLimit}&edge_limit=${this.edgeLimit}`;
    const statusUrl = `${this.apiBase}/api/graphops/eve/status`;
    const [graphResponse, eveResponse] = await Promise.all([
      this.fetchImpl.call(globalThis, graphUrl, {credentials: "same-origin", cache: "no-store"}),
      this.fetchImpl.call(globalThis, statusUrl, {credentials: "same-origin", cache: "no-store"}),
    ]);
    const graph = await graphResponse.json();
    const eve = eveResponse.ok ? await eveResponse.json() : {status: "unavailable", committed: 0};
    if (!graphResponse.ok || !["ok", "empty"].includes(graph.status)) {
      this.#status(`LIVE HYPERGRAPH // UNAVAILABLE // HTTP ${graphResponse.status}`); return graph;
    }
    this.#status(`LIVE HYPERGRAPH // ${graph.status.toUpperCase()} // ${graph.nodeCount ?? graph.nodes.length} NODES // ${graph.edgeCount ?? graph.edges.length} EDGES\nEVE // ${eve.status?.toUpperCase() ?? "UNKNOWN"} // ${eve.committed ?? 0} COMMITTED // RAW PACKETS NOT EXPOSED`);
    if (graph.graphRevision !== this.graphRevision) {
      this.graphRevision = graph.graphRevision; this.render(graph);
      this.root.dispatchEvent(new CustomEvent("scythe-web:live-graph-revision", {bubbles: true,
        detail: {graphRevision: graph.graphRevision, nodeCount: graph.nodes.length, edgeCount: graph.edges.length}}));
    }
    return graph;
  }

  render(graph) {
    while (this.svg.firstChild) this.svg.removeChild(this.svg.firstChild);
    const width = Math.max(this.svg.clientWidth || 420, 240);
    const height = Math.max(this.svg.clientHeight || 260, 160);
    this.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const nodes = graph.nodes.slice(0, this.nodeLimit);
    const positions = layout(nodes, width, height);
    for (const edge of graph.edges.slice(0, this.edgeLimit)) {
      const members = (edge.nodes ?? []).filter((id) => positions.has(id));
      if (members.length < 2) continue;
      const origin = positions.get(members[0]);
      for (const member of members.slice(1)) {
        const target = positions.get(member); const line = document.createElementNS(SVG_NS, "line");
        const style = evidenceStyle(edge.evidenceClass ?? "INFERRED");
        line.setAttribute("x1", origin.x); line.setAttribute("y1", origin.y);
        line.setAttribute("x2", target.x); line.setAttribute("y2", target.y);
        line.setAttribute("stroke", style.color); line.setAttribute("stroke-opacity", String(style.alpha));
        line.setAttribute("stroke-width", edge.evidenceClass === "OBSERVED" ? "2.5" : "1.4");
        if (edge.evidenceClass === "INFERRED") line.setAttribute("stroke-dasharray", "5 5");
        line.classList.add("live-hypergraph__edge"); line.dataset.entityId = edge.id;
        line.addEventListener("click", () => this.#select({kind: "graph-edge", entityId: edge.id,
          graphRevision: graph.graphRevision, observedAt: edge.observedAt ?? edge.timestamp ?? null}));
        this.svg.appendChild(line);
      }
    }
    for (const node of nodes) {
      const point = positions.get(node.id); if (!point) continue;
      const style = evidenceStyle(node.evidenceClass ?? "INFERRED");
      const group = document.createElementNS(SVG_NS, "g"); group.classList.add("live-hypergraph__node");
      group.setAttribute("transform", `translate(${point.x} ${point.y})`); group.dataset.entityId = node.id;
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("r", node.kind === "network_host" ? "7" : "6");
      circle.setAttribute("fill", style.color); circle.setAttribute("fill-opacity", String(style.alpha));
      circle.setAttribute("stroke", "#071422"); circle.setAttribute("stroke-width", "2");
      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = `${node.id}\n${node.kind}\n${node.evidenceClass}`; group.append(circle, title);
      group.addEventListener("click", () => this.#select({kind: graphKind(node), entityId: node.id,
        graphRevision: graph.graphRevision, ...(node.position ? {position: node.position} : {}), observedAt: node.observedAt ?? null}));
      this.svg.appendChild(group);
    }
  }

  #select(detail) {
    this.root.dispatchEvent(new CustomEvent("scythe-web:graph-selection", {bubbles: true, detail}));
  }

  #status(text) { if (this.statusRoot) this.statusRoot.textContent = text; }
  destroy() { this.running = false; clearTimeout(this.timer); this.timer = null; }
}
