import {graphEntityTooltip} from "./graphEntityTooltip.js";
import {locationBoundary, locationEstimates, projectLocation} from "./hypergraphLocation.js";

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(document, tag, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, String(value));
  return element;
}

export class HypergraphLocationView {
  constructor({root, controller, nodeLimit = 500}) {
    if (!root || !controller) throw new TypeError("location view root and live graph controller are required");
    this.root = root; this.controller = controller; this.nodeLimit = nodeLimit;
    this.document = root.ownerDocument ?? globalThis.document;
    this.window = this.document?.defaultView ?? globalThis;
    this.svg = root.querySelector("[data-graph-location-map]");
    this.status = root.querySelector("[data-graph-location-status]");
    this.visible = false; this.graph = null; this.unsubscribe = null; this.resizeObserver = null;
    this.tooltip = this.document.createElement("div");
    this.tooltip.className = "live-hypergraph__tooltip"; this.tooltip.hidden = true;
    this.root.appendChild(this.tooltip);
  }

  start() {
    this.unsubscribe = this.controller.subscribe((update) => {
      if (update.available && update.graph) { this.graph = update.graph; if (this.visible) this.render(); }
    });
    if (this.window.ResizeObserver) {
      this.resizeObserver = new this.window.ResizeObserver(() => { if (this.visible) this.render(); });
      this.resizeObserver.observe(this.root);
    }
    return this;
  }

  setVisible(visible) {
    this.visible = Boolean(visible);
    if (this.visible) this.render(); else this.tooltip.hidden = true;
  }

  render() {
    if (!this.svg || !this.graph) return;
    this.svg.replaceChildren();
    const width = Math.max(this.svg.clientWidth || 420, 240);
    const height = Math.max(this.svg.clientHeight || 240, 150);
    this.svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const plot = svg(this.document, "g", {class: "location-map__plot"});
    const background = svg(this.document, "rect", {x: 18, y: 18, width: width - 36, height: height - 36,
      fill: "#030914", stroke: "#24506a"});
    plot.appendChild(background);
    for (let longitude = -120; longitude <= 120; longitude += 60) {
      const a = projectLocation(-90, longitude, width, height);
      const b = projectLocation(90, longitude, width, height);
      plot.appendChild(svg(this.document, "line", {x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        stroke: "#183349", "stroke-width": 1, "stroke-dasharray": "2 4"}));
    }
    for (let latitude = -60; latitude <= 60; latitude += 30) {
      const a = projectLocation(latitude, -180, width, height);
      const b = projectLocation(latitude, 180, width, height);
      plot.appendChild(svg(this.document, "line", {x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        stroke: "#183349", "stroke-width": 1, "stroke-dasharray": "2 4"}));
    }
    const estimates = locationEstimates(this.graph, this.nodeLimit);
    for (const estimate of estimates) {
      const point = projectLocation(estimate.latitude, estimate.longitude, width, height);
      if (estimate.uncertaintyRadiusKm > 0) {
        const radius = Math.max(4, Math.min(32, estimate.uncertaintyRadiusKm / 45));
        plot.appendChild(svg(this.document, "circle", {cx: point.x, cy: point.y, r: radius,
          fill: "rgba(247,209,84,.07)", stroke: "rgba(247,209,84,.42)", "stroke-dasharray": "3 3"}));
      }
      const marker = svg(this.document, "circle", {cx: point.x, cy: point.y, r: 5,
        fill: "#f7d154", stroke: "#fff", "stroke-width": 1, tabindex: 0,
        class: "location-map__marker", "data-entity-id": estimate.node.id});
      const tooltip = graphEntityTooltip(estimate.node);
      const title = svg(this.document, "title"); title.textContent = tooltip; marker.appendChild(title);
      marker.addEventListener("pointerenter", (event) => this.#showTooltip(event, tooltip));
      marker.addEventListener("pointermove", (event) => this.#showTooltip(event, tooltip));
      marker.addEventListener("pointerleave", () => { this.tooltip.hidden = true; });
      const select = () => this.root.dispatchEvent(new CustomEvent("scythe-web:graph-selection", {bubbles: true,
        detail: {kind: "graph-node", entityId: estimate.node.id, entityType: estimate.node.kind,
          graphRevision: this.graph.graphRevision, observedAt: estimate.node.observedAt ?? null}}));
      marker.addEventListener("click", select);
      marker.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") select(); });
      plot.appendChild(marker);
    }
    this.svg.appendChild(plot);
    if (this.status) this.status.textContent = locationBoundary(estimates.length, this.graph.nodes?.length ?? 0);
  }

  #showTooltip(event, text) {
    const bounds = this.root.getBoundingClientRect?.() ?? {left: 0, top: 0};
    this.tooltip.textContent = text; this.tooltip.hidden = false;
    this.tooltip.style.left = `${Number(event.clientX || 0) - bounds.left + 12}px`;
    this.tooltip.style.top = `${Number(event.clientY || 0) - bounds.top + 12}px`;
  }

  destroy() { this.unsubscribe?.(); this.resizeObserver?.disconnect(); this.tooltip.remove(); }
}
