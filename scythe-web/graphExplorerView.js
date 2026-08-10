import {graphEntityTooltip} from "./graphEntityTooltip.js";

function epoch(value) {
  if (!value) return null;
  const milliseconds = new Date(value).getTime();
  return Number.isFinite(milliseconds) ? milliseconds / 1000 : null;
}

export function buildExplorerQuery(state = {}) {
  const query = new URLSearchParams();
  const add = (key, value) => { if (value !== "" && value != null) query.set(key, String(value)); };
  add("q", String(state.text ?? "").trim()); add("protocol", String(state.protocol ?? "").trim().toLowerCase());
  add("start", epoch(state.start)); add("end", epoch(state.end));
  if (state.focusEnabled && state.focusId) {
    const requestedDepth = Number(state.depth);
    add("focus_id", state.focusId); add("depth", Math.min(Math.max(
      Number.isFinite(requestedDepth) ? requestedDepth : 1, 0), 2));
  }
  add("node_limit", state.nodeLimit ?? 100); add("edge_limit", state.edgeLimit ?? 150);
  add("node_offset", state.nodeOffset ?? 0); add("edge_offset", state.edgeOffset ?? 0);
  return query;
}

export function explorerEntityKind(entity, fallback = "graph-node") {
  const kind = String(entity?.kind ?? "").toLowerCase();
  if (kind.includes("flow") || kind.includes("edge") || kind.includes("contradict")) return "graph-edge";
  if (kind.includes("event") || kind.includes("burst")) return "event";
  return fallback;
}

export function formatExplorerCounts(result) {
  const count = result?.counts ?? {};
  return `AVAILABLE // ${count.availableNodes ?? 0} NODES · ${count.availableEdges ?? 0} EDGES\n` +
    `SCANNED // ${count.scannedNodes ?? 0} NODES · ${count.scannedEdges ?? 0} ELIGIBLE EDGES\n` +
    `MATCHED // ${count.matchedNodes ?? 0} NODES · ${count.matchedEdges ?? 0} EDGES\n` +
    `RETURNED // ${count.returnedNodes ?? 0} NODES · ${count.returnedEdges ?? 0} EDGES` +
    (result?.scanTruncated ? "\nSCAN // TRUNCATED AT DECLARED BOUND" : "\nSCAN // COMPLETE WITHIN DECLARED BOUND");
}

