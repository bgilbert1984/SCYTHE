const GRAPH_KINDS = new Set(["graph-node", "graph-edge", "event"]);

function missingLabels({hasGraph, hasRf, pinCount}) {
  return {
    graph: hasGraph ? [] : ["graph entity"],
    rfGraph: [...(hasRf ? [] : ["RF cell"]), ...(hasGraph ? [] : ["graph entity"])],
    time: pinCount === 2 ? [] : ["two UTC time pins"],
    causal: [...(hasRf ? [] : ["RF cell"]), ...(hasGraph ? [] : ["graph entity"]),
      ...(pinCount === 2 ? [] : ["two UTC time pins"])],
  };
}

export function deriveContextualActions(selections = []) {
  const kinds = new Set(selections.map((item) => item.kind));
  const hasGraph = selections.some((item) => GRAPH_KINDS.has(item.kind));
  const hasRf = kinds.has("rf-cell");
  const pinCount = selections.filter((item) => item.kind === "time-pin").length;
  const missing = missingLabels({hasGraph, hasRf, pinCount});
  const action = (id, label, group, resultView, required) => Object.freeze({
    id, label, group, resultView, enabled: required.length === 0,
    missing: Object.freeze(required),
  });
  return Object.freeze([
    action("trace.provenance-impact", "TRACE PROVENANCE", "TRACE", "provenance", missing.graph),
    action("expose.contradictions", "EXPOSE CONTRADICTIONS", "TEST", "contradictions", missing.graph),
    action("correlate.rf-cell-graph", "CORRELATE RF ↔ GRAPH", "COMPARE", null, missing.rfGraph),
    action("compare.graph-delta", "GRAPH_DELTA", "COMPARE", "temporal", missing.time),
    action("compare.causal-worlds", "COMPARE CAUSAL WORLDS", "TEST", "causal-worlds", missing.causal),
  ]);
}

export class InvestigationContext {
  constructor({selectionModel, store = null} = {}) {
    if (!selectionModel) throw new TypeError("InvestigationContext requires a SelectionModel");
    this.selectionModel = selectionModel; this.store = store; this.listeners = new Set();
    this.state = {selections: [], graphRevision: null, actions: deriveContextualActions(),
      lastPlan: null, viewIntent: null};
  }

  refresh(graphRevision = null) {
    const selections = this.selectionModel.items.map((item) => ({...item}));
    this.state = {...this.state, selections,
      graphRevision: graphRevision ?? this.state.graphRevision,
      actions: deriveContextualActions(selections)};
    this.store?.captureSelections(selections, this.state.graphRevision);
    return this.#publish();
  }

  recordPlan(plan, viewIntent = null) {
    this.store?.recordPlan(plan);
    this.state = {...this.state, lastPlan: {planId: plan.planId, directiveId: plan.directiveId,
      status: plan.status, evidencePosture: plan.evidencePosture}, viewIntent};
    return this.#publish();
  }

  subscribe(listener) {
    if (typeof listener !== "function") throw new TypeError("context listener must be a function");
    this.listeners.add(listener); listener(this.snapshot());
    return () => this.listeners.delete(listener);
  }

  snapshot() { return JSON.parse(JSON.stringify(this.state)); }

  #publish() {
    const snapshot = this.snapshot();
    for (const listener of this.listeners) listener(snapshot);
    return snapshot;
  }
}
