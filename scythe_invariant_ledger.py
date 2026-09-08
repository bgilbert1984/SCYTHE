"""Whether a declared transition left the world in a state it said it would.

    before_signature + declared_transformation -> expected_after_signature
                                                        |
                                              observed_after_signature

The difference is a first-class result. Nothing here mutates anything, calls
WriteBus, or reaches a graph: a validation tool that could write would be
another unconditional ingest-and-mutate pipeline, which is the shape this
repository keeps refusing.

Three invariant classes, kept apart
-----------------------------------
Calling all three "conservation" would be seductive and wrong.

**Exact invariants** must remain identical unless a named transition authorizes
their movement. A signal-chain hash, an incident id, a channel purpose. Movement
without authorization is a prohibited change.

**Required transitions** must change, and failing to change is itself evidence.
A successful process restart must supersede the old incarnation; a retune must
advance the configuration epoch. This is where a ledger improves on ordinary
integrity checking, which only ever asks whether something moved that should
not have.

**Bounded balances** are approximate numeric relationships with a declared
tolerance and a published accounting basis. Never exact: a receiver's gain,
filter and window intentionally transform power, and demanding an exact
identity across a channelizer would be false by construction. What is required
is that the transformation *say what it accounts for*, and a balance with no
accounting basis cannot be constructed.

Absence has kinds
-----------------
``ABSENT``, ``NOT_ASSESSED``, ``UNVERIFIED``, ``UNSUPPORTED`` and ``VALUE`` are
distinct, and flattening them into ``None`` would put back the ambiguity this
repository has spent a long thread removing. In particular, comparing anything
against ``NOT_ASSESSED`` yields **INDETERMINATE** -- not "unchanged" -- because
nobody looked, and a field nobody looked at must never satisfy an invariant by
default.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Mapping, Optional, Tuple


SCHEMA = "scythe.invariant-ledger.v1"

# -- coordinate kinds -----------------------------------------------------

ABSENT = "ABSENT"                  # looked, and there is nothing there
NOT_ASSESSED = "NOT_ASSESSED"      # nobody looked
UNVERIFIED = "UNVERIFIED"          # present, and its authority is not established
UNSUPPORTED = "UNSUPPORTED"        # the apparatus cannot produce this coordinate
VALUE = "VALUE"
COORDINATE_KINDS: Tuple[str, ...] = (ABSENT, NOT_ASSESSED, UNVERIFIED,
                                     UNSUPPORTED, VALUE)

# -- comparison outcomes --------------------------------------------------

UNCHANGED = "UNCHANGED"
CHANGED = "CHANGED"
INDETERMINATE = "INDETERMINATE"
COMPARISONS: Tuple[str, ...] = (UNCHANGED, CHANGED, INDETERMINATE)

# -- verdicts, in precedence order ---------------------------------------
#
# COMPARISON_DOMAIN_CHANGED comes first because nothing else is comparable once
# it fires: a boot boundary destroys the ordering that made incarnation
# comparison mean anything, and reporting a prohibited change underneath it
# would be describing a comparison that could not be made.
COMPARISON_DOMAIN_CHANGED = "COMPARISON_DOMAIN_CHANGED"
EVIDENCE_MISSING = "EVIDENCE_MISSING"
PROHIBITED_CHANGE = "PROHIBITED_CHANGE"
REQUIRED_CHANGE_NOT_OBSERVED = "REQUIRED_CHANGE_NOT_OBSERVED"
NUMERIC_BALANCE_EXCEEDED = "NUMERIC_BALANCE_EXCEEDED"
INVARIANTS_SATISFIED = "INVARIANTS_SATISFIED"
VERDICT_PRECEDENCE: Tuple[str, ...] = (
    COMPARISON_DOMAIN_CHANGED, EVIDENCE_MISSING, PROHIBITED_CHANGE,
    REQUIRED_CHANGE_NOT_OBSERVED, NUMERIC_BALANCE_EXCEEDED, INVARIANTS_SATISFIED)

VERDICT_NOTES: Dict[str, str] = {
    COMPARISON_DOMAIN_CHANGED: (
        "A COORDINATE THAT DEFINES THE COMPARISON DOMAIN MOVED. THE OTHER "
        "COORDINATES ARE NOT COMPARABLE ACROSS IT, AND THIS IS NOT A FAILED "
        "TRANSITION -- IT IS THE ABSENCE OF A DOMAIN IN WHICH TO JUDGE ONE"),
    EVIDENCE_MISSING: (
        "A COORDINATE THE CONTRACT NAMES WAS NOT ASSESSED. NOBODY LOOKED, AND "
        "A FIELD NOBODY LOOKED AT MUST NOT SATISFY AN INVARIANT BY DEFAULT"),
    PROHIBITED_CHANGE: (
        "A COORDINATE MOVED THAT THE TRANSITION DID NOT AUTHORIZE TO MOVE"),
    REQUIRED_CHANGE_NOT_OBSERVED: (
        "A COORDINATE THE TRANSITION REQUIRES TO MOVE DID NOT MOVE. FAILING TO "
        "CHANGE IS EVIDENCE, NOT THE ABSENCE OF IT"),
    NUMERIC_BALANCE_EXCEEDED: (
        "A DECLARED BOUNDED BALANCE DID NOT HOLD WITHIN ITS OWN TOLERANCE"),
    INVARIANTS_SATISFIED: "EVERY DECLARED INVARIANT HELD",
}


class LedgerContractError(ValueError):
    """A contract that cannot be evaluated, refused at construction."""


@dataclass(frozen=True)
class Coordinate:
    """One typed component of a signature. Absence has kinds."""

    kind: str
    value: Any = None

    def __post_init__(self) -> None:
        if self.kind not in COORDINATE_KINDS:
            raise LedgerContractError(
                f"unknown coordinate kind {str(self.kind)[:48]!r}; expected one "
                f"of {', '.join(COORDINATE_KINDS)}")
        if self.kind != VALUE and self.value is not None:
            raise LedgerContractError(
                f"{self.kind} carries no value; a non-VALUE coordinate holding "
                f"one is two claims at once")

    @classmethod
    def of(cls, value: Any) -> "Coordinate":
        return cls(VALUE, value)

    def compare(self, other: "Coordinate") -> str:
        """UNCHANGED, CHANGED or INDETERMINATE. Never a silent default.

        NOT_ASSESSED on either side is indeterminate whatever the other side
        holds: nobody looked, so nothing about movement can be concluded --
        including that there was none.
        """
        if self.kind == NOT_ASSESSED or other.kind == NOT_ASSESSED:
            return INDETERMINATE
        if self.kind != other.kind:
            return CHANGED
        if self.kind != VALUE:
            return UNCHANGED
        return UNCHANGED if self.value == other.value else CHANGED

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "value": self.value}


def signature(**coordinates: Any) -> Dict[str, Coordinate]:
    """Convenience: plain values become VALUE coordinates, Coordinates pass."""
    built = {}
    for name, item in coordinates.items():
        built[name] = item if isinstance(item, Coordinate) else Coordinate.of(item)
    return built


# -- bounded balances -----------------------------------------------------

@dataclass(frozen=True)
class BoundedBalance:
    """total ~= sum(components) +/- tolerance, with a published basis.

    The accounting basis is required. A transformation that cannot say what it
    accounts for -- window coherent gain, equivalent noise bandwidth, discarded
    transients, decimation, DC exclusion, clipping, bins outside the retained
    product -- cannot have its balance checked, because the residual would be
    absorbing effects nobody named.
    """

    name: str
    total_field: str
    component_fields: Tuple[str, ...]
    tolerance: float
    accounting_basis: Tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.component_fields:
            raise LedgerContractError(f"{self.name}: a balance needs components")
        if not self.accounting_basis:
            raise LedgerContractError(
                f"{self.name}: a balance with no published accounting basis "
                f"cannot be checked; its residual would absorb effects nobody "
                f"named")
        if not math.isfinite(self.tolerance) or self.tolerance < 0:
            raise LedgerContractError(f"{self.name}: tolerance must be finite and >= 0")


@dataclass(frozen=True)
class TransitionContract:
    """What one named operation is permitted and required to do."""

    name: str
    must_preserve: Tuple[str, ...] = ()
    must_change: Tuple[str, ...] = ()
    may_change: Tuple[str, ...] = ()
    # Coordinates whose movement destroys the comparability of the rest.
    domain_fields: Tuple[str, ...] = ()
    balances: Tuple[BoundedBalance, ...] = ()
    # Claims this transition may not assert. A retune does not measure a
    # resonance, and a declaration does not calibrate a response; a transition
    # that emitted either would be manufacturing evidence out of bookkeeping.
    # Read from the `claims_field` coordinate, whose value is a collection of
    # asserted claim tokens.
    prohibited_claims: Tuple[str, ...] = ()
    claims_field: str = "claims"

    def __post_init__(self) -> None:
        groups = {"must_preserve": set(self.must_preserve),
                  "must_change": set(self.must_change),
                  "may_change": set(self.may_change),
                  "domain_fields": set(self.domain_fields)}
        names = list(groups)
        for index, first in enumerate(names):
            for second in names[index + 1:]:
                overlap = groups[first] & groups[second]
                if overlap:
                    raise LedgerContractError(
                        f"{self.name}: {', '.join(sorted(overlap))} appears in "
                        f"both {first} and {second}; a coordinate cannot have "
                        f"two rules")

    def declared_fields(self) -> Tuple[str, ...]:
        return tuple(sorted(set(self.must_preserve) | set(self.must_change)
                            | set(self.may_change) | set(self.domain_fields)))


@dataclass(frozen=True)
class Finding:
    """One coordinate, and what was wrong with it."""

    verdict: str
    field: str
    expected: str
    observed: str
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    detail: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"verdict": self.verdict, "field": self.field,
                "expected": self.expected, "observed": self.observed,
                "before": self.before, "after": self.after,
                "detail": self.detail, "authority": "CONTRACT_EVALUATION"}


@dataclass(frozen=True)
class InvariantVerdict:
    """One verdict, and every finding that supports it.

    Two levels for the same reason admission has two: the verdict says what
    happened to the transition, and a transition has one fate; the findings say
    which coordinates were involved, and there may be several.
    """

    verdict: str
    transition: str
    findings: Tuple[Finding, ...] = ()

    @property
    def satisfied(self) -> bool:
        return self.verdict == INVARIANTS_SATISFIED

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "verdict": self.verdict,
            "verdict_note": VERDICT_NOTES[self.verdict],
            "transition": self.transition,
            "findings": [f.as_dict() for f in self.findings],
            "authority": "CONTRACT_EVALUATION",
            "mutates": False,
            "mutation_note": (
                "THIS IS A DERIVED PRODUCT. PROMOTING IT INTO GRAPHOPS IS A "
                "SEPARATE DECISION MADE BY SOMETHING THAT HAS READ IT"),
        }


def _balance_findings(after: Mapping[str, Coordinate],
                      balance: BoundedBalance) -> Tuple[Finding, ...]:
    needed = (balance.total_field,) + balance.component_fields
    numbers: Dict[str, float] = {}
    for name in needed:
        coordinate = after.get(name, Coordinate(NOT_ASSESSED))
        if coordinate.kind == NOT_ASSESSED:
            return (Finding(EVIDENCE_MISSING, name, "A NUMBER", NOT_ASSESSED,
                            detail=f"balance {balance.name}"),)
        if coordinate.kind != VALUE or not isinstance(coordinate.value, (int, float)):
            return (Finding(EVIDENCE_MISSING, name, "A NUMBER", coordinate.kind,
                            detail=f"balance {balance.name}"),)
        numbers[name] = float(coordinate.value)
    total = numbers[balance.total_field]
    parts = sum(numbers[name] for name in balance.component_fields)
    residual = abs(total - parts)
    if residual > balance.tolerance:
        return (Finding(
            NUMERIC_BALANCE_EXCEEDED, balance.total_field,
            f"|total - components| <= {balance.tolerance}",
            f"{residual:.6g}",
            detail=(f"balance {balance.name}; accounting basis "
                    f"{', '.join(balance.accounting_basis)}")),)
    return ()


def check_transition(before: Mapping[str, Coordinate],
                     operation: str,
                     after: Mapping[str, Coordinate],
                     contract: TransitionContract) -> InvariantVerdict:
    """Pure. No clock, no I/O, no mutation, no graph.

    Findings are collected across every declared coordinate rather than
    stopping at the first, and the verdict is the most severe among them by the
    stated precedence. A domain change suppresses the rest: once the comparison
    domain has moved, the other coordinates were never comparable, and
    reporting a prohibited change underneath it would describe a comparison
    that could not be made.
    """
    if operation != contract.name:
        raise LedgerContractError(
            f"contract {contract.name!r} cannot judge operation {operation!r}")

    findings: list = []

    # Domain first, and alone if it moved.
    for name in contract.domain_fields:
        comparison = before.get(name, Coordinate(NOT_ASSESSED)).compare(
            after.get(name, Coordinate(NOT_ASSESSED)))
        if comparison == INDETERMINATE:
            findings.append(Finding(EVIDENCE_MISSING, name, UNCHANGED, INDETERMINATE,
                                    detail="comparison domain"))
        elif comparison == CHANGED:
            findings.append(Finding(
                COMPARISON_DOMAIN_CHANGED, name, UNCHANGED, CHANGED,
                before=before.get(name, Coordinate(NOT_ASSESSED)).as_dict(),
                after=after.get(name, Coordinate(NOT_ASSESSED)).as_dict(),
                detail="the other coordinates are not comparable across this"))
    if any(f.verdict == COMPARISON_DOMAIN_CHANGED for f in findings):
        return InvariantVerdict(COMPARISON_DOMAIN_CHANGED, operation,
                                tuple(f for f in findings
                                      if f.verdict == COMPARISON_DOMAIN_CHANGED))

    for name in contract.must_preserve:
        b = before.get(name, Coordinate(NOT_ASSESSED))
        a = after.get(name, Coordinate(NOT_ASSESSED))
        comparison = b.compare(a)
        if comparison == INDETERMINATE:
            findings.append(Finding(EVIDENCE_MISSING, name, UNCHANGED, INDETERMINATE))
        elif comparison == CHANGED:
            findings.append(Finding(PROHIBITED_CHANGE, name, UNCHANGED, CHANGED,
                                    before=b.as_dict(), after=a.as_dict()))

    for name in contract.must_change:
        b = before.get(name, Coordinate(NOT_ASSESSED))
        a = after.get(name, Coordinate(NOT_ASSESSED))
        comparison = b.compare(a)
        if comparison == INDETERMINATE:
            findings.append(Finding(EVIDENCE_MISSING, name, CHANGED, INDETERMINATE))
        elif comparison == UNCHANGED:
            findings.append(Finding(REQUIRED_CHANGE_NOT_OBSERVED, name,
                                    CHANGED, UNCHANGED,
                                    before=b.as_dict(), after=a.as_dict()))

    # Anything that moved and was never declared. The claims coordinate is
    # exempt when the contract governs it: it is expected to move, and its rule
    # is which claims may appear rather than whether the set changed.
    declared = set(contract.declared_fields())
    if contract.prohibited_claims:
        declared.add(contract.claims_field)
    for name in sorted(set(before) | set(after)):
        if name in declared:
            continue
        b = before.get(name, Coordinate(NOT_ASSESSED))
        a = after.get(name, Coordinate(NOT_ASSESSED))
        if b.compare(a) == CHANGED:
            findings.append(Finding(
                PROHIBITED_CHANGE, name, "UNDECLARED_BY_THIS_TRANSITION", CHANGED,
                before=b.as_dict(), after=a.as_dict(),
                detail="a transition must enumerate what it may move"))

    # Claims the transition is not permitted to assert. Reported as a
    # prohibited change rather than a verdict of its own: the claim set moved to
    # include something this operation may not establish, which is the same kind
    # of violation as a coordinate moving without authorization.
    if contract.prohibited_claims:
        claims = after.get(contract.claims_field, Coordinate(NOT_ASSESSED))
        if claims.kind == NOT_ASSESSED:
            findings.append(Finding(
                EVIDENCE_MISSING, contract.claims_field,
                f"A CLAIM SET, TO CHECK {len(contract.prohibited_claims)} "
                f"PROHIBITED CLAIMS AGAINST", NOT_ASSESSED))
        elif claims.kind == VALUE:
            asserted = tuple(claims.value or ())
            for claim in contract.prohibited_claims:
                if claim in asserted:
                    findings.append(Finding(
                        PROHIBITED_CHANGE, contract.claims_field,
                        f"NOT {claim}", claim,
                        after=claims.as_dict(),
                        detail=(f"{contract.name} may not assert {claim}; this "
                                f"operation does not establish it")))

    for balance in contract.balances:
        findings.extend(_balance_findings(after, balance))

    if not findings:
        return InvariantVerdict(INVARIANTS_SATISFIED, operation, ())
    verdict = min((f.verdict for f in findings), key=VERDICT_PRECEDENCE.index)
    return InvariantVerdict(verdict, operation, tuple(findings))


def ledger_status() -> Dict[str, Any]:
    """The ledger's own boundaries, published rather than discovered."""
    return {
        "schema": SCHEMA,
        "coordinate_kinds": list(COORDINATE_KINDS),
        "comparisons": list(COMPARISONS),
        "verdicts": list(VERDICT_PRECEDENCE),
        "verdict_precedence": list(VERDICT_PRECEDENCE),
        "verdict_notes": dict(VERDICT_NOTES),
        "invariant_classes": ("EXACT", "REQUIRED_TRANSITION", "BOUNDED_BALANCE"),
        "balances_require_accounting_basis": True,
        "prohibited_claims_note": (
            "A CLAIM A TRANSITION MAY NOT ASSERT IS REPORTED AS A PROHIBITED "
            "CHANGE. THE CLAIM SET MOVED TO INCLUDE SOMETHING THE OPERATION "
            "DOES NOT ESTABLISH, WHICH IS THE SAME KIND OF VIOLATION AS A "
            "COORDINATE MOVING WITHOUT AUTHORIZATION"),
        "exact_balance_note": (
            "BOUNDED, NEVER EXACT. A RECEIVER'S GAIN, FILTER AND WINDOW "
            "INTENTIONALLY TRANSFORM POWER; DEMANDING AN EXACT IDENTITY ACROSS "
            "A CHANNELIZER WOULD BE FALSE BY CONSTRUCTION"),
        "not_assessed_note": (
            "COMPARING AGAINST NOT_ASSESSED IS INDETERMINATE, NEVER UNCHANGED. "
            "A FIELD NOBODY LOOKED AT MUST NOT SATISFY AN INVARIANT BY DEFAULT"),
        "mutates": False,
        "graph_promotion": "NOT_IMPLEMENTED",
        "side_effects": "NONE",
    }
