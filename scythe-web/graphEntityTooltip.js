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
  const purpose = text(entity.display?.selectionPurpose).replaceAll("_", " ");
  const activityScore = compact(entity.display?.activityScore);
  if (text(entity.kind).toLowerCase() === "geographic_city_context") {
    const geo = enrichment.geo ?? {}; const place = [labels.city || labels.name, labels.region,
      labels.country].map(text).filter(Boolean).join(", ");
    const members = Array.isArray(entity.display?.memberIds) ? entity.display.memberIds : [];
    return ["CITY CONTEXT // INFERRED", place || text(entity.id),
      `HOSTS // ${text(labels.host_count) || "0"} GEOIP-ESTIMATED MEMBERS`,
      ...members.slice(0,12).map((id)=>`· ${text(id).slice(0,96)}`),
      entity.display?.membersOmitted > 0 ? `+ ${entity.display.membersOmitted} MEMBERS OMITTED` : "",
      geo.latitude != null && geo.longitude != null ?
        `CENTROID // ${fixed(geo.latitude)}°, ${fixed(geo.longitude)}°` : "",
      "AUTHORITY // DERIVED FROM HOST GEOIP ESTIMATES",
      "INTERACTION // DISPLAY CONTEXT ONLY · NOT A GRAPHOPS EXECUTION TARGET",
      "BOUNDARY // CITY MEMBERSHIP AND CENTROID ARE INFERRED; NOT PHYSICAL DEVICE LOCATION"]
      .filter(Boolean).join("\n");
  }
  if (ip) {
    const kind = text(entity.kind).toLowerCase();
    const addressTitle = kind === "network_multicast_group" || scope === "MULTICAST"
      ? "MULTICAST GROUP" : kind === "network_unspecified_address"
        ? "UNSPECIFIED ADDRESS" : `${scope || "NETWORK"} HOST`;
    lines.push(addressTitle, ip);
    if (purpose) lines.push("", `DISPLAY PURPOSE // ${purpose}${activityScore ? ` · ACTIVITY ${activityScore}` : ""}`);
    const liveness = entity.liveness ?? {};
    if (["network_multicast_group", "network_unspecified_address"].includes(kind) ||
        ["MULTICAST", "RESERVED"].includes(scope)) {
      lines.push("", "LIVENESS // NOT APPLICABLE · NOT A UNICAST PROBE TARGET");
    } else if (["active", "inactive"].includes(liveness.state)) {
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
  return [text(entity.kind) || "GRAPH ENTITY", text(entity.id),
    purpose ? `DISPLAY PURPOSE // ${purpose}` : "", text(entity.evidenceClass) || "INFERRED"]
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
