export class InfrastructureController {
  constructor({apiBase = "", fetchImpl = globalThis.fetch, refreshMilliseconds = 7000} = {}) {
    this.apiBase = apiBase; this.fetchImpl = fetchImpl;
    this.refreshMilliseconds = Math.max(1000, Number(refreshMilliseconds) || 7000);
    this.listeners = new Set(); this.snapshot = null; this.focusId = "";
    this.since = null; this.until = null;
    this.running = false; this.timer = null;
  }
  subscribe(listener) {
    if (typeof listener !== "function") throw new TypeError("infrastructure listener must be a function");
    this.listeners.add(listener); if (this.snapshot) listener({available: true, snapshot: this.snapshot});
    return () => this.listeners.delete(listener);
  }
  async start() {
    if (this.running) return this; this.running = true; await this.refresh(); this.#schedule(); return this;
  }
  #schedule() {
    clearTimeout(this.timer);
    if (this.running) this.timer = setTimeout(async () => { try { await this.refresh(); } finally { this.#schedule(); } }, this.refreshMilliseconds);
  }
  async refresh() {
    const query = new URLSearchParams({node_limit: "500", edge_limit: "1000"});
    if (this.focusId) query.set("focus_id", this.focusId);
    if (Number.isFinite(this.since)) query.set("since", String(this.since));
    if (Number.isFinite(this.until)) query.set("until", String(this.until));
    try {
      const response = await this.fetchImpl.call(globalThis,
        `${this.apiBase}/api/graphops/infrastructure/snapshot?${query}`,
        {credentials: "same-origin", cache: "no-store"});
      const snapshot = await response.json();
      if (!response.ok || !["ok", "empty"].includes(snapshot.status)) throw new Error(snapshot.message || `HTTP ${response.status}`);
      this.snapshot = snapshot; this.#publish({available: true, snapshot}); return snapshot;
    } catch (error) { this.#publish({available: false, snapshot: this.snapshot, error}); return this.snapshot; }
  }
  setFocus(entityId) {
    const next = String(entityId ?? "").slice(0, 256); if (next === this.focusId) return false;
    this.focusId = next; if (this.running) void this.refresh(); return true;
  }
  setWindow(since, until) {
    const nextSince = Number(since), nextUntil = Number(until);
    if (!Number.isFinite(nextSince) || !Number.isFinite(nextUntil) || nextUntil <= nextSince ||
        nextUntil - nextSince > 7 * 24 * 60 * 60) return false;
    if (nextSince === this.since && nextUntil === this.until) return true;
    this.since = nextSince; this.until = nextUntil;
    if (this.running) void this.refresh(); return true;
  }
  #publish(update) { for (const listener of this.listeners) listener(update); }
  destroy() { this.running = false; clearTimeout(this.timer); this.listeners.clear(); }
}
