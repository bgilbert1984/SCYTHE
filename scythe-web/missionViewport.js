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

/** Shader codes for the globe's per-node semantic attribute; 0 draws as it always did. */
export const NODE_SEMANTIC_CODE = {unclassified: 0, observation: 1, inference: 2, correlation: 3};

/**
 * What kind of thing a node is, from what the server recorded about it: its entity kind, how its position was
 * resolved (node_geo_index.method) and the mission events that touch it. Nothing is guessed: a node with no such
 * evidence is unclassified. `basis` says which fact decided it.
 */
export function classifyNode({kind, method, events = []} = {}) {
  const observed = events.filter(event => classifyEvent(event?.event_type).key === "observation").length;
  const resolution = String(method || "").toLowerCase();
  let key = "unclassified";
  let basis = "no evidence or position method recorded";
  if (/hyperedge/i.test(String(kind || ""))) {
    key = "correlation"; basis = "hyperedge entity";
  } else if (observed) {
    key = "observation"; basis = `${observed} observation event${observed === 1 ? "" : "s"}`;
  } else if (resolution === "observed") {
    key = "observation"; basis = "position observed";
  } else if (resolution) {
    key = "inference"; basis = `position inferred (${resolution})`;
  }
  return {key, basis, code: NODE_SEMANTIC_CODE[key], ...KINDS[key]};
}

/** Count nodes by class, for the header. */
export function nodeClassCounts(classes) {
  const counts = {observation: 0, inference: 0, correlation: 0, unclassified: 0};
  for (const item of classes) counts[item.key] += 1;
  return counts;
}

/** Group events by entity once, so classifying every node is not a scan of all events per node. */
export function eventsByEntity(events) {
  const grouped = new Map();
  for (const event of events || []) {
    if (!event?.entity_id) continue;
    if (!grouped.has(event.entity_id)) grouped.set(event.entity_id, []);
    grouped.get(event.entity_id).push(event);
  }
  return grouped;
}

/** The time span of the loaded events (unix ms), or null when there is nothing to scrub between. */
export function timelineBounds(events) {
  const times = (events || []).map(event => Number(event?.timestamp)).filter(Number.isFinite);
  if (!times.length) return null;
  const start = Math.min(...times);
  const end = Math.max(...times);
  return end > start ? {start, end} : null;
}

/** The events recorded at or before `at` (unix ms); null means live: all of them. */
export function eventsUpTo(events, at) {
  if (at == null) return events || [];
  return (events || []).filter(event => Number(event?.timestamp) <= at);
}

/**
 * Nodes that did not exist yet at `at`: those whose creation event is later. Only a creation event in the loaded
 * window can hide a node, because a node first seen mid-window may simply have older events that were not loaded.
 */
export function nodesNotYetCreated(events, at) {
  const hidden = new Set();
  if (at == null) return hidden;
  const created = new Map();
  for (const event of events || []) {
    if (event?.event_type !== "NODE_CREATE" || !event.entity_id) continue;
    const time = Number(event.timestamp);
    if (Number.isFinite(time) && !(created.get(event.entity_id) <= time)) created.set(event.entity_id, time);
  }
  for (const [id, time] of created) if (time > at) hidden.add(id);
  return hidden;
}

/**
 * Nodes the server had deleted at `at`: those whose newest node event up to `at` is a NODE_DELETE. A later create or
 * update brings a node back, as it does on the live page. Only deletes in the loaded window can hide a node.
 */
export function nodesDeletedBy(events, at) {
  const hidden = new Set();
  if (at == null) return hidden;
  const history = new Map();
  for (const event of events || []) {
    const id = event?.entity_id;
    if (!id || !/^NODE_(CREATE|UPDATE|DELETE)$/.test(event.event_type || "")) continue;
    const time = Number(event.timestamp);
    if (!Number.isFinite(time) || time > at) continue;
    if (!history.has(id)) history.set(id, []);
    history.get(id).push({type: event.event_type, time, seq: Number(event.seq) || 0});
  }
  for (const [id, entries] of history) {
    entries.sort((a, b) => a.time - b.time || a.seq - b.seq);
    if (entries[entries.length - 1].type === "NODE_DELETE") hidden.add(id);
  }
  return hidden;
}

/** The scrubber's clock label: UTC time of day, with the date when the span crosses midnight. */
export function scrubLabel(at, bounds) {
  const iso = new Date(Number(at)).toISOString();
  const crosses = bounds && new Date(bounds.start).toISOString().slice(0, 10) !== new Date(bounds.end).toISOString().slice(0, 10);
  return crosses ? `${iso.slice(0, 10)} ${iso.slice(11, 19)}Z` : `${iso.slice(11, 19)}Z`;
}

