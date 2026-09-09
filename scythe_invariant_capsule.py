"""What a model may be told about a transition, and what it may say back.

Two halves, and the second is the load-bearing one.

**The capsule** is what leaves the process: a bounded, derived summary of one
invariant evaluation. No raw IQ, no unbounded prose, no coordinate values large
enough to smuggle a payload. It carries the signatures, the declared transition,
what was checked, what failed, and -- separately -- which dimensions were
*indeterminate*, because those are not failures and the distinction is the first
thing a summariser will flatten if it is not handed to it already separated.

**The guard** is what comes back. A model may explain, may suggest a next
observation, may say it disagrees. It may not waive an invariant, supply a
coordinate nobody measured, or convert an indeterminate dimension into a
failure. Those are not stylistic preferences: each of them would let prose
become evidence, which is the one thing this whole ledger exists to prevent.

The ordering is deliberate. The model proposes; the invariant layer determines
whether the proposal is admissible. Nothing here promotes anything into
GraphOps, and nothing here writes: the verdict remains a process-local derived
product, and turning one into graph evidence is a separate decision made by
something that has read it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from scythe_invariant_ledger import (
    COMPARISON_DOMAIN_CHANGED, EVIDENCE_MISSING, INVARIANTS_SATISFIED,
    NOT_ASSESSED, VERDICT_NOTES, Coordinate, InvariantVerdict,
    TransitionContract,
)


SCHEMA = "scythe.invariant-capsule.v1"

# Bounds. A capsule is a summary; anything that needs more room than this is
# not a summary, and a model prompt is not a transport for bulk data.
MAX_TEXT = 400
MAX_ITEMS = 32
MAX_COORDINATE_TEXT = 128

# What the model may not do, declared where a reader will meet it rather than
# left to a prompt that nobody diffs.
MODEL_PROHIBITIONS: Tuple[str, ...] = (
    "WAIVE_AN_INVARIANT",
    "MANUFACTURE_A_MISSING_COORDINATE",
    "CONVERT_AN_INDETERMINATE_DIMENSION_INTO_A_FAILURE",
    "OVERRIDE_A_VERDICT",
)
MODEL_PERMITTED: Tuple[str, ...] = (
    "EXPLAIN_WHY_A_TRANSITION_WAS_REFUSED",
    "NAME_WHICH_COORDINATE_BROKE_COMPARABILITY",
    "SUGGEST_AN_OBSERVATION_THAT_WOULD_DISTINGUISH_FAILURE_FROM_AN_ABSENT_DOMAIN",
    "GROUP_REPEATED_VIOLATIONS_BY_APPARATUS_REVISION",
    "STATE_DISAGREEMENT_AS_COMMENTARY",
)

# Fields a response may carry. Anything else is refused: an allow-list is the
# only policy that stays correct when a model invents a field.
ALLOWED_RESPONSE_FIELDS = frozenset({
    "explanation", "suggested_observations", "disputed_findings",
    "grouped_by",
})

# Refusals for a response that overstepped.
VERDICT_OVERRIDE_ATTEMPTED = "VERDICT_OVERRIDE_ATTEMPTED"
COORDINATE_MANUFACTURED = "COORDINATE_MANUFACTURED"
INDETERMINATE_CONVERTED_TO_FAILURE = "INDETERMINATE_CONVERTED_TO_FAILURE"
UNKNOWN_RESPONSE_FIELD = "UNKNOWN_RESPONSE_FIELD"
RESPONSE_UNBOUNDED = "RESPONSE_UNBOUNDED"
RESPONSE_REFUSALS: Dict[str, str] = {
    VERDICT_OVERRIDE_ATTEMPTED: (
        "THE RESPONSE ASSERTED A VERDICT. A MODEL PROPOSES; THE INVARIANT LAYER "
        "DETERMINES ADMISSIBILITY, AND A RESPONSE THAT CAN SET A VERDICT HAS "
        "REVERSED THAT"),
    COORDINATE_MANUFACTURED: (
        "THE RESPONSE SUPPLIED A COORDINATE VALUE. A COORDINATE COMES FROM AN "
        "APPARATUS, AND ONE SUPPLIED BY A SUMMARISER IS PROSE WEARING A "
        "MEASUREMENT'S NAME"),
    INDETERMINATE_CONVERTED_TO_FAILURE: (
        "THE RESPONSE TREATED AN INDETERMINATE DIMENSION AS A FAILURE. NOBODY "
        "LOOKED, WHICH IS NOT THE SAME AS SOMETHING BEING WRONG, AND A "
        "RECOVERY-FAILURE COUNT THAT ABSORBS IT STOPS MEANING ANYTHING"),
    UNKNOWN_RESPONSE_FIELD: (
        "THE RESPONSE CARRIED A FIELD THIS BOUNDARY DOES NOT ACCEPT"),
    RESPONSE_UNBOUNDED: (
        "THE RESPONSE EXCEEDED THE DECLARED BOUNDS FOR ITS SHAPE"),
}


class CapsuleRefused(ValueError):
    """A capsule that cannot be built without exceeding its own bounds."""


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value)[:limit]


def _coordinate_summary(coordinate: Coordinate) -> Dict[str, Any]:
    """A coordinate, bounded. Values become text so nothing large travels."""
    if coordinate.kind != "VALUE":
        return {"kind": coordinate.kind}
    return {"kind": "VALUE", "value": _text(coordinate.value, MAX_COORDINATE_TEXT)}


def _signature_summary(sig: Mapping[str, Coordinate]) -> Dict[str, Any]:
    names = sorted(sig)[:MAX_ITEMS]
    return {name: _coordinate_summary(sig[name]) for name in names}


def indeterminate_dimensions(before: Mapping[str, Coordinate],
                             after: Mapping[str, Coordinate]) -> Tuple[str, ...]:
    """Coordinates nobody assessed on either side.

    Kept apart from violations deliberately. A summariser handed one list will
    read it as one kind of thing, and "nobody looked" is not "something is
    wrong" -- it is the absence of the evidence that would decide.
    """
    names = set(before) | set(after)
    return tuple(sorted(
        name for name in names
        if before.get(name, Coordinate(NOT_ASSESSED)).kind == NOT_ASSESSED
        or after.get(name, Coordinate(NOT_ASSESSED)).kind == NOT_ASSESSED))


@dataclass(frozen=True)
class EvidenceCapsule:
    """A bounded, derived summary. Never a transport for measurements."""

    payload: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.payload)


def evidence_capsule(before: Mapping[str, Coordinate],
                     after: Mapping[str, Coordinate],
                     contract: TransitionContract,
                     verdict: InvariantVerdict) -> EvidenceCapsule:
    """Build what a model may be told. Pure, bounded, and derived."""
    checked = tuple(contract.declared_fields())[:MAX_ITEMS]
    violations = tuple(
        {"verdict": finding.verdict, "field": finding.field,
         "expected": _text(finding.expected, MAX_COORDINATE_TEXT),
         "observed": _text(finding.observed, MAX_COORDINATE_TEXT),
         "detail": None if finding.detail is None else _text(finding.detail)}
        for finding in verdict.findings[:MAX_ITEMS])
    indeterminate = indeterminate_dimensions(before, after)[:MAX_ITEMS]
    payload = {
        "schema": SCHEMA,
        "entity_signature_before": _signature_summary(before),
        "declared_transition": {
            "name": contract.name,
            "must_preserve": list(contract.must_preserve[:MAX_ITEMS]),
            "must_change": list(contract.must_change[:MAX_ITEMS]),
            "domain_fields": list(contract.domain_fields[:MAX_ITEMS]),
        },
        "entity_signature_after": _signature_summary(after),
        "invariants_checked": list(checked),
        "violations": list(violations),
        # Separate from violations, and named so it cannot be read as one.
        "indeterminate_dimensions": list(indeterminate),
        "indeterminate_note": (
            "NOBODY LOOKED AT THESE. THAT IS NOT A FAILURE, AND TREATING IT AS "
            "ONE IS A PROHIBITED RESPONSE"),
        "verdict": verdict.verdict,
        "verdict_note": VERDICT_NOTES[verdict.verdict],
        "authority": {
            "verdict_authority": "CONTRACT_EVALUATION",
            "model_role": "PROPOSES_ONLY",
            "model_may": list(MODEL_PERMITTED),
            "model_may_not": list(MODEL_PROHIBITIONS),
            "promotion": "NOT_IMPLEMENTED",
            "mutates": False,
        },
        "contains_samples": False,
        "bounds": {"max_text": MAX_TEXT, "max_items": MAX_ITEMS,
                   "max_coordinate_text": MAX_COORDINATE_TEXT},
    }
    return EvidenceCapsule(payload)


@dataclass(frozen=True)
class ResponseReview:
    """Whether a model's response may be recorded beside the verdict."""

    admissible: bool
    refusals: Tuple[str, ...] = ()
    detail: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "admissible": self.admissible,
            "refusals": list(self.refusals),
            "reasons": [RESPONSE_REFUSALS[r] for r in self.refusals],
            "detail": list(self.detail),
            "authority": "CONTRACT_EVALUATION",
            "note": ("A REFUSED RESPONSE CHANGES NOTHING ABOUT THE VERDICT. THE "
                     "TRANSITION WAS ALREADY JUDGED, AND THIS ONLY DECIDES "
                     "WHETHER THE COMMENTARY MAY BE KEPT BESIDE IT"),
        }


