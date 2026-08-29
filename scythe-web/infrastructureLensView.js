function el(document, tag, className, text = "") {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
}

export class InfrastructureLensView {
  constructor({root, controller}) {
    if (!root || !controller) throw new TypeError("infrastructure lens root and controller are required");
    this.root = root; this.controller = controller; this.document = root.ownerDocument ?? globalThis.document;
    this.window = this.document.defaultView ?? globalThis; this.snapshot = null; this.visible = false;
    this.infrastructureSelection = null;
    this.overlays = {declared: true, controlPlane: true, contradictions: true};
  }
  start() {
    this.unsubscribe = this.controller.subscribe((update) => {
      if (update.snapshot) this.snapshot = update.snapshot;
      if (this.visible) this.render(update.error);
    }); return this;
  }
  setVisible(value) { this.visible = Boolean(value); if (this.visible) this.render(); }
  setInfrastructureSelection(selection) {
    this.infrastructureSelection = ["peeringdb-facility", "infrastructure-domain"].includes(selection?.kind)
      ? selection : null;
    if (this.visible) this.render();
  }
  render(error = null) {
    this.root.replaceChildren();
    if (error && !this.snapshot) { this.root.append(el(this.document, "pre", "infra-lens__status", `INFRAFLOW // UNAVAILABLE // ${error.message}`)); return; }
    const data = this.snapshot;
    if (!data) { this.root.append(el(this.document, "pre", "infra-lens__status", "INFRAFLOW // WAITING FOR EVIDENCE")); return; }
    const summary = data.summary ?? {};
    const contradictions = data.infrastructureContradictions ?? {};
    const contradictionSummary = contradictions.summary ?? {};
    const domainByAsn = new Map((data.domains ?? []).filter((item) => item.asn)
      .map((item) => [Number(item.asn), item]));
    this.root.append(el(this.document, "pre", "infra-lens__status",
      `INFRAFLOW // ${String(data.status).toUpperCase()} // ${summary.domains ?? 0} DOMAINS // ${summary.observedFlows ?? 0} OBSERVED FLOWS\nPEERINGDB // ${summary.peeringdbNetworks ?? 0} DECLARED NETWORKS // RIS // ${summary.controlPlaneObservations ?? 0} CONTROL-PLANE OBSERVATIONS\nTENSIONS // ${contradictionSummary.findings ?? 0} FINDINGS // ${contradictionSummary.changes ?? 0} CHANGES // ${contradictionSummary.withheldTests ?? 0} TESTS WITHHELD`));
    if (this.infrastructureSelection) {
      const selection = this.infrastructureSelection;
      const panel = el(this.document, "section", "infra-lens__selection");
      const actions = el(this.document, "div", "infra-lens__selection-actions");
      if (selection.kind === "infrastructure-domain") {
        const domain = selection.domain ?? {};
        panel.append(el(this.document, "h3", "infra-lens__selection-title",
          `SELECTED ${domain.asn ? `ASN ${domain.asn}` : domain.id ?? "NETWORK DOMAIN"} // OWNERSHIP + GEOIP INFERRED`));
        panel.append(el(this.document, "div", "infra-lens__selection-body",
          `${domain.organization ?? "OWNERSHIP UNRESOLVED"}\nDOMAIN // ${domain.id ?? "UNKNOWN"}\nOBSERVED HOSTS // ${(domain.hostIds ?? []).length}\nPREFIXES // ${(domain.prefixes ?? []).join(", ") || "NONE IN CURRENT SCOPE"}\nOWNERSHIP AUTHORITY // ${selection.authority ?? "HOST_PREFIX_ENRICHMENT"}\nPLACEMENT AUTHORITY // ${domain.placementAuthority ?? "GEOIP_ESTIMATE_CENTROID"}\nINFERRED CENTROID // ${Number.isFinite(domain.latitude) && Number.isFinite(domain.longitude) ? `${domain.latitude.toFixed(5)}°, ${domain.longitude.toFixed(5)}° ±${domain.uncertaintyRadiusKm ?? 0} km` : "UNAVAILABLE"}`));
        for (const hostId of (domain.hostIds ?? []).slice(0, 64)) {
          const button = el(this.document, "button", "infra-lens__card",
            `OPEN OBSERVED HOST // ${hostId}`);
          button.type = "button"; button.addEventListener("click", () => this.#select("graph-node", hostId));
          actions.append(button);
        }
        if (!actions.children.length) actions.append(el(this.document, "div", "infra-lens__empty",
          "NO OBSERVED HOST IS AVAILABLE FOR THIS INFERRED DOMAIN"));
      } else {
        const facility = selection.facility ?? {};
        const relatedDomains = (facility.environmentAsns ?? []).map((asn) => domainByAsn.get(Number(asn))).filter(Boolean);
        panel.append(el(this.document, "h3", "infra-lens__selection-title",
          `SELECTED FAC ${facility.id ?? "UNKNOWN"} // PEERINGDB SELF-REPORTED`));
        panel.append(el(this.document, "div", "infra-lens__selection-body",
          `${facility.name ?? "UNNAMED"}\n${[facility.city, facility.state, facility.country].filter(Boolean).join(", ") || "LOCATION UNDECLARED"}\nCOORDINATES // ${Number.isFinite(facility.latitude) && Number.isFinite(facility.longitude) ? `${facility.latitude.toFixed(5)}°, ${facility.longitude.toFixed(5)}°` : "UNAVAILABLE"}\nENVIRONMENT ASNs // ${(facility.environmentAsns ?? []).join(", ") || "NONE IN CURRENT SCOPE"}\nUPDATED // ${facility.updated ?? "UNKNOWN"}`));
        for (const domain of relatedDomains) {
          const hostId = domain.observedHostIds?.[0]; if (!hostId) continue;
          const button = el(this.document, "button", "infra-lens__card infra-lens__card--declared",
            `OPEN RELATED ${domain.id} // ${domain.organization ?? "OWNERSHIP UNRESOLVED"}\n${hostId}`);
          button.type = "button"; button.addEventListener("click", () => this.#select("graph-node", hostId));
          actions.append(button);
        }
        if (!actions.children.length) actions.append(el(this.document, "div", "infra-lens__empty",
          "NO OBSERVED HOST IN THE CURRENT GRAPH MATCHES THIS FACILITY'S DECLARED ASN PRESENCE"));
      }
      panel.append(actions, el(this.document, "div", "infra-lens__selection-boundary", selection.boundary));
      this.root.append(panel);
    }
    const toggles = el(this.document, "div", "infra-lens__toggles");
    for (const [key, label] of [["declared", "DECLARED PDB"], ["controlPlane", "RIS CONTROL PLANE"],
      ["contradictions", "EVIDENCE TENSIONS"]]) {
      const button = el(this.document, "button", "infra-lens__toggle", label); button.type = "button";
      button.setAttribute("aria-pressed", String(this.overlays[key]));
      button.addEventListener("click", () => {
        this.overlays[key] = !this.overlays[key]; button.setAttribute("aria-pressed", String(this.overlays[key]));
        this.root.dispatchEvent(new this.window.CustomEvent("scythe-web:infra-overlay", {bubbles: true,
          detail: {...this.overlays}}));
      }); toggles.append(button);
    }
    this.root.append(toggles);
    const grid = el(this.document, "div", "infra-lens__grid");
    const domains = el(this.document, "section", "infra-lens__column");
    domains.append(el(this.document, "h3", "infra-lens__title", "NETWORK DOMAINS // INFERRED"));
    for (const domain of data.domains ?? []) {
      const card = el(this.document, "button", "infra-lens__card"); card.type = "button";
      const place = domain.centroid ? `${domain.centroid.latitude.toFixed(2)}°, ${domain.centroid.longitude.toFixed(2)}° ±${domain.centroid.uncertaintyRadiusKm} km` : "NO PUBLIC LOCATION ESTIMATE";
      card.textContent = `${domain.id} // ${domain.organization}\n${domain.hostCount} OBSERVED HOSTS // ${place}`;
      const hostId = domain.observedHostIds?.[0]; card.disabled = !hostId;
      card.addEventListener("click", () => this.#select("graph-node", hostId)); domains.append(card);
    }
    const flows = el(this.document, "section", "infra-lens__column");
    flows.append(el(this.document, "h3", "infra-lens__title", "DOMAIN FLOWS // OBSERVED"));
    for (const flow of data.observedFlows ?? []) {
      const card = el(this.document, "button", "infra-lens__card infra-lens__card--flow"); card.type = "button";
      card.textContent = `${flow.sourceDomain} → ${flow.targetDomain}\n${flow.protocol.toUpperCase()} // ${flow.flowCount} FLOWS // ${flow.bytes} BYTES\nROUTE // UNOBSERVED`;
      const edgeId = flow.memberEdgeIds?.[0]; card.disabled = !edgeId;
      card.addEventListener("click", () => this.#select("graph-edge", edgeId)); flows.append(card);
    }
    if (!(data.observedFlows ?? []).length) flows.append(el(this.document, "div", "infra-lens__empty", "NO CROSS-DOMAIN FLOW IN BOUNDED SNAPSHOT"));
    const declared = el(this.document, "section", "infra-lens__column");
    const pdb = data.peeringdbEvidence ?? {};
    declared.append(el(this.document, "h3", "infra-lens__title", "PEERINGDB // SELF-REPORTED"));
    for (const network of pdb.networks ?? []) {
      const card = el(this.document, "button", "infra-lens__card infra-lens__card--declared",
        `AS${network.asn} // ${network.name ?? "UNNAMED"}\nTYPE ${network.info_type ?? "UNDECLARED"} // POLICY ${network.policy_general ?? "UNDECLARED"}\nUPDATED ${network.updated ?? "UNKNOWN"}`);
      card.type = "button"; const hostId = domainByAsn.get(Number(network.asn))?.observedHostIds?.[0];
      card.disabled = !hostId; card.title = "Open a revision-pinned observed host from this declared network";
      card.addEventListener("click", () => this.#select("graph-node", hostId)); declared.append(card);
    }
    if (!(pdb.networks ?? []).length) declared.append(el(this.document, "div", "infra-lens__empty",
      `PEERINGDB // ${String(pdb.status ?? "UNAVAILABLE").toUpperCase()} // ${pdb.reason ?? pdb.refreshError ?? "NO RECORDS"}`));
    const control = el(this.document, "section", "infra-lens__column");
    const ris = data.controlPlaneEvidence ?? {};
    control.append(el(this.document, "h3", "infra-lens__title", "RIS LIVE // COLLECTOR VANTAGE"));
    for (const row of (ris.controlPlanePaths ?? []).slice(-32).reverse()) {
      const path = (row.asPath ?? []).map((hop) => Array.isArray(hop) ? `{${hop.join(",")}}` : hop).join(" → ") || "NO AS PATH";
      const card = el(this.document, "button", "infra-lens__card infra-lens__card--control",
        `${row.messageType} ${row.prefix}\n${row.collectorId} @ ${row.collectorReceivedIso}\n${path}\nDATA PLANE // NON-AUTHORITATIVE`);
      card.type = "button";
      const origins = Array.isArray(row.originAsn) ? row.originAsn : [row.originAsn];
      const hostId = origins.map((asn) => domainByAsn.get(Number(asn))?.observedHostIds?.[0]).find(Boolean);
      card.disabled = !hostId; card.title = "Open a revision-pinned observed host matching this control-plane origin";
      card.addEventListener("click", () => this.#select("graph-node", hostId)); control.append(card);
    }
    if (!(ris.controlPlanePaths ?? []).length) control.append(el(this.document, "div", "infra-lens__empty",
      `RIS LIVE // ${String(ris.status ?? "UNAVAILABLE").toUpperCase()} // WAITING FOR PREFIX-RELEVANT UPDATE`));
    const tensions = el(this.document, "section", "infra-lens__column");
    tensions.append(el(this.document, "h3", "infra-lens__title", "EVIDENCE TENSIONS // UNRESOLVED"));
    for (const finding of contradictions.findings ?? []) {
      const card = el(this.document, "button", "infra-lens__card infra-lens__card--contradiction");
      card.type = "button";
      card.textContent = `${finding.kind} // ${finding.status}\n${finding.subject ?? finding.prefix ?? "UNSCOPED"}\n${finding.boundary}\nFALSIFIER // ${finding.falsifier}`;
      const domain = (data.domains ?? []).find((item) => item.id === finding.subject);
      const hostId = domain?.observedHostIds?.[0]; card.disabled = !hostId;
      card.title = "Open an observed host implicated by this unresolved source disagreement";
      card.addEventListener("click", () => this.#select("graph-node", hostId)); tensions.append(card);
    }
    for (const change of contradictions.changes ?? []) tensions.append(el(this.document, "div",
      "infra-lens__card infra-lens__card--change",
      `${change.kind} // ${change.prefix}\n${change.collectorId} // ${change.variants} OBSERVED VARIANTS\n${change.boundary}`));
    for (const withheld of contradictions.withheld ?? []) tensions.append(el(this.document, "div",
      "infra-lens__card infra-lens__card--withheld",
      `${withheld.kind}\n${withheld.reason}\nNEEDED // ${withheld.needed ?? "ADDITIONAL REVISION-PINNED EVIDENCE"}`));
    if (!(contradictions.findings ?? []).length && !(contradictions.changes ?? []).length)
      tensions.prepend(el(this.document, "div", "infra-lens__empty", "NO SOURCE DISAGREEMENT DETECTED IN THE BOUNDED WINDOW"));
    grid.append(domains, flows, declared, control, tensions); this.root.append(grid,
      el(this.document, "div", "infra-lens__boundary", `BOUNDARY // ${data.boundary}`));
  }
  #select(kind, entityId) {
    if (!entityId) return;
    this.root.dispatchEvent(new this.window.CustomEvent("scythe-web:graph-selection", {bubbles: true, detail: {
      kind, entityId, graphRevision: this.snapshot.graphRevision,
    }}));
  }
  destroy() { this.unsubscribe?.(); }
}
