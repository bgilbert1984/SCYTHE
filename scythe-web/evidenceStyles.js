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
  OBSERVED: Object.freeze({
    label: "OBSERVED",
    line: "solid",
    color: "#b7ffdc",
    alpha: 0.96,
    cssClass: "scythe-evidence-observed",
  }),
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
  INFERRED: Object.freeze({
    label: "INFERRED",
    line: "dashed",
    color: "#f7d154",
    alpha: 0.82,
    cssClass: "scythe-evidence-inferred",
  }),
  COUNTERFACTUAL: Object.freeze({
    label: "COUNTERFACTUAL",
    line: "dotted",
    color: "#bb83ff",
    alpha: 0.62,
    cssClass: "scythe-evidence-counterfactual",
  }),
});

export function evidenceStyle(evidenceClass) {
  const style = EVIDENCE_STYLES[evidenceClass];
  if (!style) {
    throw new Error(`Unsupported evidence class: ${String(evidenceClass)}`);
  }
  return style;
}

export const HOST_LIVENESS_STYLES = Object.freeze({
  active: Object.freeze({label: "ACTIVE · ICMP MEASURED", color: "#38f28f", alpha: 1}),
  inactive: Object.freeze({label: "INACTIVE · ICMP NO REPLY", color: "#ff4f64", alpha: 1}),
});

export const DISPLAY_PURPOSE_STYLES = Object.freeze({
  SELECTED_CONTEXT: Object.freeze({label: "SELECTED CONTEXT", color: "#ffffff", alpha: 1}),
  MOST_ACTIVE: Object.freeze({label: "MOST ACTIVE", color: "#00d4ff", alpha: 1}),
  EXPLICIT_SIGNAL: Object.freeze({label: "EXPLICIT SIGNAL", color: "#ff8c42", alpha: 1}),
  NEW_ARRIVAL: Object.freeze({label: "NEW ARRIVAL", color: "#bb83ff", alpha: 1}),
  NETWORK_DIVERSITY: Object.freeze({label: "NETWORK DIVERSITY", color: "#f7d154", alpha: .96}),
  STABLE_CONTEXT: Object.freeze({label: "STABLE CONTEXT", color: "#7890a8", alpha: .9}),
});

export function graphPurposeStyle(node) {
  const purpose = node?.display?.selectionPurpose;
  return DISPLAY_PURPOSE_STYLES[purpose] ?? evidenceStyle(node?.evidenceClass ?? "INFERRED");
}

export function hostLivenessStyle(node) {
  if (String(node?.kind ?? "").toLowerCase() !== "network_host") return null;
  return HOST_LIVENESS_STYLES[node?.liveness?.state] ?? null;
}

export const FLOW_TYPE_STYLES = Object.freeze({
  SECURITY_SIGNAL: Object.freeze({label: "SECURITY SIGNAL", color: "#ff4f64", alpha: 1}),
  DNS: Object.freeze({label: "DNS", color: "#f7d154", alpha: .98}),
  HTTP: Object.freeze({label: "HTTP", color: "#00d4ff", alpha: .98}),
  TLS: Object.freeze({label: "TLS", color: "#bb83ff", alpha: .98}),
  TLS_OR_QUIC: Object.freeze({label: "TLS / QUIC CANDIDATE", color: "#9d7cff", alpha: .9}),
  SERVICE_DISCOVERY: Object.freeze({label: "SERVICE DISCOVERY", color: "#ff8c42", alpha: .98}),
  ICMP: Object.freeze({label: "ICMP", color: "#63ffd1", alpha: .98}),
  OTHER: Object.freeze({label: "OTHER TRANSPORT", color: "#7890a8", alpha: .82}),
});

function flowPorts(labels) {
  return new Set([Number(labels?.src_port), Number(labels?.dest_port ?? labels?.dst_port)]
    .filter(Number.isFinite));
}

export function classifyFlowType(edge) {
  const labels = edge?.labels ?? {};
  const declared = String(labels.flow_type ?? edge?.display?.flowType ?? "").toUpperCase();
  if (FLOW_TYPE_STYLES[declared]) return declared;
  if (labels.alert_signature) return "SECURITY_SIGNAL";
  const appProto = String(labels.app_proto ?? "").toLowerCase();
  if (labels.dns_rrname || labels.dns_rrtype || labels.dns_rcode) return "DNS";
  if (labels.http_hostname || labels.http_url || labels.http_method || labels.http_status) return "HTTP";
  if (labels.tls_sni || labels.tls_version || labels.tls_ja3_hash) return "TLS";
  if (["dns", "mdns"].includes(appProto)) return "DNS";
  if (["http", "http2"].includes(appProto)) return "HTTP";
  if (["tls", "ssl"].includes(appProto)) return "TLS";
  if (["quic", "http3"].includes(appProto)) return "TLS_OR_QUIC";
  if (["ssdp", "llmnr"].includes(appProto)) return "SERVICE_DISCOVERY";
  const ports = flowPorts(labels); const destination = String(labels.dest_ip ?? labels.dst_ip ?? "");
  const multicast = destination.startsWith("239.") || destination.startsWith("ff");
  if (multicast || [1900, 5353, 5355].some((port) => ports.has(port))) return "SERVICE_DISCOVERY";
  const proto = String(labels.proto ?? "").toLowerCase();
  if (["icmp", "icmpv6", "icmp6"].includes(proto)) return "ICMP";
  if (ports.has(53)) return "DNS";
  if ([80, 8000, 8080].some((port) => ports.has(port))) return "HTTP";
  if ([443, 8443].some((port) => ports.has(port))) return "TLS_OR_QUIC";
  return "OTHER";
}

