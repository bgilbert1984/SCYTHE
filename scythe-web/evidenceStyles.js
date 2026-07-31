/**
 * Presentation rules shared by every SCYTHE-Web overlay.
 *
 * These styles communicate provenance. They never alter, promote, or infer an
 * evidence class.
 */
export const EVIDENCE_CLASSES = Object.freeze([
  "MEASURED",
  "SOLVER_OUTPUT",
  "REDUCED_ORDER",
  "SYNTHETIC",
  "ILLUSTRATIVE",
]);

export const EVIDENCE_STYLES = Object.freeze({
  MEASURED: Object.freeze({
    label: "MEASURED",
    line: "solid",
    color: "#63ffd1",
    alpha: 1.0,
    cssClass: "scythe-evidence-measured",
  }),
  SOLVER_OUTPUT: Object.freeze({
    label: "SOLVER OUTPUT",
    line: "hashed",
    color: "#00d4ff",
    alpha: 0.94,
    cssClass: "scythe-evidence-solver-output",
  }),
  REDUCED_ORDER: Object.freeze({
    label: "REDUCED ORDER",
    line: "dotted",
    color: "#f7d154",
    alpha: 0.9,
    cssClass: "scythe-evidence-reduced-order",
  }),
  SYNTHETIC: Object.freeze({
    label: "SYNTHETIC",
    line: "solid",
    color: "#bb83ff",
    alpha: 0.48,
    cssClass: "scythe-evidence-synthetic",
  }),
  ILLUSTRATIVE: Object.freeze({
    label: "ILLUSTRATIVE",
    line: "dashed",
    color: "#ff8c42",
    alpha: 0.78,
    cssClass: "scythe-evidence-illustrative",
  }),
});

export function evidenceStyle(evidenceClass) {
  const style = EVIDENCE_STYLES[evidenceClass];
  if (!style) {
    throw new Error(`Unsupported evidence class: ${String(evidenceClass)}`);
  }
  return style;
}

/**
 * Build a Cesium Entity polyline material without making Cesium a dependency
 * of the sampler or tests.
 */
export function cesiumPolylineMaterial(Cesium, evidenceClass) {
  if (!Cesium?.Color || !Cesium?.PolylineDashMaterialProperty) {
    throw new Error("A compatible Cesium namespace is required");
  }

  const style = evidenceStyle(evidenceClass);
  const color = Cesium.Color.fromCssColorString(style.color).withAlpha(style.alpha);

  if (style.line === "solid") {
    return color;
  }

  const dashPattern = style.line === "dotted" ? 0xaaaa : 0xf0f0;
  const dashLength = style.line === "dotted" ? 8 : 20;
  return new Cesium.PolylineDashMaterialProperty({
    color,
    dashLength,
    dashPattern,
  });
}
