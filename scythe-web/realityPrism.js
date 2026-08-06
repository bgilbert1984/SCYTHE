function number(value, digits = 4) {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : "UNAVAILABLE";
}

export function renderRealityPrism(root, parameters) {
  if (!root) return null;
  const provenance = parameters.provenance ?? {};
  const documentRoot = root.ownerDocument ?? globalThis.document;
  root.hidden = false;
  root.innerHTML = "";
  const title = documentRoot.createElement("strong");
  title.textContent = "REALITY PRISM // RF CELL";
  const content = documentRoot.createElement("pre");
  content.textContent = [
    `${parameters.quantity} // ${number(parameters.authoritativeValue)} ${parameters.units}`,
    `DISPLAY // ${number(parameters.displayValue)} ${parameters.units}`,
    `DISPLAY − AUTHORITY // ${number(parameters.displayDelta, 6)} ${parameters.units}`,
    `DECISION // ${parameters.coverage == null ? "NO THRESHOLD" : parameters.coverage ? "COVERED" : "COVERAGE GAP"}`,
    `THRESHOLD // ${parameters.threshold ?? "NONE"} ${parameters.units} ${parameters.comparison ?? ""}`,
    `AUTHORITY // ${parameters.authorityAsset}`,
    `SHA-256 // ${parameters.authorityAssetSha256}`,
    `INTERPOLATION // ${parameters.interpolation}`,
    `SOLVER // ${provenance.solverName ?? "UNKNOWN"} ${provenance.solverVersion ?? ""}`,
    "BOUNDARY // SOLVER_OUTPUT; DISPLAY VISUALIZATION IS NOT AUTHORITATIVE",
    "FALSIFIER // Collect a calibrated field measurement at this location.",
  ].join("\n");
  root.append(title, content);
  return { previousHidden: true };
}
