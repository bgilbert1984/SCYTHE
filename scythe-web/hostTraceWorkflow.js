function finite(value) { const number = Number(value); return Number.isFinite(number) ? number : null; }
function text(value, fallback = "—") { const result = String(value ?? "").trim(); return result || fallback; }

export function geoPathPoints(result) {
  return (result?.geoPath ?? []).flatMap((hop) => {
    const geo = hop.geo ?? {}; const latitude = finite(hop.lat ?? geo.lat);
    const longitude = finite(hop.lon ?? geo.lon);
    return latitude == null || longitude == null ? [] : [{
      hop: Number(hop.hop), ip: text(hop.ip), latitude, longitude,
      city: text(geo.city, "UNKNOWN PLACE"), confidence: finite(geo.confidence),
      authority: "INFERRED_GEOIP_ESTIMATE",
    }];
  }).slice(0, 30);
}

export function formatHostTracePrompt(result, {entityContext = ""} = {}) {
  const probe = result?.probe ?? {}; const route = result?.traceroute ?? {};
  const hops = (route.hops ?? []).slice(0, Number(result?.maxHops) || 30);
  const rtt = finite(probe.rtt_avg_ms ?? probe.rtt_ms);
  const routeRtt = finite(route.total_rtt_ms);
  const routeClass = text(result?.evidenceClasses?.route, "UNAVAILABLE");
  const routeIsMeasured = routeClass === "MEASURED";
  const routeIsSynthetic = routeClass === "SYNTHETIC";
  const probeReason = text(probe.reason, ""); const routeReason = text(route.reason, "");
  const lines = [
    "GRAPHOPS PROMPT // HOST ROUTE TRACE",
    `TARGET // ${text(result?.target)}`,
    `GRAPH REVISION // ${text(result?.selection?.graphRevision)}`,
    `CACHE // ${result?.cached ? "REUSED ≤30 S" : "FRESH EXECUTION"}`,
  ];
  if (String(entityContext).trim()) lines.push("", String(entityContext).trim());
  lines.push("",
    `PROBE RTT // ${rtt == null ? "UNAVAILABLE" : `${rtt.toFixed(2)} ms`} // ${text(result?.evidenceClasses?.rtt, "UNAVAILABLE")}${probeReason ? ` // ${probeReason}` : ""}`,
    `ROUTE // ${hops.length} HOPS // ${routeClass}${routeRtt != null && routeIsMeasured ? ` // ${routeRtt.toFixed(2)} ms TO LAST RESPONDING HOP` : ""}${routeReason ? ` // ${routeReason}` : ""}`,
    `GEO-PATH // ${geoPathPoints(result).length} ESTIMATED WAYPOINTS // ${text(result?.evidenceClasses?.geography, "UNAVAILABLE")}`,
  );
  if (hops.length) {
    lines.push("", routeIsMeasured ? "MEASURED HOP TRACE" :
      (routeIsSynthetic ? "SYNTHETIC HOPS // NOT MEASUREMENTS" : "UNCLASSIFIED HOP DATA"));
    for (const hop of hops) {
      const hopRtt = finite(hop.rtt_ms); const geo = hop.geo ?? {};
      const place = [geo.city, geo.country].map((item) => text(item, "")).filter(Boolean).join(", ");
      const flags = [hop.anomaly, hop.physics_anomaly?.type].map((item) => text(item, "")).filter(Boolean);
      lines.push(`${String(hop.hop ?? "?").padStart(2, "0")}  ${text(hop.ip)}  ${hopRtt == null ? "*" : `${hopRtt.toFixed(2)} ms`}${place ? `  ≈ ${place}` : ""}${flags.length ? `  ⚠ ${flags.join(" + ")}` : ""}`);
    }
  }
  let prompt = "Resolve the listed capability or collection failure, then repeat the observation; do not interpret missing route data as reachability evidence.";
  if (routeIsSynthetic) {
    prompt = "Discard synthetic hops as evidence, acquire an authorized route measurement, and do not infer reachability, latency, or geography from this fallback.";
  } else if (routeIsMeasured) {
    prompt = "Explain route anomalies, distinguish measured latency from inferred geography, and identify the next observation that would falsify the path interpretation.";
  } else if (rtt != null) {
    prompt = "Interpret the measured RTT without inventing a route, then identify the next authorized observation needed to establish the path.";
  }
  lines.push("", `BOUNDARY // ${text(result?.boundary)}`, `PROMPT // ${prompt}`);
  return lines.join("\n");
}

export async function runHostTrace(selection, {fetchImpl = globalThis.fetch, apiBase = ""} = {}) {
  const response = await fetchImpl.call(globalThis, `${apiBase}/api/graphops/host-trace`, {
    method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({entityId: selection.entityId, graphRevision: selection.graphRevision, maxHops: 20}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error ?? `HTTP ${response.status}`);
  if (!result?.bounded || result.rawPacketsExposed !== false) throw new Error("host trace response violated its bounded contract");
  return result;
}
