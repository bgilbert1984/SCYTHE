import {sessionToken} from "./missionGovernance.js";
import {describeEvent, entityProvenance, evidenceCounts, missionLifecycle, tailOffset} from "./missionViewport.js";

const WINDOW = 200;     // how many of the mission's newest events the viewport reads
const RIBBON = 8;       // how many of those the ribbon shows

/**
 * The globe's mission viewport: a scene tint for the mission's lifecycle, a header with evidence counts, an event
 * ribbon, and a provenance inspector for the selected node. It only reads: the mission record, the mission's events
 * and the pending approvals. Where the server records nothing (a hash, a WriteBus entry) it says "not recorded".
 */
export class MissionViewportPanel {
  constructor({root, tint, apiBase = "", missionId = null, fetchImpl = globalThis.fetch?.bind(globalThis), storages = [],
               pollMs = 5000, setTimer = globalThis.setInterval?.bind(globalThis)} = {}) {
    Object.assign(this, {root, tint, apiBase, missionId, fetch: fetchImpl, storages});
    this.state = {mission: null, reachable: true, pending: [], events: [], total: null, selected: null};
    this.build();
    this.render();
    this.refresh();
    if (setTimer && pollMs > 0) setTimer(() => { if (!globalThis.document?.hidden) this.refresh(); }, pollMs);
  }

  el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  build() {
    this.root.textContent = "";
    this.header = this.el("div", "mvp__header");
    this.lifecycle = this.el("span", "mvp__lifecycle");
    this.mission = this.el("span", "mvp__mission");
    this.counts = this.el("span", "mvp__counts");
    this.header.append(this.lifecycle, this.mission, this.counts);
    this.ribbon = this.el("div", "mvp__ribbon");
    this.ribbon.setAttribute("aria-label", "Mission event stream");
    this.inspector = this.el("section", "mvp__inspector");
    this.inspector.hidden = true;
    this.root.append(this.header, this.ribbon, this.inspector);
  }

  async get(path, headers = {}) {
    try {
      const response = await this.fetch(`${this.apiBase}${path}`, {cache: "no-store", credentials: "same-origin", headers});
      let data = null;
      try { data = await response.json(); } catch { /* not JSON */ }
      return {status: response.status, data};
    } catch {
      return {status: -1, data: null};
    }
  }

  async refresh() {
    if (!this.missionId) return this.render();
    const id = encodeURIComponent(this.missionId);
    const token = sessionToken(...this.storages);
    const [mission, first, pending] = await Promise.all([
      this.get(`/api/missions/${id}`),
      this.get(`/api/missions/${id}/events?limit=1&include_total=1`),
      token ? this.get("/api/mcp/proposals/pending", {"X-Session-Token": token}) : Promise.resolve({status: 0})]);
    const s = this.state;
    s.reachable = mission.status === 200 || mission.status === 404;
    s.mission = mission.status === 200 ? mission.data.mission : null;
    s.pending = pending.status === 200 ? pending.data.proposals || [] : [];
    if (first.status === 200) {
      s.total = first.data.total ?? null;
      const events = await this.get(`/api/missions/${id}/events?limit=${WINDOW}&offset=${tailOffset(s.total, WINDOW)}`);
      if (events.status === 200) s.events = events.data.events || [];
    }
    this.render();
  }

  select(entityId) {
    this.state.selected = entityId || null;
    this.renderInspector();
  }

  render() {
    const s = this.state;
    const life = missionLifecycle({mission: s.mission, missionId: this.missionId, pending: s.pending, reachable: s.reachable});
    this.root.dataset.lifecycle = life.key;
    if (this.tint) this.tint.dataset.tone = life.tone;
    this.lifecycle.textContent = life.label.toUpperCase();
    this.lifecycle.dataset.tone = life.tone;
    this.lifecycle.title = life.detail;
    this.mission.textContent = this.missionId ? `MISSION ${s.mission?.name || this.missionId}` : "";
    const counts = evidenceCounts(s.events);
    this.counts.textContent = "";
    for (const key of ["observation", "correlation", "inference"]) {
      const info = describeEvent({event_type: {observation: "probe", correlation: "HYPEREDGE_CREATE", inference: "CONFIDENCE_MUTATION"}[key]}).kind;
      const badge = this.el("span", "mvp__count", `${info.glyph} ${counts[key]} ${info.label.toLowerCase()}${counts[key] === 1 ? "" : "s"}`);
      badge.title = info.note;
      this.counts.append(badge);
    }
    this.renderRibbon();
    this.renderInspector();
  }

  rowFor(row) {
    const node = this.el("span", "mvp__event");
    node.dataset.kind = row.kind.key;
    node.title = `${row.kind.label}: ${row.kind.note}`;
    node.textContent = `${row.kind.glyph} ${row.time} ${row.type}${row.entityId ? ` ${row.entityId}` : ""}`;
    return node;
  }

  renderRibbon() {
    this.ribbon.textContent = "";
    const rows = this.state.events.slice(-RIBBON).map(describeEvent);
    this.ribbon.hidden = !this.missionId;
    if (!rows.length) {
      this.ribbon.append(this.el("span", "mvp__empty", this.missionId ? "No events recorded for this mission." : ""));
      return;
    }
    for (const row of rows) this.ribbon.append(this.rowFor(row));
  }

  renderInspector() {
    const {selected, events, total} = this.state;
    this.inspector.hidden = !selected;
    this.inspector.textContent = "";
    if (!selected) return;
    this.inspector.append(this.el("h3", "mvp__heading", `PROVENANCE · ${selected}`));
    this.inspector.append(this.el("div", "mvp__line", `Mission: ${this.missionId || "none selected"}`));
    const rows = this.missionId ? entityProvenance(events, selected) : [];
    if (!rows.length) {
      this.inspector.append(this.el("div", "mvp__empty",
        this.missionId ? "No events for this entity in the mission's recent events." : "Open with ?mission=<id> to trace events."));
      return;
    }
    if (total > events.length) {
      this.inspector.append(this.el("div", "mvp__line", `Showing the newest ${events.length} of ${total} mission events.`));
    }
    for (const row of rows) {
      const item = this.el("div", "mvp__trace");
      item.dataset.kind = row.kind.key;
      item.append(this.rowFor(row));
      item.append(this.el("div", "mvp__line",
        `seq ${row.seq ?? "?"} · session ${row.sessionId || "?"} · hash ${row.hash || "not recorded"} · WriteBus ${row.writebusEntry || "not recorded"}`));
      this.inspector.append(item);
    }
  }
}
