import assert from "node:assert/strict";
import test from "node:test";

import {LIFECYCLE_STAGES, describeAction, describeOutcome, governanceBadges, lifecyclePosition, phaseExplanation,
  sessionToken} from "./missionGovernance.js";

const store = entries => ({getItem: key => entries[key] ?? null});

test("the session token comes from the console's stored session, and a bad store is no session", () => {
  const key = "scythe:orchestrator:brokerSession";
  assert.equal(sessionToken(store({[key]: JSON.stringify({sessionToken: "t1"})})), "t1");
  assert.equal(sessionToken(store({}), store({[key]: JSON.stringify({token: "t2"})})), "t2");
  assert.equal(sessionToken(store({[key]: "{not json"})), null);
  assert.equal(sessionToken({getItem() { throw new Error("blocked"); }}), null);
  assert.equal(sessionToken(undefined), null);
});

test("every status has a place in the lifecycle, and only ended-early statuses are terminal", () => {
  assert.deepEqual(lifecyclePosition("pending_approval"), {stage: 1, terminal: null});
  assert.deepEqual(lifecyclePosition("approved"), {stage: 2, terminal: null});
  assert.deepEqual(lifecyclePosition("executed"), {stage: 4, terminal: null});
  for (const status of ["denied", "expired", "blocked", "rejected", "failed"]) {
    assert.equal(lifecyclePosition(status).terminal, status);
  }
  assert.equal(LIFECYCLE_STAGES[lifecyclePosition("executed").stage], "Executed");
  assert.equal(LIFECYCLE_STAGES[lifecyclePosition("pending_approval").stage], "Pending approval");
});

test("actions read as what they do", () => {
  assert.equal(describeAction({tool: "rf_tune", params: {frequency_hz: 145.35e6, mode: "NFM"}}), "Tune SDR to 145.350 MHz NFM");
  assert.equal(describeAction({tool: "rf_capture_stop"}), "Stop capture");
  assert.equal(describeAction({tool: "rf_stream_configure", params: {fft_size: 1024}}), "Configure stream (fft_size=1024)");
  assert.equal(describeAction({tool: "embed_entity"}), "embed_entity");
});

test("a blocked or waiting action always says why", () => {
  assert.equal(describeOutcome({status: "pending_approval", approval_mode: "SECOND_PRINCIPAL"}), "Awaiting a second operator");
  assert.equal(describeOutcome({status: "blocked", reason: "Phase 0 is observe-only; mutation execution is disabled"}),
    "Blocked by phase 0 (observe-only)");
  assert.equal(describeOutcome({status: "blocked", reason: "budget"}), "Blocked: budget");
  assert.equal(describeOutcome({status: "denied", denied_by: {callsign: "BRAVO"}}), "Denied by BRAVO");
  assert.equal(describeOutcome({status: "executed", approved_by: {operator_id: "op-2"}}), "Executed; approved by op-2");
  assert.equal(describeOutcome({status: "expired"}), "Expired: not approved in time");
  assert.equal(describeOutcome({status: "failed", reason: "rig refused"}), "Failed: rig refused");
});

test("governance badges explain the deployment, and a disabled lifecycle shows only itself", () => {
  const badges = governanceBadges({lifecycle_enabled: true, phase: {value: 0}, remote_phase_control_enabled: false,
                                   approval_modes: {HARDWARE_CONTROL: "SECOND_PRINCIPAL"}});
  assert.deepEqual(badges.map(b => [b.key, b.value, b.tone]), [
    ["lifecycle", "Active", "ok"], ["phase", "0 · observe-only", "warn"], ["approval", "SECOND_PRINCIPAL", "lock"],
    ["remote", "Disabled", "ok"]]);
  assert.equal(governanceBadges({lifecycle_enabled: false}).length, 1);
  assert.deepEqual(governanceBadges(null), []);
});

test("the phase explanation says why phase 0 may be fixed", () => {
  assert.match(phaseExplanation({phase: {value: 0}, remote_phase_control_enabled: false}), /remote phase changes are disabled/);
  assert.match(phaseExplanation({phase: {value: 0}, remote_phase_control_enabled: true}), /may change it/);
  assert.equal(phaseExplanation({phase: {value: 2}}), "");
});
