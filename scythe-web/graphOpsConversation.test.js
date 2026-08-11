import assert from "node:assert/strict";
import test from "node:test";

import {askGraphOps, formatGraphOpsConversation} from "./graphOpsConversation.js";

const payload = {status: "completed", bounded: true, modelAuthority: "INTERPRETIVE_ONLY",
  ollamaRoute: "LOCAL_FALLBACK", maxSteps: 1,
  question: "What changed?", selection: {kind: "graph-node", entityId: "host:a", graphRevision: "graph-1"},
  boundary: "OLLAMA INTERPRETS; IT DOES NOT EXECUTE DIRECTIVES", result: {model: "gemma3:1b", confidence: .7,
    report: {situation: "One host changed", assessment: "Evidence is sparse", direction: "Measure again"}}};

test("conversation sends only a pinned selection reference and read-only mode", async () => {
  let request;
  const fetchImpl = async (url, init) => { request = {url, init};
    return new Response(JSON.stringify(payload), {status: 200}); };
  await askGraphOps("What changed?", {kind: "graph-node", entityId: "host:a",
    graphRevision: "graph-1", enrichment: {unsafe: true}}, {fetchImpl});
  assert.equal(request.url, "/api/graphops/conversation");
  assert.deepEqual(JSON.parse(request.init.body), {mode: "ask", question: "What changed?", maxSteps: 3,
    selection: {kind: "graph-node", entityId: "host:a", graphRevision: "graph-1"}});
});

test("conversation rendering includes tooltip context and epistemic boundary", () => {
  const output = formatGraphOpsConversation(payload, {entityContext: "NETWORK // INFERRED · LOCAL DB"});
  assert.match(output, /GRAPHOPS CONVERSATION \/\/ COMPLETED \/\/ OLLAMA/);
  assert.match(output, /NETWORK \/\/ INFERRED/);
  assert.match(output, /OLLAMA ROUTE \/\/ LOCAL_FALLBACK/);
  assert.match(output, /REASONING BUDGET \/\/ 1 BOUNDED STEP/);
  assert.match(output, /SELECTION PIN \/\/ ORIGINAL REVISION RETAINED/);
  assert.match(output, /ASSESSMENT \/\/ Evidence is sparse/);
  assert.match(output, /DOES NOT EXECUTE DIRECTIVES/);
});

test("conversation rejects responses that claim execution authority", async () => {
  const fetchImpl = async () => new Response(JSON.stringify({...payload,
    modelAuthority: "EXECUTIVE"}), {status: 200});
  await assert.rejects(() => askGraphOps("Why?", payload.selection, {fetchImpl}), /bounded contract/);
});
