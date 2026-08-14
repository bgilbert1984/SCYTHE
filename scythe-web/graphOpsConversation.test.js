import assert from "node:assert/strict";
import test from "node:test";

import {askGraphOps, askGraphOpsCloudFullFidelity, formatCloudFullFidelityConversation,
  formatGraphOpsConversation, operatorQuestionOnly} from "./graphOpsConversation.js";

const payload = {status: "completed", bounded: true, modelAuthority: "INTERPRETIVE_ONLY",
  ollamaRoute: "LOCAL_FALLBACK", maxSteps: 1,
  question: "What changed?", selection: {kind: "graph-node", entityId: "host:a", graphRevision: "graph-1"},
  boundary: "OLLAMA INTERPRETS; IT DOES NOT EXECUTE DIRECTIVES", result: {model: "gemma3:1b", confidence: .7,
    report: {situation: "One host changed", assessment: "Evidence is sparse", direction: "Measure again"}}};

test("conversation sends only a pinned selection reference and read-only mode", async () => {
  let request; const controller = new AbortController();
  const fetchImpl = async (url, init) => { request = {url, init};
    return new Response(JSON.stringify(payload), {status: 200}); };
  await askGraphOps("What changed?", {kind: "graph-node", entityId: "host:a",
    graphRevision: "graph-1", enrichment: {unsafe: true}}, {fetchImpl, signal: controller.signal});
  assert.equal(request.url, "/api/graphops/conversation");
  assert.deepEqual(JSON.parse(request.init.body), {mode: "ask", question: "What changed?", maxSteps: 3,
    selection: {kind: "graph-node", entityId: "host:a", graphRevision: "graph-1"}});
  assert.equal(request.init.signal, controller.signal);
});

test("rendered host-trace transcripts collapse to the operator prompt", () => {
  assert.equal(operatorQuestionOnly("GRAPHOPS // HOST TRACE // COMPLETED\nTARGET // 1.1.1.1\n" +
    "PROMPT // Explain route anomalies and identify a falsifier."),
  "Explain route anomalies and identify a falsifier.");
  assert.equal(operatorQuestionOnly("What changed?"), "What changed?");
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

test("full-fidelity Cloud sends only an evidence reference after explicit acknowledgement", async () => {
  let request;
  const cloudPayload = {...payload, mode: "cloud-full-fidelity",
    ollamaRoute: "OLLAMA_CLOUD_FULL_FIDELITY", directiveExecution: false,
    evidenceId: "trace-1", result: {model: "gpt-oss:20b", report: {
      situation: "Measured route", anomalies: "One RTT spike",
      measuredVsInferred: "RTT measured; GeoIP inferred", assessment: "Bounded",
      falsifier: "Repeat trace", direction: "Measure again", confidence: .6,
      validationConstraints: ["GEOIP_UNCORROBORATED_CONFIDENCE_CEILING_0.60"]}},
    disclosureReceipt: {route: "OLLAMA_CLOUD_FULL_FIDELITY", capsuleId: "ffc-1",
      capsuleSha256: "a".repeat(64), destination: "OLLAMA_CLOUD", model: "gpt-oss:20b",
      disclosed: {exactIpAddresses: 4, exactLocations: 2, incidentEdges: 3, memberNodes: 0},
      excluded: ["CREDENTIALS"]}};
  const fetchImpl = async (url, init) => { request = {url, init};
    return new Response(JSON.stringify(cloudPayload), {status: 200}); };
  await assert.rejects(() => askGraphOpsCloudFullFidelity("Explain", payload.selection, "trace-1",
    {fetchImpl}), /not acknowledged/);
  await askGraphOpsCloudFullFidelity("Explain", {...payload.selection, enrichment: {unsafe: true}},
    "trace-1", {fetchImpl, acknowledgeExactDisclosure: true});
  assert.equal(request.url, "/api/graphops/conversation/cloud-full-fidelity");
  assert.deepEqual(JSON.parse(request.init.body), {mode: "cloud-full-fidelity", question: "Explain",
    evidenceId: "trace-1", acknowledgeExactDisclosure: true, selection: payload.selection});
  assert.doesNotMatch(request.init.body, /unsafe/);

  const output = formatCloudFullFidelityConversation(cloudPayload);
  assert.match(output, /FULL-FIDELITY DISCLOSURE RECEIPT/);
  assert.match(output, /4 EXACT IPs/);
  assert.match(output, /SHA-256 \/\/ a{64}/);
  assert.match(output, /MEASURED VS INFERRED/);
  assert.match(output, /VALIDATION \/\/ GEOIP_UNCORROBORATED/);
});
