function copy(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }

export function investigationKey(selection) {
  if (!selection?.entityId) throw new TypeError("investigation selection requires an entityId");
  return `${selection.kind ?? "graph-node"}:${selection.entityId}`;
}

export class GraphOpsInvestigationTabs {
  constructor({root, onActivate = () => {}, onBeforeActivate = () => {}, maxTabs = 12} = {}) {
    if (!root) throw new TypeError("GraphOps investigation tab root is required");
    this.root = root; this.document = root.ownerDocument ?? globalThis.document;
    this.onActivate = onActivate; this.onBeforeActivate = onBeforeActivate;
    this.maxTabs = Math.min(Math.max(Number(maxTabs) || 12, 1), 32);
    this.records = new Map(); this.activeKey = null;
  }

  open(selection, {label = null, state = {}} = {}) {
    const key = investigationKey(selection);
    let record = this.records.get(key);
    if (!record) {
      record = {key, selection: copy(selection), label: label || String(selection.entityId),
        state: {question: "", output: "", outputHidden: true, status: "GRAPHOPS DIRECTIVE",
          conversationState: "OLLAMA // READY", traceEvidence: null, ...copy(state)}};
      this.records.set(key, record);
      while (this.records.size > this.maxTabs) {
        const oldest = this.records.keys().next().value;
        if (oldest === key) break;
        this.records.delete(oldest);
      }
    } else {
      record.selection = copy(selection);
      if (label) record.label = label;
    }
    this.activate(key);
    return copy(record);
  }

  activate(key, {notify = true} = {}) {
    if (!this.records.has(key)) return null;
    if (this.activeKey && this.activeKey !== key) this.onBeforeActivate(this.get(this.activeKey));
    this.activeKey = key; this.render();
    const record = this.get(key);
    if (notify) this.onActivate(record);
    return record;
  }

  update(key, patch) {
    const record = this.records.get(key);
    if (!record) return null;
    record.state = {...record.state, ...copy(patch)};
    this.render();
    return this.get(key);
  }

  active() { return this.activeKey ? this.get(this.activeKey) : null; }
  get(key) { const value = this.records.get(key); return value ? copy(value) : null; }

  render() {
    this.root.replaceChildren();
    for (const record of this.records.values()) {
      const button = this.document.createElement("button");
      button.type = "button"; button.role = "tab"; button.dataset.investigationKey = record.key;
      button.setAttribute("aria-selected", String(record.key === this.activeKey));
      button.title = record.selection.entityId;
      const short = String(record.label).replace(/^host:/, "");
      button.textContent = short.length > 28 ? `${short.slice(0, 25)}…` : short;
      button.addEventListener("click", () => this.activate(record.key));
      this.root.append(button);
    }
    this.root.hidden = this.records.size === 0;
  }
}
