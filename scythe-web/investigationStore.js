function copy(value) {
  return value == null ? value : JSON.parse(JSON.stringify(value));
}

export class InvestigationStore {
  constructor({storage = null, key = "scythe.graphops.investigation.v1", maxPlans = 32} = {}) {
    this.storage = storage; this.key = key; this.maxPlans = Math.min(Math.max(maxPlans, 1), 128);
    this.listeners = new Set();
    this.state = {version: 1, investigationId: null, selections: [], graphRevision: null,
      plans: [], worldStack: null, updatedAt: null};
    this.#load();
  }

  #load() {
    if (!this.storage) return;
    try {
      const value = JSON.parse(this.storage.getItem(this.key));
      if (value?.version === 1 && Array.isArray(value.selections) && Array.isArray(value.plans)) {
        this.state = {...this.state, ...value, plans: value.plans.slice(-this.maxPlans)};
      }
    } catch { /* corrupted browser state is ignored, never executed */ }
  }

  #commit() {
    this.state.updatedAt = new Date().toISOString();
    try { this.storage?.setItem(this.key, JSON.stringify(this.state)); } catch { /* storage is advisory */ }
    const snapshot = this.snapshot();
    for (const listener of this.listeners) listener(snapshot);
    return snapshot;
  }

  subscribe(listener) {
    this.listeners.add(listener); listener(this.snapshot());
    return () => this.listeners.delete(listener);
  }

  captureSelections(selections, graphRevision = null) {
    this.state.selections = copy(selections).slice(0, 16);
    this.state.graphRevision = graphRevision ?? this.state.graphRevision;
    return this.#commit();
  }

  recordPlan(plan) {
    const record = {planId: plan.planId, directiveId: plan.directiveId, status: plan.status,
      summary: plan.summary, evidencePosture: plan.evidencePosture, expiresAt: plan.expiresAt};
    this.state.plans = [...this.state.plans.filter((item) => item.planId !== record.planId), record]
      .slice(-this.maxPlans);
    return this.#commit();
  }

  replaceWorldStack(worldStack) {
    this.state.worldStack = copy(worldStack);
    this.state.investigationId = worldStack?.investigationId ?? this.state.investigationId;
    return this.#commit();
  }

  snapshot() { return copy(this.state); }
}
