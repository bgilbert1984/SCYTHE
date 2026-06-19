"""
capture_policy.py — Policy DSL for auto-capture authorization.

TAK-GPT never runs tcpdump.  It emits machine-verifiable intent via
pcap.capture commands.  This module evaluates whether a capture command
should be auto-authorized, requires operator approval, or is denied.

The policy engine evaluates a capture command dict against a set of rules.
Each rule has:
    - conditions  — predicates on graph state / command fields
    - action      — AUTHORIZE | REQUIRE_APPROVAL | DENY
    - constraints — max_duration, allowed_interfaces, max_concurrent, etc.

---------------------------------------------------------------------
Design principles
---------------------------------------------------------------------
1.  *Least privilege*: default action is REQUIRE_APPROVAL.
2.  *Deterministic*: same input → same output (no randomness).
3.  *Auditable*: every decision returns a structured verdict dict.
4.  *Extensible*: add rules via ``add_rule()`` or load from JSON.

Usage:
    from capture_policy import CapturePolicy, PolicyRule
    policy = CapturePolicy()
    policy.add_rule(PolicyRule(
        name="auto_critical",
        conditions={"priority": "critical", "trust_posture": "inference-heavy"},
        action="AUTHORIZE",
        constraints={"max_duration": 120, "allowed_interfaces": ["any"]},
    ))
    verdict = policy.evaluate(capture_command, graph_context)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

VALID_ACTIONS = {"AUTHORIZE", "REQUIRE_APPROVAL", "DENY"}
DEFAULT_ACTION = "REQUIRE_APPROVAL"

# Default safety constraints applied to all AUTHORIZE verdicts
DEFAULT_CONSTRAINTS = {
    "max_duration": 300,           # 5 minutes max without escalation
    "max_concurrent": 3,           # max simultaneous capture sessions
    "allowed_interfaces": ["any", "eth0", "wlan0"],
    "max_file_size_mb": 500,
}


# ─────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class PolicyRule:
    """A single policy rule for capture authorization."""
    name: str = ""
    description: str = ""
    conditions: Dict[str, Any] = field(default_factory=dict)
    action: str = DEFAULT_ACTION       # AUTHORIZE | REQUIRE_APPROVAL | DENY
    constraints: Dict[str, Any] = field(default_factory=dict)
    priority_order: int = 100          # lower = evaluated first
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyVerdict:
    """Result of evaluating a capture command against the policy."""
    action: str = DEFAULT_ACTION       # AUTHORIZE | REQUIRE_APPROVAL | DENY
    matched_rule: str = ""             # name of the rule that fired
    constraints: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    evaluated_at: str = ""
    command_task_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────
# Policy engine
# ─────────────────────────────────────────────────────────────────────────

class CapturePolicy:
    """Evaluates pcap.capture commands against configurable rules.

    The engine iterates rules in ``priority_order`` (ascending).  The first
    rule whose conditions match produces the verdict.  If no rule matches
    the default action is REQUIRE_APPROVAL.
    """

    def __init__(self) -> None:
        self.rules: List[PolicyRule] = []
        self._load_defaults()

    # ── rule management ──────────────────────────────────────────────

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a policy rule.  Duplicate names overwrite."""
        self.rules = [r for r in self.rules if r.name != rule.name]
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority_order)

    def remove_rule(self, name: str) -> bool:
        before = len(self.rules)
        self.rules = [r for r in self.rules if r.name != name]
        return len(self.rules) < before

    def list_rules(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self.rules]

    def load_rules_from_list(self, rules: List[Dict[str, Any]]) -> int:
        """Bulk-load rules from a list of dicts. Returns count loaded."""
        count = 0
        for rd in rules:
            try:
                rule = PolicyRule(
                    name=rd.get("name", f"rule_{count}"),
                    description=rd.get("description", ""),
                    conditions=rd.get("conditions", {}),
                    action=rd.get("action", DEFAULT_ACTION),
                    constraints=rd.get("constraints", {}),
                    priority_order=int(rd.get("priority_order", 100)),
                    enabled=rd.get("enabled", True),
                )
                if rule.action not in VALID_ACTIONS:
                    rule.action = DEFAULT_ACTION
                self.add_rule(rule)
                count += 1
            except Exception as e:
                logger.warning("[capture_policy] skipping bad rule: %s", e)
        return count

    # ── evaluation ───────────────────────────────────────────────────

    def evaluate(
        self,
        command: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyVerdict:
        """Evaluate a pcap.capture command against all rules.

        Args:
            command: pcap.capture command dict from emit_capture_command()
            context: optional graph context dict with keys like:
                trust_posture, stale_count, active_capture_count,
                operator_role, etc.

        Returns:
            PolicyVerdict with action, matched_rule, constraints, reason.
        """
        ctx = context or {}
        now = time.time()

        # Merge command fields + context for condition matching
        # No synthetic keys — use native __exists operators in conditions
        match_data = {
            **command,
            **ctx,
        }

        for rule in self.rules:
            if not rule.enabled:
                continue
            if self._conditions_match(rule.conditions, match_data):
                # Apply safety constraints
                constraints = {**DEFAULT_CONSTRAINTS, **rule.constraints}

                # Enforce max_duration cap
                req_dur = command.get("duration_seconds", 60)
                max_dur = constraints.get("max_duration", 300)
                if req_dur > max_dur:
                    constraints["capped_duration"] = max_dur
                    constraints["requested_duration"] = req_dur

                # Check interface allowlist
                req_iface = command.get("interface", "any")
                allowed = constraints.get("allowed_interfaces", ["any"])
                if req_iface not in allowed:
                    return PolicyVerdict(
                        action="DENY",
                        matched_rule=rule.name,
                        constraints=constraints,
                        reason=f"Interface '{req_iface}' not in allowed list: {allowed}",
                        evaluated_at=_iso_now(),
                        command_task_id=command.get("task_id", ""),
                    )

                # Check max concurrent
                active = ctx.get("active_capture_count", 0)
                max_conc = constraints.get("max_concurrent", 3)
                if active >= max_conc and rule.action == "AUTHORIZE":
                    return PolicyVerdict(
                        action="REQUIRE_APPROVAL",
                        matched_rule=rule.name,
                        constraints=constraints,
                        reason=f"Max concurrent captures ({max_conc}) reached — escalating to approval",
                        evaluated_at=_iso_now(),
                        command_task_id=command.get("task_id", ""),
                    )

                return PolicyVerdict(
                    action=rule.action,
                    matched_rule=rule.name,
                    constraints=constraints,
                    reason=f"Rule '{rule.name}' matched: {rule.description}",
                    evaluated_at=_iso_now(),
                    command_task_id=command.get("task_id", ""),
                )

        # No rule matched → default
        return PolicyVerdict(
            action=DEFAULT_ACTION,
            matched_rule="__default__",
            constraints=DEFAULT_CONSTRAINTS.copy(),
            reason="No policy rule matched — defaulting to REQUIRE_APPROVAL",
            evaluated_at=_iso_now(),
            command_task_id=command.get("task_id", ""),
        )

    # ── condition matching ───────────────────────────────────────────

    @staticmethod
    def _conditions_match(
        conditions: Dict[str, Any], data: Dict[str, Any]
    ) -> bool:
        """Check if all conditions are satisfied by the data.

        Supported condition operators:
            "key": value           — exact match
            "key__gte": value      — >=
            "key__lte": value      — <=
            "key__gt": value       — >
            "key__lt": value       — <
            "key__in": [values]    — value in list
            "key__contains": value — value in data[key] (for lists/strings)
            "key__exists": bool    — key presence check
        """
        if not conditions:
            return True

        for cond_key, cond_val in conditions.items():
            # Parse operator suffix
            parts = cond_key.split("__")
            field_name = parts[0]
            op = parts[1] if len(parts) > 1 else "eq"

            actual = data.get(field_name)

            if op == "exists":
                # Treat None and empty string/list as "not existing"
                present = actual is not None and actual != "" and actual != []
                if cond_val and not present:
                    return False
                if not cond_val and present:
                    return False
            elif op == "eq":
                if actual != cond_val:
                    return False
            elif op == "gte":
                if actual is None or actual < cond_val:
                    return False
            elif op == "lte":
                if actual is None or actual > cond_val:
                    return False
            elif op == "gt":
                if actual is None or actual <= cond_val:
                    return False
            elif op == "lt":
                if actual is None or actual >= cond_val:
                    return False
            elif op == "in":
                if actual not in (cond_val or []):
                    return False
            elif op == "contains":
                if actual is None:
                    return False
                if cond_val not in actual:
                    return False
            else:
                # Unknown operator → skip (permissive)
                logger.warning("[capture_policy] unknown operator: %s", op)

        return True

    # ── default rules ────────────────────────────────────────────────

    def _load_defaults(self) -> None:
        """Load sensible default policy rules."""
        defaults = [
            PolicyRule(
                name="auto_critical_inference_heavy",
                description="Auto-authorize critical tasks when trust posture is inference-heavy",
                conditions={
                    "priority": "critical",
                    "trust_posture": "inference-heavy",
                },
                action="AUTHORIZE",
                constraints={
                    "max_duration": 120,
                    "allowed_interfaces": ["any", "eth0", "wlan0"],
                },
                priority_order=10,
            ),
            PolicyRule(
                name="auto_high_with_filter",
                description="Auto-authorize high-priority tasks that have a BPF filter set",
                conditions={
                    "priority": "high",
                    "filter__exists": True,
                },
                action="AUTHORIZE",
                constraints={
                    "max_duration": 180,
                    "allowed_interfaces": ["any", "eth0", "wlan0"],
                },
                priority_order=20,
            ),
            PolicyRule(
                name="deny_long_captures",
                description="Deny captures longer than 10 minutes without explicit approval",
                conditions={
                    "duration_seconds__gt": 600,
                },
                action="DENY",
                constraints={},
                priority_order=5,
            ),
            PolicyRule(
                name="require_approval_medium",
                description="Medium priority tasks require operator approval",
                conditions={
                    "priority": "medium",
                },
                action="REQUIRE_APPROVAL",
                constraints={
                    "max_duration": 300,
                },
                priority_order=50,
            ),
            PolicyRule(
                name="require_approval_no_filter",
                description="Require approval when filter is default/empty (broad capture)",
                conditions={
                    "filter": "ip or ip6",
                    "priority__in": ["medium", "low"],
                },
                action="REQUIRE_APPROVAL",
                constraints={
                    "max_duration": 60,
                },
                priority_order=30,
            ),
        ]
        for rule in defaults:
            self.add_rule(rule)


# ─────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────

def _iso_now() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


# Singleton
_policy_instance: Optional[CapturePolicy] = None


def get_capture_policy() -> CapturePolicy:
    """Return the singleton CapturePolicy instance."""
    global _policy_instance
    if _policy_instance is None:
        _policy_instance = CapturePolicy()
    return _policy_instance
