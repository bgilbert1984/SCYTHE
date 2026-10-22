import {LIFECYCLE_STAGES, describeAction, describeOutcome, governanceBadges, lifecyclePosition, phaseExplanation,
  sessionToken} from "./missionGovernance.js";

/**
 * Mission Governance: the lifecycle's state for the operator. Shows the mission, phase and approval mode, every recent
 * proposal on the proposal -> approval -> execution path, and an Approval Center over the existing pending/decision
 * routes. It decides nothing itself: every action is a request the server's lifecycle answers.
 */
export class MissionGovernancePanel {
  constructor({root, fetchImpl = globalThis.fetch?.bind(globalThis), storages = [], missionId = null, pollMs = 10000,
               setTimer = globalThis.setInterval?.bind(globalThis)} = {}) {
    this.root = root;
    this.fetch = fetchImpl;
    this.storages = storages;
    this.missionId = missionId;
    this.state = {governance: null, pending: [], recent: [], status: "loading", message: "", expanded: false};
    this.build();
    this.render();
    this.refresh();
    if (setTimer && pollMs > 0) setTimer(() => { if (!globalThis.document?.hidden) this.refresh(); }, pollMs);
  }

  build() {
    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    this.el = el;
    this.root.textContent = "";
    this.bar = el("div", "mgov__bar");
    this.title = el("span", "mgov__title", "MISSION OPERATIONS");
    this.mission = el("span", "mgov__mission");
    this.badges = el("span", "mgov__badges");
    this.toggle = el("button", "mgov__toggle", "Approvals");
    this.toggle.type = "button";
    this.toggle.id = "mission-governance-toggle";
    this.toggle.setAttribute("aria-expanded", "false");
    this.toggle.addEventListener("click", () => { this.state.expanded = !this.state.expanded; this.render(); });
    this.bar.append(this.title, this.mission, this.badges, this.toggle);
    this.body = el("div", "mgov__body");
    this.body.hidden = true;
    this.note = el("div", "mgov__note");
    this.center = el("section", "mgov__section");
    this.recent = el("section", "mgov__section");
    this.body.append(this.note, this.center, this.recent);
    this.root.append(this.bar, this.body);
  }

  async call(path, options = {}) {
    const token = sessionToken(...this.storages);
    if (!token) return {status: 0, data: null, noSession: true};
    try {
      const response = await this.fetch(path, {...options, cache: "no-store", credentials: "same-origin",
        headers: {...(options.headers || {}), "X-Session-Token": token}});
      let data = null;
      try { data = await response.json(); } catch { /* not JSON */ }
      return {status: response.status, data, noSession: false};
    } catch (error) {
      return {status: -1, data: null, noSession: false, error};
    }
  }

  async refresh() {
    const [governance, pending, recent] = await Promise.all([
      this.call("/api/mcp/governance"), this.call("/api/mcp/proposals/pending"), this.call("/api/mcp/proposals/recent?limit=25")]);
    const s = this.state;
    if (governance.noSession || governance.status === 401) {
      Object.assign(s, {governance: null, pending: [], recent: [], status: "signed-out",
        message: "Sign in on the main console to see governance state and act on approvals."});
    } else if (governance.status !== 200) {
      Object.assign(s, {status: "unavailable", message: `Governance state is unavailable (HTTP ${governance.status > 0 ? governance.status : "network"}).`});
    } else {
      Object.assign(s, {governance: governance.data, status: "ok", message: "",
        pending: pending.status === 200 ? pending.data.proposals || [] : [],
        recent: recent.status === 200 ? recent.data.proposals || [] : []});
      this.caller = governance.data.caller || null;
    }
    this.render();
  }

  async decide(proposalId, decision) {
    const result = await this.call(`/api/mcp/proposals/${encodeURIComponent(proposalId)}/decision`, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({decision})});
    const body = result.data || {};
    const message = result.status === 200 && body.status === "ok"
      ? (decision === "approve" ? "Approved and executed." : "Denied.")
      : `Not ${decision === "approve" ? "approved" : "denied"}: ${body.message || body.reason || `HTTP ${result.status}`}`;
    await this.refresh();
    this.state.message = message;     // shown until the next refresh, which reports the lifecycle's own state
    this.render();
  }

  stripFor(item) {
    const {stage, terminal} = lifecyclePosition(item.status);
    const strip = this.el("span", "mgov__strip");
    LIFECYCLE_STAGES.forEach((label, index) => {
      const step = this.el("span", "mgov__step", label);
      if (index < stage || (index === stage && !terminal)) step.dataset.reached = "true";
      if (index === stage) step.dataset.current = "true";
      if (index === stage && terminal) step.dataset.terminal = terminal;
      strip.append(step);
    });
    return strip;
  }

  render() {
    const s = this.state;
    this.mission.textContent = this.missionId ? `MISSION ${this.missionId}` : "NO MISSION SELECTED";
    this.mission.title = this.missionId ? "" : "Open this page with ?mission=<id> to scope it to a mission";
    this.badges.textContent = "";
    for (const badge of governanceBadges(s.governance)) {
      const node = this.el("span", "mgov__badge");
      node.dataset.tone = badge.tone;
      node.dataset.key = badge.key;
      node.textContent = `${badge.label}: ${badge.value}`;
      if (badge.key === "phase") node.title = phaseExplanation(s.governance);
      this.badges.append(node);
    }
    this.toggle.textContent = s.pending.length ? `Approvals (${s.pending.length})` : "Approvals";
    this.toggle.setAttribute("aria-expanded", String(s.expanded));
    this.body.hidden = !s.expanded;
    this.note.textContent = s.message;
    this.note.hidden = !s.message;
    this.renderCenter();
    this.renderRecent();
  }

  renderCenter() {
    const s = this.state;
    this.center.textContent = "";
    this.center.append(this.el("h3", "mgov__heading", "Approval Center"));
    if (!s.pending.length) {
      this.center.append(this.el("div", "mgov__empty", s.status === "ok" ? "No commands are waiting for approval." : "Not available."));
      return;
    }
    for (const item of s.pending) {
      const row = this.el("div", "mgov__row");
      row.dataset.proposal = item.proposal_id;
      const who = item.proposed_by?.callsign || item.proposed_by?.operator_id || "an operator";
      row.append(this.el("span", "mgov__what", `${describeAction(item)} — ${who}`));
      row.append(this.stripFor({status: "pending_approval"}));
      if (item.own) {
        row.append(this.el("em", "mgov__own", "Yours: another operator must approve"));
      } else {
        for (const [decision, label] of [["approve", "Approve"], ["deny", "Deny"]]) {
          const button = this.el("button", "mgov__action", label);
          button.type = "button";
          button.dataset.decision = decision;
          button.addEventListener("click", () => this.decide(item.proposal_id, decision));
          row.append(button);
        }
      }
      this.center.append(row);
    }
  }

  renderRecent() {
    this.recent.textContent = "";
    this.recent.append(this.el("h3", "mgov__heading", "Recent proposals"));
    if (!this.state.recent.length) {
      this.recent.append(this.el("div", "mgov__empty", this.state.status === "ok" ? "No proposals yet." : "Not available."));
      return;
    }
    for (const item of this.state.recent) {
      const row = this.el("div", "mgov__row");
      row.dataset.status = item.status;
      row.append(this.el("span", "mgov__what", describeAction(item)), this.stripFor(item),
        this.el("span", "mgov__outcome", describeOutcome(item)));
      this.recent.append(row);
    }
  }
}
