const PANELS = Object.freeze(["autopilot", "semantic", "spectrum", "events"]);
const LABELS = Object.freeze({autopilot: "AUTOPILOT", semantic: "SEMANTIC MEMORY",
  spectrum: "SPECTRUM / FIELD", events: "EVENT STREAM"});

function el(document, tag, className, text = "") {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
}

export function normalizeWorkbenchSelection(selection) {
  if (!selection?.entityId) return null;
  return {kind: String(selection.kind ?? "graph-node").slice(0, 32),
    entityId: String(selection.entityId).slice(0, 256),
    graphRevision: String(selection.graphRevision ?? "").slice(0, 128),
    ...(selection.observedAt ? {observedAt: selection.observedAt} : {})};
}

export function collectWorkbenchEntityIds(value, output = new Set(), depth = 0) {
  if (depth > 6 || output.size >= 24) return output;
  if (typeof value === "string") {
    if (/^(host:|event:|flow:|edge:|hyperedge:|rf:)/i.test(value)) output.add(value.slice(0, 256));
    return output;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectWorkbenchEntityIds(item, output, depth + 1);
    return output;
  }
  if (!value || typeof value !== "object") return output;
  for (const [key, item] of Object.entries(value)) {
    if (["id", "entity_id", "entityId", "node_id", "nodeId"].includes(key) && typeof item === "string" &&
      /^(host:|event:|flow:|edge:|hyperedge:|rf:)/i.test(item))
      output.add(item.slice(0, 256));
    collectWorkbenchEntityIds(item, output, depth + 1);
  }
  return output;
}

export function workbenchEntityKind(entityId) {
  const value = String(entityId ?? "").toLowerCase();
  if (value.startsWith("event:")) return "event";
  if (value.startsWith("flow:") || value.startsWith("edge:") || value.startsWith("hyperedge:")) return "graph-edge";
  return "graph-node";
}

export function formatWorkbenchStatus(snapshot) {
  const label = LABELS[snapshot?.panel] ?? "CONTEXTUAL WORKBENCH";
  const ok = (snapshot?.records ?? []).filter((record) => record.status === "ok").length;
  const total = snapshot?.records?.length ?? 0;
  const selection = snapshot?.selection?.entityId || "ENVIRONMENT";
  const revision = snapshot?.selection?.graphRevision || "LIVE";
  return `${label} // ${String(snapshot?.status ?? "WAITING").toUpperCase()} // ${ok}/${total} READ TOOLS\n` +
    `SCOPE // ${selection} // REVISION ${revision}`;
}

export function formatWorkbenchInvestigation(snapshot) {
  const status = formatWorkbenchStatus(snapshot);
  const records = (snapshot?.records ?? []).map((record) =>
    `\nMCP TOOL // ${record.tool} // ${record.status.toUpperCase()}\nAUTHORITY // ${record.authority}\n` +
    (record.status === "ok" ? JSON.stringify(record.result, null, 2) : `REASON // ${record.error}`)).join("\n");
  const proposals = (snapshot?.proposals ?? []).map((item) =>
    `PROPOSAL ONLY // ${item.tool} // ${item.boundary}`).join("\n");
  return `GRAPHOPS CONTEXTUAL WORKBENCH\n${status}${records}\n\n${proposals}\n` +
    `BOUNDARY // ${snapshot?.boundary ?? "READ-ONLY BOUNDED OBSERVATION"}`;
}

export class ContextualWorkbench {
  constructor({roots, fetchImpl = globalThis.fetch, apiBase = "", refreshMilliseconds = 5000} = {}) {
    if (!roots || PANELS.some((panel) => !roots[panel])) throw new TypeError("all contextual workbench roots are required");
    this.roots = roots; this.fetchImpl = fetchImpl; this.apiBase = apiBase;
    this.refreshMilliseconds = Math.max(2000, Number(refreshMilliseconds) || 5000);
    this.document = roots.autopilot.ownerDocument ?? globalThis.document;
    this.window = this.document.defaultView ?? globalThis; this.selection = null;
    this.snapshots = new Map(); this.visiblePanel = null; this.sequence = 0; this.timer = null;
    for (const panel of PANELS) this.#renderWaiting(panel);
  }

  setSelection(selection) {
    const next = normalizeWorkbenchSelection(selection);
    if (JSON.stringify(next) === JSON.stringify(this.selection)) return false;
    this.selection = next;
    if (this.visiblePanel) void this.refresh(this.visiblePanel);
    return true;
  }

  setVisible(panel) {
    this.visiblePanel = PANELS.includes(panel) ? panel : null;
    clearTimeout(this.timer); this.timer = null;
    if (!this.visiblePanel) return;
    const snapshot = this.snapshots.get(panel);
    if (snapshot) this.#render(panel, snapshot); else this.#renderWaiting(panel);
    void this.refresh(panel);
  }

