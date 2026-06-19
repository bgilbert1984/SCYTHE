"""mcp_rl.py — Reinforcement learning on tool sequences.

Learns optimal tool call sequences based on stability-aware rewards.

WARNING: Only enable after Phase 2 (limited mutation) stability is demonstrated.

Reward function:
  reward = signal_gain
         - mutation_cost * weight_mutation
         - rate_limit_hits * weight_rl
         - oscillation_penalty * weight_oscillation
         - error_count * weight_error
         - entropy_volatility * weight_entropy

Optimization target: minimal mutations, stable entropy, maximum signal retention.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
import json

logger = logging.getLogger(__name__)


@dataclass
class ToolSequenceTrajectory:
    """
    Records a sequence of tool calls with associated metrics and reward.
    """
    sequence: List[str]  # tool names in order
    params_list: List[Dict[str, Any]]  # parameters for each tool

    # Outcomes
    initial_entropy: float = 0.0
    final_entropy: float = 0.0
    edge_count_delta: int = 0
    mutation_count: int = 0
    error_count: int = 0
    rate_limit_hits: int = 0
    oscillation_occurred: bool = False
    total_latency_ms: float = 0.0

    # Computed reward
    reward: float = 0.0

    def compute_reward(
        self,
        weight_mutation: float = 0.5,
        weight_rl: float = 2.0,
        weight_oscillation: float = 3.0,
        weight_error: float = 3.0,
        weight_entropy: float = 1.0,
        weight_reinforcement_gini: float = 1.5,
        weight_entropy_slope: float = 1.0,
        weight_tool_skew: float = 1.0,
    ) -> float:
        """
        Compute stability-aware reward for this trajectory.

        Higher reward = better (minimal mutations, stable entropy, no errors, no drift).

        NEW: Added drift penalties to prevent reinforcement concentration, entropy trends,
        and tool distribution skew.
        """
        signal_gain = max(0.0, abs(self.edge_count_delta))
        mutation_cost = self.mutation_count * weight_mutation
        rl_cost = self.rate_limit_hits * weight_rl
        oscillation_penalty = weight_oscillation if self.oscillation_occurred else 0.0
        error_cost = self.error_count * weight_error
        entropy_volatility = abs(self.final_entropy - self.initial_entropy) * weight_entropy

        # NEW: Drift penalties
        # These prevent RL from learning to repeatedly call same tool chain
        # if it yields small positive signal
        reinforcement_gini_penalty = getattr(self, 'reinforcement_gini', 0.0) * weight_reinforcement_gini
        entropy_slope_penalty = abs(getattr(self, 'entropy_slope', 0.0)) * weight_entropy_slope
        tool_sequence_redundancy_penalty = getattr(self, 'tool_redundancy_score', 0.0) * weight_tool_skew

        reward = (
            signal_gain
            - mutation_cost
            - rl_cost
            - oscillation_penalty
            - error_cost
            - entropy_volatility
            - reinforcement_gini_penalty  # NEW
            - entropy_slope_penalty  # NEW
            - tool_sequence_redundancy_penalty  # NEW
        )

        self.reward = reward
        return reward


@dataclass
class QValue:
    """Q-learning state-action value."""
    state_hash: str
    action_tool: str
    q_value: float = 0.0
    visit_count: int = 0
    avg_reward: float = 0.0


class ToolSequenceLearner:
    """
    Simple Q-learning model for tool sequences.

    State: (graph_entropy, edge_count, recent_mutations)
    Action: next_tool_to_call
    Reward: stability-aware outcome metric

    Learns: which tool sequences maximize signal gain while minimizing mutations.
    """

    def __init__(self, alpha: float = 0.1, gamma: float = 0.99, epsilon: float = 0.1):
        """
        Parameters
        ----------
        alpha : learning rate (0-1)
        gamma : discount factor (0-1)
        epsilon : exploration probability (0-1)
        """
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon

        self.q_table: Dict[str, Dict[str, QValue]] = defaultdict(dict)
        self.trajectories: List[ToolSequenceTrajectory] = []
        self.enabled = False
        self.min_stable_samples = 20  # require 20+ stable samples before learning

    def can_enable(self, orchestrator_status: Dict[str, Any]) -> bool:
        """Check if RL should be enabled based on system stability."""
        stability = orchestrator_status.get("stability", {})

        # Only enable if system is demonstrably stable
        is_stable = stability.get("is_stable", False)
        is_oscillating = stability.get("is_oscillating", False)

        # Require at least 20 samples, no oscillation detected
        samples_ok = len(self.trajectories) >= self.min_stable_samples

        can_enable = is_stable and not is_oscillating and samples_ok

        if can_enable and not self.enabled:
            logger.info("[RL] System stable, enabling sequence learning")
            self.enabled = True

        if not can_enable and self.enabled:
            logger.warning("[RL] System instability detected, disabling learning")
            self.enabled = False

        return self.enabled

    def record_trajectory(
        self,
        trajectory: ToolSequenceTrajectory,
    ) -> None:
        """Record observed sequence and outcomes."""
        trajectory.compute_reward()
        self.trajectories.append(trajectory)

        if self.enabled:
            self.update_q_values(trajectory)

    def update_q_values(self, trajectory: ToolSequenceTrajectory) -> None:
        """Update Q-table based on observed trajectory."""
        if len(trajectory.sequence) < 2:
            return

        reward = trajectory.reward

        for i in range(len(trajectory.sequence) - 1):
            current_state = self._state_hash(trajectory, i)
            action = trajectory.sequence[i + 1]

            if current_state not in self.q_table:
                self.q_table[current_state] = {}

            if action not in self.q_table[current_state]:
                self.q_table[current_state][action] = QValue(
                    state_hash=current_state,
                    action_tool=action,
                )

            qval = self.q_table[current_state][action]

            # Q-learning update
            old_q = qval.q_value
            next_state_max_q = self._max_q_next_state(trajectory, i + 1)
            new_q = old_q + self.alpha * (reward + self.gamma * next_state_max_q - old_q)

            qval.q_value = new_q
            qval.visit_count += 1
            qval.avg_reward = (qval.avg_reward * (qval.visit_count - 1) + reward) / qval.visit_count

    def _state_hash(self, trajectory: ToolSequenceTrajectory, index: int) -> str:
        """Hash the state at a given index in trajectory."""
        # Simplified state: entropy band, edge count band, mutation count
        entropy_band = int(trajectory.initial_entropy / 0.1)
        edge_band = int(trajectory.edge_count_delta / 10)
        mut_count = min(trajectory.mutation_count, 5)

        return f"state_{entropy_band}_{edge_band}_{mut_count}"

    def _max_q_next_state(self, trajectory: ToolSequenceTrajectory, index: int) -> float:
        """Get max Q-value for next state."""
        if index >= len(trajectory.sequence) - 1:
            return 0.0

        next_state = self._state_hash(trajectory, index)
        if next_state not in self.q_table:
            return 0.0

        return max(
            (qval.q_value for qval in self.q_table[next_state].values()),
            default=0.0,
        )

    def recommend_next_tool(
        self,
        current_entropy: float,
        edge_count: int,
        recent_mutations: int,
        available_tools: List[str],
        prefer_observation: bool = True,
    ) -> Optional[str]:
        """
        Use learned Q-values to recommend next tool.

        With epsilon probability, explore randomly.
        Otherwise, exploit best-known Q-value.
        """
        if not self.enabled:
            return None

        import random

        state = f"state_{int(current_entropy/0.1)}_{int(edge_count/10)}_{min(recent_mutations, 5)}"

        # Epsilon-greedy: explore or exploit
        if random.random() < self.epsilon:
            # Explore: random tool from available
            return random.choice(available_tools) if available_tools else None

        # Exploit: best Q-value for this state
        if state not in self.q_table:
            return None

        state_q = self.q_table[state]

        # Filter by available tools
        candidates = [t for t in available_tools if t in state_q]
        if not candidates:
            return None

        # Prefer observation tools if flag set
        if prefer_observation:
            observation_tools = [t for t in candidates if "query" in t or "get" in t]
            if observation_tools:
                return max(observation_tools, key=lambda t: state_q[t].q_value)

        # Otherwise return highest Q-value tool
        return max(candidates, key=lambda t: state_q[t].q_value)

    def get_statistics(self) -> Dict[str, Any]:
        """Return learning statistics."""
        total_reward = sum(t.reward for t in self.trajectories)
        avg_reward = total_reward / len(self.trajectories) if self.trajectories else 0.0

        total_q_states = len(self.q_table)
        total_q_entries = sum(len(actions) for actions in self.q_table.values())

        return {
            "enabled": self.enabled,
            "trajectories_recorded": len(self.trajectories),
            "avg_trajectory_reward": avg_reward,
            "q_table_states": total_q_states,
            "q_table_entries": total_q_entries,
            "alpha": self.alpha,
            "gamma": self.gamma,
            "epsilon": self.epsilon,
        }

    def to_json(self) -> str:
        """Export learned model to JSON."""
        export = {
            "enabled": self.enabled,
            "statistics": self.get_statistics(),
            "q_table": {
                state: {
                    action: {
                        "q_value": qval.q_value,
                        "visit_count": qval.visit_count,
                        "avg_reward": qval.avg_reward,
                    }
                    for action, qval in actions.items()
                }
                for state, actions in self.q_table.items()
            },
        }
        return json.dumps(export, indent=2)


class MinimalMutationPlanner:
    """
    Uses learned RL model to plan minimal-mutation sequences.

    Plan: [query_hot_entities → query_recent_edges → reinforce_edge]

    Instead of: [run_tak_ml → prune → decay → run_tak_ml → prune]
    """

    def __init__(self, learner: ToolSequenceLearner):
        self.learner = learner

    def plan_sequence(
        self,
        current_entropy: float,
        edge_count: int,
        recent_mutations: int,
        available_tools: List[str],
        max_length: int = 5,
    ) -> List[str]:
        """
        Plan a sequence of tools that maximizes signal while minimizing mutations.
        """
        if not self.learner.enabled:
            # Fallback: prefer observation tools
            return [t for t in available_tools if "query" in t or "get" in t][:max_length]

        plan = []
        current_ent = current_entropy
        current_edges = edge_count
        current_muts = recent_mutations

        for _ in range(max_length):
            recommended = self.learner.recommend_next_tool(
                current_entropy=current_ent,
                edge_count=current_edges,
                recent_mutations=current_muts,
                available_tools=available_tools,
                prefer_observation=True,
            )

            if recommended is None:
                break

            plan.append(recommended)

            # Update state estimate (simplified)
            if "query" in recommended:
                # Queries don't change state
                pass
            elif "prune" in recommended or "decay" in recommended:
                current_muts += 1
                current_edges = max(0, current_edges - 10)

        return plan


# Integration hook for RFC/MCP

def integrate_rl_with_registry(registry, learner: ToolSequenceLearner) -> None:
    """
    Patch registry to track trajectories for learning.

    Call this after registry instantiation to enable RL telemetry.
    """
    original_execute = registry.execute

    def execute_with_rl_tracking(engine, name, params, agent_mode="observe", mutation_budget=None):
        start_entropy = getattr(engine, "entropy", 0.0)
        start_edge_count = len(getattr(engine, "edges", []))

        result = original_execute(engine, name, params, agent_mode, mutation_budget)

        end_entropy = getattr(engine, "entropy", 0.0)
        end_edge_count = len(getattr(engine, "edges", []))

        # Simple trajectory recording
        if learner.enabled:
            traj = ToolSequenceTrajectory(
                sequence=[name],
                params_list=[params],
                initial_entropy=start_entropy,
                final_entropy=end_entropy,
                edge_count_delta=end_edge_count - start_edge_count,
                mutation_count=1 if registry._tools[name].mutates_state else 0,
            )
            learner.record_trajectory(traj)

        return result

    registry.execute = execute_with_rl_tracking
