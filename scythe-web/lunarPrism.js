export function renderLunarPrism(root, parameters) {
  if (!root || !parameters) throw new TypeError("Lunar prism root and parameters are required");
  const elevation = parameters.elevationMeters == null ? "NOT ASSERTED" : `${parameters.elevationMeters.toFixed(2)} m`;
  const artifacts = (parameters.artifacts ?? []).map((item) =>
    `${item.instrument ?? "REFERENCE"} // ${item.role}\nSHA-256 // ${item.sha256}`).join("\n\n");
  root.textContent = [
    "LUNAR REALITY PRISM",
    `BODY // ${parameters.celestialBody}`,
    `FRAME // ${parameters.referenceFrame}`,
    `LOCATION // ${parameters.latitudeDegrees.toFixed(4)}° LAT // ${parameters.longitudeDegrees.toFixed(4)}° LON`,
    `SPATIAL AUTHORITY // ${parameters.spatialAuthority}`,
    `TERRAIN AUTHORITY // ${parameters.terrainAuthority}`,
    `ELEVATION // ${elevation}`,
    `EVIDENCE // ${parameters.evidenceClass}`,
    "BOUNDARY // REFERENCE IMAGERY IS NOT A SAMPLE SURFACE",
    "",
    artifacts,
    "",
    `FALSIFIER // ${(parameters.limitations ?? [])[0] ?? "Ingest a registered LOLA DEM tile."}`,
  ].join("\n");
  return root.textContent;
}