/** The two ends of an edge event, from its payload, as the server's edge cache reads them; null when it names none. */
export function edgeEndpoints(event) {
  const payload = event?.payload && typeof event.payload === "object" ? event.payload : {};
  const nodes = Array.isArray(payload.nodes) ? payload.nodes : [];
  const src = payload.src || payload.source || nodes[0];
  const dst = payload.dst || payload.target || (nodes.length > 1 ? nodes[nodes.length - 1] : undefined);
  return src && dst ? {src: String(src), dst: String(dst)} : null;
}

/** How an edge event was tied to a drawn edge. */
export const MATCH_MODE = Object.freeze({ID: "ID", ENDPOINTS: "ENDPOINTS", UNMATCHED: "UNMATCHED"});

/**
 * Which rendered edge each edge event is about, and how that was decided:
 *   ID         the event's entity_id is the id of a drawn edge;
 *   ENDPOINTS  it is not, but the payload names the same two ends as exactly one drawn edge;
 *   UNMATCHED  neither. Two drawn edges with the same ends are ambiguous and are never matched by their ends.
 * `matches` lists every edge event with its mode, `resolved` groups the matched events by drawn edge id, and `identity`
 * counts the modes, so a mismatch is visible rather than silent.
 */
export function resolveEdgeEvents(events, edges = new Map()) {
  const byEnds = new Map();
  for (const [id, edge] of edges) {
    if (!edge?.src || !edge?.dst) continue;
    const key = `${edge.src}\u0000${edge.dst}`;
    byEnds.set(key, byEnds.has(key) ? null : id);
  }
  const matches = [];
  const resolved = new Map();
  const identity = {byId: 0, byEndpoints: 0, unmatched: 0};
  for (const event of events || []) {
    if (!EDGE_EVENTS.has(event?.event_type) || !event.entity_id) continue;
    let edgeId = event.entity_id;
    let mode = MATCH_MODE.ID;
    if (!edges.has(edgeId)) {
      const ends = edgeEndpoints(event);
      const match = ends && byEnds.get(`${ends.src}\u0000${ends.dst}`);
      if (match) { edgeId = match; mode = MATCH_MODE.ENDPOINTS; } else mode = MATCH_MODE.UNMATCHED;
    }
    matches.push({event, edgeId: mode === MATCH_MODE.UNMATCHED ? null : edgeId, mode});
    if (mode === MATCH_MODE.ID) identity.byId += 1;
    else if (mode === MATCH_MODE.ENDPOINTS) identity.byEndpoints += 1;
    else identity.unmatched += 1;
    // An event with no matching drawn edge is still kept under its own id: with no drawn edge list at all, that is
    // the only identity there is, and an unmatched id hides nothing because no drawn edge carries it.
    const key = mode === MATCH_MODE.UNMATCHED ? event.entity_id : edgeId;
    if (!resolved.has(key)) resolved.set(key, []);
    resolved.get(key).push(event);
  }
  return {matches, resolved, identity};
}

const EDGE_EVENTS = new Set(["EDGE_CREATE", "EDGE_UPDATE", "EDGE_UPSERT", "EDGE_DELETE"]);

/**
 * Links that did not exist at `at`, so the graph is never drawn with an edge to a node that is hidden.
 * - an edge or hyperedge touching a node that is not yet created is hidden (it could not exist without it);
 * - an edge whose creation event in the loaded window is later is hidden;
 * - an edge whose newest event up to `at` is a delete is hidden.
 * Anything the events do not speak to stays visible: absence of evidence removes nothing.
 * `edges` maps edge id to {src, dst}; `hyperedges` maps hyperedge id to {members}.
 */
export function linksNotPresent({events, at, edges = new Map(), hyperedges = new Map(), hiddenNodes = new Set()} = {}) {
  const {resolved, identity} = resolveEdgeEvents(events, edges);
  const result = {edges: new Set(), hyperedges: new Set(), identity};
  if (at == null) return result;
  const history = new Map();
  for (const [id, group] of resolved) {
    for (const event of group) {
      const time = Number(event.timestamp);
      if (!Number.isFinite(time)) continue;
      if (!history.has(id)) history.set(id, []);
      history.get(id).push({type: event.event_type, time, seq: Number(event.seq) || 0});
    }
  }
  for (const [id, entries] of history) {
    if (edges.size && !edges.has(id)) continue;     // an unmatched event names no drawn edge: it hides nothing
    entries.sort((a, b) => a.time - b.time || a.seq - b.seq);
    const created = entries.find(entry => entry.type === "EDGE_CREATE");
    const latest = entries.filter(entry => entry.time <= at).pop();
    if (created && created.time > at) result.edges.add(id);
    else if (latest?.type === "EDGE_DELETE") result.edges.add(id);
  }
  for (const [id, edge] of edges) {
    if (hiddenNodes.has(edge?.src) || hiddenNodes.has(edge?.dst)) result.edges.add(id);
  }
  for (const [id, hyperedge] of hyperedges) {
    if ((hyperedge?.members || []).some(member => hiddenNodes.has(member))) result.hyperedges.add(id);
  }
  return result;
}
