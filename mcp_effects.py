"""Effect categories for MCP tools.

The approval boundary trusts a tool's declared ``mutates_state``.  This module
gives that flag one definition and an explicit vocabulary so a tool can say what
kind of effect it has, and so a declaration that contradicts itself is rejected
when the tool is constructed instead of being believed.

Definition: a tool mutates state when it causes an externally observable state
transition -- including state outside the local graph (a remote service, a
training system, hardware) and including admitting evidence that will change what
later analysis concludes.  "Does not call an engine mutation method" is not the
test.
"""
from __future__ import annotations

from typing import Optional

LOCAL_STATE = "LOCAL_STATE"            # this process's graph, stores, queues, caches
EXTERNAL_STATE = "EXTERNAL_STATE"      # a remote service or database
HARDWARE_CONTROL = "HARDWARE_CONTROL"  # tuner, capture bridge, other devices
TRAINING_DATA = "TRAINING_DATA"        # feedback that changes a model's training set
AUDIT_ONLY = "AUDIT_ONLY"              # writes only records about its own call
READ_ONLY = "READ_ONLY"                # no state transition

EFFECT_CATEGORIES = frozenset(
    {LOCAL_STATE, EXTERNAL_STATE, HARDWARE_CONTROL, TRAINING_DATA, AUDIT_ONLY, READ_ONLY}
)

# Categories that need no approval flow.  Every other category must be declared
# mutating, so it goes through propose -> decide -> execute.
NON_MUTATING = frozenset({READ_ONLY, AUDIT_ONLY})


def resolve_effect(name: str, mutates_state: bool, effect: Optional[str]) -> tuple[str, bool]:
    """Return ``(effect, declared)`` for a tool, or raise ``ValueError``.

    With no ``effect`` the category is inferred from ``mutates_state`` and
    ``declared`` is False, so callers can report tools that never classified
    themselves.  An explicit ``effect`` must agree with ``mutates_state``.
    """
    if effect is None:
        return (LOCAL_STATE if mutates_state else READ_ONLY), False
    if effect not in EFFECT_CATEGORIES:
        raise ValueError(f"tool {name}: unknown effect {effect!r}; expected one of {sorted(EFFECT_CATEGORIES)}")
    needs_mutating = effect not in NON_MUTATING
    if needs_mutating != bool(mutates_state):
        raise ValueError(
            f"tool {name}: effect {effect} contradicts mutates_state={bool(mutates_state)} "
            f"({'must be True' if needs_mutating else 'must be False'} for this effect)"
        )
    return effect, True


def undeclared_effects(tools) -> list[str]:
    """Names of tools that have not declared an explicit effect category."""
    return sorted(
        name for name, tool in dict(tools).items()
        if hasattr(tool, "effect_declared") and not tool.effect_declared
    )


AUTO_APPROVE = "auto_approve"
REQUIRES_REVIEW = "requires_review"


def approval_policy(tool) -> str:
    """Whether a proposal for ``tool`` may be approved without the mutation checks.

    Decided from the tool's declared effect only, never from its name.  Anything
    that is not positively known to be non-mutating needs review: an unknown tool,
    a tool that never declared an effect, and a declared effect that is mutating.
    """
    if tool is None or not getattr(tool, "effect_declared", False):
        return REQUIRES_REVIEW
    if getattr(tool, "effect", None) in NON_MUTATING and not getattr(tool, "mutates_state", True):
        return AUTO_APPROVE
    return REQUIRES_REVIEW