def review_model_response(capsule: EvidenceCapsule,
                          response: Mapping[str, Any]) -> ResponseReview:
    """Whether a response overstepped. Pure; refuses rather than repairs.

    Disagreement is permitted and is commentary. What is refused is a response
    shaped so that accepting it would change what is true.
    """
    refusals: list = []
    detail: list = []
    if not isinstance(response, Mapping):
        return ResponseReview(False, (UNKNOWN_RESPONSE_FIELD,),
                              ("a response must be a mapping",))

    unknown = sorted(set(response) - ALLOWED_RESPONSE_FIELDS)
    if unknown:
        refusals.append(UNKNOWN_RESPONSE_FIELD)
        detail.append(f"unknown fields: {', '.join(unknown)[:MAX_TEXT]}")
        # A verdict or a coordinate arriving under an unexpected name is the
        # specific thing worth naming, not merely an unknown field.
        for name in unknown:
            lowered = name.lower()
            if "verdict" in lowered or "override" in lowered:
                refusals.append(VERDICT_OVERRIDE_ATTEMPTED)
                detail.append(f"{name} asserts a verdict")
            if "coordinate" in lowered or "signature" in lowered or "value" in lowered:
                refusals.append(COORDINATE_MANUFACTURED)
                detail.append(f"{name} supplies a coordinate")

    explanation = response.get("explanation")
    if explanation is not None and len(str(explanation)) > MAX_TEXT:
        refusals.append(RESPONSE_UNBOUNDED)
        detail.append("explanation exceeds the declared bound")
    for name in ("suggested_observations", "disputed_findings", "grouped_by"):
        items = response.get(name)
        if items is None:
            continue
        if not isinstance(items, (list, tuple)):
            refusals.append(UNKNOWN_RESPONSE_FIELD)
            detail.append(f"{name} must be a list")
            continue
        if len(items) > MAX_ITEMS:
            refusals.append(RESPONSE_UNBOUNDED)
            detail.append(f"{name} exceeds {MAX_ITEMS} items")

    # An indeterminate dimension named as a failure. Disputing a real finding is
    # commentary; calling an unlooked-at dimension a failure is the conversion
    # the boundary exists to stop.
    indeterminate = set(capsule.payload.get("indeterminate_dimensions", ()))
    violated = {v["field"] for v in capsule.payload.get("violations", ())}
    for item in response.get("disputed_findings") or ():
        field = str(item)[:MAX_COORDINATE_TEXT]
        if field in indeterminate and field not in violated:
            refusals.append(INDETERMINATE_CONVERTED_TO_FAILURE)
            detail.append(f"{field} was not assessed and is not a finding")

    ordered = tuple(dict.fromkeys(refusals))
    return ResponseReview(not ordered, ordered, tuple(detail[:MAX_ITEMS]))


