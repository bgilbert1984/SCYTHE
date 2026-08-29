import {
  cesiumAreaMaterial,
  cesiumPolylineMaterial,
  evidenceStyle,
} from "./evidenceStyles.js";
import { getOperatorGeodetic } from "./geoFrames.js";

const STYLE_ELEMENT_ID = "scythe-web-monocle-styles";

function finiteOr(value, fallback = 0) {
  return Number.isFinite(value) ? value : fallback;
}

export function formatSampleForHud(sample) {
  if (!sample?.available) {
    return Object.freeze({
      state: sample?.status ?? "NO_SAMPLE",
      value: "NO VALIDATED SOLVER DATA",
      detail: sample?.reason ?? "Waiting for a contract-backed RF tile",
      evidenceClass: sample?.evidenceClass ?? null,
    });
  }
  return Object.freeze({
    state: sample.status,
    value: `${Number(sample.value).toPrecision(6)} ${sample.units}`,
    detail: `${sample.quantity} // ${sample.datasetId}`,
    evidenceClass: sample.evidenceClass,
    uncertainty: sample.uncertainty?.value == null
      ? sample.uncertainty?.kind ?? "NOT QUANTIFIED"
      : `± ${Number(sample.uncertainty.value).toPrecision(4)} ${sample.uncertainty.units}`,
    provenance: `${sample.provenance.solverName} ${sample.provenance.solverVersion}`,
  });
}

export function formatOpticalSampleForHud(sample) {
  if (!sample?.available) return sample?.reason ?? "NO VALIDATED OPTICAL DATA";
  const plane = sample.depthPlaneIndex == null ? "DOMAIN" : `PLANE ${sample.depthPlaneIndex}`;
  const value = sample.components
    ? `PHASE ${Number(sample.phaseRadians).toPrecision(5)} rad // IREL ${Number(sample.relativeIntensity).toPrecision(5)}`
    : `${Number(sample.value).toPrecision(6)} ${sample.units}`;
  return `${plane} // ${value} // ${sample.evidenceClass}`;
}

export function operatorGeodetic(viewer, Cesium) {
  return getOperatorGeodetic(viewer, Cesium);
}

function readEntityProperty(entity, name, time) {
  const property = entity?.properties?.[name];
  return property?.getValue?.(time) ?? property ?? null;
}

export function coverageCellDetail(entity, time) {
  if (!entity?.id?.startsWith?.("scythe-web:coverage:")) return null;
  const read = (name) => readEntityProperty(entity, name, time);
  return {
    evidence_class: read("evidenceClass"), visualization_is_authoritative: read("visualizationIsAuthoritative"),
    dataset_id: read("datasetId"), tile_id: read("tileId"), display_asset_hash: read("displayAssetHash"),
    longitude_degrees: Number(read("longitudeDegrees")), latitude_degrees: Number(read("latitudeDegrees")),
    height_meters: Number(read("heightMeters")) || 0, frequency_hz: Number(read("frequencyHz")),
    quantity: read("quantity"), value: Number(read("value")), display_value: Number(read("displayValue")),
    display_delta: Number(read("displayDelta")), units: read("units"), coverage: read("coverage"),
    coverage_threshold: read("coverageThreshold"), coverage_comparison: read("coverageComparison"),
    coverage_threshold_units: read("coverageThresholdUnits"), transmitter_id: read("transmitterId"),
    uncertainty: {kind: read("uncertaintyKind"), value: read("uncertaintyValue"), units: read("uncertaintyUnits")},
    provenance: {solverName: read("solverName"), solverVersion: read("solverVersion"),
      sourceRevision: read("sourceRevision"), runId: read("runId")},
  };
}

function frequencyLabel(value) {
  const frequency = Number(value);
  if (!Number.isFinite(frequency)) return "RF";
  if (frequency >= 1e9) return `${Number((frequency / 1e9).toFixed(3))} GHz`;
  if (frequency >= 1e6) return `${Number((frequency / 1e6).toFixed(3))} MHz`;
  return `${Number((frequency / 1e3).toFixed(3))} kHz`;
}

