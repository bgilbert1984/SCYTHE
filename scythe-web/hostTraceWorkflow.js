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

export function formatHostTracePrompt(result) {
  const probe = result?.probe ?? {}; const route = result?.traceroute ?? {};
  const hops = (route.hops ?? []).slice(0, Number(result?.maxHops) || 30);
  const rtt = finite(probe.rtt_avg_ms ?? probe.rtt_ms ?? route.total_rtt_ms);
  const lines = [
    "GRAPHOPS PROMPT // HOST ROUTE TRACE",
    `TARGET // ${text(result?.target)}`,
    `GRAPH REVISION // ${text(result?.selection?.graphRevision)}`,
    `CACHE // ${result?.cached ? "REUSED ≤30 S" : "FRESH EXECUTION"}`,
    "",
    `RTT // ${rtt == null ? "UNAVAILABLE" : `${rtt.toFixed(2)} ms`} // ${text(result?.evidenceClasses?.rtt, "UNAVAILABLE")}`,
    `ROUTE // ${hops.length} HOPS // ${text(result?.evidenceClasses?.route, "UNAVAILABLE")}`,
    `GEO-PATH // ${geoPathPoints(result).length} ESTIMATED WAYPOINTS // ${text(result?.evidenceClasses?.geography, "UNAVAILABLE")}`,
  ];
  if (hops.length) {
    lines.push("", "HOP TRACE");
    for (const hop of hops) {
      const hopRtt = finite(hop.rtt_ms); const geo = hop.geo ?? {};
      const place = [geo.city, geo.country].map((item) => text(item, "")).filter(Boolean).join(", ");
      const flags = [hop.anomaly, hop.physics_anomaly?.type].map((item) => text(item, "")).filter(Boolean);
      lines.push(`${String(hop.hop ?? "?").padStart(2, "0")}  ${text(hop.ip)}  ${hopRtt == null ? "*" : `${hopRtt.toFixed(2)} ms`}${place ? `  ≈ ${place}` : ""}${flags.length ? `  ⚠ ${flags.join(" + ")}` : ""}`);
    }
  }
  lines.push("", `BOUNDARY // ${text(result?.boundary)}`,
    "PROMPT // Explain route anomalies, distinguish measured latency from inferred geography, and identify the next observation that would falsify the path interpretation.");
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

