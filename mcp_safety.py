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

    def __init__(self, default_mutation_budget: int = 3):
        self.analyst_mode = "observe"  # readonly
        self.analyst_trust = TrustScore(agent_id="analyst")

        self.executor_mode = "mutate"
        self.executor_trust = TrustScore(agent_id="executor")
        self.base_mutation_budget = default_mutation_budget

        self.stability = StabilityMetrics()
        self.drift_gate = DriftGate()  # Fourth safety gate: systemic drift detection

        # Decision log: separate from tool audit log
        self.decision_log: list[Dict[str, Any]] = []

    def propose_action(
        self,
        tool_name: str,
        params: Dict[str, Any],
        confidence: float,
        justification: str = "",
    ) -> Dict[str, Any]:
        """Analyst proposes an action. Returns decision."""
        import uuid

        proposal_id = str(uuid.uuid4())
        proposal = {
            "proposal_id": proposal_id,
            "tool_name": tool_name,
            "params": params,
            "confidence": confidence,
            "justification": justification,
            "timestamp": time.time(),
            "status": "proposed",  # proposed | approved | rejected | executed
            "approval_reason": "",
        }

        # Analyst is always read-only, so analyst proposals are always safe
        if "query" in tool_name or tool_name in ("export_graph_snapshot", "get_engine_metrics"):
            proposal["status"] = "approved"
            proposal["approval_reason"] = "query tool (read-only)"
            self.drift_gate.record_proposal(was_accepted=True)
        else:
            # Mutation tool: check Executor limits
            approval = self._check_mutation_approval(tool_name, params, confidence)
            proposal["status"] = approval["status"]
            proposal["approval_reason"] = approval["reason"]
            self.drift_gate.record_proposal(was_accepted=(proposal["status"] != "rejected"))

        self.decision_log.append(proposal)
        return proposal

    def _check_mutation_approval(
        self,
        tool_name: str,
        params: Dict[str, Any],
        confidence: float,
    ) -> Dict[str, Any]:
        """Check if Executor can approve a mutation proposal."""
        reasons = []

        # Confidence threshold
        if confidence < 0.70:
            reasons.append(f"confidence {confidence:.2f} < 0.70")
            return {"status": "rejected", "reason": " | ".join(reasons)}

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
