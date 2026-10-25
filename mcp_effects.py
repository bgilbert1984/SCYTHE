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


# Approval modes (ADR 0006): the stages between authorization and execution, weakest
# first.  They describe which stages exist, not how trusted a tool is.
DIRECT = "DIRECT"                      # no approval stage
AUTOMATED = "AUTOMATED"                # policy evaluation (mcp_safety._check_mutation_approval)
SECOND_PRINCIPAL = "SECOND_PRINCIPAL"  # policy evaluation plus another identified principal
APPROVAL_MODES = (DIRECT, AUTOMATED, SECOND_PRINCIPAL)

# The architectural floor: the weakest approval mode each effect may have.  This is the
# one place the mapping lives.  It records today's behavior; only AUTOMATED is enforced
# (SECOND_PRINCIPAL has no pending state or approval action yet, #228).
MINIMUM_APPROVAL_MODE = {
    READ_ONLY: DIRECT,
    AUDIT_ONLY: DIRECT,
    LOCAL_STATE: AUTOMATED,
    TRAINING_DATA: AUTOMATED,
    EXTERNAL_STATE: AUTOMATED,
    HARDWARE_CONTROL: AUTOMATED,
}

# What an effect requires when the deployment says nothing: above the floor where the floor
# is too weak to be a silent default.  Missing configuration must not select automated
# approval for physical hardware (#228).  A deployment may state a different mode for the
# effect in ``MCP_APPROVAL_MODES``, down to the floor; that is then an explicit, visible choice.
DEFAULT_APPROVAL_MODE = {
    HARDWARE_CONTROL: SECOND_PRINCIPAL,
}


def required_approval_mode(effect: str, deployment: Optional[dict] = None, halts: bool = False) -> str:
    """The approval mode an effect requires: the floor, or a stricter deployment setting.

    ``deployment`` maps effect to a requested mode.  Without a request the mode is the
    effect's default (``DEFAULT_APPROVAL_MODE``), else its floor.  A request may raise the
    mode, or lower it to the floor at most, so no configuration undoes a guarantee the
    declared effect establishes; a request below the floor raises ``ValueError`` instead
    of being ignored.

    ``halts`` is the one exception (ADR 0006 Decision 7): a tool whose author declared in code
    that it only stops activity is capped at ``AUTOMATED``, so a deployment that raises the
    effect to ``SECOND_PRINCIPAL`` does not make a stop wait for another operator.  It comes
    from the tool, never from ``deployment``.
    """
    if effect not in MINIMUM_APPROVAL_MODE:
        raise ValueError(f"unknown effect {effect!r}; expected one of {sorted(MINIMUM_APPROVAL_MODE)}")
    floor = MINIMUM_APPROVAL_MODE[effect]
    requested = (deployment or {}).get(effect)
    if requested is None:
        requested = DEFAULT_APPROVAL_MODE.get(effect, floor)
    if requested not in APPROVAL_MODES:
        raise ValueError(f"effect {effect}: unknown approval mode {requested!r}; expected one of {list(APPROVAL_MODES)}")
    if APPROVAL_MODES.index(requested) < APPROVAL_MODES.index(floor):
        raise ValueError(f"effect {effect}: a deployment may not lower the approval mode below {floor} (asked for {requested})")
    if halts and APPROVAL_MODES.index(requested) > APPROVAL_MODES.index(AUTOMATED):
        return AUTOMATED
    return requested


APPROVAL_MODES_ENV = "MCP_APPROVAL_MODES"


