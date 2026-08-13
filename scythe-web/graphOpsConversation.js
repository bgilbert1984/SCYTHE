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