export function flowTypeStyle(edge) {
  if (String(edge?.kind ?? "").toLowerCase() !== "network_flow" &&
      !String(edge?.id ?? "").startsWith("flow:")) return evidenceStyle(edge?.evidenceClass ?? "INFERRED");
  const type = classifyFlowType(edge);
  return {...FLOW_TYPE_STYLES[type], type,
    basis: edge?.labels?.flow_type_basis ?? "DISPLAY_CLASSIFICATION"};
}

export const FLOW_DIRECTION_STYLES = Object.freeze({
  OUTBOUND: Object.freeze({label: "OUTBOUND", color: "#00d4ff"}),
  INBOUND: Object.freeze({label: "INBOUND", color: "#ff6fb7"}),
  EAST_WEST: Object.freeze({label: "LOCAL / EAST-WEST", color: "#f7d154"}),
  EXTERNAL_TRANSIT: Object.freeze({label: "EXTERNAL TRANSIT", color: "#7890a8"}),
  UNRESOLVED: Object.freeze({label: "TUPLE DIRECTION ONLY", color: "#ffffff"}),
});

export function flowDirectionStyle(edge) {
  const labels = edge?.labels ?? {};
  const direction = String(labels.operational_direction ?? "UNRESOLVED").toUpperCase();
  return {...(FLOW_DIRECTION_STYLES[direction] ?? FLOW_DIRECTION_STYLES.UNRESOLVED),
    direction: FLOW_DIRECTION_STYLES[direction] ? direction : "UNRESOLVED",
    basis: labels.direction_basis ?? "UNAVAILABLE",
    tupleBasis: labels.tuple_direction_basis ?? "OBSERVED_EVE_TUPLE"};
}

export function flowMotion(edge) {
  const labels = edge?.labels ?? {};
  const forwardPackets = Math.max(0, Number(labels.motion_forward_delta_packets) || 0);
  const reversePackets = Math.max(0, Number(labels.motion_reverse_delta_packets) || 0);
  const intervalMilliseconds = Math.max(0, Number(labels.motion_interval_ms) || 0);
  const measured = labels.motion_basis === "OBSERVED_SURICATA_COUNTER_DELTA" &&
    intervalMilliseconds > 0 && (forwardPackets > 0 || reversePackets > 0);
  return {measured, forwardPackets, reversePackets, intervalMilliseconds,
    durationSeconds: Math.min(3, Math.max(.6, intervalMilliseconds / 1000 || 1.5)),
    basis: labels.motion_basis ?? "INSUFFICIENT_TEMPORAL_COUNTERS"};
}

/** Host liveness may override node color, but never its evidence-class shape. */
export function graphNodeStyle(node) {
  const fallback = evidenceStyle(node?.evidenceClass ?? "INFERRED");
  if (String(node?.kind ?? "").toLowerCase() !== "network_host") return fallback;
  return HOST_LIVENESS_STYLES[node?.liveness?.state] ?? fallback;
}

/**
 * Build a Cesium Entity polyline material without making Cesium a dependency
 * of the sampler or tests.
 */
export function cesiumPolylineMaterial(Cesium, evidenceClass, colorOverride = null, alphaOverride = null) {
  if (!Cesium?.Color || !Cesium?.PolylineDashMaterialProperty) {
    throw new Error("A compatible Cesium namespace is required");
  }

  const style = evidenceStyle(evidenceClass);
  const color = Cesium.Color.fromCssColorString(colorOverride ?? style.color)
    .withAlpha(alphaOverride ?? style.alpha);

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

/** Evidence-distinct area fill for Cesium rectangles/polygons. */
export function cesiumAreaMaterial(Cesium, evidenceClass, alphaScale = 1) {
  const style = evidenceStyle(evidenceClass);
  const color = Cesium.Color.fromCssColorString(style.color)
    .withAlpha(Math.min(1, style.alpha * alphaScale));
  const transparent = Cesium.Color.fromCssColorString(style.color).withAlpha(0.03);
  if (style.line === "hashed" && Cesium.StripeMaterialProperty) {
    return new Cesium.StripeMaterialProperty({
      evenColor: color,
      oddColor: transparent,
      repeat: 12,
      orientation: Cesium.StripeOrientation?.DIAGONAL ??
        Cesium.StripeOrientation?.HORIZONTAL,
    });
  }
  if (style.line === "dotted" && Cesium.GridMaterialProperty) {
    return new Cesium.GridMaterialProperty({
      color,
      cellAlpha: 0.08,
      lineCount: new Cesium.Cartesian2(12, 12),
      lineThickness: new Cesium.Cartesian2(1, 1),
    });
  }
  return color;
}
