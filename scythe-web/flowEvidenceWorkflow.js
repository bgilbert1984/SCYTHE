function text(value, fallback = "—") {
  const result = String(value ?? "").trim(); return result || fallback;
}

export async function prepareFlowEvidence(selection, {
  fetchImpl = globalThis.fetch, apiBase = "", signal,
} = {}) {
  if (selection?.kind !== "graph-edge" || !selection.entityId || !selection.graphRevision) {
    throw new Error("Select a revision-pinned flow edge first");
  }
  const response = await fetchImpl.call(globalThis, `${apiBase}/api/graphops/flow-evidence`, {
    method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, signal,
    body: JSON.stringify({selection: {kind: "graph-edge", entityId: selection.entityId,
      graphRevision: selection.graphRevision}}),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);
  if (!payload?.bounded || payload.rawPacketsExposed !== false || !payload.evidenceId) {
    throw new Error("Flow evidence response violated its bounded contract");
  }
  return payload;
}

export function formatFlowEvidencePrompt(payload) {
  const flow = payload?.flow ?? {}; const transport = flow.transport ?? {};
  const direction = flow.direction ?? {}; const motion = flow.motion ?? {};
  const counters = flow.counters ?? {}; const dissections = payload?.packetDissections ?? [];
  const fields = dissections.flatMap((item) => Object.entries(item.fields ?? {}));
  const temporal = payload?.temporalDissection ?? {};
  const endpoints = flow.endpoints ?? [];
  const lines = [
    "GRAPHOPS PROMPT // FLOW ACTIVITY CAPSULE // PREPARED",
    `FLOW // ${text(flow.id)}`,
    `GRAPH REVISION // ${text(payload?.selection?.graphRevision)}`,
    `EVIDENCE // ${text(flow.evidenceClass)}`,
    `FLOW TYPE // ${text(flow.displayType)} // ${text(flow.displayTypeBasis)}`,
    `PATH // ${text(transport.src_ip)}:${text(transport.src_port, "0")} → ${text(transport.dest_ip)}:${text(transport.dest_port, "0")} // ${text(transport.proto).toUpperCase()}`,
    `OPERATIONAL DIRECTION // ${text(direction.operational_direction, "UNRESOLVED")} // ${text(direction.direction_basis, "UNAVAILABLE")} // ${text(direction.source_zone, "UNRESOLVED")} → ${text(direction.destination_zone, "UNRESOLVED")}`,
    `MOTION // ${text(motion.motion_forward_delta_packets, "—")} FORWARD · ${text(motion.motion_reverse_delta_packets, "—")} REVERSE PACKETS / ${text(motion.motion_interval_ms, "—")} ms // ${text(motion.motion_basis, "INSUFFICIENT_TEMPORAL_COUNTERS")}`,
    `OBSERVATIONS // ${text(counters.observationCount, "1")} · ${text(counters.packets, "UNAVAILABLE")} PACKETS · ${text(counters.bytes, "UNAVAILABLE")} BYTES`,
    `DIRECTIONAL // ${text(counters.packetsToServer, "—")} PKTS / ${text(counters.bytesToServer, "—")} BYTES TO SERVER · ${text(counters.packetsToClient, "—")} PKTS / ${text(counters.bytesToClient, "—")} BYTES TO CLIENT`,
    "", "ENDPOINT CONTEXT // GEOIP AND ASN ARE INFERRED",
    ...endpoints.map((item, index) => `${index === 0 ? "SOURCE" : "DESTINATION"} // ${text(item.ip)} · ${text(item.network?.organization)} · ${text(item.geoip?.city)}, ${text(item.geoip?.country)}`),
    "", `TEMPORAL DISSECTION RING // ${text(temporal.retainedEventCount, "0")} / ${text(temporal.ringLimit, "32")} EVENTS // ${text(temporal.ordering)}`,
    `WINDOW // ${text(temporal.windowStart)} → ${text(temporal.windowEnd)} // ${text(temporal.durationMilliseconds, "0")} ms`,
    `CADENCE // MEDIAN ${text(temporal.medianInterArrivalMilliseconds, "UNAVAILABLE")} ms // ${text(temporal.eventsOmittedBeforeRing, "0")} EARLIER EVENTS OMITTED // ${text(temporal.eventsExcludedAfterSelection, "0")} POST-SELECTION EVENTS EXCLUDED`,
    `SEQUENCE AUTHORITY // ${text(temporal.sequenceAuthority)}`,
    ...(dissections.length ? dissections.flatMap((item, index) => [
      `EVENT ${String(index + 1).padStart(2, "0")} // ${text(item.observedAt)} // ${text(item.eventType)} // ${text(item.eventId)}`,
      ...Object.entries(item.fields ?? {}).map(([key, value]) =>
        key === "app_proto" && String(value).toLowerCase() === "failed"
          ? "  APP_PROTO // UNCLASSIFIED // SURICATA DECODER DID NOT IDENTIFY APPLICATION; NOT AN APPLICATION FAILURE"
          : `  ${String(key).toUpperCase()} // ${text(value)}`),
    ]) : [
      "DECODED APPLICATION FIELDS // UNAVAILABLE",
      "NOTE // PORT AND TRANSPORT TUPLE ALONE DO NOT IDENTIFY APPLICATION ACTIVITY",
    ]),
    "", `CAPSULE // ${text(payload?.evidenceId)} // LOCAL PREPARATION ONLY`,
    "CLOUD // NOT YET DISCLOSED // EXPLICIT CONFIRMATION REQUIRED",
    `BOUNDARY // ${text(payload?.boundary)}`,
    `PROMPT // ${text(payload?.suggestedQuestion)}`,
  ];
  return lines.join("\n");
}