function injectStyles(documentRoot) {
  if (!documentRoot?.head || documentRoot.getElementById(STYLE_ELEMENT_ID)) return;
  const element = documentRoot.createElement("style");
  element.id = STYLE_ELEMENT_ID;
  element.textContent = `
    .scythe-web-monocle {
      position: absolute; left: 50%; bottom: 28px; transform: translateX(-50%);
      min-width: 390px; max-width: min(620px, calc(100vw - 24px));
      padding: 8px 12px; z-index: 45; pointer-events: auto;
      color: #ccefff; background: rgba(0, 12, 28, .84);
      border: 1px solid rgba(0, 212, 255, .42); border-radius: 4px;
      box-shadow: 0 0 24px rgba(0, 150, 255, .14);
      font: 12px/1.35 ui-monospace, SFMono-Regular, Consolas, monospace;
      backdrop-filter: blur(5px);
    }
    .scythe-web-monocle__row { display:flex; gap:12px; align-items:baseline; }
    .scythe-web-monocle__row--controls { margin-top:6px; align-items:center; }
    .scythe-web-monocle__title { color:#00d4ff; font-weight:800; letter-spacing:.14em; }
    .scythe-web-monocle__value { color:#fff; font-size:16px; font-weight:700; flex:1; }
    .scythe-web-monocle__detail { color:#86a9ba; overflow:hidden; text-overflow:ellipsis; }
    .scythe-web-monocle__body { margin-top:6px; display:grid; gap:2px; }
    .scythe-web-monocle--compact .scythe-web-monocle__body { display:none; }
    .scythe-web-monocle__mode, .scythe-web-monocle__correlate {
      color:#86a9ba; background:#071422; border:1px solid #24506a; padding:3px 7px;
      font:10px ui-monospace,monospace; cursor:pointer;
    }
    .scythe-web-monocle__mode[aria-pressed="true"] { color:#00d4ff; border-color:#00d4ff; }
    .scythe-web-monocle__correlate { margin-left:auto; color:#63ffd1; }
    .scythe-web-monocle__correlate:disabled { color:#526675; cursor:not-allowed; opacity:.7; }
    .scythe-web-monocle__badge {
      border:1px solid currentColor; padding:2px 6px; font-size:10px;
      letter-spacing:.08em; white-space:nowrap;
    }
    .scythe-evidence-measured { color:#63ffd1; border-style:solid; }
    .scythe-evidence-observed { color:#b7ffdc; border-style:solid; }
    .scythe-evidence-solver-output { color:#00d4ff; border-style:double; }
    .scythe-evidence-reduced-order { color:#f7d154; border-style:dotted; }
    .scythe-evidence-synthetic { color:rgba(187,131,255,.62); border-style:solid; }
    .scythe-evidence-illustrative { color:#ff8c42; border-style:dashed; }
    .scythe-evidence-inferred { color:#f7d154; border-style:dashed; }
    .scythe-evidence-counterfactual { color:#bb83ff; border-style:dotted; }
  `;
  documentRoot.head.appendChild(element);
}

function createHud(documentRoot, container, {frequencyHz = null, showOptical = false} = {}) {
  injectStyles(documentRoot);
  const root = documentRoot.createElement("section");
  root.className = "scythe-web-monocle scythe-web-monocle--compact";
  root.setAttribute("aria-live", "polite");
  root.innerHTML = `
    <div class="scythe-web-monocle__row">
      <span class="scythe-web-monocle__title">RF FIELD INSPECTOR // ${frequencyLabel(frequencyHz)}</span>
      <span data-role="evidence" class="scythe-web-monocle__badge">UNCLASSIFIED</span>
      <span data-role="value" class="scythe-web-monocle__value">NO VALIDATED SOLVER DATA</span>
    </div>
    <div class="scythe-web-monocle__row scythe-web-monocle__row--controls">
      ${["CAMERA", "HOVER", "PINNED"].map((mode) => `<button type="button" data-rf-mode="${mode}" class="scythe-web-monocle__mode" aria-pressed="${mode === "CAMERA"}">${mode}</button>`).join("")}
      <button type="button" data-role="correlate" class="scythe-web-monocle__correlate" disabled>CORRELATE WITH SELECTED HOST</button>
    </div>
    <div class="scythe-web-monocle__body">
      <div data-role="mode" class="scythe-web-monocle__detail">MODE // CAMERA</div>
      <div data-role="detail" class="scythe-web-monocle__detail">Awaiting fixed-step sample</div>
      <div data-role="decision" class="scythe-web-monocle__detail">THRESHOLD // UNAVAILABLE</div>
      <div data-role="uncertainty" class="scythe-web-monocle__detail">UNCERTAINTY // UNKNOWN</div>
      <div data-role="provenance" class="scythe-web-monocle__detail">PROVENANCE // UNAVAILABLE</div>
      <div data-role="hash" class="scythe-web-monocle__detail">DISPLAY ASSET // UNAVAILABLE</div>
      ${showOptical ? '<div data-role="optical" class="scythe-web-monocle__detail">OPTICS // WAITING</div>' : ""}
    </div>
  `;
  container.appendChild(root);
  return root;
}

