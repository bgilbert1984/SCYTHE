import { GRAPHOPS_PROTOCOL_VERSION, validateDirectiveRequest } from "./directiveProtocol.js";

export class SelectionModel {
  constructor() {
    this.items = [];
    this.revision = 0;
  }

  replace(item) {
    if (!item || item.kind !== "rf-cell") throw new TypeError("A typed rf-cell selection is required");
    this.items = [Object.freeze({ ...item })];
    this.revision += 1;
    return this.items[0];
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
