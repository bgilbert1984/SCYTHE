import { GRAPHOPS_PROTOCOL_VERSION, validateDirectiveRequest } from "./directiveProtocol.js";

export class SelectionModel {
  constructor() {
    this.items = [];
    this.revision = 0;
  }

  replace(item) {
    if (!item || !["rf-cell", "graph-node", "event"].includes(item.kind)) throw new TypeError("A typed selection is required");
    this.items = [Object.freeze({ ...item })];
    this.revision += 1;
    return this.items[0];
  }

  upsert(item) {
    if (!item || !["rf-cell", "graph-node", "event"].includes(item.kind)) throw new TypeError("A typed selection is required");
    this.items = [...this.items.filter((candidate) => candidate.kind !== item.kind), Object.freeze({ ...item })];
    this.revision += 1;
    return item;
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
