export function graphTransportNotice(detail, activeInvestigation = null) {
  if (activeInvestigation) return null;
  if (!["empty", "unavailable"].includes(detail?.status)) return null;
  return `GRAPH // ${detail.status.toUpperCase()}\n${detail.message ?? detail.reason ?? "No graph data"}`;
}
