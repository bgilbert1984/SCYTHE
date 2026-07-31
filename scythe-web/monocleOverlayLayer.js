import {
  cesiumPolylineMaterial,
  evidenceStyle,
} from "./evidenceStyles.js";

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

export function operatorGeodetic(viewer, Cesium) {
  const position = viewer?.scene?.camera?.positionWC;
  if (!position) throw new Error("Cesium camera positionWC is unavailable");
  const cartographic = Cesium.Ellipsoid.WGS84.cartesianToCartographic(position);
  if (!cartographic) throw new Error("Camera cannot be converted to WGS84");
  return Object.freeze({
    longitudeDegrees: Cesium.Math.toDegrees(cartographic.longitude),
    latitudeDegrees: Cesium.Math.toDegrees(cartographic.latitude),
    heightMeters: cartographic.height,
  });
}

function injectStyles(documentRoot) {
  if (!documentRoot?.head || documentRoot.getElementById(STYLE_ELEMENT_ID)) return;
  const element = documentRoot.createElement("style");
  element.id = STYLE_ELEMENT_ID;
  element.textContent = `
    .scythe-web-monocle {
      position: absolute; left: 50%; bottom: 28px; transform: translateX(-50%);
      min-width: 390px; max-width: min(620px, calc(100vw - 24px));
      padding: 10px 14px; z-index: 45; pointer-events: none;
      color: #ccefff; background: rgba(0, 12, 28, .84);
      border: 1px solid rgba(0, 212, 255, .42); border-radius: 4px;
      box-shadow: 0 0 24px rgba(0, 150, 255, .14);
      font: 12px/1.35 ui-monospace, SFMono-Regular, Consolas, monospace;
      backdrop-filter: blur(5px);
    }
    .scythe-web-monocle__row { display:flex; gap:12px; align-items:baseline; }
    .scythe-web-monocle__title { color:#00d4ff; font-weight:800; letter-spacing:.14em; }
    .scythe-web-monocle__value { color:#fff; font-size:16px; font-weight:700; flex:1; }
    .scythe-web-monocle__detail { color:#86a9ba; overflow:hidden; text-overflow:ellipsis; }
    .scythe-web-monocle__badge {
      border:1px solid currentColor; padding:2px 6px; font-size:10px;
      letter-spacing:.08em; white-space:nowrap;
    }
    .scythe-evidence-measured { color:#63ffd1; border-style:solid; }
    .scythe-evidence-solver-output { color:#00d4ff; border-style:double; }
    .scythe-evidence-reduced-order { color:#f7d154; border-style:dotted; }
    .scythe-evidence-synthetic { color:rgba(187,131,255,.62); border-style:solid; }
    .scythe-evidence-illustrative { color:#ff8c42; border-style:dashed; }
  `;
  documentRoot.head.appendChild(element);
}

function createHud(documentRoot, container) {
  injectStyles(documentRoot);
  const root = documentRoot.createElement("section");
  root.className = "scythe-web-monocle";
  root.setAttribute("aria-live", "polite");
  root.innerHTML = `
    <div class="scythe-web-monocle__row">
      <span class="scythe-web-monocle__title">SCYTHE // WEB MONOCLE</span>
      <span data-role="evidence" class="scythe-web-monocle__badge">UNCLASSIFIED</span>
    </div>
    <div class="scythe-web-monocle__row">
      <span data-role="value" class="scythe-web-monocle__value">NO VALIDATED SOLVER DATA</span>
    </div>
    <div data-role="detail" class="scythe-web-monocle__detail">Awaiting fixed-step sample</div>
    <div data-role="uncertainty" class="scythe-web-monocle__detail">UNCERTAINTY // UNKNOWN</div>
    <div data-role="provenance" class="scythe-web-monocle__detail">PROVENANCE // UNAVAILABLE</div>
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
 * The layer samples only through ScytheRfSampler. Cesium entities added here
 * show operator/TX geometry and provenance; they never represent propagation
 * unless a sample is available.
 */
export class MonocleOverlayLayer {
  constructor({
    viewer,
    Cesium,
    rfSampler,
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
    if (typeof rfSampler?.sample !== "function") {
      throw new TypeError("rfSampler.sample(query) is required");
    }
    if (!(fixedStepSeconds > 0)) throw new RangeError("fixedStepSeconds must be positive");

    this.viewer = viewer;
    this.Cesium = Cesium;
    this.rfSampler = rfSampler;
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
    this.destroyed = false;
  }

  start() {
    if (this.removePostRender) return this;
    if (!this.container) throw new Error("A HUD container is required");
    this.hud = createHud(this.documentRoot, this.container);
    this.#addTransmitterMarkers();
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
      const position = operatorGeodetic(this.viewer, this.Cesium);
      const rf = this.rfSampler.descriptor.physics.rf;
      const sample = await this.rfSampler.sample({
        ...position,
        utc: utc.toISOString(),
        frequencyHz: rf.frequencyHz,
        coverageThreshold: this.scenario.coverageThreshold ?? null,
      });
      if (!this.destroyed) {
        this.#renderHud(sample);
        this.#renderBearing(position, sample);
      }
    } catch (error) {
      if (!this.destroyed) {
        this.#renderHud({
          status: "CLIENT_ERROR",
          available: false,
          reason: error.message,
          evidenceClass: this.rfSampler.descriptor?.evidenceClass ?? null,
        });
      }
    } finally {
      this.inFlight = false;
    }
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
    set("uncertainty", `UNCERTAINTY // ${model.uncertainty ?? "NOT QUANTIFIED"}`);
    set("provenance", `PROVENANCE // ${model.provenance ?? "UNAVAILABLE"}`);

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
    for (const id of this.entityIds) this.viewer.entities.removeById(id);
    this.entityIds.clear();
    this.hud?.remove();
    this.hud = null;
  }
}