function activeTransmitter(scenario) {
  const transmitters = scenario?.transmitters ?? [];
  if (scenario?.activeTransmitterId) {
    return transmitters.find((tx) => tx.id === scenario.activeTransmitterId) ?? null;
  }
  return transmitters[0] ?? null;
}

/**
 * Fixed-step Cesium instrument layer.
 *
 * The layer samples only through SCYTHE RF/optical samplers. Cesium entities added here
 * show operator/TX geometry and provenance; they never represent propagation
 * unless a sample is available.
 */
export class MonocleOverlayLayer {
  constructor({
    viewer,
    Cesium,
    rfSampler,
    opticsSampler = null,
    scenario = {},
    fixedStepSeconds = 0.25,
    timeSource,
    documentRoot = globalThis.document,
    container,
  }) {
    if (!viewer?.scene?.postRender?.addEventListener) {
      throw new TypeError("A Cesium Viewer with scene.postRender is required");
    }
    if (!Cesium?.Ellipsoid?.WGS84 || !Cesium?.Cartesian3) {
      throw new TypeError("A compatible Cesium namespace is required");
    }
    if (typeof rfSampler?.sample !== "function" && typeof opticsSampler?.sample !== "function") {
      throw new TypeError("At least one SCYTHE sampler is required");
    }
    if (!(fixedStepSeconds > 0)) throw new RangeError("fixedStepSeconds must be positive");

    this.viewer = viewer;
    this.Cesium = Cesium;
    this.rfSampler = rfSampler;
    this.opticsSampler = opticsSampler;
    this.scenario = scenario;
    this.fixedStepMilliseconds = fixedStepSeconds * 1000;
    this.timeSource = timeSource ?? (() =>
      Cesium.JulianDate.toDate(viewer.clock.currentTime));
    this.documentRoot = documentRoot;
    this.container = container ??
      documentRoot?.getElementById("globe-root") ??
      documentRoot?.body;
    this.hud = null;
    this.lastStep = null;
    this.inFlight = false;
    this.removePostRender = null;
    this.entityIds = new Set();
    this.coverageGridEntityIds = new Set();
    this.coverageClickHandler = null;
    this.samplingMode = "CAMERA";
    this.hoverCell = null;
    this.pinnedCell = null;
    this.receiverSensor = null;
    this.correlationAvailable = false;
    this.destroyed = false;
  }

  start() {
    if (this.removePostRender) return this;
    if (!this.container) throw new Error("A HUD container is required");
    this.hud = createHud(this.documentRoot, this.container, {
      frequencyHz: this.rfSampler?.descriptor?.physics?.rf?.frequencyHz,
      showOptical: Boolean(this.opticsSampler),
    });
    for (const button of this.hud.querySelectorAll?.("[data-rf-mode]") ?? [])
      button.addEventListener("click", () => this.setSamplingMode(button.dataset.rfMode));
    this.hud.querySelector?.('[data-role="correlate"]')?.addEventListener("click", () => {
      if (!this.correlationAvailable) return;
      const EventClass = this.documentRoot?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
      this.container?.dispatchEvent(new EventClass("scythe-web:rf-correlate-requested", {bubbles: true,
        detail: {cell: this.pinnedCell}}));
    });
    this.#addTransmitterMarkers();
    this.#addRangeRings();
    this.#installCoverageCellInteraction();
    this.removePostRender = this.viewer.scene.postRender.addEventListener(() => {
      void this.tick();
    });
    return this;
  }

