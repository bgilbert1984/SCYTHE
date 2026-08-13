function text(value, fallback = "—") {
  const result = String(value ?? "").trim();
  return result || fallback;
}

export async function askGraphOps(question, selection, {
  fetchImpl = globalThis.fetch, apiBase = "", signal,
} = {}) {
  const utterance = String(question ?? "").trim();
  if (!utterance) throw new Error("Enter a GraphOps question");
  if (!selection?.entityId || !selection?.graphRevision) throw new Error("Select a graph node or edge first");
  const response = await fetchImpl.call(globalThis, `${apiBase}/api/graphops/conversation`, {
    method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, signal,
    body: JSON.stringify({mode: "ask", question: utterance, maxSteps: 3, selection: {
      kind: selection.kind, entityId: selection.entityId, graphRevision: selection.graphRevision,
    }}),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);
  if (!payload?.bounded || payload.modelAuthority !== "INTERPRETIVE_ONLY") {
    throw new Error("GraphOps conversation response violated its bounded contract");
  }
  return payload;
}

export async function askGraphOpsCloudFullFidelity(question, selection, evidenceId, {
  fetchImpl = globalThis.fetch, apiBase = "", signal, acknowledgeExactDisclosure = false,
} = {}) {
  const utterance = String(question ?? "").trim();
  if (!utterance) throw new Error("Enter a GraphOps question");
  if (!selection?.entityId || !selection?.graphRevision) throw new Error("Select a traced graph host first");
  if (!String(evidenceId ?? "").trim()) throw new Error("Trace the selected host before asking Cloud");
  if (acknowledgeExactDisclosure !== true) throw new Error("Exact Cloud disclosure was not acknowledged");
  const response = await fetchImpl.call(globalThis,
    `${apiBase}/api/graphops/conversation/cloud-full-fidelity`, {
      method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, signal,
      body: JSON.stringify({mode: "cloud-full-fidelity", question: utterance,
        evidenceId: String(evidenceId), acknowledgeExactDisclosure: true, selection: {
          kind: selection.kind, entityId: selection.entityId, graphRevision: selection.graphRevision,
        }}),
    });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);
  if (!payload?.bounded || payload.modelAuthority !== "INTERPRETIVE_ONLY" ||
      payload?.disclosureReceipt?.route !== "OLLAMA_CLOUD_FULL_FIDELITY" ||
      payload?.directiveExecution !== false) {
    throw new Error("Cloud conversation response violated its bounded disclosure contract");
  }
  return payload;
}

export function formatGraphOpsConversation(payload, {entityContext = ""} = {}) {
  const result = payload?.result ?? {};
  const report = result.report ?? {};
  const lines = [
    "GRAPHOPS CONVERSATION // COMPLETED // OLLAMA",
    `QUESTION // ${text(payload?.question)}`,
    `SELECTION // ${text(payload?.selection?.kind)}:${text(payload?.selection?.entityId)}`,
    `GRAPH REVISION // ${text(payload?.selection?.graphRevision)}`,
    `SELECTION PIN // ${payload?.selectionRebased ? `REBASED FROM ${text(payload?.requestedGraphRevision)}` : "ORIGINAL REVISION RETAINED"}`,
    `MODEL // ${text(result.model)}`,
    `OLLAMA ROUTE // ${text(payload?.ollamaRoute)}`,
    `REASONING BUDGET // ${text(payload?.maxSteps)} BOUNDED STEP${Number(payload?.maxSteps) === 1 ? "" : "S"}`,
    `CONFIDENCE // ${Number.isFinite(Number(result.confidence)) ? Number(result.confidence).toFixed(3) : "UNAVAILABLE"}`,
  ];
  if (String(entityContext).trim()) lines.push("", String(entityContext).trim());
  for (const [label, key] of [["SITUATION", "situation"], ["CHANGE", "change"],
    ["STRUCTURE", "structure"], ["GEOGRAPHY", "geography"],
    ["ASSESSMENT", "assessment"], ["DIRECTION", "direction"]]) {
    lines.push("", `${label} // ${text(report[key], "UNAVAILABLE")}`);
  }
  lines.push("", `CREDIBILITY // ${text(report.credibility, "UNAVAILABLE")}`,
    `BOUNDARY // ${text(payload?.boundary)}`);
  return lines.join("\n");
}

export function formatCloudFullFidelityConversation(payload, {entityContext = ""} = {}) {
  const result = payload?.result ?? {};
  const report = result.report ?? {};
  const receipt = payload?.disclosureReceipt ?? {};
  const disclosed = receipt.disclosed ?? {};
  const lines = [
    "GRAPHOPS CONVERSATION // COMPLETED // OLLAMA CLOUD // FULL FIDELITY",
    `QUESTION // ${text(payload?.question)}`,
    `SELECTION // ${text(payload?.selection?.kind)}:${text(payload?.selection?.entityId)}`,
    `GRAPH REVISION // ${text(payload?.selection?.graphRevision)}`,
    `TRACE EVIDENCE // ${text(payload?.evidenceId)}`,
    `MODEL // ${text(result.model)}`,
    `OLLAMA ROUTE // ${text(payload?.ollamaRoute)}`,
    "MODEL AUTHORITY // INTERPRETIVE ONLY",
  ];
  if (String(entityContext).trim()) lines.push("", String(entityContext).trim());
  for (const [label, key] of [["SITUATION", "situation"], ["ANOMALIES", "anomalies"],
    ["MEASURED VS INFERRED", "measuredVsInferred"], ["ASSESSMENT", "assessment"],
    ["FALSIFIER", "falsifier"], ["DIRECTION", "direction"]]) {
    lines.push("", `${label} // ${text(report[key], "UNAVAILABLE")}`);
  }
  lines.push("", `CONFIDENCE // ${Number.isFinite(Number(report.confidence))
    ? Number(report.confidence).toFixed(3) : "UNAVAILABLE"}`,
  "", "FULL-FIDELITY DISCLOSURE RECEIPT",
  `CAPSULE // ${text(receipt.capsuleId)}`,
  `SHA-256 // ${text(receipt.capsuleSha256)}`,
  `DESTINATION // ${text(receipt.destination)} // ${text(receipt.model)}`,
  `DISCLOSED // ${text(disclosed.exactIpAddresses, "0")} EXACT IPs · ${text(disclosed.exactLocations, "0")} EXACT LOCATIONS · EXACT TIMESTAMPS`,
  `GRAPH SCOPE // 1 SELECTED ENTITY · ${text(disclosed.incidentEdges, "0")} INCIDENT EDGES · ${text(disclosed.memberNodes, "0")} MEMBER NODES`,
  `EXCLUDED // ${(receipt.excluded ?? []).join(" · ") || "UNAVAILABLE"}`,
  `BOUNDARY // ${text(payload?.boundary)}`);
  return lines.join("\n");
}
