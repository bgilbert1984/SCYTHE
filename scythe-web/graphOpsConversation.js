function text(value, fallback = "—") {
  const result = String(value ?? "").trim();
  return result || fallback;
}

export function operatorQuestionOnly(value) {
  const question = String(value ?? "").trim();
  if (!/GRAPHOPS\s+(?:PROMPT|\/\/\s+HOST TRACE)/i.test(question)) return question;
  const prompts = [...question.matchAll(/^PROMPT\s*\/\/\s*(.+)$/gim)];
  return String(prompts.at(-1)?.[1] ?? "").trim() || question;
}

export async function askGraphOps(question, selection, {
  fetchImpl = globalThis.fetch, apiBase = "", signal,
} = {}) {
  const utterance = operatorQuestionOnly(question);
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
  const utterance = operatorQuestionOnly(question);
  if (!utterance) throw new Error("Enter a GraphOps question");
  if (!selection?.entityId || !selection?.graphRevision) throw new Error("Select a prepared graph entity first");
  if (!String(evidenceId ?? "").trim()) throw new Error("Prepare bounded evidence before asking Cloud");
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

export function formatTraversalReceipt(retrieval) {
  if (!retrieval?.projection) return "";
  const graph = retrieval.graph ?? {}; const projection = retrieval.projection ?? {};
  const traversal = retrieval.traversal; const paths = retrieval.paths ?? [];
  const lines = [
    "GRAPHFUSION // TRAVERSAL RECEIPT",
    `MODE // ${text(retrieval.mode).toUpperCase()} // ${text(retrieval.version)}`,
    `GRAPH // ${text(graph.revision)} // ${text(graph.detectedNodes, "0")} DETECTED NODES · ${text(graph.detectedEdges, "0")} DETECTED EDGES`,
    `PROJECTION // ${text(projection.hash)} // ${text(projection.nodes, "0")} / ${text(projection.nodeLimit, "0")} NODES · ${text(projection.edges, "0")} / ${text(projection.edgeLimit, "0")} EDGES${projection.truncated ? " · TRUNCATED" : ""}`,
  ];
  if (traversal) {
    lines.push(
      `TRAVERSAL // ${text(traversal.hash)} // ${text(traversal.maxHops, "0")} HOPS · ${text(traversal.seeds, "0")} SEEDS`,
      `INSPECTED // ${text(traversal.nodesVisited, "0")} NODES · ${text(traversal.edgesInspected, "0")} EDGES · ${text(traversal.candidatePaths, "0")} CANDIDATE PATHS`,
      `ADMITTED // ${text(traversal.admittedPaths, "0")} PATHS`);
    for (const path of paths) {
      const route = (path.steps ?? []).map((step) => text(step.id)).join(" → ");
      lines.push(`${text(path.pathId)} // ${text(path.role)} // ${Number(path.score || 0).toFixed(3)}`,
        `PATH // ${route}`);
    }
  } else {
    lines.push("TRAVERSAL // DISABLED FOR ABLATION MODE");
  }
  lines.push(`BOUNDARY // ${text(retrieval.boundary)}`);
  return lines.join("\n");
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
  const traversalReceipt = formatTraversalReceipt(payload?.retrieval);
  if (traversalReceipt) lines.push("", traversalReceipt);
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
  const projection = receipt.capsuleProjection ?? {};
  const includedRecords = Object.values(projection.includedCounts ?? {}).reduce((sum, value) => sum + (Number(value) || 0), 0);
  const omittedRecords = Object.values(projection.omittedCounts ?? {}).reduce((sum, value) => sum + (Number(value) || 0), 0);
  const lines = [
    "GRAPHOPS CONVERSATION // COMPLETED // OLLAMA CLOUD // FULL FIDELITY",
    `QUESTION // ${text(payload?.question)}`,
    `SELECTION // ${text(payload?.selection?.kind)}:${text(payload?.selection?.entityId)}`,
    `GRAPH REVISION // ${text(payload?.selection?.graphRevision)}`,
    `EVIDENCE CAPSULE // ${text(payload?.evidenceId)}`,
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
  `VALIDATION // ${(report.validationConstraints ?? []).join(" · ") || "PASSED"}`,
  "", "FULL-FIDELITY DISCLOSURE RECEIPT",
  `CAPSULE // ${text(receipt.capsuleId)}`,
  `SHA-256 // ${text(receipt.capsuleSha256)}`,
  `DESTINATION // ${text(receipt.destination)} // ${text(receipt.model)}`,
  `DISCLOSED // ${text(disclosed.exactIpAddresses, "0")} EXACT IPs · ${text(disclosed.exactLocations, "0")} EXACT LOCATIONS · EXACT TIMESTAMPS`,
  `INFRASTRUCTURE // ${text(disclosed.infrastructureDomains, "0")} DOMAINS · ${text(disclosed.observedInfrastructureFlows, "0")} OBSERVED FLOWS · ${text(disclosed.modeledPathCandidates, "0")} MODELED PATH CANDIDATES`,
  `DECLARED // ${text(disclosed.peeringdbNetworks, "0")} PEERINGDB NETWORKS · ${text(disclosed.declaredIxMemberships, "0")} IX MEMBERSHIPS`,
  `CONTROL PLANE // ${text(disclosed.controlPlaneObservations, "0")} RIS COLLECTOR-VANTAGE OBSERVATIONS · NON-AUTHORITATIVE FOR DATA PLANE`,
  `EVIDENCE TENSIONS // ${text(disclosed.infrastructureContradictions, "0")} UNRESOLVED FINDINGS · ${text(disclosed.controlPlaneChanges, "0")} OBSERVED CHANGES · ${text(disclosed.withheldInfrastructureTests, "0")} TESTS WITHHELD`,
  `PACKET DISSECTION // ${text(disclosed.packetDissections, "0")} TEMPORAL EVENTS / ${text(disclosed.temporalRingLimit, "0")} RING · ${text(disclosed.decodedPacketFields, "0")} DECODED FIELDS · ${text(disclosed.temporalEventsOmitted, "0")} EARLIER EVENTS OMITTED · ${text(disclosed.rawPacketPayloads, "0")} RAW PAYLOADS`,
  `CAPSULE PROJECTION // ${text(projection.mode)} // ${includedRecords} EXACT RECORDS INCLUDED · ${omittedRecords} ENVIRONMENT RECORDS OMITTED AND HASH-BOUND`,
  `GRAPH SCOPE // 1 SELECTED ENTITY · ${text(disclosed.incidentEdges, "0")} INCIDENT EDGES · ${text(disclosed.memberNodes, "0")} MEMBER NODES`,
  `EXCLUDED // ${(receipt.excluded ?? []).join(" · ") || "UNAVAILABLE"}`,
  `BOUNDARY // ${text(payload?.boundary)}`);
  return lines.join("\n");
}
