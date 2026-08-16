function text(value, fallback = "—") {
  const result = String(value ?? "").trim(); return result || fallback;
}

export async function prepareAddressContext(selection, {
  fetchImpl = globalThis.fetch, apiBase = "", signal,
} = {}) {
  if (selection?.kind !== "graph-node" || !selection.entityId || !selection.graphRevision) {
    throw new Error("Select a revision-pinned multicast or unspecified address first");
  }
  const response = await fetchImpl.call(globalThis, `${apiBase}/api/graphops/address-context`, {
    method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"}, signal,
    body: JSON.stringify({selection: {kind: "graph-node", entityId: selection.entityId,
      graphRevision: selection.graphRevision}}),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`);
  if (!payload?.bounded || payload.rawPacketsExposed !== false) {
    throw new Error("Address context response violated its bounded contract");
  }
  return payload;
}

export function formatAddressContextPrompt(payload) {
  const address = payload?.address ?? {}; const passive = payload?.passiveEvidence ?? {};
  const protocols = Object.entries(passive.protocolCounts ?? {});
  return [
    `GRAPHOPS PROMPT // ${text(address.addressClass)} // PREPARED`,
    `ADDRESS // ${text(address.address)} // IPv${text(address.ipVersion)}`,
    `SCOPE // ${text(address.scope)}`,
    `KNOWN GROUP // ${text(address.knownService)} // ${text(address.knownPurpose)}`,
    `GRAPH REVISION // ${text(payload?.selection?.graphRevision)}`,
    "", `PASSIVE SENSOR EVIDENCE // ${Object.entries(passive.evidenceClasses ?? {}).map(([name, count]) => `${name}:${count}`).join(" · ") || "UNAVAILABLE"}`,
    `INCIDENT FLOWS // ${text(passive.incidentFlowCount, "0")}`,
    `PROTOCOLS // ${protocols.map(([name, count]) => `${String(name).toUpperCase()}:${count}`).join(" · ") || "NONE"}`,
    `OBSERVED SENDERS // ${(passive.observedSenders ?? []).join(" · ") || "NONE IN BOUNDED SNAPSHOT"}`,
    `OBSERVED RECEIVERS // ${(passive.observedReceivers ?? []).join(" · ") || "NONE IN BOUNDED SNAPSHOT"}`,
    "", `ACTIVE TRACE // ${text(payload?.activeMeasurement?.status)}`,
    `REASON // ${text(payload?.activeMeasurement?.reason)}`,
    `BOUNDARY // ${text(payload?.boundary)}`,
    `PROMPT // ${text(payload?.suggestedQuestion)}`,
  ].join("\n");
}
