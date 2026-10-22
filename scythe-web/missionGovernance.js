/** Pure helpers for the Mission Governance panel: what the lifecycle decided, said the way an operator needs it. */

export const LIFECYCLE_STAGES = ["Proposal", "Pending approval", "Approved", "Executing", "Executed"];

const SESSION_KEYS = ["scythe:orchestrator:brokerSession"];

/** The operator session token the main console stored (shared_auth.js), or null. Never throws. */
export function sessionToken(...storages) {
  for (const storage of storages) {
    for (const key of SESSION_KEYS) {
      try {
        const saved = storage?.getItem(key);
        const token = saved && (JSON.parse(saved).sessionToken || JSON.parse(saved).token);
        if (token) return token;
      } catch { /* an unreadable store or corrupt entry is no session */ }
    }
  }
  return null;
}

/**
 * Where a proposal is in the lifecycle: `stage` indexes LIFECYCLE_STAGES (the last stage it reached), and
 * `terminal` is set when it ended without executing.
 */
export function lifecyclePosition(status) {
  switch (status) {
    case "pending_approval": return {stage: 1, terminal: null};
    case "approved": return {stage: 2, terminal: null};
    case "executed": return {stage: 4, terminal: null};
    case "failed": return {stage: 3, terminal: "failed"};
    case "denied": return {stage: 1, terminal: "denied"};
    case "expired": return {stage: 1, terminal: "expired"};
    case "blocked": return {stage: 2, terminal: "blocked"};
    case "rejected": return {stage: 0, terminal: "rejected"};
    default: return {stage: 0, terminal: null};
  }
}

function frequency(hz) {
  const value = Number(hz);
  if (!Number.isFinite(value)) return "?";
  if (Math.abs(value) >= 1e9) return `${(value / 1e9).toFixed(6)} GHz`;
  if (Math.abs(value) >= 1e6) return `${(value / 1e6).toFixed(3)} MHz`;
  if (Math.abs(value) >= 1e3) return `${(value / 1e3).toFixed(1)} kHz`;
  return `${value.toFixed(0)} Hz`;
}

/** What the proposal asks for, in a few words. */
export function describeAction(item) {
  const params = item?.params || {};
  switch (item?.tool) {
    case "rf_tune": return `Tune SDR to ${frequency(params.frequency_hz)}${params.mode ? ` ${params.mode}` : ""}`;
    case "rf_capture_start": return "Start capture";
    case "rf_capture_stop": return "Stop capture";
    case "rf_stream_configure": {
      const settings = Object.keys(params).map(key => `${key}=${params[key]}`).join(", ");
      return `Configure stream${settings ? ` (${settings})` : ""}`;
    }
    default: return String(item?.tool || "Unknown action");
  }
}

const name = who => who?.callsign || who?.operator_id || null;

/** Why the proposal is where it is, so a blocked action is never just "blocked". */
export function describeOutcome(item) {
  const reason = String(item?.reason || "");
  switch (item?.status) {
    case "pending_approval":
      return item.approval_mode === "SECOND_PRINCIPAL" ? "Awaiting a second operator" : "Awaiting approval";
    case "approved": return `Approved${name(item.approved_by) ? ` by ${name(item.approved_by)}` : ""}; not yet executed`;
    case "executed": return `Executed${name(item.approved_by) ? `; approved by ${name(item.approved_by)}` : ""}`;
    case "denied": return `Denied${name(item.denied_by) ? ` by ${name(item.denied_by)}` : ""}`;
    case "expired": return "Expired: not approved in time";
    case "blocked": return /phase 0|observe-only/i.test(reason) ? "Blocked by phase 0 (observe-only)" : `Blocked: ${reason || "by policy"}`;
    case "rejected": return `Rejected by policy${reason ? `: ${reason}` : ""}`;
    case "failed": return `Failed${reason ? `: ${reason}` : ""}`;
    default: return String(item?.status || "Unknown");
  }
}

/** One row per fact that explains why actions behave as they do. `tone`: ok, warn, lock, off. */
export function governanceBadges(governance) {
  if (!governance) return [];
  const badges = [{key: "lifecycle", label: "Lifecycle", value: governance.lifecycle_enabled ? "Active" : "Disabled",
                   tone: governance.lifecycle_enabled ? "ok" : "off"}];
  if (!governance.lifecycle_enabled) return badges;
  const phase = governance.phase || {};
  badges.push({key: "phase", label: "Phase", value: phase.value === 0 ? "0 · observe-only" : String(phase.value),
               tone: phase.value === 0 ? "warn" : "ok"});
  const hardware = governance.approval_modes?.HARDWARE_CONTROL;
  badges.push({key: "approval", label: "Approval", value: hardware || "?", tone: hardware === "SECOND_PRINCIPAL" ? "lock" : "ok"});
  badges.push({key: "remote", label: "Remote phase", value: governance.remote_phase_control_enabled ? "Enabled" : "Disabled",
               tone: governance.remote_phase_control_enabled ? "warn" : "ok"});
  return badges;
}

/** Why the phase may be fixed, in one sentence, for the tooltip on the phase badge. */
export function phaseExplanation(governance) {
  if (governance?.phase?.value !== 0) return "";
  return governance.remote_phase_control_enabled
    ? "Phase 0 is observe-only: unattended mutations are blocked. A remote caller may change it."
    : "Phase 0 is observe-only: unattended mutations are blocked, and remote phase changes are disabled.";
}
