/**
 * Deterministic, bounded visual scaling for every live-graph renderer.
 *
 * Size is presentation metadata only. It never changes evidence authority and
 * deliberately uses logarithms so one large counter cannot consume the view.
 */

function nonNegative(value) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(0, number) : 0;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

export function graphNodeScale(node) {
  const activityScore = nonNegative(node?.display?.activityScore);
  const factor = 1 + Math.min(.55, Math.log2(1 + activityScore) * .22);
  const host = String(node?.kind ?? "").toLowerCase() === "network_host";
  return {
    activityScore,
    factor,
    topologyRadius: clamp((host ? 7 : 6) * factor, host ? 7 : 6, 11),
    threeRadius: clamp((host ? 4.6 : 3.5) * factor, host ? 4.6 : 3.5, host ? 7.2 : 5.6),
    cesiumPixels: clamp((host ? 8 : 7) * factor, host ? 8 : 7, 14),
    basis: activityScore > 0 ? "ADAPTIVE_RELEVANCE_ACTIVITY_SCORE" : "ENTITY_KIND_BASELINE",
  };
}

function flowScaleResult({packets, bytes, aggregateCount, basis}) {
  const aggregate = Math.max(1, Math.floor(nonNegative(aggregateCount) || 1));
  const trafficUnits = Math.max(packets, bytes / 1500);
  const intensity = clamp(Math.log2(1 + trafficUnits) / 12, 0, 1);
  const aggregateBoost = clamp(Math.log2(aggregate) * .42, 0, 2);
  return {
    packets, bytes, trafficUnits, intensity, aggregateCount: aggregate,
    topologyWidth: clamp(1.4 + intensity * 2.6 + aggregateBoost, 1.4, 6),
    cesiumWidth: clamp(1.5 + intensity * 3.5 + aggregateBoost, 1.5, 7),
    arrowPixels: clamp(6 + intensity * 8, 6, 14),
    threeArrowScale: clamp(.75 + intensity, .75, 1.75),
    basis,
  };
}

export function graphFlowScale(edge, aggregateCount = 1) {
  const labels = edge?.labels ?? {};
  const deltaPackets = nonNegative(labels.motion_forward_delta_packets) +
    nonNegative(labels.motion_reverse_delta_packets);
  const deltaBytes = nonNegative(labels.motion_forward_delta_bytes) +
    nonNegative(labels.motion_reverse_delta_bytes);
  const measuredDelta = labels.motion_basis === "OBSERVED_SURICATA_COUNTER_DELTA" &&
    (deltaPackets > 0 || deltaBytes > 0);
  const observedPackets = nonNegative(labels.packets) ||
    nonNegative(labels.flow_pkts_toserver) + nonNegative(labels.flow_pkts_toclient);
  const observedBytes = nonNegative(labels.bytes) ||
    nonNegative(labels.flow_bytes_toserver) + nonNegative(labels.flow_bytes_toclient);
  const packets = measuredDelta ? deltaPackets : observedPackets;
  const bytes = measuredDelta ? deltaBytes : observedBytes;
  // Rough packet equivalents combine packet and byte counters without claiming
  // a packet size or bandwidth measurement.
  return flowScaleResult({packets, bytes, aggregateCount,
    basis: measuredDelta ? "MEASURED_TEMPORAL_COUNTER_DELTA" :
      Math.max(packets, bytes / 1500) > 0 ? "OBSERVED_FLOW_COUNTER" : "READABILITY_BASELINE"});
}

export function graphFlowGroupScale(edges) {
  const members = Array.isArray(edges) ? edges : [];
  const scales = members.map((edge) => graphFlowScale(edge));
  const packets = scales.reduce((sum, scale) => sum + scale.packets, 0);
  const bytes = scales.reduce((sum, scale) => sum + scale.bytes, 0);
  const bases = new Set(scales.map((scale) => scale.basis));
  const basis = bases.size === 1 ? (bases.values().next().value ?? "READABILITY_BASELINE") :
    "BOUNDED_MIXED_OBSERVED_COUNTERS";
  return flowScaleResult({packets, bytes, aggregateCount: Math.max(1, members.length), basis});
}

export const GRAPH_VISUAL_SCALE_BOUNDARY =
  "NODE SIZE = BOUNDED ADAPTIVE ACTIVITY; FLOW WIDTH AND ARROW SIZE = BOUNDED OBSERVED COUNTERS; LENGTH = LAYOUT OR ENDPOINT SEPARATION, NOT TRAFFIC, LATENCY, OR ROUTE";
