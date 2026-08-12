function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function locationEstimate(node) {
  const geo = node?.enrichment?.geo;
  const latitude = finite(geo?.latitude);
  const longitude = finite(geo?.longitude);
  if (latitude === null || longitude === null || latitude < -90 || latitude > 90 ||
      longitude < -180 || longitude > 180) return null;
  const place = [geo.city, geo.region, geo.country || geo.countryCode]
    .map((value) => String(value ?? "").trim()).filter(Boolean).join(", ");
  return {
    node,
    latitude,
    longitude,
    uncertaintyRadiusKm: finite(geo.uncertaintyRadiusKm),
    place: place || "UNNAMED GEOIP ESTIMATE",
    evidenceClass: "INFERRED",
    authority: "GEOIP_ESTIMATE",
  };
}

export function locationEstimates(graph, limit = 500) {
  return (graph?.nodes ?? []).slice(0, Math.max(0, Number(limit) || 0))
    .map(locationEstimate).filter(Boolean);
}

export function projectLocation(latitude, longitude, width, height, padding = 18) {
  const usableWidth = Math.max(1, width - padding * 2);
  const usableHeight = Math.max(1, height - padding * 2);
  return {
    x: padding + ((longitude + 180) / 360) * usableWidth,
    y: padding + ((90 - latitude) / 180) * usableHeight,
  };
}

export function locationBoundary(located, total) {
  return `LOCATION ESTIMATES // ${located} GEOIP-PLOTTED // ${Math.max(0, total - located)} UNLOCATED\n` +
    "AUTHORITY // INFERRED · LOCAL GEOIP DATABASE\n" +
    "BOUNDARY // IP NETWORK LOCATION ESTIMATE; NOT PHYSICAL DEVICE LOCATION";
}