def parse_approval_modes(text: Optional[str]) -> dict:
    """Parse a deployment approval setting such as ``HARDWARE_CONTROL=SECOND_PRINCIPAL,LOCAL_STATE=AUTOMATED``.

    Empty or ``None`` is no setting.  Anything malformed, an unknown effect or mode, a
    repeated effect, or a mode below the floor raises ``ValueError``, so a bad setting
    stops startup instead of being half applied.
    """
    modes: dict = {}
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        effect, sep, mode = (part.strip() for part in item.partition("="))
        if not sep or not effect or not mode:
            raise ValueError(f"{APPROVAL_MODES_ENV}: expected EFFECT=MODE, got {item!r}")
        if effect in modes:
            raise ValueError(f"{APPROVAL_MODES_ENV}: effect {effect} is set more than once")
        required_approval_mode(effect, {effect: mode})  # validates effect, mode and the floor
        modes[effect] = mode
    return modes


def deployment_approval_modes(environ=None) -> dict:
    """The deployment's approval settings, read from ``MCP_APPROVAL_MODES`` (see ``parse_approval_modes``)."""
    import os
    return parse_approval_modes((os.environ if environ is None else environ).get(APPROVAL_MODES_ENV))


APPROVAL_TTL_ENV = "MCP_APPROVAL_TTL_SECONDS"
DEFAULT_APPROVAL_TTL_SECONDS = 900.0


