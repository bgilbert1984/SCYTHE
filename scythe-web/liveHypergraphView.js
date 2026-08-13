import { evidenceStyle, graphPurposeStyle, hostLivenessStyle } from "./evidenceStyles.js";
import { LiveGraphController } from "./liveGraphController.js";
import { graphEntityTooltip } from "./graphEntityTooltip.js";

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
               nodeLimit = 200, edgeLimit = 300, controller = null}) {
    if (!root) throw new TypeError("live hypergraph root is required");
    this.root = root; this.apiBase = apiBase; this.fetchImpl = fetchImpl;
    this.refreshMilliseconds = Math.max(500, Number(refreshMilliseconds) || 2000);
    this.nodeLimit = Math.min(Math.max(nodeLimit, 1), 500);
    this.edgeLimit = Math.min(Math.max(edgeLimit, 1), 1000);
    this.controller = controller ?? new LiveGraphController({apiBase, fetchImpl, refreshMilliseconds,
      nodeLimit: this.nodeLimit, edgeLimit: this.edgeLimit});
    this.ownsController = !controller; this.unsubscribe = null;
    this.running = false; this.graphRevision = null;
    this.statusRoot = root.querySelector("[data-live-graph-status]");
    this.svg = root.querySelector("svg");
    this.document = root.ownerDocument ?? globalThis.document;
    this.window = this.document?.defaultView ?? globalThis;
    this.latestGraph = null; this.resizeObserver = null;
    this.tooltip = this.document?.createElement?.("div") ?? null;
    if (this.tooltip) {
      this.tooltip.className = "live-hypergraph__tooltip"; this.tooltip.hidden = true;
      this.root.appendChild?.(this.tooltip);
    }
  }

  async start() {
    this.running = true;
    this.resizeObserver = this.window.ResizeObserver ? new this.window.ResizeObserver(() => {
      if (this.latestGraph) this.render(this.latestGraph);
    }) : null;
    this.resizeObserver?.observe(this.svg);
    this.unsubscribe = this.controller.subscribe((update) => this.#update(update));
    await this.controller.start();
    return this;
  }

  async refresh() { return this.controller.refresh(); }

  #update(update) {
    this.#status(update.message);
    const graph = update.graph;
    if (!update.available || !graph) return;
    this.latestGraph = graph;
    if (update.changed || graph.graphRevision !== this.graphRevision) {
      this.graphRevision = graph.graphRevision; this.render(graph);
      this.root.dispatchEvent(new CustomEvent("scythe-web:live-graph-revision", {bubbles: true,
        detail: {graphRevision: graph.graphRevision, nodeCount: graph.nodes.length, edgeCount: graph.edges.length}}));
    }
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
      const style = graphPurposeStyle(node);
      const group = document.createElementNS(SVG_NS, "g"); group.classList.add("live-hypergraph__node");
      group.setAttribute("transform", `translate(${point.x} ${point.y})`); group.dataset.entityId = node.id;
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("r", node.kind === "network_host" ? "7" : "6");
      circle.setAttribute("fill", style.color); circle.setAttribute("fill-opacity", String(style.alpha));
      circle.setAttribute("stroke", "#071422"); circle.setAttribute("stroke-width", "2");
      const liveness = hostLivenessStyle(node);
      if (liveness) {
        const badge = document.createElementNS(SVG_NS, "circle");
        badge.setAttribute("cy", "-12"); badge.setAttribute("r", "3.5");
        badge.setAttribute("fill", liveness.color); badge.setAttribute("stroke", "#fff");
        badge.setAttribute("stroke-width", "1"); badge.classList.add("live-hypergraph__liveness-badge");
        group.appendChild(badge);
      }
      const title = document.createElementNS(SVG_NS, "title");
      const tooltipText = graphEntityTooltip(node);
      title.textContent = tooltipText; group.append(circle, title);
      group.addEventListener("pointerenter", (event) => this.#showTooltip(event, tooltipText));
      group.addEventListener("pointermove", (event) => this.#showTooltip(event, tooltipText));
      group.addEventListener("pointerleave", () => { if (this.tooltip) this.tooltip.hidden = true; });
      group.addEventListener("click", () => this.#select({kind: graphKind(node), entityId: node.id,
        entityType: node.kind, graphRevision: graph.graphRevision,
        ...(node.position ? {position: node.position} : {}), observedAt: node.observedAt ?? null}));
      this.svg.appendChild(group);
    }
  }

  #select(detail) {
    this.root.dispatchEvent(new CustomEvent("scythe-web:graph-selection", {bubbles: true, detail}));
  }

  #showTooltip(event, tooltipText) {
    if (!this.tooltip) return;
    const bounds = this.root.getBoundingClientRect?.() ?? {left: 0, top: 0};
    this.tooltip.textContent = tooltipText; this.tooltip.hidden = false;
    this.tooltip.style.left = `${Number(event?.clientX || 0) - bounds.left + 12}px`;
    this.tooltip.style.top = `${Number(event?.clientY || 0) - bounds.top + 12}px`;
  }

  #status(text) { if (this.statusRoot) this.statusRoot.textContent = text; }
  destroy() {
    this.running = false; this.unsubscribe?.(); this.unsubscribe = null;
    this.resizeObserver?.disconnect(); this.resizeObserver = null;
    if (this.ownsController) this.controller.destroy();
  }
}