function element(document, tag, className, text = null) {
  const node = document.createElement(tag); node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

export class GraphExplorerView {
  constructor({root, apiBase = "", fetchImpl = globalThis.fetch} = {}) {
    if (!root) throw new TypeError("GraphExplorerView requires a root");
    this.root = root; this.apiBase = apiBase; this.fetchImpl = fetchImpl;
    this.document = root.ownerDocument ?? globalThis.document;
    this.window = this.document?.defaultView ?? globalThis;
    this.viewport = root.querySelector("[data-graph-explorer]");
    this.form = root.querySelector("[data-graph-explorer-form]");
    this.counts = root.querySelector("[data-graph-explorer-counts]");
    this.nodes = root.querySelector("[data-graph-explorer-nodes]");
    this.edges = root.querySelector("[data-graph-explorer-edges]");
    this.previous = root.querySelector("[data-graph-explorer-previous]");
    this.next = root.querySelector("[data-graph-explorer-next]");
    this.focus = root.querySelector("[data-graph-explorer-focus]");
    if (![this.viewport, this.form, this.counts, this.nodes, this.edges].every(Boolean)) {
      throw new TypeError("Graph Explorer markup is incomplete");
    }
    this.state = {text: "", protocol: "", start: "", end: "", focusId: "", focusEnabled: false,
      depth: 1, nodeLimit: 100, edgeLimit: 150, nodeOffset: 0, edgeOffset: 0};
    this.visible = false; this.sequence = 0; this.result = null;
    this.form.addEventListener("submit", (event) => { event.preventDefault(); this.#readFilters(); this.#resetPage(); void this.refresh(); });
    root.querySelector("[data-graph-explorer-clear]")?.addEventListener("click", () => this.clear());
    this.focus?.addEventListener("click", () => { this.#readFilters(); this.state.focusEnabled = !this.state.focusEnabled;
      this.focus.setAttribute("aria-pressed", String(this.state.focusEnabled)); this.#resetPage(); void this.refresh(); });
    this.previous?.addEventListener("click", () => this.page(-1));
    this.next?.addEventListener("click", () => this.page(1));
  }

  setVisible(visible) {
    this.visible = Boolean(visible); this.viewport.hidden = !this.visible;
    if (this.visible && !this.result) void this.refresh();
  }

  setSelection(selection) {
    if (!selection?.entityId) return;
    this.state.focusId = String(selection.entityId);
    this.focus.textContent = `FOCUS // ${this.state.focusId}`;
    this.focus.disabled = false;
  }

  #readFilters() {
    const data = new FormData(this.form);
    this.state.text = String(data.get("q") ?? ""); this.state.protocol = String(data.get("protocol") ?? "");
    this.state.start = String(data.get("start") ?? ""); this.state.end = String(data.get("end") ?? "");
    this.state.depth = Number(data.get("depth") ?? 1);
  }

  #resetPage() { this.state.nodeOffset = 0; this.state.edgeOffset = 0; }

  clear() {
    this.form.reset(); Object.assign(this.state, {text: "", protocol: "", start: "", end: "",
      focusEnabled: false, depth: 1, nodeOffset: 0, edgeOffset: 0});
    this.focus.setAttribute("aria-pressed", "false"); void this.refresh();
  }

  page(direction) {
    const counts = this.result?.counts ?? {};
    if (direction > 0) {
      if (this.state.nodeOffset + this.state.nodeLimit < (counts.matchedNodes ?? 0) ||
          this.state.edgeOffset + this.state.edgeLimit < (counts.matchedEdges ?? 0)) {
        this.state.nodeOffset += this.state.nodeLimit; this.state.edgeOffset += this.state.edgeLimit;
      }
    } else {
      this.state.nodeOffset = Math.max(0, this.state.nodeOffset - this.state.nodeLimit);
      this.state.edgeOffset = Math.max(0, this.state.edgeOffset - this.state.edgeLimit);
    }
    void this.refresh();
  }

  async refresh() {
    const sequence = ++this.sequence; this.counts.textContent = "GRAPH EXPLORER // QUERYING BOUNDED INDEX…";
    try {
      const query = buildExplorerQuery(this.state);
      const response = await this.fetchImpl.call(globalThis, `${this.apiBase}/api/graphops/explorer?${query}`, {
        credentials: "same-origin", cache: "no-store"});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error ?? `HTTP ${response.status}`);
      if (!result?.bounded || !result.counts || !Array.isArray(result.nodes) || !Array.isArray(result.edges)) {
        throw new Error("explorer response violated its bounded contract");
      }
      if (sequence !== this.sequence) return;
      this.result = result; this.#render(result);
    } catch (error) {
      if (sequence !== this.sequence) return;
      this.counts.textContent = `GRAPH EXPLORER // UNAVAILABLE // ${error.message}`;
      this.nodes.replaceChildren(); this.edges.replaceChildren();
    }
  }

  #render(result) {
    const focus = result.focus?.requested
      ? `\nFOCUS // ${result.focus.id} // ${result.focus.found ? "FOUND" : "NOT FOUND"} // DEPTH ${result.focus.depth}` : "";
    const unknown = result.counts.unknownTimeExcluded
      ? `\nUNKNOWN TIME EXCLUDED // ${result.counts.unknownTimeExcluded}` : "";
    this.counts.textContent = `${formatExplorerCounts(result)}${focus}${unknown}\nREVISION // ${result.graphRevision}\nTEMPORAL // ${result.temporalSemantics}`;
    this.nodes.replaceChildren(); this.edges.replaceChildren();
    for (const node of result.nodes) this.nodes.append(this.#entity(node, "graph-node"));
    for (const edge of result.edges) this.edges.append(this.#entity(edge, "graph-edge"));
    if (!result.nodes.length) this.nodes.append(element(this.document, "div", "graph-result__empty", "NO MATCHING NODES"));
    if (!result.edges.length) this.edges.append(element(this.document, "div", "graph-result__empty", "NO MATCHING EDGES"));
    const counts = result.counts;
    this.previous.disabled = counts.nodeOffset <= 0 && counts.edgeOffset <= 0;
    this.next.disabled = counts.nodeOffset + counts.returnedNodes >= counts.matchedNodes &&
      counts.edgeOffset + counts.returnedEdges >= counts.matchedEdges;
  }

  #entity(entity, fallbackKind) {
    const button = element(this.document, "button", "graph-explorer__entity"); button.type = "button";
    const labels = entity.labels ?? {}; const enrichment = entity.enrichment ?? {};
    const network = enrichment.network ?? {}; const geo = enrichment.geo ?? {};
    const summary = fallbackKind === "graph-edge"
      ? [labels.proto, labels.dest_port && `→${labels.dest_port}`, entity.evidenceClass].filter(Boolean).join(" · ")
      : [network.asn && `AS${network.asn}`, network.organization, geo.city || geo.countryCode,
        labels.flowRole, entity.evidenceClass].filter(Boolean).join(" · ");
    button.append(element(this.document, "span", "graph-explorer__entity-id", entity.id),
      element(this.document, "span", "graph-explorer__entity-summary", summary || entity.kind));
    button.title = graphEntityTooltip(entity);
    button.addEventListener("click", () => this.root.dispatchEvent(new this.window.CustomEvent(
      "scythe-web:graph-selection", {bubbles: true, detail: {kind: explorerEntityKind(entity, fallbackKind),
        entityId: entity.id, entityType: entity.kind, graphRevision: this.result.graphRevision,
        observedAt: entity.observedAt ?? entity.timestamp ?? null}})));
    return button;
  }
}
