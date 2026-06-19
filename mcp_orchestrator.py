"""mcp_orchestrator.py — Unified orchestration layer for graduated agent autonomy.

Integrates:
  - Registry (tool definitions + execution)
  - Safety (trust scores, stability metrics)
  - Dual agents (Analyst propose, Executor execute)
  - Shadow mutations (Phase 1)
  - RL on sequences (Phase 2+)

Usage:
  orchestrator = AliasMCPOrchestrator(engine)

  # Phase 0: observe-only
  orchestrator.set_phase(0)

  # Phase 1: shadow mutations
  orchestrator.set_phase(1, dry_run=True)

  # Phase 2: limited mutations with auto-demotion
  orchestrator.set_phase(2, mutation_budget=3)

  # Handle analyst proposal
  proposal = orchestrator.propose_action(
    tool_name="reinforce_edge",
    params={"src": "host1", "dst": "host2"},
    confidence=0.85,
    justification="Cross-org reinforcement confirmed by TAK-ML"
  )

  # Handle executor decision based on proposal
  decision = orchestrator.check_proposal(proposal["proposal_id"])

  # Execute if approved
  if decision["approved"]:
    result = orchestrator.execute_proposal(proposal["proposal_id"], engine)
"""
import logging
import time
from typing import Any, Dict, Optional
from enum import Enum

from mcp_registry import Registry, build_registry
from mcp_safety import (
    DualAgentOrchestrator,
    ShadowMutationMode,
    StabilityMetrics,
)
from mcp_rl import ToolSequenceLearner, MinimalMutationPlanner

logger = logging.getLogger(__name__)


class Phase(Enum):
    """Graduated autonomy phases."""
    PHASE_0_OBSERVE_ONLY = 0
    PHASE_1_SHADOW_MUTATION = 1
    PHASE_2_LIMITED_MUTATION = 2
    PHASE_3_ADAPTIVE_BUDGET = 3