  async tick() {
    if (this.destroyed || this.inFlight) return;
    const utc = new Date(this.timeSource());
    if (!Number.isFinite(utc.getTime())) throw new Error("timeSource returned an invalid time");
    const step = Math.floor(utc.getTime() / this.fixedStepMilliseconds);
    if (step === this.lastStep) return;
    this.lastStep = step;
    this.inFlight = true;

    try {
      const activeCell = this.samplingMode === "PINNED" ? this.pinnedCell :
        this.samplingMode === "HOVER" ? this.hoverCell : null;
      const position = activeCell ? {longitudeDegrees: activeCell.longitude_degrees,
        latitudeDegrees: activeCell.latitude_degrees, heightMeters: activeCell.height_meters ?? 0} :
        operatorGeodetic(this.viewer, this.Cesium);
      if (!this.#scenarioTimeContains(utc)) {
        const unavailable = { status: "OUTSIDE_SCENARIO_TIME", available: false,
          reason: "UTC outside scenario window", evidenceClass: null };
        this.#renderHud(unavailable);
        this.#renderOpticalHud(null);
        this.#renderBearing(position, unavailable);
        this.#renderCoverageFootprint(position, unavailable);
        this.#renderUncertaintyHalo(position, unavailable);
        this.#renderOpticalCue(position, null);
        return;
      }
      const rf = this.rfSampler?.descriptor.physics.rf;
      const optical = this.opticsSampler?.descriptor.physics.optical;
      const [sample, opticalSample] = await Promise.all([
        this.rfSampler ? this.rfSampler.sample({
          ...position,
          utc: utc.toISOString(),
          frequencyHz: rf.frequencyHz,
          coverageThreshold: this.scenario.coverageThreshold ?? null,
        }) : null,
        this.opticsSampler ? this.opticsSampler.sample({
          ...position,
          wavelengthNanometers: optical.wavelengthNanometers,
          depthPlaneIndex: this.scenario.opticalDepthPlaneIndex,
        }) : null,
      ]);
      if (!this.destroyed) {
        if (sample) {
          if (!activeCell) this.#renderHud(sample);
          else this.#renderCellHud(activeCell);
          this.#renderBearing(position, sample);
          this.#renderCoverageFootprint(position, sample);
          this.#renderUncertaintyHalo(position, sample);
          await this.#renderCoverageGrid(utc);
        }
        this.#renderOpticalHud(opticalSample);
        this.#renderOpticalCue(position, opticalSample);
      }
    } catch (error) {
      if (!this.destroyed) {
        this.#renderHud({
          status: "CLIENT_ERROR",
          available: false,
          reason: error.message,
          evidenceClass: this.rfSampler?.descriptor?.evidenceClass ??
            this.opticsSampler?.descriptor?.evidenceClass ?? null,
        });
      }
    } finally {
      this.inFlight = false;
    }
  }

  setSamplingMode(mode) {
    const next = String(mode ?? "").toUpperCase();
    if (!["CAMERA", "HOVER", "PINNED"].includes(next)) throw new TypeError("RF sampling mode is invalid");
    this.samplingMode = next;
    for (const button of this.hud?.querySelectorAll?.("[data-rf-mode]") ?? [])
      button.setAttribute("aria-pressed", String(button.dataset.rfMode === next));
    const element = this.hud?.querySelector?.('[data-role="mode"]');
    if (element) element.textContent = `MODE // ${next}${next === "PINNED" && !this.pinnedCell ? " // NO CELL SELECTED" : ""}`;
    this.#setExpanded(next === "PINNED" && Boolean(this.pinnedCell));
    if (next === "PINNED" && this.pinnedCell) this.#renderCellHud(this.pinnedCell);
    this.lastStep = null; return next;
  }

  pinCoverageCell(detail) {
    if (!detail?.dataset_id || !Number.isFinite(Number(detail.longitude_degrees)) ||
        !Number.isFinite(Number(detail.latitude_degrees))) return false;
    this.pinnedCell = Object.freeze({...detail, provenance: {...(detail.provenance ?? {})},
      uncertainty: {...(detail.uncertainty ?? {})}});
    this.setSamplingMode("PINNED"); this.#renderCellHud(this.pinnedCell); return true;
  }

  setCorrelationAvailable(value) {
    this.correlationAvailable = Boolean(value && this.pinnedCell);
    const button = this.hud?.querySelector?.('[data-role="correlate"]');
    if (button) button.disabled = !this.correlationAvailable;
  }

  setReceiverSensor(sensor) {
    this.receiverSensor = sensor ? Object.freeze({...sensor}) : null;
    if (!sensor || !this.hud) return;
    this.#setExpanded(true);
    const set = (role, text) => { const element = this.hud.querySelector(`[data-role="${role}"]`);
      if (element) element.textContent = text; };
    set("mode", "MODE // RECEIVER SENSOR");
    set("detail", `${sensor.sensorId ?? "RF RECEIVER"} // ${String(sensor.bridgeState ?? "UNKNOWN").toUpperCase()} // IQ ${sensor.iqConnected ? "CONNECTED" : "DISCONNECTED"}`);
    set("decision", `TUNING // ${frequencyLabel(sensor.centerFrequencyHz)} // SAMPLE RATE ${frequencyLabel(sensor.sampleRateHz)}`);
    set("uncertainty", `LOCATION // ${sensor.locationAuthority ?? "NOT PROVIDED"}${Number.isFinite(sensor.accuracyMeters) ? ` // ±${Math.round(sensor.accuracyMeters)} m` : ""}`);
    set("provenance", `CAPTURE OWNER // ${sensor.captureOwner ?? "UNKNOWN"} // RAW IQ NOT EXPOSED`);
    set("hash", `LATEST FRAME // ${sensor.latestFrameAt ?? "NONE"}`);
  }

  #setExpanded(value) {
    this.hud?.classList?.toggle("scythe-web-monocle--compact", !value);
  }

  #installCoverageCellInteraction() {
    if (!this.Cesium.ScreenSpaceEventHandler || this.coverageClickHandler) return;
    this.coverageClickHandler = new this.Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
    this.coverageClickHandler.setInputAction((movement) => {
      const picked = this.viewer.scene.pick(movement.position);
      const entity = picked?.id;
      if (!entity?.id?.startsWith("scythe-web:coverage:")) return;
      const detail = coverageCellDetail(entity, this.viewer.clock.currentTime);
      this.pinCoverageCell(detail);
      const EventClass = this.documentRoot?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
      this.container?.dispatchEvent(new EventClass("scythe-web:coverage-cell-selected", {
        bubbles: true, detail,
      }));
    }, this.Cesium.ScreenSpaceEventType.LEFT_CLICK);
    if (this.Cesium.ScreenSpaceEventType.MOUSE_MOVE) this.coverageClickHandler.setInputAction((movement) => {
      const entity = this.viewer.scene.pick(movement.endPosition)?.id;
      const detail = coverageCellDetail(entity, this.viewer.clock.currentTime);
      this.hoverCell = detail;
      if (this.samplingMode === "HOVER" && detail) {
        this.#setExpanded(true); this.#renderCellHud(detail); this.lastStep = null;
      } else if (this.samplingMode === "HOVER") this.#setExpanded(false);
    }, this.Cesium.ScreenSpaceEventType.MOUSE_MOVE);
  }

  #renderHud(sample) {
    if (!this.hud) return;
    const model = formatSampleForHud(sample);
    const set = (role, text) => {
      const element = this.hud.querySelector(`[data-role="${role}"]`);
      if (element) element.textContent = text;
    };
    set("value", model.value);
    set("detail", `${model.state} // ${model.detail}`);
    set("mode", `MODE // ${this.samplingMode}`);
    set("decision", sample?.coverage == null ? "THRESHOLD // UNAVAILABLE" :
      `THRESHOLD // ${sample.query?.coverageThreshold?.comparison ?? "?"} ${sample.query?.coverageThreshold?.value ?? "?"} ${sample.query?.coverageThreshold?.units ?? sample.units ?? ""} // ${sample.coverage ? "PASS" : "FAIL"}`);
    set("uncertainty", `UNCERTAINTY // ${model.uncertainty ?? "NOT QUANTIFIED"}`);
    set("provenance", `PROVENANCE // ${model.provenance ?? "UNAVAILABLE"}`);
    set("hash", `DISPLAY ASSET // ${sample?.displayAssetHash ?? "UNAVAILABLE"}`);

    const badge = this.hud.querySelector('[data-role="evidence"]');
    if (badge) {
      badge.className = "scythe-web-monocle__badge";
      if (model.evidenceClass) {
        const style = evidenceStyle(model.evidenceClass);
        badge.textContent = style.label;
        badge.classList.add(style.cssClass);
      } else {
        badge.textContent = "UNCLASSIFIED";
      }
    }
  }

  #renderCellHud(cell) {
    if (!this.hud || !cell) return;
    const set = (role, text) => { const element = this.hud.querySelector(`[data-role="${role}"]`);
      if (element) element.textContent = text; };
    const displayValue = Number.isFinite(Number(cell.display_value)) ? Number(cell.display_value) : Number(cell.value);
    const delta = Number.isFinite(Number(cell.display_delta)) ? Number(cell.display_delta) : displayValue - Number(cell.value);
    set("value", `${Number(cell.value).toPrecision(6)} ${cell.units ?? ""}`);
    set("mode", `MODE // ${this.samplingMode} // ${Number(cell.latitude_degrees).toFixed(5)}°, ${Number(cell.longitude_degrees).toFixed(5)}°`);
    set("detail", `CONTRACT VALUE // ${cell.value} ${cell.units ?? ""} // DISPLAY VALUE // ${displayValue} ${cell.units ?? ""} // Δ ${Number(delta).toPrecision(4)}`);
    set("decision", `THRESHOLD // ${cell.coverage_comparison ?? "?"} ${cell.coverage_threshold ?? "?"} ${cell.coverage_threshold_units ?? cell.units ?? ""} // ${cell.coverage ? "PASS" : "FAIL"}`);
    const uncertainty = cell.uncertainty ?? {};
    set("uncertainty", `UNCERTAINTY // ${uncertainty.value == null ? uncertainty.kind ?? "NOT QUANTIFIED" : `±${uncertainty.value} ${uncertainty.units ?? ""} // ${uncertainty.kind ?? "UNSPECIFIED"}`}`);
    const provenance = cell.provenance ?? {};
    set("provenance", `SOLVER // ${provenance.solverName ?? "UNKNOWN"} ${provenance.solverVersion ?? ""} // RUN ${provenance.runId ?? "UNKNOWN"} // SOURCE ${provenance.sourceRevision ?? "UNKNOWN"}`);
    set("hash", `DISPLAY ASSET // ${cell.display_asset_hash ?? "UNAVAILABLE"} // VISUAL AUTHORITY ${cell.visualization_is_authoritative ? "AUTHORITATIVE" : "NON-AUTHORITATIVE"}`);
    const badge = this.hud.querySelector('[data-role="evidence"]');
    if (badge && cell.evidence_class) {
      const style = evidenceStyle(cell.evidence_class); badge.textContent = style.label;
      badge.className = `scythe-web-monocle__badge ${style.cssClass}`;
    }
  }

  #renderOpticalHud(sample) {
    if (!this.hud) return;
    const element = this.hud.querySelector('[data-role="optical"]');
    if (element) element.textContent = `OPTICS // ${formatOpticalSampleForHud(sample)}`;
    if (!this.rfSampler && sample?.available) {
      const model = formatOpticalSampleForHud(sample);
      const value = this.hud.querySelector('[data-role="value"]');
      if (value) value.textContent = model;
      const badge = this.hud.querySelector('[data-role="evidence"]');
      if (badge) {
        const style = evidenceStyle(sample.evidenceClass);
        badge.textContent = style.label;
        badge.className = `scythe-web-monocle__badge ${style.cssClass}`;
      }
    }
  }

  #scenarioTimeContains(utc) {
    const window = this.scenario.timeWindow;
    if (!window) return true;
    const milliseconds = utc.getTime();
    return !(window.startUtc && milliseconds < Date.parse(window.startUtc)) &&
      !(window.endUtc && milliseconds > Date.parse(window.endUtc));
  }

  #addTransmitterMarkers() {
    for (const tx of this.scenario.transmitters ?? []) {
      const id = `scythe-web:tx:${tx.id}`;
      this.viewer.entities.add({
        id,
        position: this.Cesium.Cartesian3.fromDegrees(
          tx.longitudeDegrees,
          tx.latitudeDegrees,
          finiteOr(tx.heightMeters),
        ),
        point: {
          pixelSize: 9,
          color: this.Cesium.Color.fromCssColorString("#00d4ff"),
          outlineColor: this.Cesium.Color.BLACK,
          outlineWidth: 2,
        },
        label: {
          text: tx.label ?? tx.id,
          fillColor: this.Cesium.Color.fromCssColorString("#ccefff"),
          font: "12px ui-monospace, monospace",
          pixelOffset: new this.Cesium.Cartesian2(0, -18),
        },
      });
      this.entityIds.add(id);
    }
  }

  #addRangeRings() {
    for (const tx of this.scenario.transmitters ?? []) {
      if (!(tx.rangeMeters > 0)) continue;
      const id = `scythe-web:range:${tx.id}`;
      this.viewer.entities.add({
        id,
        position: this.Cesium.Cartesian3.fromDegrees(
          tx.longitudeDegrees, tx.latitudeDegrees, finiteOr(tx.heightMeters)),
        ellipse: {
          semiMajorAxis: tx.rangeMeters,
          semiMinorAxis: tx.rangeMeters,
          fill: false,
          outline: true,
          outlineColor: this.Cesium.Color.fromCssColorString("#00d4ff").withAlpha(0.7),
          height: finiteOr(tx.heightMeters),
        },
        properties: { scytheSemantics: "DECLARED_SCENARIO_RANGE; NOT PROPAGATION" },
      });
      this.entityIds.add(id);
    }
  }

  #renderCoverageFootprint(operator, sample) {
    const id = "scythe-web:coverage-sample";
    this.viewer.entities.removeById(id);
    this.entityIds.delete(id);
    if (!sample.available || sample.coverage == null || !(this.scenario.coverageFootprintMeters > 0)) return;
    const style = evidenceStyle(sample.evidenceClass);
    const color = this.Cesium.Color.fromCssColorString(
      sample.coverage ? style.color : "#ff445e").withAlpha(Math.min(style.alpha, 0.28));
    this.viewer.entities.add({
      id,
      position: this.Cesium.Cartesian3.fromDegrees(
        operator.longitudeDegrees, operator.latitudeDegrees, operator.heightMeters),
      ellipse: {
        semiMajorAxis: this.scenario.coverageFootprintMeters,
        semiMinorAxis: this.scenario.coverageFootprintMeters,
        material: color,
        outline: true,
        outlineColor: this.Cesium.Color.fromCssColorString(style.color),
        height: 0,
      },
      properties: {
        datasetId: sample.datasetId,
        evidenceClass: sample.evidenceClass,
        visualizationIsAuthoritative: false,
        scytheSemantics: "POINT SAMPLE FOOTPRINT; NOT REGIONAL INTERPOLATION",
      },
    });
    this.entityIds.add(id);
  }

  async #renderCoverageGrid(utc) {
    for (const id of this.coverageGridEntityIds) {
      this.viewer.entities.removeById(id);
      this.entityIds.delete(id);
    }
    this.coverageGridEntityIds.clear();
    const grid = this.scenario.coverageGrid;
    const rf = this.rfSampler?.descriptor.physics.rf;
    if (!grid || !this.rfSampler || typeof this.rfSampler.sampleGrid !== "function") return;
    const cells = await this.rfSampler.sampleGrid({
      ...grid,
      heightMeters: grid.heightMeters ?? 0,
      utc: utc.toISOString(),
      frequencyHz: rf.frequencyHz,
      coverageThreshold: this.scenario.coverageThreshold ?? null,
    });
    for (const cell of cells) {
      const sample = cell.sample;
      if (!sample.available || sample.coverage == null) continue;
      const id = `scythe-web:coverage:${cell.x}:${cell.y}`;
      const style = evidenceStyle(sample.evidenceClass);
      const centerLongitude = (cell.boundsDegrees[0] + cell.boundsDegrees[2]) / 2;
      const centerLatitude = (cell.boundsDegrees[1] + cell.boundsDegrees[3]) / 2;
      const threshold = this.scenario.coverageThreshold;
      const transmitter = activeTransmitter(this.scenario);
      const fill = sample.coverage
        ? cesiumAreaMaterial(this.Cesium, sample.evidenceClass, 0.28)
        : this.Cesium.Color.fromCssColorString("#ff445e").withAlpha(0.12);
      this.viewer.entities.add({
        id,
        rectangle: {
          coordinates: this.Cesium.Rectangle.fromDegrees(...cell.boundsDegrees),
          material: fill,
          outline: true,
          outlineColor: this.Cesium.Color.fromCssColorString(style.color).withAlpha(0.65),
          height: grid.heightMeters ?? 0,
        },
        properties: {
          datasetId: sample.datasetId,
          tileId: sample.tileId,
          displayAssetHash: sample.displayAssetHash,
          evidenceClass: sample.evidenceClass,
          quantity: sample.quantity,
          value: sample.value,
          displayValue: sample.value,
          displayDelta: 0,
          units: sample.units,
          coverage: sample.coverage,
          coverageThreshold: threshold?.value ?? null,
          coverageComparison: threshold?.comparison ?? null,
          coverageThresholdUnits: threshold?.units ?? null,
          longitudeDegrees: centerLongitude,
          latitudeDegrees: centerLatitude,
          heightMeters: grid.heightMeters ?? 0,
          frequencyHz: sample.query?.frequencyHz ?? null,
          transmitterId: transmitter?.id ?? "unknown",
          solverName: sample.provenance?.solverName,
          solverVersion: sample.provenance?.solverVersion,
          sourceRevision: sample.provenance?.sourceRevision,
          runId: sample.provenance?.runId,
          uncertaintyKind: sample.uncertainty?.kind ?? null,
          uncertaintyValue: sample.uncertainty?.value ?? null,
          uncertaintyUnits: sample.uncertainty?.units ?? null,
          visualizationIsAuthoritative: false,
        },
      });
      this.coverageGridEntityIds.add(id);
      this.entityIds.add(id);
    }
  }

  #renderUncertaintyHalo(operator, sample) {
    const id = "scythe-web:uncertainty-halo";
    this.viewer.entities.removeById(id);
    this.entityIds.delete(id);
    const uncertainty = sample.uncertainty;
    if (!sample.available || !(uncertainty?.value > 0) || uncertainty.units !== "m") return;
    this.viewer.entities.add({
      id,
      position: this.Cesium.Cartesian3.fromDegrees(
        operator.longitudeDegrees, operator.latitudeDegrees, operator.heightMeters),
      ellipse: {
        semiMajorAxis: uncertainty.value,
        semiMinorAxis: uncertainty.value,
        material: this.Cesium.Color.fromCssColorString("#f7d154").withAlpha(0.14),
        outline: true,
        outlineColor: this.Cesium.Color.fromCssColorString("#f7d154").withAlpha(0.8),
        height: 0,
      },
      properties: {
        uncertaintyKind: uncertainty.kind,
        evidenceClass: sample.evidenceClass,
        visualizationIsAuthoritative: false,
      },
    });
    this.entityIds.add(id);
  }

  #renderOpticalCue(operator, sample) {
    const id = "scythe-web:optical-cue";
    this.viewer.entities.removeById(id);
    this.entityIds.delete(id);
    if (!sample?.available) return;
    const style = evidenceStyle(sample.evidenceClass);
    this.viewer.entities.add({
      id,
      position: this.Cesium.Cartesian3.fromDegrees(
        operator.longitudeDegrees, operator.latitudeDegrees, operator.heightMeters),
      point: {
        pixelSize: 13,
        color: this.Cesium.Color.fromCssColorString(style.color).withAlpha(style.alpha),
        outlineColor: this.Cesium.Color.WHITE,
        outlineWidth: 1,
      },
      label: {
        text: sample.depthPlaneIndex == null ? sample.quantity :
          `${sample.quantity} // depth ${sample.depthPlaneIndex}`,
        fillColor: this.Cesium.Color.fromCssColorString(style.color),
        font: "11px ui-monospace, monospace",
        pixelOffset: new this.Cesium.Cartesian2(0, 22),
      },
      properties: {
        datasetId: sample.datasetId,
        evidenceClass: sample.evidenceClass,
        visualizationIsAuthoritative: false,
      },
    });
    this.entityIds.add(id);
  }

  #renderBearing(operator, sample) {
    const id = "scythe-web:active-bearing";
    this.viewer.entities.removeById(id);
    this.entityIds.delete(id);
    if (!sample.available) return;

    const tx = activeTransmitter(this.scenario);
    if (!tx) return;
    this.viewer.entities.add({
      id,
      polyline: {
        positions: [
          this.Cesium.Cartesian3.fromDegrees(
            operator.longitudeDegrees,
            operator.latitudeDegrees,
            operator.heightMeters,
          ),
          this.Cesium.Cartesian3.fromDegrees(
            tx.longitudeDegrees,
            tx.latitudeDegrees,
            finiteOr(tx.heightMeters),
          ),
        ],
        width: 2,
        material: cesiumPolylineMaterial(this.Cesium, sample.evidenceClass),
        arcType: this.Cesium.ArcType.GEODESIC,
      },
    });
    this.entityIds.add(id);
  }

  destroy() {
    this.destroyed = true;
    if (typeof this.removePostRender === "function") this.removePostRender();
    this.removePostRender = null;
    this.coverageClickHandler?.destroy();
    this.coverageClickHandler = null;
    for (const id of this.entityIds) this.viewer.entities.removeById(id);
    this.entityIds.clear();
    this.hud?.remove();
    this.hud = null;
  }
}
