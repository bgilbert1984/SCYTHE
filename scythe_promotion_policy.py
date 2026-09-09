"""Whether an invariant verdict may become a graph record. Never how.

    invariant verdict
  + explicit promotion request
  + bounded capsule identity
  + promotion policy
  -> PromotionDecision

Pure. No WriteBus, no graph, no clock, no I/O. The decision names what would be
written and under which identity; constructing and performing the write belongs
to an adapter that does not exist yet, and the decision deliberately carries no
material an adapter could execute.

What does not constitute a promotion request
--------------------------------------------
**A frame arriving.** Ingest is not intent. The entire mutation boundary in
``RF_WALK_SURVEY_CONTRACT.md`` §7 exists because a pipeline whose ingest path
ends in a write has collapsed four authorities into one.

**Model commentary.** A model may explain a refusal and suggest an observation.
It may not be the reason a record exists, so a request whose justification is
model-sourced is refused rather than downgraded -- accepting it as weaker
evidence would still make prose the thing that caused a write.

**A satisfied invariant.** ``INVARIANTS_SATISFIED`` is normal operation. One
graph record per successful check is graph confetti, and a graph that records
every time nothing happened cannot be read for the times something did.

Two record classes, kept apart
------------------------------
``INVARIANT_FINDING`` says something was wrong. ``OBSERVATION_GAP`` says
something could not be judged. That is the separation the evidence capsule
established between violations and indeterminate dimensions, carried across the
mutation boundary rather than dropped at it -- a boot boundary promoted as a
finding would make a recovery-failure count a reboot count, one layer further
on than where that was last caught.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from scythe_invariant_capsule import SCHEMA as CAPSULE_SCHEMA
from scythe_invariant_ledger import (
    COMPARISON_DOMAIN_CHANGED, COORDINATE_KINDS, EVIDENCE_MISSING,
    INVARIANTS_SATISFIED, NUMERIC_BALANCE_EXCEEDED, PROHIBITED_CHANGE,
    REQUIRED_CHANGE_NOT_OBSERVED, VALUE, InvariantVerdict,
)


SCHEMA = "scythe.promotion-policy.v1"
POLICY_REVISION = "v2"
SUPPORTED_CAPSULE_SCHEMAS: Tuple[str, ...] = (CAPSULE_SCHEMA,)

# The identity namespace, versioned separately from the record shape. v1 hashed
# only the shape of a disagreement; v2 binds it to coordinates. See below.
VERDICT_DIGEST_REVISION = "v2"

# Every key this policy has ever produced is invalidated by that change. Stated
# rather than migrated: nothing durable holds a v1 key -- no execution adapter
# exists and the shadow ledger writes nothing outside its own process -- so the
# cheapest correct move is to break the namespace once, in the open, now.
PRIOR_PROMOTION_IDENTITY_COMPARABLE = False

# -- dispositions ---------------------------------------------------------

NO_PROMOTION_REQUESTED = "NO_PROMOTION_REQUESTED"
PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
PROMOTION_REFUSED = "PROMOTION_REFUSED"
DISPOSITIONS: Tuple[str, ...] = (NO_PROMOTION_REQUESTED, PROMOTION_ELIGIBLE,
                                 PROMOTION_REFUSED)

# -- record classes -------------------------------------------------------

INVARIANT_FINDING = "INVARIANT_FINDING"
OBSERVATION_GAP = "OBSERVATION_GAP"
RECORD_CLASSES: Tuple[str, ...] = (INVARIANT_FINDING, OBSERVATION_GAP)

# Which class each verdict may become, and nothing else. A verdict absent from
# this table is not promotable, which is how INVARIANTS_SATISFIED is excluded
# without a special case.
VERDICT_RECORD_CLASS: Dict[str, str] = {
    PROHIBITED_CHANGE: INVARIANT_FINDING,
    REQUIRED_CHANGE_NOT_OBSERVED: INVARIANT_FINDING,
    NUMERIC_BALANCE_EXCEEDED: INVARIANT_FINDING,
    # Nobody looked, and the domain is gone. Neither is a violation, and both
    # are worth recording as the shape of what could not be judged.
    EVIDENCE_MISSING: OBSERVATION_GAP,
    COMPARISON_DOMAIN_CHANGED: OBSERVATION_GAP,
}

# -- who may ask ----------------------------------------------------------

PROMOTION_AUTHORITIES: Tuple[str, ...] = ("OPERATOR", "PROMOTION_POLICY")
# Deliberately not an authority. A model may be a justification's *subject*,
# never its source.
MODEL_SOURCE = "MODEL"
JUSTIFICATION_SOURCES: Tuple[str, ...] = PROMOTION_AUTHORITIES + (MODEL_SOURCE,)

SUPPORTED_TARGET_GRAPHS: Tuple[str, ...] = ("scythe.graphops.evidence",)

MAX_TEXT = 240

# -- refusals -------------------------------------------------------------

VERDICT_NOT_PROMOTABLE = "VERDICT_NOT_PROMOTABLE"
INDETERMINATE_AS_FAILURE = "INDETERMINATE_AS_FAILURE"
CAPSULE_UNBOUND = "CAPSULE_UNBOUND"
CAPSULE_REVISION_UNSUPPORTED = "CAPSULE_REVISION_UNSUPPORTED"
MODEL_RESPONSE_USED_AS_AUTHORITY = "MODEL_RESPONSE_USED_AS_AUTHORITY"
DUPLICATE_PROMOTION = "DUPLICATE_PROMOTION"
AUTHORITY_INSUFFICIENT = "AUTHORITY_INSUFFICIENT"
TARGET_UNSUPPORTED = "TARGET_UNSUPPORTED"
REFUSALS: Tuple[str, ...] = (
    VERDICT_NOT_PROMOTABLE, INDETERMINATE_AS_FAILURE, CAPSULE_UNBOUND,
    CAPSULE_REVISION_UNSUPPORTED, MODEL_RESPONSE_USED_AS_AUTHORITY,
    DUPLICATE_PROMOTION, AUTHORITY_INSUFFICIENT, TARGET_UNSUPPORTED)

REFUSAL_NOTES: Dict[str, str] = {
    VERDICT_NOT_PROMOTABLE: (
        "THIS VERDICT DOES NOT BECOME A GRAPH RECORD. INVARIANTS_SATISFIED IS "
        "NORMAL OPERATION, AND ONE RECORD PER SUCCESSFUL CHECK IS CONFETTI A "
        "GRAPH CANNOT BE READ THROUGH"),
    INDETERMINATE_AS_FAILURE: (
        "AN INDETERMINATE DIMENSION WAS REQUESTED AS A FINDING. NOBODY LOOKED, "
        "OR THE COMPARISON DOMAIN WAS GONE; PROMOTING THAT AS A VIOLATION "
        "WOULD MAKE A FAILURE COUNT ABSORB THE THING IT MUST STAY CLEAN OF"),
    CAPSULE_UNBOUND: (
        "THE CAPSULE EXCEEDED ITS DECLARED BOUNDS OR CLAIMED TO CARRY SAMPLES. "
        "A CAPSULE IS A SUMMARY, AND A SUMMARY THAT GREW IS A TRANSPORT"),
    CAPSULE_REVISION_UNSUPPORTED: (
        "THE CAPSULE'S SCHEMA IS NOT ONE THIS POLICY REVISION UNDERSTANDS. A "
        "POLICY THAT GUESSES AT AN UNKNOWN SHAPE IS NOT A POLICY"),
    MODEL_RESPONSE_USED_AS_AUTHORITY: (
        "THE JUSTIFICATION IS MODEL-SOURCED. A MODEL MAY EXPLAIN A REFUSAL AND "
        "SUGGEST AN OBSERVATION; IT MAY NOT BE THE REASON A RECORD EXISTS, AND "
        "ACCEPTING IT AS WEAKER EVIDENCE WOULD STILL MAKE PROSE CAUSE A WRITE"),
    DUPLICATE_PROMOTION: (
        "THIS IDENTITY HAS ALREADY BEEN PROMOTED. RE-EVALUATION IS EXPECTED "
        "AND MUST NOT ACCUMULATE RECORDS"),
    AUTHORITY_INSUFFICIENT: (
        "THE REQUESTER IS NOT A PROMOTION AUTHORITY"),
    TARGET_UNSUPPORTED: (
        "THE NAMED TARGET GRAPH IS NOT ONE THIS POLICY REVISION WRITES TO"),
}


class PromotionIdentityError(ValueError):
    """A coordinate the identity cannot canonically encode. Never approximated.

    Raised rather than hashed loosely: an identity that guesses is exactly the
    defect v2 exists to remove.
    """


class PromotionRequestError(ValueError):
    """A request that cannot be evaluated as one."""


@dataclass(frozen=True)
class CapsuleIdentity:
    """A capsule by reference, not by value.

    The policy never receives capsule contents. It receives what the capsule
    *is* -- its schema, its digest, and its own declaration that it stayed
    within bounds and carried no samples -- so that no raw material can enter a
    decision by riding along inside one.
    """

    schema: str
    digest: str
    within_bounds: bool
    carries_samples: bool

    def as_dict(self) -> Dict[str, Any]:
        return {"schema": self.schema, "digest": self.digest,
                "within_bounds": self.within_bounds,
                "carries_samples": self.carries_samples}


@dataclass(frozen=True)
class PromotionRequest:
    """An explicit ask. Nothing implicit ever produces one."""

    requested_by: str
    target_graph: str
    justification_source: str
    justification: str = ""
    # What the requester believes should be written. Optional: the policy
    # derives the permitted class regardless, and a stated one is checked
    # against it rather than obeyed.
    record_class: Optional[str] = None

    def __post_init__(self) -> None:
        if self.justification_source not in JUSTIFICATION_SOURCES:
            raise PromotionRequestError(
                f"unknown justification_source {str(self.justification_source)[:48]!r}")
        if self.record_class is not None and self.record_class not in RECORD_CLASSES:
            raise PromotionRequestError(
                f"unknown record_class {str(self.record_class)[:48]!r}")

    def as_dict(self) -> Dict[str, Any]:
        return {"requested_by": self.requested_by,
                "target_graph": self.target_graph,
                "justification_source": self.justification_source,
                "justification": self.justification[:MAX_TEXT],
                "record_class_requested": self.record_class}


# -- promotion identity ---------------------------------------------------
#
# v1 hashed (verdict, field, expected, observed) for each finding. For a
# PROHIBITED_CHANGE the last two are the literals UNCHANGED and CHANGED, so the
# digest carried the *shape* of a disagreement and never its coordinates: a
# signal chain moving 100.1 -> 100.2 and the same chain moving 100.2 -> 100.3
# hashed to one value. A ledger doing its job then refuses the second as a
# duplicate, and a distinct finding is discarded without leaving a trace. That
# is a promotion-identity defect and not a ledger limitation -- the ledger was
# correct about the key it was given.
#
# v2 binds each finding to the canonical before and after coordinates that
# check_transition already recorded and the digest simply ignored.

MAX_IDENTITY_DEPTH = 12

# A finding side that was never recorded at all -- a prohibited claim has an
# after and no before. Distinct from a recorded coordinate of kind ABSENT:
# nobody wrote one down, versus somebody wrote down that there is none.
_NO_COORDINATE = ["none", ""]


def _encode(item: Any) -> str:
    return json.dumps(item, sort_keys=True, separators=(",", ":"))


def _canonical_value(value: Any, depth: int = 0) -> Any:
    """Type-tagged scalars, canonical container families, order-canonical.

    Scalars are tagged, so that 1, 1.0, True and "1" are four values and not
    one. Not repr(): repr is a display form, several types share one, and an
    identity built on it would silently merge things the checker
    distinguished -- which is the whole failure v2 corrects.

    Containers are deliberately *not* tagged to their concrete type. list and
    tuple share the seq family, set and frozenset share set, bytes and
    bytearray share bytes: those pairs differ in mutability and in nothing a
    coordinate asserts, and an identity that moved when a caller passed a
    tuple instead of a list would be recording the caller's plumbing. The
    families stay distinct from each other -- a seq is never a set.

    Mappings sort by encoded key, so insertion order cannot move an identity.
    Sequences keep their order, because for a sequence the order is content.
    """
    if depth > MAX_IDENTITY_DEPTH:
        raise PromotionIdentityError(
            f"coordinate value nests deeper than {MAX_IDENTITY_DEPTH}; an "
            f"identity computation must terminate")
    if value is None:
        return ["null", ""]
    # bool before int, deliberately: bool subclasses int, and True is not 1.
    if isinstance(value, bool):
        return ["bool", "true" if value else "false"]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        # Non-finite coordinates are refused rather than encoded. hex() is
        # exact for every finite float, for the infinities and for signed
        # zero, but it flattens every NaN to one token while
        # Coordinate.compare treats NaN as CHANGED -- so two findings that
        # the checker distinguished would share an identity, which is the
        # v1 defect in a smaller costume. NaN in an evidentiary coordinate
        # is an undeclared absence wearing a lab coat: the ledger already
        # has five honest kinds for not knowing a value.
        if value != value:
            raise PromotionIdentityError(
                "NaN coordinate has no promotion identity; state the absence "
                f"with a coordinate kind ({', '.join(COORDINATE_KINDS[:-1])})")
        if value in (float("inf"), float("-inf")):
            raise PromotionIdentityError(
                f"non-finite coordinate {value!r} has no promotion identity")
        # hex() round-trips a float exactly and keeps -0.0 apart from 0.0.
        # Decimal text in general does neither.
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, (bytes, bytearray)):
        return ["bytes", bytes(value).hex()]
    if isinstance(value, (list, tuple)):
        return ["seq", [_canonical_value(item, depth + 1) for item in value]]
    if isinstance(value, (set, frozenset)):
        return ["set", sorted((_canonical_value(item, depth + 1)
                               for item in value), key=_encode)]
    if isinstance(value, Mapping):
        return ["map", sorted(([_canonical_value(key, depth + 1),
                                _canonical_value(item, depth + 1)]
                               for key, item in value.items()), key=_encode)]
    raise PromotionIdentityError(
        f"coordinate value of type {type(value).__name__!r} has no canonical "
        f"encoding; hashing its repr would make two different findings one")


_COORDINATE_FIELDS = frozenset(("kind", "value"))


def _canonical_coordinate(recorded: Optional[Mapping[str, Any]]) -> Any:
    """One side of a finding, encoded. Every governed kind is representable.

    The mapping is validated rather than trusted. Coordinate enforces these
    rules at construction, but Finding has no __post_init__ and its before
    and after sides are plain dictionaries, so a hand-built verdict -- which
    the tests themselves show is supported -- reaches here unchecked. Without
    validation {"kind": "VALUE"} would encode exactly like an explicit
    VALUE(None), a non-VALUE kind could smuggle a value that is then silently
    dropped, and an extra field would be ignored. Each is a distinct pair of
    inputs collapsing to one identity: the v1 defect again, arriving by a
    different door.
    """
    if recorded is None:
        return _NO_COORDINATE
    if not isinstance(recorded, Mapping):
        raise PromotionIdentityError(
            f"a finding side must be a coordinate mapping or None, not "
            f"{type(recorded).__name__!r}")
    present = set(recorded)
    if present != _COORDINATE_FIELDS:
        missing = sorted(_COORDINATE_FIELDS - present)
        extra = sorted(present - _COORDINATE_FIELDS)
        raise PromotionIdentityError(
            f"a coordinate mapping carries exactly kind and value; "
            f"missing {missing or 'nothing'}, unexpected {extra or 'nothing'}")
    kind = recorded["kind"]
    value = recorded["value"]
    if kind not in COORDINATE_KINDS:
        raise PromotionIdentityError(
            f"unknown coordinate kind {str(kind)[:48]!r} in a finding; the "
            f"identity will not guess what it was meant to say")
    if kind != VALUE:
        # The four absence kinds carry no value by construction. Encoding one
        # anyway would let an identity depend on a field that cannot vary --
        # and dropping one that is there would hash away a contradiction.
        if value is not None:
            raise PromotionIdentityError(
                f"{kind} carries no value; a non-VALUE coordinate holding "
                f"{str(value)[:48]!r} is two claims at once")
        return ["kind", kind]
    return ["value", _canonical_value(value)]


def _canonical_finding(finding: Any) -> Any:
    """before and after sit in fixed positions, so reversing them is a change."""
    return [finding.verdict, finding.field, finding.expected, finding.observed,
            _canonical_coordinate(finding.before),
            _canonical_coordinate(finding.after)]


def verdict_digest(verdict: InvariantVerdict) -> str:
    """A stable digest of what was judged. Deterministic across evaluations.

    Findings are sorted by their own canonical encoding, so the identity
    describes the evidence set rather than the order in which the checker
    happened to reach it. A sorted list is not a set: two identical findings
    remain two.
    """
    findings = sorted((_canonical_finding(f) for f in verdict.findings),
                      key=_encode)
    material = _encode({"digest_revision": VERDICT_DIGEST_REVISION,
                        "verdict": verdict.verdict,
                        "transition": verdict.transition,
                        "findings": findings})
    return f"blake2s:{hashlib.blake2s(material.encode('utf-8'), digest_size=16).hexdigest()}"


def promotion_identity(verdict: InvariantVerdict, capsule: CapsuleIdentity,
                       target_graph: str) -> str:
    """The idempotency key. Deterministic over exactly four things.

    Source verdict digest, capsule revision, policy revision and target graph.
    Deliberately not the requester, the justification or a timestamp: the same
    finding promoted twice by two operators is one record, and a key that moved
    with who asked would make re-evaluation accumulate.

    Still four things: VERDICT_DIGEST_REVISION is bound inside the verdict
    digest itself, so a digest revision moves every key without being named
    here twice.
    """
    material = json.dumps(
        {"verdict_digest": verdict_digest(verdict),
         "capsule_revision": capsule.schema,
         "policy_revision": POLICY_REVISION,
         "target_graph": target_graph},
        sort_keys=True, separators=(",", ":"))
    return f"promotion:{hashlib.blake2s(material.encode('utf-8'), digest_size=16).hexdigest()}"


@dataclass(frozen=True)
class PromotionDecision:
    """One disposition, the record it would produce, and the reasons against."""

    disposition: str
    record_class: Optional[str] = None
    idempotency_key: Optional[str] = None
    refusals: Tuple[str, ...] = ()
    detail: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise PromotionRequestError(
                f"unknown disposition {str(self.disposition)[:48]!r}")
        if self.disposition == PROMOTION_ELIGIBLE:
            if self.refusals:
                raise PromotionRequestError(
                    "an eligible promotion carries no refusal")
            if self.record_class is None or self.idempotency_key is None:
                raise PromotionRequestError(
                    "an eligible promotion names its record class and identity")
        if self.disposition == PROMOTION_REFUSED and not self.refusals:
            raise PromotionRequestError(
                "a refusal without a reason cannot say why")
        if self.disposition == NO_PROMOTION_REQUESTED and (
                self.refusals or self.record_class):
            raise PromotionRequestError(
                "nothing was asked, so there is nothing to refuse or name")

    @property
    def eligible(self) -> bool:
        return self.disposition == PROMOTION_ELIGIBLE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "policy_revision": POLICY_REVISION,
            "disposition": self.disposition,
            "record_class": self.record_class,
            "idempotency_key": self.idempotency_key,
            "refusals": list(self.refusals),
            "reasons": [REFUSAL_NOTES[r] for r in self.refusals],
            "detail": list(self.detail),
            "authority": "PROMOTION_POLICY_EVALUATION",
            "executes": False,
            "execution_note": (
                "THIS NAMES WHAT WOULD BE WRITTEN AND UNDER WHICH IDENTITY. IT "
                "CARRIES NO WRITE ARGUMENTS: AN ADAPTER CONSTRUCTS THOSE FROM A "
                "FIXED SCHEMA, AND A DECISION THAT CARRIED THEM WOULD LET A "
                "CALLER CHOOSE WHAT IS WRITTEN"),
        }


def decide_promotion(verdict: InvariantVerdict,
                     request: Optional[PromotionRequest],
                     capsule: Optional[CapsuleIdentity] = None,
                     *, already_promoted: Sequence[str] = ()) -> PromotionDecision:
    """Pure. Decides eligibility; performs nothing.

    Refusals accumulate rather than stopping at the first, so a requester
    repairing one does not discover the next on the following attempt.
    """
    if request is None:
        # Frame arrival, a completed check, a model's commentary: none of these
        # is an ask, and the absence of one is not a refusal either.
        return PromotionDecision(NO_PROMOTION_REQUESTED)

    refusals: list = []
    detail: list = []

    if request.requested_by not in PROMOTION_AUTHORITIES:
        refusals.append(AUTHORITY_INSUFFICIENT)
        detail.append(f"requested_by={request.requested_by[:64]}")
    if request.justification_source == MODEL_SOURCE:
        refusals.append(MODEL_RESPONSE_USED_AS_AUTHORITY)
    if request.target_graph not in SUPPORTED_TARGET_GRAPHS:
        refusals.append(TARGET_UNSUPPORTED)
        detail.append(f"target_graph={request.target_graph[:64]}")

    if capsule is None:
        refusals.append(CAPSULE_UNBOUND)
        detail.append("no capsule identity supplied")
    else:
        if capsule.schema not in SUPPORTED_CAPSULE_SCHEMAS:
            refusals.append(CAPSULE_REVISION_UNSUPPORTED)
            detail.append(f"capsule_schema={capsule.schema[:64]}")
        if not capsule.within_bounds or capsule.carries_samples:
            refusals.append(CAPSULE_UNBOUND)

    permitted = VERDICT_RECORD_CLASS.get(verdict.verdict)
    if permitted is None:
        refusals.append(VERDICT_NOT_PROMOTABLE)
        detail.append(f"verdict={verdict.verdict}")
    elif request.record_class is not None and request.record_class != permitted:
        if permitted == OBSERVATION_GAP and request.record_class == INVARIANT_FINDING:
            refusals.append(INDETERMINATE_AS_FAILURE)
            detail.append(f"{verdict.verdict} is an observation gap")
        else:
            # The mirror error: a determinate violation asked for as a gap.
            # Understating it is refused for the same reason overstating is,
            # and it reuses VERDICT_NOT_PROMOTABLE because the verdict is not
            # promotable AS THAT CLASS.
            refusals.append(VERDICT_NOT_PROMOTABLE)
            detail.append(
                f"{verdict.verdict} is a {permitted}, not a {request.record_class}")

    key = None
    if capsule is not None and permitted is not None:
        key = promotion_identity(verdict, capsule, request.target_graph)
        if key in tuple(already_promoted):
            refusals.append(DUPLICATE_PROMOTION)

    ordered = tuple(dict.fromkeys(refusals))
    if ordered:
        return PromotionDecision(PROMOTION_REFUSED, refusals=ordered,
                                 detail=tuple(detail[:8]))
    return PromotionDecision(PROMOTION_ELIGIBLE, record_class=permitted,
                             idempotency_key=key)


def policy_status() -> Dict[str, Any]:
    """The policy's own terms, published rather than discovered."""
    return {
        "schema": SCHEMA,
        "policy_revision": POLICY_REVISION,
        "dispositions": list(DISPOSITIONS),
        "record_classes": list(RECORD_CLASSES),
        "verdict_record_class": dict(VERDICT_RECORD_CLASS),
        "not_promotable": [INVARIANTS_SATISFIED],
        "refusals": list(REFUSALS),
        "refusal_notes": dict(REFUSAL_NOTES),
        "promotion_authorities": list(PROMOTION_AUTHORITIES),
        "model_is_an_authority": False,
        "supported_capsule_schemas": list(SUPPORTED_CAPSULE_SCHEMAS),
        "supported_target_graphs": list(SUPPORTED_TARGET_GRAPHS),
        "identity_over": ("SOURCE_VERDICT_DIGEST", "CAPSULE_REVISION",
                          "POLICY_REVISION", "TARGET_GRAPH"),
        "identity_note": (
            "NOT THE REQUESTER, THE JUSTIFICATION OR A TIMESTAMP. THE SAME "
            "FINDING PROMOTED TWICE BY TWO OPERATORS IS ONE RECORD, AND A KEY "
            "THAT MOVED WITH WHO ASKED WOULD MAKE RE-EVALUATION ACCUMULATE"),
        "verdict_digest_revision": VERDICT_DIGEST_REVISION,
        "prior_promotion_identity_comparable": PRIOR_PROMOTION_IDENTITY_COMPARABLE,
        "identity_break_note": (
            "v1 HASHED ONLY THE SHAPE OF A DISAGREEMENT, SO TWO DISTINCT "
            "MOVEMENTS OF ONE FIELD SHARED AN IDENTITY AND THE SECOND WAS "
            "REFUSED AS A DUPLICATE. v2 BINDS EACH FINDING TO ITS CANONICAL "
            "BEFORE AND AFTER COORDINATES. NO KEY ISSUED UNDER v1 IS "
            "COMPARABLE WITH ONE ISSUED UNDER v2"),
        "implicit_requests": "NONE",
        "implicit_note": (
            "A FRAME ARRIVING, A CHECK COMPLETING AND A MODEL COMMENTING ARE "
            "NOT PROMOTION REQUESTS. INGEST IS NOT INTENT"),
        "executes": False,
        "shadow_ledger": "NOT_IMPLEMENTED",
        "execution_adapter": "NOT_IMPLEMENTED",
        "side_effects": "NONE",
    }
