function finite(value) {
  const number = Number(value); return Number.isFinite(number) ? number : null;
}

function validPoint(latitude, longitude) {
  return latitude !== null && longitude !== null && latitude >= -90 && latitude <= 90 &&
    longitude >= -180 && longitude <= 180;
}

function explicitPosition(node) {
  const position = node?.position;
  if (!Array.isArray(position) || position.length < 2) return null;
  const latitude = finite(position[0]); const longitude = finite(position[1]);
  if (!validPoint(latitude, longitude)) return null;
  if (String(node?.metadata?.geospatialAuthority ?? "").toUpperCase() === "ABSENT") return null;
  return {latitude, longitude, heightMeters: finite(position[2]) ?? 0,
    uncertaintyRadiusKm: finite(node?.metadata?.uncertainty_radius),
    placementAuthority: String(node?.metadata?.geospatialAuthority ?? "GRAPH_POSITION"),
    placementEvidenceClass: node?.evidenceClass ?? "OBSERVED", coLocatedAtSensor: false};
}

function sensorPlacement(sensorVantage) {
  const latitude = finite(sensorVantage?.latitude); const longitude = finite(sensorVantage?.longitude);
  if (!validPoint(latitude, longitude)) return null;
  return {latitude, longitude, heightMeters: finite(sensorVantage?.heightMeters) ?? 0,
    uncertaintyRadiusKm: Math.max(0, finite(sensorVantage?.accuracyMeters) ?? 0) / 1000,
    placementAuthority: String(sensorVantage?.authority ?? "OPERATOR_SENSOR_VANTAGE"),
    placementEvidenceClass: String(sensorVantage?.evidenceClass ?? "MEASURED"),
    sensorId: String(sensorVantage?.sensorId ?? "browser-vantage")};
}

export function geographicGraphPlacement(node, sensorVantage = null) {
  const explicit = explicitPosition(node); if (explicit) return explicit;
  const geo = node?.enrichment?.geo ?? {};
  const latitude = finite(geo.latitude); const longitude = finite(geo.longitude);
  if (validPoint(latitude, longitude)) return {
    latitude, longitude, heightMeters: 0,
    uncertaintyRadiusKm: Math.max(0, finite(geo.uncertaintyRadiusKm) ?? 0),
    placementAuthority: "GEOIP_ESTIMATE", placementEvidenceClass: "INFERRED",
    placementSource: geo.source ?? null, coLocatedAtSensor: false,
  };
  const kind = String(node?.kind ?? "").toLowerCase();
  const scope = String(node?.enrichment?.scope ?? "").toUpperCase();
  if (kind === "network_unspecified_address") return null;
  if (scope !== "PRIVATE" && kind !== "network_multicast_group") return null;
  const vantage = sensorPlacement(sensorVantage); if (!vantage) return null;
  return {...vantage, coLocatedAtSensor: true,
    placementAuthority: "VANTAGE_COLOCATED_DISPLAY",
    inheritedVantageAuthority: vantage.placementAuthority,
    placementEvidenceClass: "ILLUSTRATIVE"};
}

export function geographicProjectionRevision(nodes, sensorVantage = null) {
  const values = (nodes ?? []).map((node) => {
    const p = geographicGraphPlacement(node, sensorVantage);
    return p ? [node.id, p.latitude, p.longitude, p.uncertaintyRadiusKm,
      p.placementAuthority, p.placementSource?.sha256 ?? ""] : [node.id, null];
  });
  let hash = 2166136261;
  for (const char of JSON.stringify(values)) { hash ^= char.charCodeAt(0); hash = Math.imul(hash, 16777619); }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

export function geographicArcWaypoints(source, target, segments = 20) {
  const count = Math.min(48, Math.max(4, Number(segments) || 20));
  let deltaLongitude = target.longitude - source.longitude;
  if (deltaLongitude > 180) deltaLongitude -= 360;
  if (deltaLongitude < -180) deltaLongitude += 360;
  const distanceScale = Math.hypot(target.latitude - source.latitude, deltaLongitude);
  const peak = Math.min(900_000, Math.max(22_000, distanceScale * 12_000));
  return Array.from({length: count + 1}, (_, index) => {
    const fraction = index / count;
    if (distanceScale < .001 && (index === 0 || index === count)) return {
      latitude: source.latitude, longitude: source.longitude,
      heightMeters: Math.max(source.heightMeters ?? 0, target.heightMeters ?? 0), fraction};
    if (distanceScale < .001) return {
      latitude: source.latitude + Math.sin(Math.PI * fraction) * .32,
      longitude: source.longitude + Math.sin(Math.PI * 2 * fraction) * .46,
      heightMeters: Math.max(source.heightMeters ?? 0, target.heightMeters ?? 0) +
        Math.sin(Math.PI * fraction) * 28_000, fraction};
    let longitude = source.longitude + deltaLongitude * fraction;
    if (longitude > 180) longitude -= 360;
    if (longitude < -180) longitude += 360;
    return {latitude: source.latitude + (target.latitude - source.latitude) * fraction,
      longitude, heightMeters: Math.max(source.heightMeters ?? 0, target.heightMeters ?? 0) +
        Math.sin(Math.PI * fraction) * peak, fraction};
  });
}