def deployment_approval_ttl(environ=None) -> float:
    """Seconds a ``pending_approval`` proposal stays approvable (``MCP_APPROVAL_TTL_SECONDS``, default 900).

    Anything that is not a positive number raises ``ValueError``, so a bad value stops startup.
    """
    import math
    import os
    raw = (os.environ if environ is None else environ).get(APPROVAL_TTL_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_APPROVAL_TTL_SECONDS
    try:
        ttl = float(raw)
    except ValueError:
        raise ValueError(f"{APPROVAL_TTL_ENV}: expected a number of seconds, got {raw!r}") from None
    if not math.isfinite(ttl) or ttl <= 0:
        raise ValueError(f"{APPROVAL_TTL_ENV}: must be a positive number of seconds, got {raw!r}")
    return ttl


def validate_approval_table() -> list[str]:
    """One message per way ``MINIMUM_APPROVAL_MODE`` disagrees with the effect taxonomy."""
    problems = []
    for effect in sorted(EFFECT_CATEGORIES - set(MINIMUM_APPROVAL_MODE)):
        problems.append(f"approval table: effect {effect} has no minimum mode")
    for effect in sorted(set(MINIMUM_APPROVAL_MODE) - EFFECT_CATEGORIES):
        problems.append(f"approval table: {effect!r} is not an effect category")
    for effect, mode in sorted(MINIMUM_APPROVAL_MODE.items()):
        if mode not in APPROVAL_MODES:
            problems.append(f"approval table: effect {effect} has unknown mode {mode!r}")
        elif (effect in NON_MUTATING) != (mode == DIRECT):
            problems.append(f"approval table: effect {effect} mapped to {mode}; only non-mutating effects may be DIRECT, and they must be")
    for effect, mode in sorted(DEFAULT_APPROVAL_MODE.items()):
        if effect not in MINIMUM_APPROVAL_MODE:
            problems.append(f"approval table: default for {effect!r}, which is not an effect category")
        elif mode not in APPROVAL_MODES:
            problems.append(f"approval table: effect {effect} has unknown default mode {mode!r}")
        elif APPROVAL_MODES.index(mode) < APPROVAL_MODES.index(MINIMUM_APPROVAL_MODE[effect]):
            problems.append(f"approval table: default mode {mode} for {effect} is below its floor")
    return problems


AUTO_APPROVE = "auto_approve"
REQUIRES_REVIEW = "requires_review"
# NOTE: REQUIRES_REVIEW currently means automated policy evaluation only.  No
# independent (second-principal) approval is required: the proposal is approved
# by the mutation checks in mcp_safety (caller-supplied confidence, executor
# trust, graph stability), and the proposer can execute it.  The name is kept
# for when a second-principal rule is introduced.  See issue #228.


def approval_policy(tool) -> str:
    """Whether a proposal for ``tool`` may be approved without the mutation checks.

    Decided from the tool's declared effect only, never from its name.  Anything
    that is not positively known to be non-mutating needs review: an unknown tool,
    a tool that never declared an effect, and a declared effect that is mutating.
    A tool is auto-approved exactly when its effect's minimum mode is ``DIRECT``.
    """
    if tool is None or not getattr(tool, "effect_declared", False):
        return REQUIRES_REVIEW
    mode = MINIMUM_APPROVAL_MODE.get(getattr(tool, "effect", None))
    if mode == DIRECT and not getattr(tool, "mutates_state", True):
        return AUTO_APPROVE
    return REQUIRES_REVIEW


def _halting_problems(name, tool, effect, mutating) -> list[str]:
    """A ``halts`` tool is exempt from independent approval, so the declaration is narrow.

    It must mutate state (a stop is still a mutation, audited and authorized), must not be
    ``TRAINING_DATA``, and must take no parameters: a tool that can be told where, how or what
    to do can start, configure or retarget, and is not a halting action.
    """
    out = []
    if not mutating:
        out.append(f"{name}: halts is declared on a tool that does not mutate state")
    if effect == TRAINING_DATA:
        out.append(f"{name}: a {TRAINING_DATA} tool can never be a halting action")
    params = getattr(tool, "parameters", None) or {}
    if params.get("properties") or params.get("additionalProperties") is not False:
        out.append(f"{name}: a halting tool must take no parameters (empty properties, additionalProperties false)")
    return out


def validate_tool_contract(tools) -> list[str]:
    """Return one message per violation of the declarative tool contract.

    Checks metadata only, never whether the tool's implementation exists (that is
    the runtime capability question in #220).  ``tools`` maps registered name to
    Tool or ToolDef.  Every tool must have: an object schema for parameters and returns, an
    explicitly declared effect, ``mutates_state`` that agrees with that effect, and
    an approval policy that matches it (non-mutating effects are auto-approved,
    every other effect requires review).
    """
    problems = validate_approval_table()
    for name, tool in sorted(dict(tools).items()):
        if getattr(tool, "name", name) != name:
            problems.append(f"{name}: registered under a different name than tool.name={tool.name!r}")
        # Registry tools carry parameters/returns; ToolDef (GraphOps) carries input_schema only.
        fields = {"parameters": getattr(tool, "parameters", getattr(tool, "input_schema", None))}
        if hasattr(tool, "returns"):
            fields["returns"] = tool.returns
        for field, schema in fields.items():
            if not isinstance(schema, dict) or schema.get("type") != "object":
                problems.append(f"{name}: {field} must be an object schema")
        if not getattr(tool, "effect_declared", False):
            problems.append(f"{name}: effect is not explicitly declared")
        effect = getattr(tool, "effect", None)
        if effect not in EFFECT_CATEGORIES:
            problems.append(f"{name}: unknown effect {effect!r}")
            continue
        mutating = effect not in NON_MUTATING
        if bool(tool.mutates_state) != mutating:
            problems.append(f"{name}: mutates_state={bool(tool.mutates_state)} contradicts effect {effect}")
        if getattr(tool, "halts", False):
            problems.extend(_halting_problems(name, tool, effect, mutating))
        expected = REQUIRES_REVIEW if mutating else AUTO_APPROVE
        if getattr(tool, "effect_declared", False) and approval_policy(tool) != expected:
            problems.append(f"{name}: approval policy {approval_policy(tool)} does not match effect {effect}")
    return problems


class ToolContractError(RuntimeError):
    """Registered tools violate the declarative contract.  Raised at startup, never swallowed."""

    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__(
            f"{len(self.problems)} MCP tool contract violation(s): " + "; ".join(self.problems))


def assert_tool_contract(tools) -> None:
    """Raise ``ToolContractError`` when ``validate_tool_contract`` finds a violation.

    This judges the metadata of tools that are registered.  A tool whose integration is
    unavailable is simply not registered, so it is not a violation.
    """
    problems = validate_tool_contract(tools)
    if problems:
        raise ToolContractError(problems)
