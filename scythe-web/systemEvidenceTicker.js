import { deriveClassifierState, deriveOutcomeBreakdown } from "./rfClassificationOutcomes.js";
export const sanitizeTickerText = (value, fallback = "UNKNOWN", limit = 120) => {
  const result = String(value ?? "").replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return (result || fallback).slice(0, limit);
};

const countBy = (items, value) => {
  const counts = new Map();
  for (const item of items) {
    const key = sanitizeTickerText(value(item), "UNCLASSIFIED", 40).toUpperCase();
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
};

export function tickerItemsFromGraphUpdate(update) {
  if (!update?.available || !update.graph) return [
    `LIVE GRAPH // ${sanitizeTickerText(update?.retained ? "DEGRADED · RETAINING LAST SNAPSHOT" : update?.message, "UNAVAILABLE", 140)}`,
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
    `GRAPH // ${nodes.length}/${detectedNodes} NODES · ${edges.length}/${detectedEdges} EDGES · ${sanitizeTickerText(detail.tier, "BOUNDED")} LENS · REV ${sanitizeTickerText(graph.graphRevision, "UNAVAILABLE", 16)}`,
    `EVE // ${Number(eve.committed) || 0} COMMITTED · ${Number(eve.replayed) || 0} REPLAYED · ${Number(eve.deduplicated) || 0} DEDUPLICATED`,
    `FLOW MIX // ${protocols.slice(0, 3).map(([name, count]) => `${name} ${count}`).join(" · ") || "NO RETAINED FLOWS"}`,
    `DIRECTION // ${directions.slice(0, 3).map(([name, count]) => `${name} ${count}`).join(" · ") || "UNRESOLVED"}`,
    `BOUNDED HOST STATE // ${active} ACTIVE · ${inactive} INACTIVE · ${Math.max(0, nodes.length - active - inactive)} UNCLASSIFIED · CURRENT LENS`,
    `EVIDENCE TENSIONS // ${tensions} DECLARED CONTRADICTIONS IN BOUNDED VIEW`,
  ];
}

export function tickerItemsFromRfStatus(payload, {statusObservedAt = null} = {}) {
  const bridge = payload?.bridge ?? {}; const config = bridge.config ?? {};
  if (!Object.keys(bridge).length) return ["RF RECEIVER // STATUS UNAVAILABLE"];
  const frequency = Number(config.center_frequency_hz); const sampleRate = Number(config.sample_rate_hz);
  const products = bridge.products ?? {};
  const fftState = sanitizeTickerText(products.fft_frames?.state, "UNAVAILABLE", 20).toUpperCase();
  const sparseState = sanitizeTickerText(products.sparse_supports?.state, "UNAVAILABLE", 20).toUpperCase();
  const classifications = payload?.observations?.signal_classifications;
  const count = (name) => Math.max(0, Number(classifications?.[name]) || 0);
  // A detection count means nothing until the reader knows whether a classifier
  // ran. Phase 0 ships the contract, not the detector, and says so.
  const classifier = deriveClassifierState(payload);
  const breakdown = deriveOutcomeBreakdown(payload)
    .map((row) => `${row.label} ${row.count}`).join(" · ");
  // UNDECLARED, not IMPLEMENTED, when the server says nothing: a missing field
  // must never read as a working detector.
  const axisState = (axis, key) => sanitizeTickerText(
    payload?.observations?.classifier?.axes?.[axis]?.[key], "UNDECLARED", 20).toUpperCase();
  // Raw IQ is now retained in a bounded process-local ring. A live retention
  // must be visible in the same place the declared absences are, or the panel
  // only ever tells the reader about things the system is not doing.
  const retention = bridge.iq_retention ?? null;
  return [
    `RF RECEIVER // ${sanitizeTickerText(config.sensor_id, "UNNAMED SENSOR", 80)} · ${sanitizeTickerText(bridge.bridge_state).toUpperCase()} · IQ ${bridge.iq_connected ? "CONNECTED" : "DISCONNECTED"}`,
    `RF PRODUCTS // FFT ${fftState} · SPARSE EVENTS ${sparseState} · RAW IQ LOCAL ONLY`,
    classifications ? `RF DETECTIONS // DIGITAL ${count("digital")} · ANALOGUE ${count("analogue")} · UNCLASSIFIED ${count("unclassified")} · RETAINED EVENTS ${count("total")} · DERIVED SUMMARY` : "RF DETECTIONS // COUNTS UNAVAILABLE",
    // Three axes, three separate absences. Collapsing them would put the ticker
    // back to implying one classifier that either works or does not.
    `RF AXES // MODULATION ${axisState("modulation", "detector")} · SYMBOL CLOCK ${classifier.state} · PROTOCOL DECODER ${axisState("protocol", "decoder")}`,
    `RF CLASSIFIER // ${classifier.state} · ANALOGUE DETECTOR ${classifier.analogueDetector}`
      + (breakdown ? ` · UNCLASSIFIED BECAUSE ${breakdown}` : ""),
    `RF TUNING // ${Number.isFinite(frequency) ? `${(frequency / 1e6).toFixed(3)} MHz` : "UNAVAILABLE"} · ${Number.isFinite(sampleRate) ? `${(sampleRate / 1e6).toFixed(3)} MS/s` : "RATE UNAVAILABLE"}`,
    retention
      ? `RF IQ RETENTION // ${sanitizeTickerText(retention.iq_retention, "UNDECLARED", 40).toUpperCase()}`
        + ` · ${retention.iq_retention_active === true ? "ACTIVE" : "INACTIVE"}`
        + (retention.iq_retention_active === true
            // Effective, not configured. At a rate where the fixed allocation
            // holds less than the configured window, printing the request would
            // overstate how much history the ring can be asked about.
            ? ` · ${Math.max(0, Number(retention.effective_retention_ms) || 0)} ms`
              + (retention.capacity_limited === true
                  ? ` EFFECTIVE OF ${Math.max(0, Number(retention.configured_retention_ms) || 0)} ms CONFIGURED · CAPACITY LIMITED`
                  : "")
              + ` · ${Math.max(0, Number(retention.capacity_samples) || 0)} SAMPLES`
              + ` · RING ${sanitizeTickerText(retention.ring?.state, "UNDECLARED", 20).toUpperCase()}`
            : ` · ${sanitizeTickerText(retention.inactive_reason, "UNDECLARED", 40).toUpperCase()}`)
        + ` · RAW IQ ${retention.raw_iq_exposed === true ? "EXPOSED" : "NOT EXPOSED"}`
        // 32, not 20: AVAILABLE_NOT_INTEGRATED is 24 characters and a state
        // truncated to AVAILABLE_NOT_INTEGR reads as a different claim.
        + ` · CHANNELIZER ${sanitizeTickerText(retention.channelizer_state, "UNDECLARED", 32).toUpperCase()}`
      : "RF IQ RETENTION // UNDECLARED · THE BRIDGE PUBLISHED NO RETENTION BLOCK",
    `RF FRESHNESS // STATUS POLLED ${statusObservedAt ? new Date(statusObservedAt).toISOString() : "UNAVAILABLE"} · LAST FFT FRAME ${sanitizeTickerText(bridge.latest_frame_at, "UNAVAILABLE", 40)}`,
  ];
}

export class SystemEvidenceTicker {
  constructor({root, fetchImpl = globalThis.fetch, rfRefreshMilliseconds = 15_000,
               now = () => Date.now()} = {}) {
    if (!root) throw new TypeError("system evidence ticker root is required");
    this.root = root; this.document = root.ownerDocument ?? globalThis.document;
    this.track = root.querySelector("[data-system-ticker-track]");
    this.summary = root.querySelector("[data-system-ticker-summary]");
    this.toggle = root.querySelector("[data-system-ticker-toggle]");
    this.fetchImpl = fetchImpl; this.now = now;
    this.rfRefreshMilliseconds = Math.max(5_000, Number(rfRefreshMilliseconds) || 15_000);
    this.channels = new Map([["boot", ["SYSTEM EVIDENCE // CONNECTING TO BOUNDED SOURCES"]]]);
    this.timer = null; this.started = false; this.rfController = null; this.accessibleSignature = "";
  }

  start() {
    if (this.started) return this;
    this.started = true;
    this.toggle?.addEventListener("click", this.onToggle = () => {
      const paused = this.root.dataset.paused !== "true";
      this.root.dataset.paused = String(paused); this.toggle.setAttribute("aria-pressed", String(paused));
      const action = paused ? "Resume evidence ticker motion" : "Pause evidence ticker motion";
      this.toggle.textContent = paused ? "▶" : "Ⅱ"; this.toggle.title = action;
      this.toggle.setAttribute("aria-label", action);
    });
    this.#render("boot"); void this.refreshRf(); this.#scheduleRf(); return this;
  }

  updateGraph(update) {
    this.channels.delete("boot"); this.channels.set("graph", tickerItemsFromGraphUpdate(update));
    const graph = update?.graph; const tensions = [...(graph?.nodes ?? []), ...(graph?.edges ?? [])]
      .reduce((sum, entity) => sum + (Array.isArray(entity.contradictions) ? entity.contradictions.length : 0), 0);
    this.#render(`graph:${Boolean(update?.available)}:${Boolean(update?.retained)}:${update?.detail?.tier ?? ""}:${tensions}`);
  }

  updateRf(payload, observedAt = this.now()) {
    this.channels.set("rf", tickerItemsFromRfStatus(payload, {statusObservedAt: observedAt}));
    const bridge = payload?.bridge ?? {};
    const products = bridge.products ?? {};
    const classifications = payload?.observations?.signal_classifications ?? {};
    this.#render(`rf:${bridge.bridge_state ?? "unavailable"}:${Boolean(bridge.iq_connected)}:`
      + `${products.fft_frames?.state ?? "unavailable"}:${products.sparse_supports?.state ?? "unavailable"}:`
      + `${classifications.digital ?? "?"}:${classifications.analogue ?? "?"}:${classifications.unclassified ?? "?"}`);
  }

  note(key, value) {
    const noteKey = sanitizeTickerText(key, "EVENT", 32); const observedAt = new Date(this.now()).toISOString();
    this.channels.set(`note:${noteKey}`, [`CURRENT FOCUS // ${sanitizeTickerText(value, "BOUNDED EVENT", 180)} · SELECTED ${observedAt}`]);
    this.#render(`note:${noteKey}:${observedAt}`);
  }

  async refreshRf() {
    const controller = new AbortController(); this.rfController?.abort(); this.rfController = controller;
    try {
      const response = await this.fetchImpl.call(globalThis, "/api/graphops/rf-bridge/status",
        {credentials:"same-origin",cache:"no-store",signal:controller.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (this.started && !controller.signal.aborted) this.updateRf(payload, this.now());
    } catch {
      if (this.started && !controller.signal.aborted) this.updateRf(null, this.now());
    } finally { if (this.rfController === controller) this.rfController = null; }
  }

  #scheduleRf() {
    clearTimeout(this.timer);
    if (this.started) this.timer = setTimeout(async () => { await this.refreshRf(); this.#scheduleRf(); },
      this.rfRefreshMilliseconds);
  }

  #render(announcementSignature = "") {
    const items = [...this.channels.values()].flat().filter(Boolean).slice(0, 12);
    if (!items.length || !this.track) return;
    const content = items.join("   ◆   ");
    const group = () => { const span = this.document.createElement("span"); span.className = "notice__group";
      span.textContent = content; return span; };
    this.track.replaceChildren(group(), group());
    if (this.summary && announcementSignature && announcementSignature !== this.accessibleSignature) {
      this.accessibleSignature = announcementSignature; this.summary.textContent = items.join(". ");
    }
  }

  destroy() { this.started = false; clearTimeout(this.timer); this.timer = null;
    this.rfController?.abort(); this.rfController = null;
    if (this.onToggle) this.toggle?.removeEventListener("click", this.onToggle); }
}
