"""The alignment half of walking-survey admission.

Mirrors ``rf_walk_survey_metadata``: an assessment carries the **evidence**
beside the **facts**, and the facts alone are what admission consumes.

    AlignmentAssessment
    ├── TimeAlignedJoin           the evidence, with propagated uncertainty
    └── AlignmentAdmissionFacts   two booleans, for the verdict

The join owns the evidence. It carries the measured separation, the declared
uncertainty, any cross-clock mapping uncertainty, the resulting timing budget
and pose budget, the method and the authority. The booleans only *map* that
evidence into admission reasons, and mapping is lossy on purpose: ``VERIFIED``
and ``BOUNDED`` both admit, and collapsing them at the verdict is correct. What
must not happen is the collapse reaching the record -- §5 of the contract
requires a ``BOUNDED`` join to propagate its contribution to pose uncertainty
rather than discard it, and it can only do that if the join survives alongside
the booleans.

This module decides nothing about alignment. ``rf_receiver_state`` does that;
here the join is performed and its result is translated.

Chain disagreement is now expressible, and still not produced
-------------------------------------------------------------
``time_align`` can refuse with ``SIGNAL_CHAIN_CHANGED`` or
``RECEIVER_STATE_CHAIN_CHANGED`` when a frame's identity differs from an
expectation. Reason vocabulary v2 gives both an honest admission code, so
``facts_for`` maps them exactly rather than raising: §4 previously had codes
only for identities that were *absent*, and a disagreement is a different
failure with a different repair.

This module still accepts **no expected hashes**. Nothing in this phase
establishes a survey-level expectation to compare against -- that belongs to
whatever owns a survey's identity over time, and it does not exist. A vocabulary
that can describe a result is not a mechanism that produces one, so these
refusals remain unreachable from here; what changed is that they would now be
reported rather than crash if a future caller supplied an expectation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from rf_receiver_state import (
    ALIGNMENT_CAPABILITIES, ALIGNMENT_STATES, JOIN_REFUSALS,
    AcquisitionInterval, ClockMapping, ReceiverState, TimeAlignedJoin,
    may_update_posterior, time_align,
)
from rf_walk_survey_admission import (
    REASON_VOCABULARY_REVISION, AlignmentAdmissionFacts,
)


def _facts_payload(facts: AlignmentAdmissionFacts) -> Dict[str, bool]:
    return {"time_alignment_unverified": facts.time_alignment_unverified,
            "receiver_state_stale": facts.receiver_state_stale,
            "signal_chain_changed": facts.signal_chain_changed,
            "receiver_state_chain_changed": facts.receiver_state_chain_changed}


SCHEMA = "scythe.rf-walk-survey-alignment.v1"
CONTRACT = "docs/RF_WALK_SURVEY_CONTRACT.md"

CHAIN_CHANGE_NOTE = (
    "SIGNAL_CHAIN_CHANGED AND RECEIVER_STATE_CHAIN_CHANGED ARE MAPPED EXACTLY "
    "UNDER REASON VOCABULARY v2 (CONTRACT SECTION 4, AMENDMENT A). THIS MODULE "
    "STILL ACCEPTS NO EXPECTED HASHES: NOTHING YET OWNS A SURVEY'S IDENTITY "
    "OVER TIME, SO NOTHING YET SUPPLIES AN EXPECTATION TO DISAGREE WITH. A "
    "VOCABULARY THAT CAN DESCRIBE A RESULT IS NOT A MECHANISM THAT PRODUCES ONE"
)

# Every alignment status, mapped to the two facts. Total by construction: a
# status absent from this table raises rather than defaulting to admitted.
#
# A refusal is not in here. Nothing joined, so there is no status to map, and
# the facts for that case are named separately below.
_STATUS_FACTS: Dict[str, AlignmentAdmissionFacts] = {
    "VERIFIED": AlignmentAdmissionFacts(False, False, False, False),
    "BOUNDED": AlignmentAdmissionFacts(False, False, False, False),
    "UNVERIFIED": AlignmentAdmissionFacts(True, False, False, False),
    # Something did join, and it was too old. That is not "nothing joined",
    # which is why STALE does not also set time_alignment_unverified.
    "STALE": AlignmentAdmissionFacts(False, True, False, False),
}

# A refusal means nothing joined the observation to a receiver state. Staleness
# cannot arise from one: it is a property of a join that happened.
_REFUSED_FACTS = AlignmentAdmissionFacts(True, False, False, False)

# The two refusals that are not "nothing joined" but "the identities disagree".
# They do NOT set time_alignment_unverified: the join was refused on
# comparability, which is a different failure from having no join to make, and
# reporting both would tell an operator to look in two places for one fault.
_CHAIN_CHANGE_FACTS: Dict[str, AlignmentAdmissionFacts] = {
    "SIGNAL_CHAIN_CHANGED": AlignmentAdmissionFacts(False, False, True, False),
    "RECEIVER_STATE_CHAIN_CHANGED": AlignmentAdmissionFacts(False, False, False, True),
}


class AlignmentUnmappable(ValueError):
    """A join outcome the admission vocabulary cannot express.

    Narrowed by vocabulary v2: the two chain-disagreement refusals are mapped
    now, and this remains only for a join refusal or status the vocabulary has
    genuinely never had a code for -- a guard against a future refusal being
    added to ``rf_receiver_state`` and silently defaulting to admitted here.
    """


@dataclass(frozen=True)
class AlignmentAssessment:
    """Evidence beside facts. Neither substitutes for the other."""

    join: TimeAlignedJoin
    facts: AlignmentAdmissionFacts

    @property
    def joined(self) -> bool:
        return bool(self.join.joined)

    @property
    def may_update_surface(self) -> bool:
        """What the join permits. Not what admission concludes.

        Admission has other reasons to refuse a surface -- an unbound signal
        chain, an unsupported power unit -- and this says nothing about them.
        """
        return may_update_posterior(self.join)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "contract": CONTRACT,
            "contract_section": "5",
            "join": self.join.to_dict(),
            "facts": _facts_payload(self.facts),
            "may_update_surface": self.may_update_surface,
            "facts_are_lossy": True,
            "lossy_note": (
                "VERIFIED AND BOUNDED MAP TO THE SAME TWO BOOLEANS BECAUSE BOTH "
                "ADMIT. THE DISTINCTION SURVIVES IN THE JOIN, WHICH IS WHERE A "
                "CONSUMER PROPAGATING BOUNDED'S POSE CONTRIBUTION MUST READ IT"),
            "decides": "ALIGNMENT_FACTS_ONLY_NOT_A_VERDICT",
            "verdict_note": (
                "THESE FACTS ARE HALF OF AN ADMISSION. "
                "AdmissionFacts.from_stages REQUIRES METADATA FACTS TOO"),
        }


def facts_for(join: TimeAlignedJoin) -> AlignmentAdmissionFacts:
    """Translate one join into the two admission facts. Pure and total."""
    if not join.joined:
        if join.refusal in _CHAIN_CHANGE_FACTS:
            return _CHAIN_CHANGE_FACTS[join.refusal]
        if join.refusal not in JOIN_REFUSALS:
            raise AlignmentUnmappable(
                f"unmapped join refusal {join.refusal!r}; the mapping must be "
                f"total, and defaulting to admitted would be the wrong "
                f"direction to fail in")
        return _REFUSED_FACTS
    try:
        return _STATUS_FACTS[join.alignment_status]
    except KeyError:                                   # pragma: no cover - guard
        raise AlignmentUnmappable(
            f"unknown alignment status {join.alignment_status!r}; the mapping "
            f"must be total, and defaulting to admitted would be the wrong "
            f"direction to fail in")


def assess_alignment(acquisition: Optional[AcquisitionInterval],
                     state: Optional[ReceiverState], *,
                     clock_mapping: Optional[ClockMapping] = None
                     ) -> AlignmentAssessment:
    """Join, then translate. Pure: no clock, no I/O, no state, no mutation.

    Deliberately takes no expected hashes. See the module docstring and
    ``CHAIN_CHANGE_GAP``.
    """
    join = time_align(acquisition, state, clock_mapping=clock_mapping)
    return AlignmentAssessment(join=join, facts=facts_for(join))


def alignment_status() -> Dict[str, Any]:
    """The stage's boundaries, published rather than discovered."""
    return {
        "schema": SCHEMA,
        "contract": CONTRACT,
        "alignment_authority": "rf_receiver_state.time_align",
        "alignment_states": list(ALIGNMENT_STATES),
        "capabilities_authority": "rf_receiver_state.ALIGNMENT_CAPABILITIES",
        "join_refusals": list(JOIN_REFUSALS),
        "status_to_facts": {status: _facts_payload(facts)
                            for status, facts in _STATUS_FACTS.items()},
        "refused_facts": _facts_payload(_REFUSED_FACTS),
        "chain_change_facts": {refusal: _facts_payload(facts)
                               for refusal, facts in _CHAIN_CHANGE_FACTS.items()},
        "reason_vocabulary_revision": REASON_VOCABULARY_REVISION,
        "accepts_expected_hashes": False,
        "chain_change_note": CHAIN_CHANGE_NOTE,
        "decides_metadata_facts": False,
        "decides_surface_update": False,
        "side_effects": "NONE",
    }
