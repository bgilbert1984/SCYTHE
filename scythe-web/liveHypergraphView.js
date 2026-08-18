import { evidenceStyle, flowDirectionStyle, flowMotion, flowTypeStyle, graphPurposeStyle, hostLivenessStyle } from "./evidenceStyles.js";
import { LiveGraphController } from "./liveGraphController.js";
import { graphEntityTooltip } from "./graphEntityTooltip.js";
import {GRAPH_VISUAL_SCALE_BOUNDARY, graphFlowScale, graphNodeScale} from "./graphVisualScale.js";
import {projectCityContext} from "./cityContextProjection.js";
import {separatePlanarNodes, topologyEdgeGeometry} from "./topologyGeometry.js";

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
    this.reducedMotion = Boolean(this.window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
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
    const displayGraph = projectCityContext(graph);
    const nodes = displayGraph.nodes.slice(0, this.nodeLimit + displayGraph.cityContext.nodeCount);
    const initial = layout(nodes, width, height);
    for (const city of nodes.filter((node) => node.kind === "geographic_city_context")) {
      const hosts = displayGraph.edges.filter((edge) => edge.kind === "geoip_city_membership" &&
        edge.nodes?.includes(city.id)).flatMap((edge) => edge.nodes.filter((id) => id !== city.id))
        .map((id) => initial.get(id)).filter(Boolean);
      if (hosts.length) initial.set(city.id, {x:hosts.reduce((sum,p)=>sum+p.x,0)/hosts.length,
        y:hosts.reduce((sum,p)=>sum+p.y,0)/hosts.length});
    }
    const positions = separatePlanarNodes(nodes, initial, width, height);
    const radii = new Map(nodes.map((node) => [node.id, graphNodeScale(node).topologyRadius]));
    for (const edge of displayGraph.edges.slice(0, this.edgeLimit + displayGraph.cityContext.edgeCount)) {
      const members = (edge.nodes ?? []).filter((id) => positions.has(id));
      if (members.length < 2) continue;
      const origin = positions.get(members[0]);
      for (const member of members.slice(1)) {
        const target = positions.get(member); const line = document.createElementNS(SVG_NS, "line");
        const evidence = evidenceStyle(edge.evidenceClass ?? "INFERRED");
        const style = flowTypeStyle(edge);
        const direction = flowDirectionStyle(edge); const motion = flowMotion(edge);
        const scale = graphFlowScale(edge);
        const geometry = topologyEdgeGeometry(origin, target, radii.get(members[0]), radii.get(member),
          scale.arrowPixels);
        line.setAttribute("x1", geometry.start.x); line.setAttribute("y1", geometry.start.y);
        line.setAttribute("x2", geometry.end.x); line.setAttribute("y2", geometry.end.y);
        line.setAttribute("stroke", style.color); line.setAttribute("stroke-opacity", String(style.alpha));
        line.setAttribute("stroke-width", String(scale.topologyWidth));
        if (evidence.line !== "solid") line.setAttribute("stroke-dasharray",
          evidence.line === "dotted" ? "2 4" : "5 5");
        line.classList.add("live-hypergraph__edge"); line.dataset.entityId = edge.id;
        if (edge.kind === "geoip_city_membership") line.classList.add("live-hypergraph__edge--context");
        line.dataset.flowType = style.type ?? "";
        const title = document.createElementNS(SVG_NS, "title");
        title.textContent = edge.kind === "geoip_city_membership" ?
          `CITY MEMBERSHIP // INFERRED\n${edge.id}\nRELATION // HOST GEOIP ESTIMATE → CITY CONTEXT\nBOUNDARY // DISPLAY-DERIVED; NOT A PHYSICAL LINK OR GRAPHOPS EXECUTION TARGET` :
          `${style.label ?? "GRAPH EDGE"}\n${edge.id}\nTUPLE // SOURCE → DESTINATION · ${String(direction.tupleBasis).replaceAll("_", " ")}\nOPERATIONAL // ${direction.label} · ${String(direction.basis).replaceAll("_", " ")}\nMOTION // ${motion.measured ? `${motion.forwardPackets} FORWARD · ${motion.reversePackets} REVERSE PACKETS / ${motion.intervalMilliseconds} ms` : "STATIC · INSUFFICIENT TEMPORAL COUNTER DELTAS"}\nVISUAL SCALE // ${scale.basis.replaceAll("_", " ")} · WIDTH ${scale.topologyWidth.toFixed(2)} · ARROW ${scale.arrowPixels.toFixed(2)} PX\n${String(style.basis ?? "DISPLAY CLASSIFICATION").replaceAll("_", " ")}\n${evidence.label}\nBOUNDARY // ${GRAPH_VISUAL_SCALE_BOUNDARY}`;
        line.appendChild(title);
        const selectEdge = () => this.#select({kind: "graph-edge", entityId: edge.id,
          entityType: edge.kind,
          graphRevision: graph.graphRevision, observedAt: edge.observedAt ?? edge.timestamp ?? null});
        if (!edge.display?.selectionDisabled) line.addEventListener("click", selectEdge);
        this.svg.appendChild(line);
        const directional = edge.display?.directional !== false;
        if (directional && geometry.arrowVisible) {
          const {x:mx,y:my} = geometry.arrow; const {ux,uy,arrowLength} = geometry;
          const arrowHalfWidth = arrowLength * .48;
          const arrow = this.document.createElementNS(SVG_NS, "polygon");
          arrow.setAttribute("points", `${mx + ux * arrowLength * .56},${my + uy * arrowLength * .56} ${mx - ux * arrowLength * .44 - uy * arrowHalfWidth},${my - uy * arrowLength * .44 + ux * arrowHalfWidth} ${mx - ux * arrowLength * .44 + uy * arrowHalfWidth},${my - uy * arrowLength * .44 - ux * arrowHalfWidth}`);
          arrow.setAttribute("fill", direction.color); arrow.setAttribute("stroke", "#071422");
          arrow.setAttribute("stroke-width", "1"); arrow.classList.add("live-hypergraph__direction-arrow");
          arrow.dataset.entityId = edge.id; arrow.dataset.operationalDirection = direction.direction;
          const arrowTitle = this.document.createElementNS(SVG_NS, "title"); arrowTitle.textContent = title.textContent;
          arrow.appendChild(arrowTitle); arrow.addEventListener("click", selectEdge);
          this.svg.appendChild(arrow);
        }
        if (directional && motion.measured && !this.reducedMotion) {
          const addParticle = (from, to, reverse, count) => {
            if (!(count > 0)) return;
            const particle = this.document.createElementNS(SVG_NS, "circle");
            particle.setAttribute("r", String(Math.min(3.2, 1.5 + Math.log2(count + 1) * .35)));
            particle.setAttribute("fill", reverse ? "#ff6fb7" : "#ffffff");
            particle.setAttribute("pointer-events", "none");
            particle.classList.add("live-hypergraph__flow-particle"); particle.dataset.direction = reverse ? "reverse" : "forward";
            const animation = this.document.createElementNS(SVG_NS, "animateMotion");
            animation.setAttribute("path", `M ${from.x} ${from.y} L ${to.x} ${to.y}`);
            animation.setAttribute("dur", `${motion.durationSeconds}s`); animation.setAttribute("repeatCount", "indefinite");
            particle.appendChild(animation); this.svg.appendChild(particle);
          };
          addParticle(geometry.start, geometry.end, false, motion.forwardPackets);
          addParticle(geometry.end, geometry.start, true, motion.reversePackets);
        }
      }
    }
    for (const node of nodes) {
      const point = positions.get(node.id); if (!point) continue;
      const style = graphPurposeStyle(node);
      const scale = graphNodeScale(node);
      const group = document.createElementNS(SVG_NS, "g"); group.classList.add("live-hypergraph__node");
      if (node.kind === "geographic_city_context") group.classList.add("live-hypergraph__node--city");
      group.setAttribute("transform", `translate(${point.x} ${point.y})`); group.dataset.entityId = node.id;
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("r", String(scale.topologyRadius));
      circle.setAttribute("fill", style.color); circle.setAttribute("fill-opacity", String(style.alpha));
      circle.setAttribute("stroke", "#071422"); circle.setAttribute("stroke-width", "2");
      const liveness = hostLivenessStyle(node);
      if (liveness) {
        const badge = document.createElementNS(SVG_NS, "circle");
        badge.setAttribute("cy", String(-scale.topologyRadius - 5)); badge.setAttribute("r", "3.5");
        badge.setAttribute("fill", liveness.color); badge.setAttribute("stroke", "#fff");
        badge.setAttribute("stroke-width", "1"); badge.classList.add("live-hypergraph__liveness-badge");
        group.appendChild(badge);
      }
      const title = document.createElementNS(SVG_NS, "title");
      const tooltipText = `${graphEntityTooltip(node)}\n\nVISUAL SCALE // ${scale.basis.replaceAll("_", " ")} · NODE RADIUS ${scale.topologyRadius.toFixed(2)} PX\nBOUNDARY // SIZE IS PRESENTATION METADATA; IT DOES NOT CHANGE EVIDENCE AUTHORITY`;
      title.textContent = tooltipText; group.append(circle, title);
      group.addEventListener("pointerenter", (event) => this.#showTooltip(event, tooltipText));
      group.addEventListener("pointermove", (event) => this.#showTooltip(event, tooltipText));
      group.addEventListener("pointerleave", () => { if (this.tooltip) this.tooltip.hidden = true; });
      if (!node.display?.selectionDisabled) group.addEventListener("click", () => this.#select({kind: graphKind(node), entityId: node.id,
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
