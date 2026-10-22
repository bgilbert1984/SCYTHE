/** Pure helpers for the globe's mission viewport: what the mission, its events and a selected node say, without inventing state. */

/**
 * The scene tint for where the mission is. Only states the server records are reported: the mission record is
 * open or closed, and the approval lifecycle may hold a command for a second operator. There is no Draft,
 * Collecting or Frozen state to show, so none is shown.
 */
export function missionLifecycle({mission, missionId, pending = [], reachable = true} = {}) {
  if (!missionId) return {key: "none", label: "No mission", tone: "gray", detail: "Open this page with ?mission=<id>"};
  if (!reachable) return {key: "unknown", label: "Unavailable", tone: "gray", detail: "The mission record could not be read"};
  if (!mission) return {key: "unknown", label: "Unknown mission", tone: "gray", detail: `No mission ${missionId}`};
  if (mission.status === "closed") return {key: "archived", label: "Archived", tone: "purple", detail: "Mission closed"};
  const waiting = pending.filter(item => item?.approval_mode === "SECOND_PRINCIPAL");
  if (waiting.length) {
    return {key: "awaiting", label: "Awaiting second operator", tone: "yellow",
      detail: `${waiting.length} command${waiting.length === 1 ? "" : "s"} waiting for approval`};
  }
  if (mission.status === "open") return {key: "active", label: "Active", tone: "green", detail: "Mission open"};
  return {key: "unknown", label: String(mission.status || "Unknown"), tone: "gray", detail: "Unrecognised mission status"};
}

const KINDS = {
  observation: {glyph: "●", label: "Observation", note: "Something a sensor or session reported"},
  graph: {glyph: "○", label: "Graph write", note: "A change to the graph; it does not say what was observed"},
  correlation: {glyph: "⬢", label: "Correlation", note: "SCYTHE grouped entities"},
  inference: {glyph: "△", label: "Inference", note: "SCYTHE changed what it believes"},
  unclassified: {glyph: "·", label: "Unclassified", note: "Event type not mapped to an evidence class"},
};

const EVENT_KINDS = {
  probe: "observation", flow: "observation", test_flow: "observation", rf_uav_detection: "observation",
  tls_session: "observation", tcp_syn: "observation", tcp_rst: "observation", dns_exchange: "observation",
  "entity.move": "observation",
  hyperedge_create: "correlation",
  confidence_mutation: "inference", intent_update: "inference",
};

/** Which evidence class an event belongs to, so "what happened" is never drawn like "what SCYTHE thinks". */
export function classifyEvent(eventType) {
  const type = String(eventType || "").toLowerCase();
  let key = EVENT_KINDS[type];
  if (!key && /^(node|edge|entity)_/.test(type)) key = "graph";
  if (!key && type === "add_edge") key = "graph";
  return {key: key || "unclassified", ...KINDS[key || "unclassified"]};
}

const clock = ms => {
  const date = new Date(Number(ms));
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(11, 19) : "--:--:--";
};

/** One ribbon / provenance row for an event. Hash and WriteBus entry appear only if the event itself carries them. */
export function describeEvent(event) {
  const kind = classifyEvent(event?.event_type);
  const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
  const hash = payload.sha256 || payload.hash || payload.payload_hash || null;
  return {
    seq: event?.seq ?? null, time: clock(event?.timestamp), type: event?.event_type || "unknown",
    entityId: event?.entity_id || null, sessionId: event?.session_id || null, kind,
    hash, writebusEntry: payload.writebus_entry || payload.write_id || null,
  };
}

/** The mission's events that touch one entity, oldest first, as provenance rows. */
export function entityProvenance(events, entityId) {
  return (events || []).filter(event => event?.entity_id === entityId).map(describeEvent);
}

/** Counts by evidence class, for the header. */
export function evidenceCounts(events) {
  const counts = {observation: 0, graph: 0, correlation: 0, inference: 0, unclassified: 0};
  for (const event of events || []) counts[classifyEvent(event?.event_type).key] += 1;
  return counts;
}

/** The offset that reads the last `count` events of a mission whose total is known. */
export function tailOffset(total, count) {
  return Math.max(0, (Number(total) || 0) - count);
}
