/**
 * NESDR instrument surface for the SPECTRUM tab.
 *
 * Replaces the generic JSON record viewer with a receiver console: identity
 * strip, live FFT trace, rolling waterfall, hardware-health rail and the
 * sparse-estimator rail including its null outcomes.
 *
 * This pass is read-only. Tuning, presets and survey are deliberately absent —
 * tuning must travel through the guarded rf_tune proposal contract, never a
 * Rigctl connection opened from browser code.
 */

import {
  HEALTH, deriveClassificationSummary, deriveHardwareHealth, deriveIdentity,
  deriveSparseRail, deriveSpectrumFrame, deriveTuningRegime, formatHz, sensorAnchorNotice,
} from "./nesdrSpectrumModel.js";
import {
  DBFS_CEILING, DBFS_FLOOR, HISTORY_ROWS, PALETTE, WaterfallHistory, drawTrace, drawWaterfall,
} from "./spectrumCanvasRenderer.js";

const el = (doc, tag, className, text) => {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

const LEVEL_CLASS = Object.freeze({
  [HEALTH.LIVE]: "nesdr__state--live",
  [HEALTH.DEGRADED]: "nesdr__state--degraded",
  [HEALTH.FAILED]: "nesdr__state--failed",
  [HEALTH.UNDECLARED]: "nesdr__state--undeclared",
});

export class NesdrSpectrumView {
  constructor({
    root, fetchImpl = globalThis.fetch, apiBase = "",
    statusUrl = "/api/graphops/rf-bridge/status",
    spectrumUrl = "/api/graphops/rf-spectrum/latest?include_bins=1",
    sparseUrl = "/api/graphops/rf-sparse/status",
    supportsUrl = "/api/graphops/rf-sparse/supports?limit=12",
    refreshMilliseconds = 1000,
    now = () => Date.now() / 1000,
  } = {}) {
    if (!root) throw new TypeError("NesdrSpectrumView requires a root element");
    this.root = root;
    this.document = root.ownerDocument ?? globalThis.document;
    this.fetchImpl = fetchImpl;
    this.apiBase = apiBase;
    this.urls = {status: statusUrl, spectrum: spectrumUrl, sparse: sparseUrl, supports: supportsUrl};
    this.refreshMilliseconds = Math.max(250, Number(refreshMilliseconds) || 1000);
    this.now = now;
    this.history = new WaterfallHistory({rows: HISTORY_ROWS, bins: 512});
    this.timer = null;
    this.controller = null;
    this.started = false;
    this.cursorHz = null;
    this.lastSequence = null;
    this.state = {status: null, spectrum: null, sparse: null, supports: null, error: null};
    this.#buildSkeleton();
  }

  #buildSkeleton() {
    const doc = this.document;
    this.root.replaceChildren();
    this.root.classList.add("nesdr");

    this.identityStrip = el(doc, "div", "nesdr__identity");
    this.headline = el(doc, "div", "nesdr__headline", "NESDR SMArt v5 // AWAITING BRIDGE STATUS");
    this.regimeLine = el(doc, "div", "nesdr__regime");

    this.traceCanvas = el(doc, "canvas", "nesdr__canvas nesdr__canvas--trace");
    this.traceCanvas.width = 512; this.traceCanvas.height = 150;
    this.waterfallCanvas = el(doc, "canvas", "nesdr__canvas nesdr__canvas--waterfall");
    this.waterfallCanvas.width = 512; this.waterfallCanvas.height = HISTORY_ROWS;

    this.resolutionLine = el(doc, "div", "nesdr__resolution");
    this.freshnessLine = el(doc, "div", "nesdr__freshness");
    this.healthRail = el(doc, "div", "nesdr__health");
    this.sparseRail = el(doc, "div", "nesdr__sparse");
    this.classificationLine = el(doc, "div", "nesdr__classification");
    this.anchorLine = el(doc, "div", "nesdr__anchor");

    const plot = el(doc, "div", "nesdr__plot");
    plot.append(this.traceCanvas, this.waterfallCanvas, this.resolutionLine, this.freshnessLine);
    const columns = el(doc, "div", "nesdr__columns");
    columns.append(plot, this.sparseRail);

    this.root.append(this.headline, this.identityStrip, this.regimeLine, columns,
                     this.classificationLine, this.healthRail, this.anchorLine);

    this.traceCanvas.addEventListener("click", (event) => this.#selectCursor(event));
    this.#renderAnchor();
  }

  #selectCursor(event) {
    const frame = this.state.spectrumFrame;
    if (!frame?.available) return;
    const rect = typeof this.traceCanvas.getBoundingClientRect === "function"
      ? this.traceCanvas.getBoundingClientRect() : {left: 0, width: this.traceCanvas.width};
    const width = rect.width || this.traceCanvas.width;
    const ratio = width ? (event.clientX - rect.left) / width : 0;
    const span = frame.maxFrequencyHz - frame.minFrequencyHz;
    if (!Number.isFinite(span) || span <= 0) return;
    this.cursorHz = frame.minFrequencyHz + Math.min(1, Math.max(0, ratio)) * span;
    this.#renderPlot();
  }

  async #load(key) {
    const response = await this.fetchImpl.call(globalThis, `${this.apiBase}${this.urls[key]}`,
      {credentials: "same-origin", signal: this.controller?.signal});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async refresh() {
    this.controller?.abort();
    this.controller = new AbortController();
    try {
      const [status, spectrum, sparse, supports] = await Promise.all([
        this.#load("status"), this.#load("spectrum"),
        this.#load("sparse").catch(() => ({status: "unavailable"})),
        this.#load("supports").catch(() => ({supports: []})),
      ]);
      this.state = {status, spectrum, sparse, supports, error: null};
    } catch (error) {
      if (error?.name === "AbortError") return;
      this.state = {...this.state, error: error?.message ?? "unreachable"};
    }
    this.render();
  }

  render() {
    const {status, spectrum, sparse, supports, error} = this.state;
    const config = status?.bridge?.config ?? {};
    const now = this.now();
    const frame = deriveSpectrumFrame(spectrum ?? {}, {config, now});
    const fftStale = String(status?.bridge?.products?.fft_frames?.state ?? "").toLowerCase() !== "live";
    frame.stale = fftStale;
    this.state.spectrumFrame = frame;

    const sensorId = config.sensor_id ?? "UNDECLARED";
    const liveness = error ? "STATUS UNREACHABLE" : fftStale ? "STALE" : "LIVE";
    this.headline.textContent = `NOOELEC NESDR SMArt v5 // ${sensorId} // ${liveness}`;
    this.headline.className = `nesdr__headline ${error || fftStale
      ? "nesdr__state--degraded" : "nesdr__state--live"}`;

    this.#renderIdentity(status ?? {});
    const regime = deriveTuningRegime(status ?? {});
    this.regimeLine.textContent = `TUNING REGIME // ${regime.regime} · ${regime.note}`;

    // Only advance the waterfall on a genuinely new frame, so a stale product
    // does not paint repeated rows that look like continuing acquisition.
    if (frame.available && frame.sequence !== this.lastSequence) {
      this.history.push(frame.bins);
      this.lastSequence = frame.sequence;
    }
    this.#renderPlot();
    this.#renderResolution(frame);
    this.#renderFreshness(frame, error);
    this.#renderHealth(status ?? {}, sparse ?? {}, now);
    this.#renderSparse(sparse ?? {}, supports ?? {});
    this.#renderClassification(status ?? {});
  }

  #renderIdentity(status) {
    const doc = this.document;
    this.identityStrip.replaceChildren();
    for (const row of deriveIdentity(status)) {
      const cell = el(doc, "div", "nesdr__identity-cell");
      cell.append(el(doc, "span", "nesdr__identity-label", row.label),
                  el(doc, "span", "nesdr__identity-value", row.value),
                  el(doc, "span", "nesdr__identity-authority", row.authority));
      this.identityStrip.append(cell);
    }
  }

  #renderPlot() {
    const frame = this.state.spectrumFrame;
    const traceContext = this.traceCanvas.getContext?.("2d");
    if (traceContext && frame) {
      drawTrace(traceContext, frame, {
        width: this.traceCanvas.width, height: this.traceCanvas.height, cursorHz: this.cursorHz,
      });
    }
    const waterfallContext = this.waterfallCanvas.getContext?.("2d");
    if (waterfallContext) {
      drawWaterfall(waterfallContext, this.history,
        {width: this.waterfallCanvas.width, height: this.waterfallCanvas.height});
    }
  }

  #renderResolution(frame) {
    const widths = frame.widths;
    const native = widths.nativeBinWidthHz === null ? "UNDECLARED"
      : `${widths.fftSize} BINS · ${formatHz(widths.nativeBinWidthHz)}`;
    const display = widths.analysisBinWidthHz === null ? "UNDECLARED"
      : `${widths.publishedBins} BINS · ${formatHz(widths.analysisBinWidthHz)}`;
    const cursor = this.cursorHz === null ? "NONE" : formatHz(this.cursorHz);
    this.resolutionLine.textContent =
      `NATIVE FFT // ${native}\nDISPLAY PRODUCT // ${display} · ${widths.reduction}\n`
      + `SCALE // FIXED ${DBFS_FLOOR} to ${DBFS_CEILING} dBFS · NOT PER-FRAME NORMALIZED\n`
      + `CURSOR // ${cursor}${frame.truncated ? "\nBINS TRUNCATED // AXIS NO LONGER SPANS SAMPLE RATE" : ""}`;
  }

  #renderFreshness(frame, error) {
    if (error) {
      this.freshnessLine.textContent = `FFT // UNREACHABLE · ${error}`;
      this.freshnessLine.className = "nesdr__freshness nesdr__state--failed";
      return;
    }
    if (!frame.available) {
      this.freshnessLine.textContent = `FFT // ${frame.reason ?? "NO BOUNDED FRAME"} · NO TRACE DRAWN`;
      this.freshnessLine.className = "nesdr__freshness nesdr__state--failed";
      return;
    }
    const age = frame.ageSeconds === null ? "UNDECLARED" : `${frame.ageSeconds.toFixed(2)}s AGO`;
    this.freshnessLine.textContent =
      `FFT // SEQ ${frame.sequence ?? "?"} · ${age} · PEAK ${frame.peakDbfs ?? "?"} dBFS `
      + `@ ${formatHz(frame.peakFrequencyHz)} · FLOOR ${frame.noiseFloorDbfs ?? "?"} dBFS`;
    this.freshnessLine.className = `nesdr__freshness ${frame.stale
      ? "nesdr__state--degraded" : "nesdr__state--live"}`;
  }

  #renderHealth(status, sparse, now) {
    const doc = this.document;
    this.healthRail.replaceChildren();
    for (const row of deriveHardwareHealth(status, {now, sparse})) {
      const cell = el(doc, "div", `nesdr__health-row ${LEVEL_CLASS[row.level] ?? ""}`);
      cell.append(el(doc, "span", "nesdr__health-label", row.label),
                  el(doc, "span", "nesdr__health-value", row.value));
      if (row.detail) cell.append(el(doc, "span", "nesdr__health-detail", row.detail));
      this.healthRail.append(cell);
    }
  }

  #renderSparse(sparseStatus, supportsPayload) {
    const doc = this.document;
    const rail = deriveSparseRail(sparseStatus, supportsPayload);
    this.sparseRail.replaceChildren();
    const header = el(doc, "div", `nesdr__sparse-header ${LEVEL_CLASS[rail.level] ?? ""}`);
    header.textContent = `SPARSE SUPPORTS // ${rail.state}`;
    this.sparseRail.append(header);

    // A null outcome is always rendered. A blank rail could mean "nothing
    // supported", "analyzer failed" or "data never arrived" — different realities.
    const outcome = el(doc, "pre", "nesdr__sparse-outcome");
    outcome.textContent = `CURRENT WINDOW // ${rail.outcome ?? "NONE"}\n`
      + `WINDOWS COMPLETED // ${rail.windowCount ?? 0}\n`
      + `SUPPORTS // ${rail.cards.length}\n${rail.note}`;
    this.sparseRail.append(outcome);

    for (const card of rail.cards) {
      const node = el(doc, "div", "nesdr__sparse-card");
      node.append(el(doc, "div", "nesdr__sparse-frequency", formatHz(card.carrierHz)));
      node.append(el(doc, "div", "nesdr__sparse-family", `${card.model} // ${card.atomFamily}`));
      const rows = [
        ["SNR", card.snrDb === null ? "UNDECLARED" : `${card.snrDb.toFixed(1)} dB`],
        ["PERSISTENCE", card.persistence === null ? "UNDECLARED" : card.persistence.toFixed(2)],
        ["RESIDUAL DROP", card.residualReduction === null ? "UNDECLARED" : card.residualReduction.toFixed(2)],
        ["UNCERTAINTY", card.uncertaintyHz === null ? "UNDECLARED" : `±${formatHz(card.uncertaintyHz)}`],
        ["AUTHORITY", card.authority],
      ];
      for (const [label, value] of rows) {
        const line = el(doc, "div", "nesdr__sparse-row");
        line.append(el(doc, "span", "nesdr__sparse-label", label),
                    el(doc, "span", "nesdr__sparse-value", value));
        node.append(line);
      }
      this.sparseRail.append(node);
    }
  }

  #renderClassification(status) {
    const summary = deriveClassificationSummary(status);
    this.classificationLine.textContent = summary.available
      ? `RF DETECTIONS // DIGITAL ${summary.digital} · ANALOGUE ${summary.analogue} · `
        + `UNCLASSIFIED ${summary.unclassified} · RETAINED EVENTS ${summary.total}\n`
        + `${summary.note} · A FAMILY INFERENCE DOES NOT REPLACE AN ESTIMATOR OUTCOME`
      : `RF DETECTIONS // ${summary.note}`;
  }

  #renderAnchor() {
    const anchor = sensorAnchorNotice();
    this.anchorLine.textContent = `SENSOR LOCATION // ${anchor.sensorLocation}\n`
      + `EMITTER LOCATION // ${anchor.emitterLocation}\n${anchor.reason}`;
  }

  start() {
    if (this.started) return this;
    this.started = true;
    const tick = () => {
      this.refresh().finally(() => {
        if (this.started) this.timer = setTimeout(tick, this.refreshMilliseconds);
      });
    };
    tick();
    return this;
  }

  stop() {
    this.started = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.controller?.abort();
    this.controller = null;
    return this;
  }
}

export {PALETTE};
