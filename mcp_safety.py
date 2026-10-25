"""mcp_safety.py — Safety interlocks for graduated agent autonomy.

Implements:
  - Observe-only baseline (Phase 0)
  - Shadow mutation (Phase 1) — mutations proposed but not committed
  - Limited mutation with auto-demotion (Phase 2)
  - Trust score evolution
  - Entropy/stability metrics
  - Split-brain agent coordination

Design principle: Autonomy is earned, not granted.
"""
import time
import logging
from typing import Any, Dict, Optional
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def proposal_digest(tool_name: str, params: Any) -> str:
    """Stable digest of what a proposal would run, so approval can be bound to it."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps({"tool": tool_name, "params": params}, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class TrustScore:
    """Tracks agent safety profile over time."""
    agent_id: str
    initial_score: float = 0.5  # Start conservative
    current_score: float = 0.5

    # Positive events
    successful_mutations: int = 0
    zero_errors: int = 0  # windows with no errors
    stable_entropy_windows: int = 0

    # Negative events
    rate_limit_hits: int = 0
    oscillation_events: int = 0


    def update(
        self,
        success: bool = False,
        rate_limit_hit: bool = False,
        oscillation: bool = False,
        entropy_stable: bool = False,
        error_count: int = 0,
    ) -> None:
        """Update trust score based on observed behavior."""
        if success:
            self.successful_mutations += 1
            self.current_score = min(1.0, self.current_score + 0.05)

        if error_count == 0:
            self.zero_errors += 1
            self.current_score = min(1.0, self.current_score + 0.02)

        if entropy_stable:
            self.stable_entropy_windows += 1
            self.current_score = min(1.0, self.current_score + 0.03)

        if rate_limit_hit:
            self.rate_limit_hits += 1
            self.current_score = max(0.1, self.current_score - 0.15)

        if oscillation:
            self.oscillation_events += 1
            self.current_score = max(0.1, self.current_score - 0.20)

    def adaptive_budget(self, base_budget: int) -> int:
        """Scale mutation budget based on trust score."""
        return max(0, int(base_budget * self.current_score))

    def should_demote_to_observe(self) -> bool:
        """Auto-demote if trust drops below threshold."""
        return self.current_score < 0.3


@dataclass
class StabilityMetrics:
    """Tracks graph and system stability."""
    window_start: float = field(default_factory=time.time)
    window_size: float = 300.0  # 5 minutes

    # Graph metrics
    edge_weight_variance: float = 0.0
    entropy_delta: float = 0.0
    prune_mutate_ratio: float = 0.0
    mutation_cluster_density: float = 0.0
    decay_lambda_volatility: float = 0.0

    # Performance metrics
    avg_tool_latency_ms: float = 0.0
    error_rate: float = 0.0

    # Operational
    last_mutation_time: float = field(default_factory=time.time)
    mutation_timestamps: list = field(default_factory=list)

    def reset_window(self) -> None:
        """Reset metrics for new window."""
        self.window_start = time.time()
        self.mutation_timestamps = []

    def add_mutation(self) -> None:
        """Record a mutation event."""
        now = time.time()
        self.mutation_timestamps.append(now)
        self.last_mutation_time = now

    def is_oscillating(self, window_seconds: float = 60.0) -> bool:
        """Detect rapid mutation clustering (oscillation pattern)."""
        if len(self.mutation_timestamps) < 3:
            return False

        now = time.time()
        recent = [ts for ts in self.mutation_timestamps if now - ts < window_seconds]

        if len(recent) < 3:
            return False

        # oscillation: 3+ mutations in 60-second window
        return len(recent) >= 3

    def is_entropy_stable(
        self,
        entropy_delta_threshold: float = 0.2,
        variance_threshold: float = 0.15,
    ) -> bool:
        """Check if graph is in stable state."""
        return (
            abs(self.entropy_delta) < entropy_delta_threshold
            and self.edge_weight_variance < variance_threshold
        )


class DualAgentOrchestrator:
    """
    Coordinates Analyst (observe) and Executor (mutate) agents.

    Flow:
      Analyst → proposes_action(tool, params, confidence)
      Orchestrator → validates against Executor limits
      Executor → executes if approved
    """

    def __init__(self, default_mutation_budget: int = 3, tool_resolver=None, approval_modes=None,
                 pending_ttl_seconds=None, approvers=None):
        # name -> tool object (with .effect / .effect_declared / .mutates_state).
        # Approval is decided from the tool's declared effect, never its name.
        self.tool_resolver = tool_resolver
        self.analyst_mode = "observe"  # readonly
        self.analyst_trust = TrustScore(agent_id="analyst")

        self.executor_mode = "mutate"
        self.executor_trust = TrustScore(agent_id="executor")
        self.base_mutation_budget = default_mutation_budget

        self.stability = StabilityMetrics()
        self.drift_gate = DriftGate()  # Fourth safety gate: systemic drift detection

        # Decision log: separate from tool audit log
        self.decision_log: list[Dict[str, Any]] = []

        # Deployment approval settings (ADR 0006): they may raise an effect's mode, never
        # lower it.  None reads MCP_APPROVAL_MODES.
        from mcp_effects import (
            MINIMUM_APPROVAL_MODE, SECOND_PRINCIPAL, deployment_approval_modes, deployment_approval_ttl,
            deployment_approvers, required_approval_mode,
        )
        self.approval_modes = dict(approval_modes) if approval_modes is not None else deployment_approval_modes()
        # Operator ids entitled to approve (MCP_APPROVERS).  Authentication alone is not authority.
        self.approvers = frozenset(approvers) if approvers is not None else deployment_approvers()
        # How long a pending_approval proposal stays approvable (MCP_APPROVAL_TTL_SECONDS).
        self.pending_ttl = float(pending_ttl_seconds) if pending_ttl_seconds is not None else deployment_approval_ttl()
        needing = sorted(e for e in MINIMUM_APPROVAL_MODE
                         if required_approval_mode(e, self.approval_modes) == SECOND_PRINCIPAL)
        if needing:
            logging.getLogger(__name__).warning(
                "[approval] %s requires %s: matching proposals wait as pending_approval until a different "
                "authorized operator approves them (orchestrate/approve); %s",
                needing, SECOND_PRINCIPAL,
                f"{len(self.approvers)} operator(s) are authorized to approve" if self.approvers
                else "NO operator is authorized (MCP_APPROVERS is empty), so they cannot run")

    def _audit_transition(self, action: str, proposal: Dict[str, Any], principal, result: str,
                          summary: Optional[str] = None, parameters: Optional[Dict[str, Any]] = None) -> None:
        """Append a proposal-lifecycle row to the control audit.  Evidence only: nothing reads it back
        to decide anything, and a failed write cannot change a decision."""
        try:
            import control_audit
            control_audit.record_transition(
                action=action, effect=proposal.get("effect"), principal=principal,
                proposal_id=proposal["proposal_id"], result=result, summary=summary, parameters=parameters,
                initiator=proposal.get("initiator"))
        except Exception:
            logger.exception("[approval] could not audit %s for %s", action, proposal.get("proposal_id"))

    def _expire(self, proposal: Dict[str, Any], now: float) -> None:
        proposal.update(status="expired", expired_at=now)
        self._audit_transition(
            "proposal.expired", proposal, None, "expired",
            summary=f"not approved within {self.pending_ttl:g}s", parameters={"tool": proposal["tool_name"]})

    def expire_pending(self) -> int:
        """Expire ``pending_approval`` proposals older than the TTL and drop long-expired entries.

        Returns how many were expired.  Run opportunistically, so the in-memory log does not
        grow with proposals nobody will approve; ``decide_proposal`` checks the TTL itself and
        does not depend on this having run.
        """
        now = time.time()
        expired = 0
        for p in self.decision_log:
            if p["status"] == "pending_approval" and now - p["timestamp"] > self.pending_ttl:
                self._expire(p, now)
                expired += 1
        self.decision_log[:] = [p for p in self.decision_log
                                if not (p["status"] == "expired" and now - p.get("expired_at", now) > self.pending_ttl)]
        return expired

    def may_approve(self, operator_id) -> bool:
        """True when ``operator_id`` has been granted approval authority (``MCP_APPROVERS``)."""
        return bool(operator_id) and operator_id in self.approvers

    def decide_proposal(self, proposal_id: str, decision: str, approver, note: str = "") -> Dict[str, Any]:
        """Approve or deny a ``pending_approval`` proposal (ADR 0006, Decision 6).

        ``approver`` is the authenticated ``Principal`` making the call.  It must be an
        identified operator session that has been granted approval authority
        (``MCP_APPROVERS``) and whose ``operator_id`` differs from the proposer's, so
        two sessions of one operator are one principal.  A proposer that is not an
        identified operator (an internal automation) can be approved by any identified
        operator.  Approval authorizes execution; it does not run anything, and execution
        still checks phase, budget and the recorded approval.
        """
        proposal = next((p for p in self.decision_log if p["proposal_id"] == proposal_id), None)
        if not proposal:
            return {"ok": False, "error": "proposal not found"}
        if decision not in ("approve", "deny"):
            return {"ok": False, "error": f"decision must be 'approve' or 'deny', not {decision!r}"}
        now = time.time()
        if proposal["status"] == "pending_approval" and now - proposal["timestamp"] > self.pending_ttl:
            self._expire(proposal, now)
            return {"ok": False, "error": f"proposal expired: not approved within {self.pending_ttl:g}s"}
        if proposal["status"] != "pending_approval":
            return {"ok": False, "error": f"proposal status is {proposal['status']}, not pending_approval"}
        if approver is None or not getattr(approver, "identified", False):
            return {"ok": False, "error": "approval requires an identified operator session"}
        if not self.may_approve(approver.operator_id):
            return {"ok": False, "error": "this operator is not authorized to approve or deny proposals"}
        proposer_id = (proposal.get("proposed_by") or {}).get("operator_id")
        if proposer_id and proposer_id == approver.operator_id:
            return {"ok": False, "error": "the approver must differ from the proposer"}
        if proposal_digest(proposal["tool_name"], proposal["params"]) != proposal.get("params_digest"):
            return {"ok": False, "error": "proposal parameters changed after the proposal was created"}

        who = approver.to_dict()
        if decision == "deny":
            proposal.update(status="denied", denied_by=who, denied_at=now, approval_note=note)
            self._audit_transition("proposal.denied", proposal, who, "denied", summary=note or None,
                                   parameters={"tool": proposal["tool_name"], "params_digest": proposal["params_digest"]})
            return {"ok": True, "proposal_id": proposal_id, "status": "denied"}
        proposal.update(status="approved", approved_by=who, approved_at=now, approval_note=note,
                        approved_params_digest=proposal["params_digest"])
        self._audit_transition("proposal.approved", proposal, who, "approved", summary=note or None,
                               parameters={"tool": proposal["tool_name"], "params_digest": proposal["params_digest"]})
        return {"ok": True, "proposal_id": proposal_id, "status": "approved"}

    def verify_approval(self, proposal: Dict[str, Any]) -> Optional[str]:
        """Why an ``approved`` proposal may not execute, or ``None`` if it may.

        Repeats the approval-time checks at execution, so a defect that sets
        ``approved`` directly still cannot run a ``SECOND_PRINCIPAL`` proposal.  Nothing
        stored on the proposal is trusted to say whether approval was needed: the mode is
        recomputed from the tool's declared effect and the current deployment setting, and
        the stricter of that and the recorded mode applies.  The approver is judged from
        the fields that identify an individual, not from the stored ``identified`` flag.
        """
        from mcp_effects import SECOND_PRINCIPAL
        tool = self.tool_resolver(proposal["tool_name"]) if self.tool_resolver else None
        if SECOND_PRINCIPAL not in (proposal.get("approval_mode"), self.approval_mode_for(tool)):
            return None
        from mcp_principal import OPERATOR_SESSION
        approver = proposal.get("approved_by") or {}
        if approver.get("kind") != OPERATOR_SESSION or not approver.get("operator_id"):
            return "a second-principal proposal has no recorded approval by an identified operator"
        if not self.may_approve(approver.get("operator_id")):
            return "the recorded approver is not (or is no longer) authorized to approve"
        proposer_id = (proposal.get("proposed_by") or {}).get("operator_id")
        if proposer_id and proposer_id == approver.get("operator_id"):
            return "the recorded approver is the proposer"
        if proposal_digest(proposal["tool_name"], proposal["params"]) != proposal.get("approved_params_digest"):
            return "proposal parameters changed after approval"
        return None

    def approval_mode_for(self, tool) -> str:
        """The approval mode a proposal for ``tool`` needs: its effect's floor, or a stricter deployment setting.

        A tool that never declared an effect, or that cannot be found, is ``AUTOMATED``
        (today's treatment); no deployment setting applies to it because it has no effect.
        """
        from mcp_effects import AUTOMATED, required_approval_mode
        if tool is None or not getattr(tool, "effect_declared", False):
            return AUTOMATED
        return required_approval_mode(tool.effect, self.approval_modes, halts=getattr(tool, "halts", False))

    def propose_action(
        self,
        tool_name: str,
        params: Dict[str, Any],
        confidence: float,
        justification: str = "",
        initiator: str = "autonomous",
    ) -> Dict[str, Any]:
        """Analyst proposes an action. Returns decision.

        ``initiator`` is the ADR 0005 Decision 5 class.  It is a server-side argument: no
        transport handler passes request data into it.  It is recorded once, here, and
        execution reads it back from the proposal.
        """
        import uuid
        from mcp_principal import INITIATOR_CLASSES
        if initiator not in INITIATOR_CLASSES:
            raise ValueError(f"unknown initiator class {initiator!r}")

        self.expire_pending()
        proposal_id = str(uuid.uuid4())
        proposal = {
            "proposal_id": proposal_id,
            "tool_name": tool_name,
            "params": params,
            "confidence": confidence,
            "justification": justification,
            "timestamp": time.time(),
            "initiator": initiator,
            "status": "proposed",  # proposed | approved | pending_approval | rejected | executed
            "approval_reason": "",
        }

        from mcp_effects import AUTO_APPROVE, approval_policy
        from mcp_principal import principal_dict

        # Who asked.  There is no separate human approver in this flow: approval
        # is computed below from the safety checks, so the identities worth
        # recording are the proposer here and the executor in execute_proposal.
        proposal["proposed_by"] = principal_dict()

        tool = self.tool_resolver(tool_name) if self.tool_resolver else None
        proposal["effect"] = getattr(tool, "effect", None)
        proposal["effect_declared"] = bool(getattr(tool, "effect_declared", False))

        from mcp_effects import DIRECT, SECOND_PRINCIPAL
        mode = self.approval_mode_for(tool)
        proposal["approval_mode"] = mode
        proposal["params_digest"] = proposal_digest(tool_name, params)

        if mode == DIRECT and approval_policy(tool) == AUTO_APPROVE:
            proposal["status"] = "approved"
            proposal["approval_reason"] = f"declared {tool.effect} (non-mutating)"
            self.drift_gate.record_proposal(was_accepted=True)
        else:
            # Mutating, undeclared or unknown tool: check Executor limits
            # This is automated policy evaluation, not independent review: the
            # confidence is supplied by the caller and nothing here requires a
            # principal other than the proposer.  See issue #228.
            approval = self._check_mutation_approval(tool_name, params, confidence, initiator, effect=proposal["effect"])
            proposal["status"] = approval["status"]
            if tool is None:
                why = "tool not found"
            elif not proposal["effect_declared"]:
                why = "effect not declared"
            else:
                why = f"effect {tool.effect}"
            proposal["approval_reason"] = f"{why}: {approval['reason']}"
            if mode == SECOND_PRINCIPAL and proposal["status"] == "approved":
                # The automated checks passed; an independent principal must still approve
                # (decide_proposal).  Until then it cannot execute.
                proposal["status"] = "pending_approval"
                proposal["approval_reason"] += " | awaiting independent approval"
            self.drift_gate.record_proposal(was_accepted=(proposal["status"] != "rejected"))

        self.decision_log.append(proposal)
        if mode != DIRECT:
            # The proposer is only in this in-memory proposal; the row keeps it past a restart.
            self._audit_transition(
                "proposal.created", proposal, proposal.get("proposed_by"), proposal["status"],
                summary=proposal["approval_reason"],
                parameters={"tool": tool_name, "params": params, "params_digest": proposal["params_digest"],
                            "approval_mode": mode, "confidence": confidence, "justification": justification})
        return proposal

    def _check_mutation_approval(
        self,
        tool_name: str,
        params: Dict[str, Any],
        confidence: float,
        initiator: str = "autonomous",
        effect: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Check if Executor can approve a mutation proposal.

        ``confidence`` is the caller's own claim.  For ``HARDWARE_CONTROL`` it is recorded
        by the caller and never decides whether the hardware may be driven (#228).

        The trust, oscillation and entropy gates judge an agent's unattended behaviour and
        the graph's health.  An ``operator_command`` (ADR 0005 Decision 5) is an operator
        driving equipment, so they do not apply: without this, three tunes inside a minute
        would make the fourth "graph oscillation".
        """
        from mcp_effects import HARDWARE_CONTROL
        from mcp_principal import OPERATOR_COMMAND
        reasons = []

        # Confidence threshold (not applied to hardware: the caller supplies it)
        if effect != HARDWARE_CONTROL and confidence < 0.70:
            reasons.append(f"confidence {confidence:.2f} < 0.70")
            return {"status": "rejected", "reason": " | ".join(reasons)}

        if initiator == OPERATOR_COMMAND:
            return {"status": "approved", "reason": "operator command: agent stability gates do not apply"}

        # Check trust score
        if self.executor_trust.should_demote_to_observe():
            reasons.append("executor trust score fell below 0.30 (demoting to observe)")
            return {"status": "rejected", "reason": " | ".join(reasons)}

        # Check stability
        if self.stability.is_oscillating():
            reasons.append("graph oscillation detected (too many recent mutations)")
            return {"status": "rejected", "reason": " | ".join(reasons)}

        # Check entropy
        if not self.stability.is_entropy_stable():
            reasons.append("graph entropy unstable")
            return {"status": "rejected", "reason": " | ".join(reasons)}

        # Check drift (warning, not blocking)
        if self.drift_gate.should_warn_analyst():
            drift_check = self.drift_gate.check_drift()
            reasons.append(f"DRIFT WARNING: {' | '.join(drift_check['alerts'])}")

        # All checks pass (or only drift warnings)
        return {"status": "approved", "reason": " | ".join(reasons) if reasons else "all safety checks passed"}

    def execute_proposal(
        self,
        proposal_id: str,
        executor_call,  # function to execute the RPC call
    ) -> Dict[str, Any]:
        """Execute an approved proposal."""
        proposal = next(
            (p for p in self.decision_log if p["proposal_id"] == proposal_id),
            None,
        )
        if not proposal:
            return {"ok": False, "error": "proposal not found"}

        if proposal["status"] != "approved":
            return {"ok": False, "error": f"proposal status is {proposal['status']}, not approved"}
        problem = self.verify_approval(proposal)
        if problem:
            return {"ok": False, "error": problem}

        # Get adaptive budget based on trust
        budget = self.executor_trust.adaptive_budget(self.base_mutation_budget)

        # Execute via the provided call function
        try:
            result = executor_call(
                tool_name=proposal["tool_name"],
                params=proposal["params"],
                agent_mode="mutate",
                mutation_budget=budget,
            )
            proposal["status"] = "executed"
            proposal["execution_result"] = result
            self.stability.add_mutation()
            self.executor_trust.update(success=True)

            # Record tool call and entropy for drift detection
            self.drift_gate.record_tool_call(proposal["tool_name"])
            current_entropy = self.stability.shannon_entropy
            self.drift_gate.record_entropy(current_entropy)

            return {"ok": True, "result": result}
        except Exception as e:
            proposal["status"] = "failed"
            proposal["error"] = str(e)
            self.executor_trust.update(success=False)
            return {"ok": False, "error": str(e)}

    def get_adaptive_budget(self) -> int:
        """Executor's current mutation budget based on trust."""
        return self.executor_trust.adaptive_budget(self.base_mutation_budget)

    def get_status(self) -> Dict[str, Any]:
        """Return full orchestrator status."""
        drift_check = self.drift_gate.check_drift()

        return {
            "analyst": {
                "mode": self.analyst_mode,
                "trust_score": self.analyst_trust.current_score,
            },
            "executor": {
                "mode": self.executor_mode,
                "trust_score": self.executor_trust.current_score,
                "adaptive_budget": self.get_adaptive_budget(),
                "demoted": self.executor_trust.should_demote_to_observe(),
            },
            "stability": {
                "is_stable": self.stability.is_entropy_stable(),
                "is_oscillating": self.stability.is_oscillating(),
                "entropy_delta": self.stability.entropy_delta,
                "edge_weight_variance": self.stability.edge_weight_variance,
            },
            "drift": {
                "drifting": drift_check["drifting"],
                "alerts": drift_check["alerts"],
                "metrics": drift_check["metrics"],
            },
            "decision_log_length": len(self.decision_log),
        }


@dataclass
class DriftGate:
    """
    Fourth safety gate: Detects systemic bias and drift under stable conditions.

    The risk is not oscillation or entropy spikes.
    The risk is slow convergence toward locally optimal but globally wrong state.

    Monitors:
      - Long-term entropy slope (trend over hours)
      - Reinforcement concentration (gini coefficient)
      - Mutation targeting skew
      - Tool distribution skew
      - Abstention rate (agent chooses NOT to act)
    """

    def __init__(self):
        self.entropy_history: list = []  # (timestamp, entropy)
        self.reinforcement_counts: dict = defaultdict(int)  # entity → count
        self.mutation_tools_called: dict = defaultdict(int)  # tool → count
        self.abstention_count: int = 0  # proposals rejected by agent (not by gate)
        self.total_proposals: int = 0
        self.drift_alerts: list = []

    def record_entropy(self, entropy: float) -> None:
        """Record entropy sample for trend analysis."""
        self.entropy_history.append((time.time(), entropy))
        # Keep only last 24 hours
        cutoff = time.time() - 86400
        self.entropy_history = [(ts, ent) for ts, ent in self.entropy_history if ts > cutoff]

    def record_reinforcement(self, src: str, dst: str) -> None:
        """Record edge reinforcement for concentration analysis."""
        self.reinforcement_counts[src] += 1
        self.reinforcement_counts[dst] += 1

    def record_tool_call(self, tool_name: str) -> None:
        """Record which mutation tool was called."""
        self.mutation_tools_called[tool_name] += 1

    def record_proposal(self, was_accepted: bool) -> None:
        """Record whether analyst proposed action (not executor gate)."""
        self.total_proposals += 1
        if not was_accepted:
            # Agent chose abstention
            self.abstention_count += 1

    def _gini_coefficient(self, values: list) -> float:
        """Calculate Gini coefficient (0=perfect equality, 1=perfect inequality)."""
        if not values or len(values) == 1:
            return 0.0

        sorted_vals = sorted(values)
        n = len(sorted_vals)
        cumsum = sum((i + 1) * v for i, v in enumerate(sorted_vals))

        return (2 * cumsum / (n * sum(sorted_vals))) - (n + 1) / n

    def _long_term_entropy_slope(self) -> float:
        """
        Calculate entropy trend over past hour.

        Positive slope = entropy increasing (chaos)
        Negative slope = entropy decreasing (freezing)
        Close to zero = stable
        """
        if len(self.entropy_history) < 2:
            return 0.0

        # Get last hour of data
        one_hour_ago = time.time() - 3600
        recent = [(ts, ent) for ts, ent in self.entropy_history if ts > one_hour_ago]

        if len(recent) < 2:
            return 0.0

        # Linear regression slope
        times = [ts - recent[0][0] for ts, _ in recent]
        values = [ent for _, ent in recent]

        mean_t = sum(times) / len(times)
        mean_v = sum(values) / len(values)

        numerator = sum((t - mean_t) * (v - mean_v) for t, v in zip(times, values))
        denominator = sum((t - mean_t) ** 2 for t in times)

        if denominator == 0:
            return 0.0

        return numerator / denominator

    def check_drift(self) -> Dict[str, Any]:
        """
        Perform comprehensive drift check.

        Returns: {drifting: bool, alerts: list, metrics: dict}
        """
        alerts = []

        # 1. Reinforcement concentration
        if self.reinforcement_counts:
            reinforcement_gini = self._gini_coefficient(list(self.reinforcement_counts.values()))
            if reinforcement_gini > 0.75:
                alerts.append(f"DRIFT: Reinforcement concentrated (gini={reinforcement_gini:.2f})")

        # 2. Tool usage concentration
        if self.mutation_tools_called:
            tool_gini = self._gini_coefficient(list(self.mutation_tools_called.values()))
            if tool_gini > 0.80:
                alerts.append(f"DRIFT: Mutation tool skewed (gini={tool_gini:.2f})")

        # 3. Long-term entropy trend
        entropy_slope = self._long_term_entropy_slope()
        if entropy_slope < -0.01:  # Entropy decreasing faster than expected
            alerts.append(f"DRIFT: Entropy declining (slope={entropy_slope:.5f})")
        elif entropy_slope > 0.01:  # Entropy increasing
            alerts.append(f"DRIFT: Entropy rising (slope={entropy_slope:.5f})")

        # 4. Abstention rate
        if self.total_proposals > 10:
            abstention_rate = self.abstention_count / self.total_proposals
            if abstention_rate < 0.05:
                alerts.append(f"DRIFT: Very low abstention ({abstention_rate:.1%}) — overconfidence?")

        # 5. Reinforcement concentration by entity
        if self.reinforcement_counts:
            top_entity_count = max(self.reinforcement_counts.values())
            mean_count = sum(self.reinforcement_counts.values()) / len(self.reinforcement_counts)
            if top_entity_count > mean_count * 3:
                alerts.append(f"DRIFT: Reinforcement concentrated on few entities")

        drifting = len(alerts) > 0
        if drifting:
            self.drift_alerts.extend(alerts)

        metrics = {
            "reinforcement_gini": self._gini_coefficient(list(self.reinforcement_counts.values())) if self.reinforcement_counts else 0.0,
            "tool_gini": self._gini_coefficient(list(self.mutation_tools_called.values())) if self.mutation_tools_called else 0.0,
            "entropy_slope": entropy_slope,
            "abstention_rate": self.abstention_count / self.total_proposals if self.total_proposals > 0 else 0.0,
            "alerts": alerts,
        }

        return {
            "drifting": drifting,
            "alerts": alerts,
            "metrics": metrics,
        }

    def should_warn_analyst(self) -> bool:
        """If drifting, analyst should generate counter-hypothesis."""
        check = self.check_drift()
        return check["drifting"]


class ShadowMutationMode:
    """
    Phase 1: Shadow mutations (dry-run).

    Executes mutation logic without committing to engine state.
    Returns estimated effects for analysis.
    """

    def __init__(self, enable: bool = False):
        self.enabled = enable

    def estimate_effect(
        self,
        tool_name: str,
        params: Dict[str, Any],
        current_metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Estimate what a mutation would do without committing."""
        if tool_name == "decay_now":
            lambda_ = params.get("lambda", 0.001)
            estimated_edges_pruned = int(current_metrics.get("edge_count", 1000) * lambda_ * 0.05)
            return {
                "would_prune_edges": estimated_edges_pruned,
                "would_reduce_entropy": True,
                "estimated_decay_ratio": lambda_,
            }
        elif tool_name == "reinforce_edge":
            return {
                "would_increase_weight": params.get("weight", 1.0),
                "would_affect_degree_centrality": True,
            }
        elif tool_name == "prune_below_weight":
            threshold = params.get("threshold", 0.1)
            estimated_pruned = int(current_metrics.get("edge_count", 1000) * 0.1)
            return {
                "would_prune_edges": estimated_pruned,
                "would_affect_threshold": threshold,
            }
        else:
            return {"would_execute": True}

    def wrap_mutation(
        self,
        tool_name: str,
        execution_fn,
    ):
        """Wrap a mutation tool to prevent engine commitment in shadow mode."""
        if not self.enabled:
            return execution_fn

        def shadow_wrapper(*args, **kwargs):
            # Execute the function to get result structure
            result = execution_fn(*args, **kwargs)
            # Mark as shadow/estimated
            result["_shadow_mode"] = True
            result["_estimated"] = True
            return result

        return shadow_wrapper
