const text = (value, fallback = "UNKNOWN", limit = 120) => {
  const result = String(value ?? "").replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return (result || fallback).slice(0, limit);
};

const countBy = (items, value) => {
  const counts = new Map();
  for (const item of items) {
    const key = text(value(item), "UNCLASSIFIED", 40).toUpperCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
};

export function tickerItemsFromGraphUpdate(update) {
  if (!update?.available || !update.graph) return [
    `LIVE GRAPH // ${text(update?.retained ? "DEGRADED · RETAINING LAST SNAPSHOT" : update?.message, "UNAVAILABLE", 140)}`,
  ];
  const graph = update.graph; const nodes = graph.nodes ?? []; const edges = graph.edges ?? [];
  const detectedNodes = Number(graph.detectedNodeCount ?? graph.nodeCount ?? nodes.length) || 0;
  const detectedEdges = Number(graph.detectedEdgeCount ?? graph.edgeCount ?? edges.length) || 0;
  const flows = edges.filter((edge) => edge.kind === "network_flow" || String(edge.id ?? "").startsWith("flow:"));
  const protocols = countBy(flows, (edge) => edge.labels?.app_proto || edge.labels?.proto);
  const directions = countBy(flows, (edge) => edge.labels?.operational_direction);
  const active = nodes.filter((node) => node.liveness?.state === "active").length;
  const inactive = nodes.filter((node) => node.liveness?.state === "inactive").length;
  const tensions = [...nodes, ...edges].reduce((sum, entity) =>
    sum + Math.min(Array.isArray(entity.contradictions) ? entity.contradictions.length : 0, 20), 0);
  const eve = update.eve ?? {}; const detail = update.detail ?? {};
  return [
    `GRAPH // ${nodes.length}/${detectedNodes} NODES · ${edges.length}/${detectedEdges} EDGES · ${text(detail.tier, "BOUNDED")} LENS`,
    `EVE // ${Number(eve.committed) || 0} COMMITTED · ${Number(eve.replayed) || 0} REPLAYED · ${Number(eve.deduplicated) || 0} DEDUPLICATED`,
    `FLOW MIX // ${protocols.slice(0, 3).map(([name, count]) => `${name} ${count}`).join(" · ") || "NO RETAINED FLOWS"}`,
    `DIRECTION // ${directions.slice(0, 3).map(([name, count]) => `${name} ${count}`).join(" · ") || "UNRESOLVED"}`,
    `HOST PING // ${active} ACTIVE · ${inactive} INACTIVE · ${Math.max(0, nodes.length - active - inactive)} UNCLASSIFIED`,
    `EVIDENCE TENSIONS // ${tensions} DECLARED CONTRADICTIONS IN BOUNDED VIEW`,
  ];
}

export function tickerItemsFromRfStatus(payload) {
  const bridge = payload?.bridge ?? {}; const config = bridge.config ?? {};
  if (!Object.keys(bridge).length) return ["RF RECEIVER // STATUS UNAVAILABLE"];
  const frequency = Number(config.center_frequency_hz); const sampleRate = Number(config.sample_rate_hz);
  return [
    `RF RECEIVER // ${text(config.sensor_id, "UNNAMED SENSOR", 80)} · ${text(bridge.bridge_state).toUpperCase()} · IQ ${bridge.iq_connected ? "CONNECTED" : "DISCONNECTED"}`,
    `RF TUNING // ${Number.isFinite(frequency) ? `${(frequency / 1e6).toFixed(3)} MHz` : "UNAVAILABLE"} · ${Number.isFinite(sampleRate) ? `${(sampleRate / 1e6).toFixed(3)} MS/s` : "RATE UNAVAILABLE"} · RAW IQ NOT EXPOSED`,
  ];
}

export class SystemEvidenceTicker {
  constructor({root, fetchImpl = globalThis.fetch, rfRefreshMilliseconds = 15_000} = {}) {
    if (!root) throw new TypeError("system evidence ticker root is required");
    this.root = root; this.document = root.ownerDocument ?? globalThis.document;
    this.track = root.querySelector("[data-system-ticker-track]");
    this.summary = root.querySelector("[data-system-ticker-summary]");
    this.toggle = root.querySelector("[data-system-ticker-toggle]");
    this.fetchImpl = fetchImpl; this.rfRefreshMilliseconds = Math.max(5_000, Number(rfRefreshMilliseconds) || 15_000);
    this.channels = new Map([["boot", ["SYSTEM EVIDENCE // CONNECTING TO BOUNDED SOURCES"]]]);
    this.timer = null; this.started = false;
  }

  start() {
    if (this.started) return this;
    this.started = true;
    this.toggle?.addEventListener("click", this.onToggle = () => {
      const paused = this.root.dataset.paused !== "true";
      this.root.dataset.paused = String(paused); this.toggle.setAttribute("aria-pressed", String(paused));
      this.toggle.textContent = paused ? "▶" : "Ⅱ"; this.toggle.title = paused ? "Resume evidence ticker" : "Pause evidence ticker";
    });
    this.#render(); void this.refreshRf(); this.#scheduleRf(); return this;
  }

  updateGraph(update) { this.channels.delete("boot"); this.channels.set("graph", tickerItemsFromGraphUpdate(update)); this.#render(); }

  updateRf(payload) { this.channels.set("rf", tickerItemsFromRfStatus(payload)); this.#render(); }

  note(key, value) {
    this.channels.set(`note:${text(key, "EVENT", 32)}`, [text(value, "BOUNDED EVENT", 180)]); this.#render();
  }

  async refreshRf() {
    try {
      const response = await this.fetchImpl.call(globalThis, "/api/graphops/rf-bridge/status",
        {credentials:"same-origin",cache:"no-store"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      this.updateRf(await response.json());
    } catch { this.updateRf(null); }
  }

  #scheduleRf() {
    clearTimeout(this.timer);
    if (this.started) this.timer = setTimeout(async () => { await this.refreshRf(); this.#scheduleRf(); },
      this.rfRefreshMilliseconds);
  }

  #render() {
    const items = [...this.channels.values()].flat().filter(Boolean).slice(0, 12);
    if (!items.length || !this.track) return;
    const content = items.join("   ◆   ");
    const group = () => { const span = this.document.createElement("span"); span.className = "notice__group";
      span.textContent = content; return span; };
    this.track.replaceChildren(group(), group());
    if (this.summary) this.summary.textContent = items.join(". ");
  }

  destroy() { this.started = false; clearTimeout(this.timer); this.timer = null;
    if (this.onToggle) this.toggle?.removeEventListener("click", this.onToggle); }
}
