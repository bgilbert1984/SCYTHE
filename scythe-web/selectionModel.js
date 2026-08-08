import { GRAPHOPS_PROTOCOL_VERSION, validateDirectiveRequest } from "./directiveProtocol.js";

export class SelectionModel {
  constructor() {
    this.items = [];
    this.revision = 0;
  }

  replace(item) {
    if (!item || !["rf-cell", "lunar-location", "graph-node", "graph-edge", "event", "time-pin"].includes(item.kind)) throw new TypeError("A typed selection is required");
    this.items = [Object.freeze({ ...item })];
    this.revision += 1;
    return this.items[0];
  }

  upsert(item) {
    if (!item || !["rf-cell", "lunar-location", "graph-node", "graph-edge", "event", "time-pin"].includes(item.kind)) throw new TypeError("A typed selection is required");
    if (item.kind === "time-pin") {
      const pins = [...this.items.filter((candidate) => candidate.kind === "time-pin"), Object.freeze({...item})].slice(-2);
      this.items = [...this.items.filter((candidate) => candidate.kind !== "time-pin"), ...pins];
    } else {
      const graphKinds = new Set(["graph-node", "graph-edge", "event"]);
      this.items = [...this.items.filter((candidate) => ["rf-cell", "lunar-location"].includes(item.kind)
        ? candidate.kind !== item.kind : !graphKinds.has(candidate.kind)), Object.freeze({ ...item })];
    }
    this.revision += 1;
    return item;
  }

  clear(kind) {
    this.items = this.items.filter((item) => item.kind !== kind);
    this.revision += 1;
  }

  directiveRequest({ directive = "explain.coverage-cell", utterance = "", parameters = {}, mode = "preview" } = {}) {
    if (!this.items.length) throw new Error("No selection is active");
    const directiveId = `dir-${Date.now()}-${this.revision}`;
    return validateDirectiveRequest({
      protocolVersion: GRAPHOPS_PROTOCOL_VERSION, directiveId, directive, utterance,
      selection: this.items.map((item) => ({ ...item })), parameters,
      requestedMode: mode, idempotencyKey: `${directiveId}:${mode}`,
    });
  }
}