  async refresh(panel = this.visiblePanel) {
    if (!PANELS.includes(panel)) return null;
    const sequence = ++this.sequence; const root = this.roots[panel];
    root.dataset.loading = "true";
    try {
      const response = await this.fetchImpl.call(globalThis, `${this.apiBase}/api/graphops/workbench`, {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({panel, ...(this.selection ? {selection: this.selection} : {})}),
      });
      const snapshot = await response.json();
      if (!response.ok) throw new Error(snapshot.error ?? `HTTP ${response.status}`);
      if (sequence !== this.sequence && panel === this.visiblePanel) return snapshot;
      this.snapshots.set(panel, snapshot); this.#render(panel, snapshot); return snapshot;
    } catch (error) {
      this.#renderError(panel, error); return null;
    } finally {
      root.dataset.loading = "false";
      if (panel === this.visiblePanel && panel !== "semantic") {
        clearTimeout(this.timer);
        this.timer = setTimeout(() => void this.refresh(panel), this.refreshMilliseconds);
      }
    }
  }

  #renderWaiting(panel) {
    const root = this.roots[panel]; root.replaceChildren(el(this.document, "pre", "workbench__status",
      `${LABELS[panel]} // WAITING FOR BOUNDED MCP EVIDENCE`));
  }

  #renderError(panel, error) {
    const retained = this.snapshots.get(panel);
    if (retained) this.#render(panel, retained, error); else {
      const root = this.roots[panel]; root.replaceChildren(el(this.document, "pre", "workbench__status workbench__status--error",
        `${LABELS[panel]} // UNAVAILABLE\nREASON // ${error.message}`));
    }
  }

  #render(panel, snapshot, error = null) {
    const root = this.roots[panel]; root.replaceChildren();
    const toolbar = el(this.document, "div", "workbench__toolbar");
    const status = el(this.document, "pre", "workbench__status", formatWorkbenchStatus(snapshot) +
      (error ? `\nREFRESH // UNAVAILABLE // RETAINED RESULT // ${error.message}` : ""));
    const refresh = el(this.document, "button", "workbench__action", "REFRESH"); refresh.type = "button";
    refresh.addEventListener("click", () => void this.refresh(panel));
    const investigate = el(this.document, "button", "workbench__action", "OPEN IN GRAPHOPS");
    investigate.type = "button"; investigate.disabled = !snapshot.selection?.entityId;
    investigate.addEventListener("click", () => root.dispatchEvent(new this.window.CustomEvent(
      "scythe-web:workbench-investigate", {bubbles: true, detail: {panel, snapshot,
        selection: snapshot.selection, output: formatWorkbenchInvestigation(snapshot)}})));
    toolbar.append(status, refresh, investigate); root.append(toolbar);
    const grid = el(this.document, "div", "workbench__grid");
    for (const record of snapshot.records ?? []) {
      const card = el(this.document, "article", `workbench__card workbench__card--${record.status}`);
      card.append(el(this.document, "div", "workbench__card-title",
        `MCP // ${record.tool} // ${record.status.toUpperCase()}`),
      el(this.document, "div", "workbench__authority", `AUTHORITY // ${record.authority}`));
      if (record.status === "ok") card.append(el(this.document, "pre", "workbench__payload",
        JSON.stringify(record.result, null, 2)));
      else card.append(el(this.document, "div", "workbench__error", record.error ?? "UNAVAILABLE"));
      const entities = [...collectWorkbenchEntityIds(record.result)];
      if (entities.length) {
        const chips = el(this.document, "div", "workbench__entities");
        for (const entityId of entities) {
          const button = el(this.document, "button", "workbench__entity", entityId); button.type = "button";
          button.addEventListener("click", () => root.dispatchEvent(new this.window.CustomEvent(
            "scythe-web:graph-selection", {bubbles: true, detail: {kind: workbenchEntityKind(entityId), entityId,
              graphRevision: snapshot.selection?.graphRevision ?? ""}})));
          chips.append(button);
        }
        card.append(chips);
      }
      grid.append(card);
    }
    if (!grid.children.length) grid.append(el(this.document, "div", "workbench__empty", "NO MCP RESULTS RETURNED"));
    root.append(grid);
    const proposals = el(this.document, "section", "workbench__proposals");
    proposals.append(el(this.document, "div", "workbench__proposal-title", "GUARDED CAPABILITIES // NOT EXECUTED"));
    for (const proposal of snapshot.proposals ?? []) proposals.append(el(this.document, "div", "workbench__proposal",
      `${proposal.tool} // ${proposal.boundary}`));
    root.append(proposals, el(this.document, "div", "workbench__boundary", `BOUNDARY // ${snapshot.boundary}`));
  }

  destroy() { clearTimeout(this.timer); this.timer = null; this.sequence += 1; }
}
