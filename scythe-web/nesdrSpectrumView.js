/**
 * NESDR instrument surface for the SPECTRUM tab.
 *
 * Replaces the generic JSON record viewer with a receiver console: identity
 * strip, live FFT trace, rolling waterfall, hardware-health rail and the
 * sparse-estimator rail including its null outcomes.
 *
 * Tuning is proposal-only. A click posts to /api/graphops/rf-tune/propose, which
 * records an rf_tune proposal in the MCP safety gate and returns a receipt. This
 * module opens no Rigctl connection and cannot execute a proposal. No gain
 * control is offered: the bridge reports no supported gain values, and a control
 * restricted to "actual supported values" cannot be honored without them.
 */

import {
  HEALTH, deriveClassificationSummary, deriveHardwareHealth, deriveIdentity,
  deriveSparseRail, deriveSpectrumFrame, deriveTuningRegime, formatHz, sensorAnchorNotice,
} from "./nesdrSpectrumModel.js";
import {
  BUNDLE_ANTENNAS, CORROBORATION, FEEDLINES, NO_AUTODETECT, antennaById,
  corroborateAntenna, declareAntenna,
} from "./rfAntennaDeclaration.js";
import { PRESET_BOUNDARY, RECEIVE_PRESETS, presetCentres } from "./rfBandPresets.us.js";
import {
  COVERAGE, coverageRibbon, planSurvey, retainTileProduct, surveyProgress,
} from "./rfSurveyController.js";
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
    tuneUrl = "/api/graphops/rf-tune/propose",
    antennaUrl = "/api/graphops/rf-antenna",
    antennaDeclareUrl = "/api/graphops/rf-antenna/declare",
    refreshMilliseconds = 1000,
    now = () => Date.now() / 1000,
  } = {}) {
    if (!root) throw new TypeError("NesdrSpectrumView requires a root element");
    this.root = root;
    this.document = root.ownerDocument ?? globalThis.document;
    this.fetchImpl = fetchImpl;
    this.apiBase = apiBase;
    this.urls = {status: statusUrl, spectrum: spectrumUrl, sparse: sparseUrl,
                 supports: supportsUrl, antenna: antennaUrl};
    this.tuneUrl = tuneUrl;
    this.antennaUrl = antennaUrl;
    this.antennaDeclareUrl = antennaDeclareUrl;
    this.refreshMilliseconds = Math.max(250, Number(refreshMilliseconds) || 1000);
    this.now = now;
    this.history = new WaterfallHistory({rows: HISTORY_ROWS, bins: 512});
    this.timer = null;
    this.controller = null;
    this.started = false;
    this.cursorHz = null;
    this.lastSequence = null;
    this.state = {status: null, spectrum: null, sparse: null, supports: null, error: null};
    this.stepHz = 25_000;
    this.mode = "RAW";
    this.survey = null;
    this.tileProducts = new Map();
    this.lastReceipt = null;
    // The antenna is never sensed. It stays null until an operator says otherwise.
    this.antenna = null;
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

    this.coverageRibbonEl = el(doc, "div", "nesdr__ribbon");
    this.tuningPanel = el(doc, "div", "nesdr__tuning");
    this.antennaPanel = el(doc, "div", "nesdr__antenna");
    this.surveyPanel = el(doc, "div", "nesdr__survey");
    this.receiptLine = el(doc, "pre", "nesdr__receipt");

    const plot = el(doc, "div", "nesdr__plot");
    plot.append(this.traceCanvas, this.waterfallCanvas, this.coverageRibbonEl,
                this.resolutionLine, this.freshnessLine);
    const columns = el(doc, "div", "nesdr__columns");
    columns.append(plot, this.sparseRail);

    this.root.append(this.headline, this.identityStrip, this.regimeLine, columns,
                     this.tuningPanel, this.antennaPanel, this.surveyPanel,
                     this.receiptLine, this.classificationLine, this.healthRail,
                     this.anchorLine);
    this.#buildTuning();
    this.#buildAntenna();
    this.#buildSurvey();

    this.traceCanvas.addEventListener("click", (event) => this.#selectCursor(event));
    this.#renderAnchor();
  }

  #buildTuning() {
    const doc = this.document;
    this.tuningPanel.replaceChildren();
    this.tuningPanel.append(el(doc, "div", "nesdr__section-title", "FINE TUNE // PROPOSAL ONLY"));

    const row = el(doc, "div", "nesdr__controls");
    this.frequencyInput = el(doc, "input", "nesdr__input");
    this.frequencyInput.type = "number"; this.frequencyInput.step = "0.000001";
    this.frequencyInput.setAttribute("aria-label", "Centre frequency in MHz");
    row.append(el(doc, "span", "nesdr__control-label", "CENTRE MHz"), this.frequencyInput);

    this.modeSelect = el(doc, "select", "nesdr__input");
    this.modeSelect.setAttribute("aria-label", "Demodulation mode");
    for (const mode of ["RAW", "AM", "NFM", "WFM", "USB", "LSB", "CW"]) {
      const option = el(doc, "option", null, mode);
      option.value = mode;
      this.modeSelect.append(option);
    }
    this.modeSelect.addEventListener("change", () => { this.mode = this.modeSelect.value; });
    row.append(el(doc, "span", "nesdr__control-label", "MODE"), this.modeSelect);
    this.tuningPanel.append(row);

    const steps = el(doc, "div", "nesdr__controls");
    steps.append(el(doc, "span", "nesdr__control-label", "STEP"));
    for (const [label, hz] of [["100 Hz", 100], ["1 kHz", 1e3], ["5 kHz", 5e3],
                               ["12.5 kHz", 12.5e3], ["25 kHz", 25e3], ["100 kHz", 100e3],
                               ["1 MHz", 1e6]]) {
      const button = el(doc, "button", "nesdr__step", label);
      button.type = "button";
      button.addEventListener("click", () => { this.stepHz = hz; this.#renderTuningState(); });
      steps.append(button);
    }
    const down = el(doc, "button", "nesdr__step nesdr__step--nudge", "− STEP");
    const up = el(doc, "button", "nesdr__step nesdr__step--nudge", "+ STEP");
    down.type = "button"; up.type = "button";
    down.addEventListener("click", () => this.#nudge(-1));
    up.addEventListener("click", () => this.#nudge(1));
    steps.append(down, up);
    this.tuningPanel.append(steps);

    const actions = el(doc, "div", "nesdr__controls");
    const recentre = el(doc, "button", "nesdr__step", "RECENTRE ON SELECTED PEAK");
    recentre.type = "button";
    recentre.addEventListener("click", () => {
      const target = this.cursorHz ?? this.state.spectrumFrame?.peakFrequencyHz;
      if (!Number.isFinite(target)) {
        this.#showReceipt(null, "NO CURSOR OR PEAK SELECTED; NOTHING WAS PROPOSED");
        return undefined;
      }
      this.frequencyInput.value = (target / 1e6).toFixed(6);
      return this.#proposeTune(target);
    });
    const propose = el(doc, "button", "nesdr__step nesdr__step--primary", "PROPOSE TUNE");
    propose.type = "button";
    propose.addEventListener("click", () => {
      const raw = String(this.frequencyInput.value ?? "").trim();
      const megahertz = raw === "" ? Number.NaN : Number(raw);
      if (!Number.isFinite(megahertz) || megahertz <= 0) {
        this.#showReceipt(null, "CENTRE FREQUENCY IS NOT A POSITIVE NUMBER; NOTHING WAS PROPOSED");
        return undefined;
      }
      return this.#proposeTune(megahertz * 1e6);
    });
    actions.append(recentre, propose);
    this.tuningPanel.append(actions);

    // The bridge publishes no supported gain values, so no gain control exists.
    this.tuningPanel.append(el(doc, "div", "nesdr__control-note",
      "GAIN // UNDECLARED · NO CONTROL OFFERED: THE BRIDGE REPORTS NO SUPPORTED GAIN VALUES"));
    this.stepLine = el(doc, "div", "nesdr__control-note");
    this.tuningPanel.append(this.stepLine);
    this.#renderTuningState();

    const presets = el(doc, "div", "nesdr__controls");
    presets.append(el(doc, "span", "nesdr__control-label", "RECEIVE PRESETS"));
    for (const preset of RECEIVE_PRESETS) {
      const button = el(doc, "button", "nesdr__step", preset.label);
      button.type = "button";
      button.title = preset.note;
      button.addEventListener("click", () => this.#applyPreset(preset));
      presets.append(button);
    }
    this.tuningPanel.append(presets,
      el(doc, "div", "nesdr__control-note", PRESET_BOUNDARY));
  }

  #renderTuningState() {
    if (!this.stepLine) return;
    this.stepLine.textContent = `STEP // ${formatHz(this.stepHz)} · `
      + "EVERY CHANGE IS AN rf_tune PROPOSAL; NONE IS EXECUTED HERE";
  }

  #nudge(direction) {
    const raw = String(this.frequencyInput.value ?? "").trim();
    const current = raw === "" ? Number.NaN : Number(raw) * 1e6;
    const base = Number.isFinite(current) && current > 0
      ? current : this.state.spectrumFrame?.centerFrequencyHz;
    if (!Number.isFinite(base)) {
      this.#showReceipt(null, "NO CENTRE FREQUENCY TO STEP FROM; NOTHING WAS PROPOSED");
      return undefined;
    }
    const target = base + direction * this.stepHz;
    this.frequencyInput.value = (target / 1e6).toFixed(6);
    return this.#proposeTune(target);
  }

  #applyPreset(preset) {
    const sampleRate = Number(this.state.status?.bridge?.config?.sample_rate_hz);
    const centres = presetCentres(preset, sampleRate);
    if (!centres.length) {
      this.#showReceipt(null, `PRESET ${preset.label} YIELDS NO CENTRE FOR THE REPORTED SPAN`);
      return undefined;
    }
    this.mode = preset.mode;
    if (this.modeSelect) this.modeSelect.value = preset.mode;
    // A preset wider than one span becomes a survey plan, not a single claim.
    if (centres.length > 1 && Number.isFinite(preset.startHz)) {
      this.survey = planSurvey({startHz: preset.startHz, endHz: preset.endHz,
                                sampleRateHz: sampleRate});
      this.tileProducts.clear();
      this.#renderSurvey();
    }
    this.frequencyInput.value = (centres[0] / 1e6).toFixed(6);
    return this.#proposeTune(centres[0]);
  }

  async #proposeTune(frequencyHz) {
    const body = {frequency_hz: frequencyHz, justification: "SPECTRUM instrument tab"};
    if (this.mode && this.mode !== "RAW") body.mode = this.mode;
    else if (this.mode === "RAW") body.mode = "RAW";
    try {
      const response = await this.fetchImpl.call(globalThis, `${this.apiBase}${this.tuneUrl}`, {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
      });
      const payload = await response.json();
      if (!response.ok) {
        this.#showReceipt(null, `REFUSED // ${payload.error ?? `HTTP ${response.status}`}`);
        return payload;
      }
      this.lastReceipt = payload;
      this.#showReceipt(payload, null);
      return payload;
    } catch (error) {
      this.#showReceipt(null, `UNREACHABLE // ${error?.message ?? "tune endpoint"}`);
      return null;
    }
  }

  #showReceipt(payload, failure) {
    if (failure) {
      this.receiptLine.textContent = `TUNE PROPOSAL // ${failure}\nEXECUTED // NO · RIGCTL CONTACTED // NO`;
      this.receiptLine.className = "nesdr__receipt nesdr__state--degraded";
      return;
    }
    const receipt = payload?.receipt ?? {};
    this.receiptLine.textContent =
      `TUNE PROPOSAL // ${String(receipt.status ?? "UNKNOWN").toUpperCase()}\n`
      + `PROPOSAL // ${receipt.proposalId ?? "UNRECORDED"}\n`
      + `PARAMS // ${JSON.stringify(payload?.params ?? {})}\n`
      + `REGIME // ${payload?.regime ?? "UNDECLARED"}\n`
      + `RECEIPT // ${String(receipt.requestHash ?? "").slice(0, 16)}\n`
      + `EXECUTED // ${receipt.executed ? "YES" : "NO"} · RIGCTL CONTACTED // `
      + `${payload?.rigctlContacted ? "YES" : "NO"}\n`
      + `${(payload?.boundary ?? []).join("\n")}`;
    this.receiptLine.className = "nesdr__receipt nesdr__state--live";
  }

  /**
   * The antenna panel. It offers a declaration and states, once and plainly,
   * that no receiver measurement can supply one — so the omission is understood
   * as a fact about the hardware rather than a feature nobody built yet.
   */
  #buildAntenna() {
    const doc = this.document;
    this.antennaPanel.replaceChildren();
    this.antennaPanel.append(el(doc, "div", "nesdr__section-title", "ANTENNA DECLARATION"));
    this.antennaPanel.append(el(doc, "div", "nesdr__control-note", NO_AUTODETECT.headline));

    const why = el(doc, "ul", "nesdr__autodetect");
    for (const reason of NO_AUTODETECT.reasons) why.append(el(doc, "li", null, reason));
    this.antennaPanel.append(why);

    const row = el(doc, "div", "nesdr__controls");
    this.antennaSelect = el(doc, "select", "nesdr__select");
    this.antennaSelect.setAttribute("aria-label", "Declared antenna");
    const blank = el(doc, "option", null, "— SELECT THE ATTACHED ANTENNA —");
    blank.value = "";
    this.antennaSelect.append(blank);
    for (const entry of BUNDLE_ANTENNAS) {
      const option = el(doc, "option", null, entry.label);
      option.value = entry.id;
      option.title = entry.vendorDescription;
      this.antennaSelect.append(option);
    }

    this.feedlineSelect = el(doc, "select", "nesdr__select");
    this.feedlineSelect.setAttribute("aria-label", "Declared feedline");
    for (const entry of FEEDLINES) {
      const option = el(doc, "option", null, entry.label);
      option.value = entry.id;
      this.feedlineSelect.append(option);
    }

    this.extensionInput = el(doc, "input", "nesdr__input");
    this.extensionInput.type = "number";
    this.extensionInput.setAttribute("aria-label", "Telescopic extension in millimetres");
    this.extensionInput.setAttribute("placeholder", "EXTENSION mm (TELESCOPIC ONLY)");

    const declare = el(doc, "button", "nesdr__step nesdr__step--primary", "DECLARE ANTENNA");
    declare.addEventListener("click", () => this.#declareAntenna());

    row.append(this.antennaSelect, this.feedlineSelect, this.extensionInput, declare);
    this.antennaPanel.append(row);

    this.antennaStateLine = el(doc, "div", "nesdr__antenna-state");
    this.corroborationLine = el(doc, "div", "nesdr__corroboration");
    this.antennaPanel.append(this.antennaStateLine, this.corroborationLine);
    this.#renderAntenna(this.state?.spectrumFrame ?? null);
  }

  /** Map the server's declaration record onto the client declaration shape. */
  #adoptDeclaration(record) {
    if (!record?.antenna_id) { this.antenna = null; return; }
    const built = declareAntenna({
      antennaId: record.antenna_id,
      feedlineId: record.feedline_id ?? "direct",
      extensionMm: record.extension_mm ?? null,
      note: record.note ?? "",
      declaredAt: record.declared_at ?? this.now(),
    });
    this.antenna = built.valid ? built.declaration : null;
  }

  async #declareAntenna() {
    const antennaId = String(this.antennaSelect.value ?? "").trim();
    if (!antennaId) {
      this.antennaStateLine.textContent =
        "NOTHING WAS DECLARED · SELECT THE ANTENNA THAT IS PHYSICALLY ATTACHED";
      return undefined;
    }
    // Only a telescopic mast has an extension. Sending one for a fixed mast is
    // refused by the contract, so it is not sent at all.
    const entry = antennaById(antennaId);
    const rawExtension = String(this.extensionInput.value ?? "").trim();
    const body = {antenna_id: antennaId, feedline_id: this.feedlineSelect.value || "direct"};
    if (entry?.adjustable && rawExtension !== "") body.extension_mm = Number(rawExtension);

    try {
      const response = await this.fetchImpl.call(
        globalThis, `${this.apiBase}${this.antennaDeclareUrl}`,
        {method: "POST", credentials: "same-origin",
         headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      const payload = await response.json();
      if (!response.ok || payload?.status === "refused") {
        this.antennaStateLine.textContent =
          `DECLARATION REFUSED // ${payload?.error ?? `HTTP ${response.status}`}`;
        return undefined;
      }
      this.#adoptDeclaration(payload?.antenna);
      this.lastAntennaReceipt = payload?.receipt ?? null;
    } catch (error) {
      this.antennaStateLine.textContent =
        `DECLARATION NOT RECORDED // ${error?.message ?? "unreachable"}`;
      return undefined;
    }
    this.#renderAntenna(this.state?.spectrumFrame ?? null);
    this.#renderHealth(this.state?.status ?? {}, this.state?.sparse ?? {}, this.now());
    return this.antenna;
  }

  #renderAntenna(frame) {
    if (!this.antennaStateLine) return;
    if (!this.antenna) {
      this.antennaStateLine.textContent =
        "ANTENNA // UNDECLARED · THE OPERATOR HAS NOT SAID WHAT IS ATTACHED";
      this.antennaStateLine.className = "nesdr__antenna-state nesdr__state--undeclared";
    } else {
      const parts = [`ANTENNA // ${this.antenna.label}`, `FEEDLINE // ${this.antenna.feedlineLabel}`,
                     `AUTHORITY // ${this.antenna.authority}`];
      if (this.antenna.quarterWaveHz !== null) {
        parts.push(`DERIVED QUARTER WAVE // ${formatHz(this.antenna.quarterWaveHz)} (ESTIMATE)`);
      }
      if (this.antenna.resonanceHz !== null) {
        parts.push(`VENDOR CENTRE // ${formatHz(this.antenna.resonanceHz)}`);
      }
      const receipt = this.lastAntennaReceipt;
      if (receipt?.declarationHash) {
        parts.push(`DECLARATION // ${String(receipt.declarationHash).slice(0, 16)}`);
      }
      if (receipt?.signalChainChanged) {
        parts.push("SIGNAL CHAIN CHANGED · EARLIER PRODUCTS ARE NOT DIRECTLY COMPARABLE");
      }
      this.antennaStateLine.textContent = parts.join(" · ");
      this.antennaStateLine.className = "nesdr__antenna-state nesdr__state--live";
    }

    const result = corroborateAntenna(this.antenna, {
      frame, centerHz: frame?.centerFrequencyHz ?? null});
    const lines = [`PORT CORROBORATION // ${result.outcome}`, result.reason, result.discrimination];
    if (result.agreement) lines.splice(1, 0, `AGREEMENT // ${result.agreement}`);
    this.corroborationLine.textContent = lines.join(" · ");
    this.corroborationLine.className = `nesdr__corroboration ${
      result.outcome === CORROBORATION.INSUFFICIENT ? "nesdr__state--undeclared"
        : result.agreement && /CONTRADICTS/.test(result.agreement) ? "nesdr__state--degraded"
        : "nesdr__state--live"}`;
  }

  #buildSurvey() {
    const doc = this.document;
    this.surveyPanel.replaceChildren();
    this.surveyPanel.append(el(doc, "div", "nesdr__section-title", "SURVEY // STITCHED, NOT SIMULTANEOUS"));
    this.surveySummary = el(doc, "pre", "nesdr__survey-summary");
    this.surveyPanel.append(this.surveySummary);
    this.#renderSurvey();
  }

  #renderSurvey() {
    if (!this.surveySummary) return;
    if (!this.survey?.valid) {
      this.surveySummary.textContent =
        "SURVEY // NONE PLANNED\nSELECT A SPAN PRESET TO PLAN A BOUNDED SURVEY\n"
        + "COVERAGE // A STITCHED SURVEY IS NEVER A SIMULTANEOUS WIDEBAND CAPTURE";
      this.coverageRibbonEl.replaceChildren();
      return;
    }
    const frame = this.state.spectrumFrame;
    const ribbon = coverageRibbon(this.survey, {
      liveCenterHz: frame?.available ? frame.centerFrequencyHz : null, now: this.now(),
    });
    const progress = surveyProgress(ribbon);
    this.surveySummary.textContent =
      `SURVEY // ${formatHz(this.survey.startHz)}–${formatHz(this.survey.endHz)}\n`
      + `STEP // ${formatHz(this.survey.stepHz)} · DWELL // ${this.survey.dwellSeconds.toFixed(1)}s\n`
      + `PROGRESS // ${progress.visited}/${progress.total} VISITED\n`
      + `COVERAGE // ${progress.live} LIVE · ${progress.recent} RECENT · `
      + `${progress.stale} STALE · ${progress.never} NEVER OBSERVED\n${this.survey.boundary}`;
    this.#renderRibbon(ribbon);
  }

  #renderRibbon(ribbon) {
    const doc = this.document;
    this.coverageRibbonEl.replaceChildren();
    const CLASS = {
      [COVERAGE.LIVE]: "nesdr__tile--live", [COVERAGE.RECENT]: "nesdr__tile--recent",
      [COVERAGE.STALE]: "nesdr__tile--stale", [COVERAGE.NEVER]: "nesdr__tile--never",
    };
    for (const tile of ribbon) {
      const cell = el(doc, "span", `nesdr__tile ${CLASS[tile.state] ?? ""}`);
      cell.title = `${formatHz(tile.minHz)}–${formatHz(tile.maxHz)} // ${tile.state}`;
      this.coverageRibbonEl.append(cell);
    }
  }

  /** Record a bounded product for whichever planned tile the live span covers. */
  #retainVisitedTile(frame, now) {
    if (!this.survey?.valid || !frame?.available || frame.stale) return;
    const span = this.survey.spanHz;
    const tile = this.survey.tiles.find(
      (candidate) => Math.abs(candidate.centerHz - frame.centerFrequencyHz) < span / 2);
    if (!tile) return;
    tile.observedAt = now;
    const product = retainTileProduct(tile, {
      frame, observedAt: now, config: this.state.status?.bridge?.config ?? {},
      sparseOutcome: this.state.sparse?.latest_outcome ?? null,
      signalChainHash: this.state.sparse?.chain?.signal_chain_hash ?? null,
    });
    if (product) this.tileProducts.set(tile.centerHz, product);
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
      const [status, spectrum, sparse, supports, antenna] = await Promise.all([
        this.#load("status"), this.#load("spectrum"),
        this.#load("sparse").catch(() => ({status: "unavailable"})),
        this.#load("supports").catch(() => ({supports: []})),
        this.#load("antenna").catch(() => null),
      ]);
      this.state = {status, spectrum, sparse, supports, error: null};
      // The server holds the declaration. A failed read leaves the last known
      // declaration in place rather than silently reverting it to UNDECLARED.
      if (antenna) this.#adoptDeclaration(antenna?.current?.antenna);
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
      this.#retainVisitedTile(frame, now);
    }
    this.#renderPlot();
    this.#renderSurvey();
    this.#renderResolution(frame);
    this.#renderFreshness(frame, error);
    this.#renderHealth(status ?? {}, sparse ?? {}, now);
    this.#renderSparse(sparse ?? {}, supports ?? {});
    this.#renderClassification(status ?? {});
    this.#renderAntenna(frame);
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
    for (const row of deriveHardwareHealth(status, {now, sparse, antenna: this.antenna})) {
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
