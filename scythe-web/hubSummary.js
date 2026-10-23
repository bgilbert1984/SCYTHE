/**
 * What the hub can say about an instance and its missions, from what the orchestrator and the instance report.
 *
 * Nothing here is inferred. A field the instance did not report is "unknown" or null and is shown as such; it is never filled
 * with a default that looks healthy. Shapes follow GET /api/scythe/instances (scythe_orchestrator.py), whose `authority` is the
 * instance's GET /api/authority/state, and GET /api/missions.
 */

export const STATE_MARK = Object.freeze({ok: "✔", down: "✖", locked: "🔒", pending: "…", stale: "!", unknown: "?"});

const STALE_AFTER_SECONDS = 30;    // the orchestrator checks each instance every 5 s
const RECENT_MISSIONS = 5;

const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const count = (value) => (Number.isFinite(value) && value >= 0 ? value : null);

/** Seconds since an ISO time, or null when it is missing or unreadable. */
export function ageSeconds(iso, now) {
  const then = Date.parse(iso);
  return Number.isFinite(then) ? Math.max(0, Math.round((now - then) / 1000)) : null;
}

/** Counts of nodes, edges, sessions and behavior groups: the authority state if reported, else the instance info, else null each. */
export function instanceCounts(instance) {
  const authority = instance?.authority?.authoritative_state;
  if (isObject(authority)) {
    return {nodes: count(authority.nodes), edges: count(authority.edges), sessions: count(authority.sessions), bsgs: count(authority.bsgs), source: "authority"};
  }
  const info = instance?.info;
  if (isObject(info) && Object.keys(info).length) {
    return {nodes: count(info.node_count), edges: count(info.edge_count), sessions: count(info.session_count), bsgs: count(info.behavior_groups), source: "info"};
  }
  return {nodes: null, edges: null, sessions: null, bsgs: null, source: null};
}

const flag = (value, okDetail, badDetail) =>
  typeof value === "boolean" ? {state: value ? "ok" : "down", detail: value ? okDetail : badDetail} : {state: "unknown", detail: "not reported"};

/** One row per thing the instance reports about itself. A state is ok, down, locked, pending, stale or unknown. */
export function instanceHealth(instance, now = Date.now()) {
  const authority = isObject(instance?.authority) ? instance.authority : {};
  const health = isObject(authority.health) ? authority.health : {};
  const rows = [];
  const status = instance?.status;
  rows.push({key: "instance", label: "Instance", ...(status === "running" ? {state: "ok", detail: "running"}
    : status === "dead" ? {state: "down", detail: "process exited"}
    : status ? {state: "pending", detail: String(status)} : {state: "unknown", detail: "not reported"})});
  rows.push({key: "live", label: "Live updates", ...(typeof instance?.socket_io_ready === "boolean"
    ? (instance.socket_io_ready ? {state: "ok", detail: "socket ready"} : {state: "pending", detail: "socket not ready"})
    : {state: "unknown", detail: "not reported"})});
  rows.push({key: "graph", label: "Graph engine", ...flag(health.graph_engine, "present", "absent")});
  rows.push({key: "authority", label: "Authority store", ...(typeof health.authority === "string" && health.authority
    ? {state: "ok", detail: health.authority} : {state: "unknown", detail: "not reported"})});
  rows.push({key: "llm", label: "LLM", ...flag(health.llm, "available", "unavailable")});
  const write = authority.write_access;
  rows.push({key: "writes", label: "Writes", ...(isObject(write) && typeof write.enabled === "boolean"
    ? (write.enabled ? {state: "ok", detail: "an operator session exists"} : {state: "locked", detail: write.reason || "no authenticated operator"})
    : {state: "unknown", detail: "not reported"})});
  const age = ageSeconds(instance?.last_health, now);
  rows.push({key: "checked", label: "Health check", ...(age === null ? {state: "unknown", detail: "never"}
    : age > STALE_AFTER_SECONDS ? {state: "stale", detail: `${age}s ago`} : {state: "ok", detail: `${age}s ago`})});
  return rows;
}

/** Open, closed and other missions, and the newest few. Rows that are not objects with an id are skipped, not counted. */
export function summarizeMissions(missions) {
  const rows = (Array.isArray(missions) ? missions : []).filter((row) => isObject(row) && typeof row.mission_id === "string" && row.mission_id);
  const total = {total: rows.length, open: 0, closed: 0, other: 0};
  for (const row of rows) {
    if (row.status === "open") total.open++;
    else if (row.status === "closed") total.closed++;
    else total.other++;
  }
  const stamp = (row) => Number(row.updated_at ?? row.created_at) || 0;
  const recent = [...rows].sort((a, b) => stamp(b) - stamp(a)).slice(0, RECENT_MISSIONS)
    .map((row) => ({id: row.mission_id, name: row.name || row.mission_id, status: row.status || "unknown", updatedAt: stamp(row) || null}));
  return {...total, recent};
}

/** "3 nodes · 12 edges …", with "not reported" for what the instance did not say. */
export function countsLine(counts) {
  const part = (value, noun) => (value === null ? `${noun}: not reported` : `${value} ${noun}`);
  return [part(counts.nodes, "nodes"), part(counts.edges, "edges"), part(counts.sessions, "sessions"), part(counts.bsgs, "BSGs")].join(" · ");
}
