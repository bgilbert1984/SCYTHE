function text(value) { return String(value ?? "").trim(); }

function fixed(value, digits = 3) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "";
}

function compact(value) {
  const number = Number(value);
  return Number.isFinite(number) ? String(Number(number.toFixed(2))) : "";
}

function timestamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "";
  const milliseconds = number > 10_000_000_000 ? number : number * 1000;
  const date = new Date(milliseconds);
  return Number.isNaN(date.valueOf()) ? "" : date.toISOString();
}

/** Build the one tooltip truth used by both the SVG and Three.js surfaces. */
export function graphEntityTooltip(entity = {}) {
  const enrichment = entity.enrichment ?? {};
  const labels = entity.labels ?? {};
  const ip = text(enrichment.ip || labels.ip || (text(entity.id).startsWith("host:") ? text(entity.id).slice(5) : ""));
  const scope = text(enrichment.scope);
  const lines = [];
  if (ip) {
    lines.push(`${scope || "NETWORK"} HOST`, ip);
    const liveness = entity.liveness ?? {};
    if (["active", "inactive"].includes(liveness.state)) {
      const rtt = Number(liveness.rttMs);
      lines.push("", `LIVENESS // ${liveness.state.toUpperCase()} · ICMP MEASURED`,
        `${liveness.tool || "PING"}${Number.isFinite(rtt) ? ` · ${compact(rtt)} ms` : " · NO REPLY"}`);
    } else {
      lines.push("", "LIVENESS // UNKNOWN · NOT YET MEASURED");
    }
    const network = enrichment.network;
    if (network?.asn) {
      lines.push("", "NETWORK // INFERRED · LOCAL DB",
        `AS${network.asn}${text(network.organization) ? ` · ${text(network.organization)}` : ""}`);
      if (network.prefix) lines.push(`PREFIX · ${network.prefix}`);
    }
    const geo = enrichment.geo;
    if (geo && (geo.city || geo.region || geo.country || geo.countryCode || geo.latitude != null)) {
      const place = [geo.city, geo.region, geo.country || geo.countryCode].map(text).filter(Boolean).join(", ");
      lines.push("", "PLACE ESTIMATE // INFERRED · GEOIP");
      if (place) lines.push(place);
      if (geo.latitude != null && geo.longitude != null) {
        const uncertainty = geo.uncertaintyRadiusKm != null ? ` · ±${compact(geo.uncertaintyRadiusKm)} km` : "";
        lines.push(`${fixed(geo.latitude)}°, ${fixed(geo.longitude)}°${uncertainty}`);
      }
    } else if (scope && scope !== "PUBLIC") {
      lines.push("", "PLACE // NOT APPLICABLE TO LOCAL SCOPE");
    }
    const observed = timestamp(entity.observedAt);
    if (labels.flowRole || observed) {
      lines.push("", "ACTIVITY // OBSERVED");
      if (labels.flowRole) lines.push(`ROLE · ${text(labels.flowRole).toUpperCase()}`);
      if (observed) lines.push(`LAST SEEN · ${observed}`);
    }
    lines.push("", `IP PRESENCE // ${text(entity.evidenceClass) || "INFERRED"}`,
      "GEOIP IS AN ESTIMATE · TOPOLOGY IS NOT GEOLOCATION");
    return lines.join("\n");
  }
  return [text(entity.kind) || "GRAPH ENTITY", text(entity.id), text(entity.evidenceClass) || "INFERRED"]
    .filter(Boolean).join("\n");
}

/** Render tooltip facts as prompt context without upgrading enrichment to evidence. */
export function graphEntityPromptContext(entity = {}) {
  const tooltip = graphEntityTooltip(entity);
  if (!tooltip) return "";
  return [
    "SELECTED GRAPH ENTITY // TOOLTIP CONTEXT",
    tooltip,
    "CONTEXT AUTHORITY // MIXED; EACH CLAIM RETAINS ITS LABEL",
    "BOUNDARY // DISPLAY ENRICHMENT GUIDES QUESTIONS; SERVER-RESOLVED GRAPH EVIDENCE GOVERNS ANSWERS",
  ].join("\n");
}