def capsule_status() -> Dict[str, Any]:
    """The boundary's own terms, published rather than kept in a prompt."""
    return {
        "schema": SCHEMA,
        "model_role": "PROPOSES_ONLY",
        "model_may": list(MODEL_PERMITTED),
        "model_may_not": list(MODEL_PROHIBITIONS),
        "response_fields": sorted(ALLOWED_RESPONSE_FIELDS),
        "response_refusals": dict(RESPONSE_REFUSALS),
        "bounds": {"max_text": MAX_TEXT, "max_items": MAX_ITEMS,
                   "max_coordinate_text": MAX_COORDINATE_TEXT},
        "carries_samples": False,
        "sample_note": (
            "A CAPSULE IS A DERIVED SUMMARY. RAW IQ NEVER ENTERS ONE, AND "
            "COORDINATE VALUES ARE TRUNCATED SO NOTHING LARGE CAN TRAVEL IN A "
            "FIELD MEANT FOR AN IDENTIFIER"),
        "graph_promotion": "NOT_IMPLEMENTED",
        "promotion_note": (
            "THE VERDICT IS A PROCESS-LOCAL DERIVED PRODUCT. TURNING ONE INTO "
            "GRAPHOPS EVIDENCE IS A SEPARATE DECISION MADE BY SOMETHING THAT "
            "HAS READ IT, AND NOTHING HERE CALLS A BUS"),
        "mutates": False,
        "side_effects": "NONE",
    }
