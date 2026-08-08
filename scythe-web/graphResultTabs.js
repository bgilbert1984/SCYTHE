import {validateViewIntent} from "./viewIntent.js";

const VIEW_LABELS = Object.freeze({provenance: "PROVENANCE", temporal: "TEMPORAL WAKE",
  contradictions: "CONTRADICTIONS"});

function element(document, tag, className, text = null) {
  const node = document.createElement(tag); node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function countIntent(intent) {
  if (intent.view === "provenance") return (intent.payload.path?.nodes?.length ?? 0) +
    (intent.payload.path?.edges?.length ?? 0);
  if (intent.view === "temporal") {
    const delta = intent.payload.delta ?? {};
    return ["addedNodes", "addedEdges", "removedNodes", "removedEdges", "changedNodes", "changedEdges"]
      .reduce((sum, key) => sum + (delta[key]?.length ?? 0), 0);
  }
  return intent.payload.findings?.length ?? 0;
}

function entityKind(entity, fallback = "graph-node") {
  const kind = String(entity?.kind ?? "").toLowerCase();
  if (kind.includes("flow") || kind.includes("edge") || kind.includes("contradict")) return "graph-edge";
  if (kind.includes("event") || kind.includes("burst")) return "event";
  return fallback;
}

export class GraphResultTabs {
  constructor({root, onSelect = () => undefined, fallbackView = "3d"} = {}) {
    if (!root) throw new TypeError("GraphResultTabs requires a root");
    this.root = root; this.document = root.ownerDocument ?? globalThis.document;
    this.window = this.document?.defaultView ?? globalThis; this.onSelect = onSelect;
    this.fallbackView = fallbackView; this.intents = new Map(); this.controls = new Map();
    this.views = new Map(); this.activeView = null;
    this.tabList = root.querySelector(".live-hypergraph__modes");
    this.legend = root.querySelector(".live-hypergraph__legend");
  }

  applyIntent(input) {
    const intent = validateViewIntent(input); this.intents.set(intent.view, intent);
    this.#ensure(intent.view); this.#render(intent); this.onSelect(intent.view); return intent;
  }

  #ensure(view) {
    if (this.controls.has(view)) return;
    const shell = element(this.document, "span", "live-hypergraph__context-tab");
    shell.dataset.resultTab = view; shell.setAttribute("role", "presentation");
    const activate = element(this.document, "button", "live-hypergraph__context-tab-main", VIEW_LABELS[view]);
    activate.type = "button"; activate.setAttribute("role", "tab"); activate.setAttribute("aria-selected", "false");
    activate.addEventListener("click", () => this.onSelect(view));
    const close = element(this.document, "button", "live-hypergraph__context-tab-close", "×");
    close.type = "button"; close.setAttribute("aria-label", `Close ${VIEW_LABELS[view]} result tab`);
    close.addEventListener("click", () => this.close(view)); shell.append(activate, close);
    this.tabList.append(shell); this.controls.set(view, {shell, activate, close});

    const viewport = element(this.document, "section", "live-hypergraph__viewport live-hypergraph__result");
    viewport.dataset.resultView = view; viewport.setAttribute("aria-label", `${VIEW_LABELS[view]} GraphOps result`);
    viewport.setAttribute("role", "tabpanel");
    viewport.hidden = true; this.root.insertBefore(viewport, this.legend); this.views.set(view, viewport);
  }

  setVisible(view) {
    this.activeView = this.views.has(view) ? view : null;
    for (const [name, viewport] of this.views) viewport.hidden = name !== this.activeView;
    for (const [name, control] of this.controls) {
      control.activate.setAttribute("aria-pressed", String(name === this.activeView));
      control.activate.setAttribute("aria-selected", String(name === this.activeView));
    }
  }

  close(view) {
    const wasActive = this.activeView === view;
    this.controls.get(view)?.shell.remove(); this.views.get(view)?.remove();
    this.controls.delete(view); this.views.delete(view); this.intents.delete(view);
    if (wasActive) this.onSelect(this.fallbackView);
  }

  #render(intent) {
    const viewport = this.views.get(intent.view); viewport.replaceChildren();
    const control = this.controls.get(intent.view); const count = countIntent(intent);
    control.activate.textContent = `${VIEW_LABELS[intent.view]} • ${count}`;
    viewport.append(element(this.document, "div", "graph-result__authority",
      `${intent.title} // ${intent.evidencePosture.toUpperCase()} // PLAN ${intent.planId}`));
    if (intent.view === "provenance") this.#renderProvenance(viewport, intent);
    if (intent.view === "temporal") this.#renderTemporal(viewport, intent);
    if (intent.view === "contradictions") this.#renderContradictions(viewport, intent);
    viewport.append(element(this.document, "div", "graph-result__boundary", `BOUNDARY // ${intent.boundary}`));
  }

  #selectionButton(entity, fallbackKind = "graph-node") {
    const button = element(this.document, "button", "graph-result__entity", entity.id ?? "UNKNOWN ENTITY");
    button.type = "button"; button.addEventListener("click", () => {
      this.root.dispatchEvent(new this.window.CustomEvent("scythe-web:graph-selection", {bubbles: true, detail: {
        kind: entityKind(entity, fallbackKind), entityId: entity.id,
        graphRevision: entity.graphRevision ?? this.intents.get(this.activeView)?.graphRevision ?? null,
        observedAt: entity.observedAt ?? entity.timestamp ?? null,
      }}));
    });
    return button;
  }

  #renderProvenance(viewport, intent) {
    const path = intent.payload.path ?? {};
    const lattice = element(this.document, "div", "graph-result__lattice");
    const root = element(this.document, "div", "graph-result__column");
    root.append(element(this.document, "div", "graph-result__column-title", "ROOT"),
      element(this.document, "div", "graph-result__root", path.root ?? "UNDECLARED"));
    const entities = element(this.document, "div", "graph-result__column");
    entities.append(element(this.document, "div", "graph-result__column-title",
      `TRAVERSAL // DEPTH ${path.depth ?? "?"}`));
    for (const node of path.nodes ?? []) entities.append(this.#selectionButton(node));
    for (const edge of path.edges ?? []) entities.append(this.#selectionButton(edge, "graph-edge"));
    if (!(path.nodes?.length || path.edges?.length)) entities.append(element(this.document, "div", "graph-result__empty", "NO TRAVERSED ENTITIES"));
    const sources = element(this.document, "div", "graph-result__column");
    sources.append(element(this.document, "div", "graph-result__column-title", "DECLARED SOURCES"));
    for (const source of path.sources ?? []) sources.append(element(this.document, "div", "graph-result__source",
      `${source.entityId} ← ${typeof source.source === "string" ? source.source : JSON.stringify(source.source)}`));
    if (!path.sources?.length) sources.append(element(this.document, "div", "graph-result__empty", "NO DECLARED SOURCE"));
    lattice.append(root, entities, sources); viewport.append(lattice);
  }

  #renderTemporal(viewport, intent) {
    const delta = intent.payload.delta ?? {};
    const from = Number(delta.from); const to = Number(delta.to);
    viewport.append(element(this.document, "div", "graph-result__time-window",
      `${Number.isFinite(from) ? new Date(from * 1000).toISOString() : "FROM PENDING"}  ⟶  ${Number.isFinite(to) ? new Date(to * 1000).toISOString() : "TO PENDING"}`));
    const metrics = element(this.document, "div", "graph-result__metrics");
    const groups = [["ADDED NODES", delta.addedNodes], ["ADDED EDGES", delta.addedEdges],
      ["REMOVED NODES", delta.removedNodes], ["REMOVED EDGES", delta.removedEdges],
      ["CHANGED NODES", delta.changedNodes], ["CHANGED EDGES", delta.changedEdges]];
    for (const [label, items] of groups) {
      const metric = element(this.document, "div", `graph-result__metric graph-result__metric--${label.startsWith("ADDED") ? "added" : label.startsWith("REMOVED") ? "removed" : "changed"}`);
      metric.append(element(this.document, "strong", "", String(items?.length ?? 0)), element(this.document, "span", "", label));
      metrics.append(metric);
    }
    viewport.append(metrics);
    const changes = element(this.document, "div", "graph-result__changes");
    for (const [label, items] of groups) for (const item of (items ?? []).slice(0, 40)) {
      const entity = item.after ?? item.before ?? item;
      const row = element(this.document, "div", "graph-result__change");
      row.append(element(this.document, "span", "graph-result__change-kind", label), this.#selectionButton(entity,
        label.includes("EDGE") ? "graph-edge" : "graph-node")); changes.append(row);
    }
    if (!changes.children.length) changes.append(element(this.document, "div", "graph-result__empty", "NO STRUCTURAL CHANGE IN RETAINED WINDOW"));
    viewport.append(changes);
  }

  #renderContradictions(viewport, intent) {
    viewport.append(element(this.document, "div", "graph-result__root", `ROOT // ${intent.payload.root ?? "UNKNOWN"}`));
    const field = element(this.document, "div", "graph-result__contradictions");
    for (const finding of intent.payload.findings ?? []) {
      const card = element(this.document, "article", "graph-result__contradiction");
      card.append(element(this.document, "div", "graph-result__fracture", "CAUSAL FRACTURE"),
        this.#selectionButton(finding, "graph-edge"),
        element(this.document, "div", "graph-result__reason", finding.reason ?? "explicit contradiction"),
        element(this.document, "div", "graph-result__members", `CLAIMS // ${(finding.nodes ?? []).join(" ↔ ")}`));
      field.append(card);
    }
    if (!field.children.length) field.append(element(this.document, "div", "graph-result__empty",
      "NO EXPLICIT CONTRADICTION RELATIONS IN BOUNDED TRAVERSAL"));
    viewport.append(field);
  }
}