class AliasMCPOrchestrator:
    """
    Unified orchestration for tool registry + safety + dual agents.

    Handles proposal/execution lifecycle with safety interlocks at each stage.
    """

    def __init__(self, engine: Any):
        self.engine = engine

        # Registry (tool definitions + execution)
        self.registry = build_registry(engine) if isinstance(build_registry(engine), Registry) else Registry()

        # Safety layer
        self.dual_agent = DualAgentOrchestrator(default_mutation_budget=3)
        self.shadow_mode = ShadowMutationMode(enable=False)

        # RL learning (Phase 3+)
        self.rl_learner = ToolSequenceLearner()
        self.rl_planner = MinimalMutationPlanner(self.rl_learner)

        # Operational state
        self.phase = Phase.PHASE_0_OBSERVE_ONLY
        self.dry_run = False

        # Phase rollback automation
        self.instability_detected_at: Optional[float] = None
        self.instability_threshold_seconds = 300  # rollback after 5 minutes of instability

    def set_phase(
        self,
        phase: int,
        dry_run: bool = False,
        mutation_budget: int = 3,
    ) -> Dict[str, Any]:
        """Transition to a new autonomy phase."""
        prev_phase = self.phase

        if phase == 0:
            self.phase = Phase.PHASE_0_OBSERVE_ONLY
            self.dry_run = False
            self.shadow_mode.enabled = False
            logger.info("[orchestrator] Phase 0: Observe-only baseline")
        elif phase == 1:
            self.phase = Phase.PHASE_1_SHADOW_MUTATION
            self.dry_run = dry_run
            self.shadow_mode.enabled = True
            logger.info("[orchestrator] Phase 1: Shadow mutations (dry_run=%s)", dry_run)
        elif phase == 2:
            self.phase = Phase.PHASE_2_LIMITED_MUTATION
            self.dry_run = False
            self.shadow_mode.enabled = False
            self.dual_agent.base_mutation_budget = mutation_budget
            logger.info("[orchestrator] Phase 2: Limited mutations (budget=%d)", mutation_budget)
        elif phase == 3:
            self.phase = Phase.PHASE_3_ADAPTIVE_BUDGET
            self.dry_run = False
            self.shadow_mode.enabled = False
            self.rl_learner.min_stable_samples = 10  # lower threshold for RL
            logger.info("[orchestrator] Phase 3: Adaptive budget + RL learning")
        else:
            return {"ok": False, "error": f"unknown phase {phase}"}

        return {
            "ok": True,
            "prev_phase": prev_phase.value,
            "new_phase": phase,
            "dry_run": self.dry_run,
            "mutation_budget": getattr(self.dual_agent, "base_mutation_budget", None),
        }

    def propose_action(
        self,
        tool_name: str,
        params: Dict[str, Any],
        confidence: float = 0.75,
        justification: str = "",
        agent_id: str = "analyst",
    ) -> Dict[str, Any]:
        """
        Analyst proposes an action.

        Returns proposal dict with approval status.
        """
        proposal = self.dual_agent.propose_action(
            tool_name=tool_name,
            params=params,
            confidence=confidence,
            justification=justification,
        )

        proposal["agent_id"] = agent_id

        logger.info(
            "[orchestrator] Proposal %s: %s status=%s (confidence=%.2f)",
            proposal["proposal_id"][:8],
            tool_name,
            proposal["status"],
            confidence,
        )

        return proposal

    def check_proposal(self, proposal_id: str) -> Dict[str, Any]:
        """Check approval status of a proposal."""
        proposal = next(
            (p for p in self.dual_agent.decision_log if p["proposal_id"] == proposal_id),
            None,
        )
        if not proposal:
            return {"ok": False, "error": "proposal not found"}

        return {
            "ok": True,
            "proposal_id": proposal_id,
            "tool_name": proposal["tool_name"],
            "approved": proposal["status"] in ("approved", "executed"),
            "status": proposal["status"],
            "approval_reason": proposal.get("approval_reason", ""),
            "confidence": proposal.get("confidence", 0.0),
        }

    def execute_proposal(
        self,
        proposal_id: str,
    ) -> Dict[str, Any]:
        """Execute an approved proposal via the executor agent."""
        proposal = next(
            (p for p in self.dual_agent.decision_log if p["proposal_id"] == proposal_id),
            None,
        )
        if not proposal:
            return {"ok": False, "error": "proposal not found"}

        if proposal["status"] != "approved":
            return {"ok": False, "error": f"proposal status is {proposal['status']}, not approved"}

        tool_name = proposal["tool_name"]
        params = proposal["params"]

        # Get adaptive budget for this phase
        if self.phase in (Phase.PHASE_2_LIMITED_MUTATION, Phase.PHASE_3_ADAPTIVE_BUDGET):
            mutation_budget = self.dual_agent.executor_trust.adaptive_budget(
                self.dual_agent.base_mutation_budget
            )
        else:
            mutation_budget = None

        # Execute via registry
        try:
            if self.shadow_mode.enabled and self.dry_run:
                # Phase 1: shadow mutation
                current_metrics = {
                    "edge_count": len(getattr(self.engine, "edges", [])),
                    "node_count": len(getattr(self.engine, "nodes", {})),
                }
                estimated_effect = self.shadow_mode.estimate_effect(
                    tool_name,
                    params,
                    current_metrics,
                )
                result = {
                    "_shadow_mode": True,
                    "_estimated": True,
                    **estimated_effect,
                }
                proposal["status"] = "executed"
                proposal["execution_result"] = result
            else:
                # Normal execution
                result = self.registry.execute(
                    self.engine,
                    tool_name,
                    params,
                    agent_mode="mutate" if tool_name in self._mutation_tools() else "observe",
                    mutation_budget=mutation_budget,
                )
                proposal["status"] = "executed"
                proposal["execution_result"] = result
                self.dual_agent.stability.add_mutation()
                self.dual_agent.executor_trust.update(success=True)

            logger.info(
                "[orchestrator] Proposal %s executed: %s",
                proposal_id[:8],
                tool_name,
            )

            return {"ok": True, "result": result}
        except Exception as e:
            proposal["status"] = "failed"
            proposal["error"] = str(e)
            self.dual_agent.executor_trust.update(success=False)
            logger.error("[orchestrator] Proposal %s failed: %s", proposal_id[:8], e)
            return {"ok": False, "error": str(e)}

    def get_organism_status(self) -> Dict[str, Any]:
        """Return full orchestrator status (the "organism status")."""
        # Check for auto-rollback triggers
        self.auto_rollback_phase_if_needed()

        status = {
            "phase": self.phase.value,
            "dry_run": self.dry_run,
            "timestamp": time.time(),
        }

        # Dual agent status (includes drift metrics)
        dual_status = self.dual_agent.get_status()
        status["agents"] = dual_status

        # Drift status
        if "drift" in dual_status:
            status["drift"] = dual_status["drift"]
            if dual_status["drift"]["drifting"]:
                if self.instability_detected_at is None:
                    self.instability_detected_at = time.time()
                duration = time.time() - self.instability_detected_at
                status["drift"]["instability_duration_seconds"] = duration
                if duration > self.instability_threshold_seconds:
                    status["drift"]["queued_for_rollback"] = True

        # RL status (if applicable)
        if self.rl_learner.enabled or self.phase == Phase.PHASE_3_ADAPTIVE_BUDGET:
            status["rl"] = self.rl_learner.get_statistics()

        # Recent proposals
        recent_proposals = self.dual_agent.decision_log[-10:]
        status["recent_proposals"] = [
            {
                "proposal_id": p["proposal_id"][:8],
                "tool": p["tool_name"],
                "status": p["status"],
                "confidence": p.get("confidence", 0.0),
            }
            for p in recent_proposals
        ]

        return status

    def _mutation_tools(self) -> set:
        """Get list of mutation tools from registry."""
        mutations = set()
        if hasattr(self.registry, "_tools"):
            for name, tool in self.registry._tools.items():
                if tool.mutates_state:
                    mutations.add(name)
        return mutations

    # ─────────────────────────────────────────────────────────────────
    # Metrics & Observability
    # ─────────────────────────────────────────────────────────────────

    def get_decision_log(self, limit: int = 50) -> list:
        """Return recent decision log (proposals)."""
        return self.dual_agent.decision_log[-limit:]

    def export_decision_timeline(self) -> str:
        """Export decision log as CSV for analysis."""
        import csv
        from io import StringIO

        output = StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "timestamp",
                "proposal_id",
                "tool_name",
                "confidence",
                "status",
                "approval_reason",
            ],
        )
        writer.writeheader()

        for proposal in self.dual_agent.decision_log:
            writer.writerow({
                "timestamp": proposal.get("timestamp", ""),
                "proposal_id": proposal.get("proposal_id", "")[:8],
                "tool_name": proposal.get("tool_name", ""),
                "confidence": proposal.get("confidence", ""),
                "status": proposal.get("status", ""),
                "approval_reason": proposal.get("approval_reason", ""),
            })

        return output.getvalue()

    def check_should_auto_demote(self) -> bool:
        """Check if executor should be auto-demoted to observe mode."""
        return self.dual_agent.executor_trust.should_demote_to_observe()

    def auto_demote_if_needed(self) -> Dict[str, Any]:
        """Check trust score and auto-demote to observe if needed."""
        if self.check_should_auto_demote():
            logger.warning(
                "[orchestrator] Auto-demoting executor to observe (trust score %.2f)",
                self.dual_agent.executor_trust.current_score,
            )
            return self.set_phase(0)
        return {"ok": True, "demoted": False}

    def check_should_auto_rollback_phase(self) -> bool:
        """Check if phase should be auto-rolled back due to persistent instability."""
        # Check for drift
        if self.dual_agent.drift_gate.should_warn_analyst():
            if self.instability_detected_at is None:
                self.instability_detected_at = time.time()
            else:
                duration = time.time() - self.instability_detected_at
                if duration > self.instability_threshold_seconds:
                    return True
        else:
            # System is stable again
            self.instability_detected_at = None

        return False

    def auto_rollback_phase_if_needed(self) -> Dict[str, Any]:
        """Perform systemic phase rollback if instability persists."""
        if self.check_should_auto_rollback_phase():
            current = self.phase.value
            new_phase = max(0, current - 1)
            logger.warning(
                "[orchestrator] Auto-rolling back phase due to persistent instability: %d → %d",
                current,
                new_phase,
            )
            self.instability_detected_at = None  # Reset counter
            return self.set_phase(new_phase)
        return {"ok": True, "rolled_back": False}
