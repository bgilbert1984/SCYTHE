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

Expected-hash comparison is deliberately absent
-----------------------------------------------
``time_align`` can refuse with ``SIGNAL_CHAIN_CHANGED`` or
``RECEIVER_STATE_CHAIN_CHANGED`` when a frame's identity differs from an
expectation. Two things are true about that here:

* nothing in this phase establishes a survey-level expectation to compare
  against -- that belongs to whatever owns a survey's identity over time, and
  it does not exist;
* the admission vocabulary has **no reason code** for a chain that *changed*.
  §4's three unbound codes are about identities that are *absent*.
  ``RF_WALK_SURVEY_CONTRACT.md`` §6 says these failures happen "at join", and
  §4 cannot express them. That is a contract gap, recorded in
  ``CHAIN_CHANGE_GAP`` and not papered over.

So this module does not accept expected hashes. Those refusals are unreachable
from here rather than silently mapped onto a reason that would misdescribe
them, and a caller cannot produce one by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from rf_receiver_state import (
    ALIGNMENT_CAPABILITIES, ALIGNMENT_STATES, JOIN_REFUSALS,
    AcquisitionInterval, ClockMapping, ReceiverState, TimeAlignedJoin,
    may_update_posterior, time_align,
)
from rf_walk_survey_admission import AlignmentAdmissionFacts


SCHEMA = "scythe.rf-walk-survey-alignment.v1"
CONTRACT = "docs/RF_WALK_SURVEY_CONTRACT.md"

CHAIN_CHANGE_GAP = (
    "time_align CAN REFUSE WITH SIGNAL_CHAIN_CHANGED OR "
    "RECEIVER_STATE_CHAIN_CHANGED, AND THE ADMISSION VOCABULARY HAS NO REASON "
    "CODE FOR AN IDENTITY THAT CHANGED -- ITS THREE UNBOUND CODES ARE ABOUT "
    "IDENTITIES THAT ARE ABSENT. CONTRACT SECTION 6 PLACES THESE FAILURES 'AT "
    "JOIN' AND SECTION 4 CANNOT EXPRESS THEM. THIS MODULE THEREFORE ACCEPTS NO "
    "EXPECTED HASHES, SO THE REFUSALS ARE UNREACHABLE FROM HERE RATHER THAN "
    "MAPPED ONTO A REASON THAT WOULD MISDESCRIBE THEM"
)

# Every alignment status, mapped to the two facts. Total by construction: a
# status absent from this table raises rather than defaulting to admitted.
#
# A refusal is not in here. Nothing joined, so there is no status to map, and
# the facts for that case are named separately below.
_STATUS_FACTS: Dict[str, AlignmentAdmissionFacts] = {
    "VERIFIED": AlignmentAdmissionFacts(time_alignment_unverified=False,
                                        receiver_state_stale=False),
    "BOUNDED": AlignmentAdmissionFacts(time_alignment_unverified=False,
                                       receiver_state_stale=False),
    "UNVERIFIED": AlignmentAdmissionFacts(time_alignment_unverified=True,
                                          receiver_state_stale=False),
    # Something did join, and it was too old. That is not "nothing joined",
    # which is why STALE does not also set time_alignment_unverified.
    "STALE": AlignmentAdmissionFacts(time_alignment_unverified=False,
                                     receiver_state_stale=True),
}

# A refusal means nothing joined the observation to a receiver state, whatever
# the reason. Staleness cannot arise from a refusal: it is a property of a join
# that happened.
_REFUSED_FACTS = AlignmentAdmissionFacts(time_alignment_unverified=True,
                                         receiver_state_stale=False)


class AlignmentUnmappable(ValueError):
    """A join outcome the admission vocabulary cannot express."""


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
            "facts": {
                "time_alignment_unverified": self.facts.time_alignment_unverified,
                "receiver_state_stale": self.facts.receiver_state_stale,
            },
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
        if join.refusal in ("SIGNAL_CHAIN_CHANGED", "RECEIVER_STATE_CHAIN_CHANGED"):
            raise AlignmentUnmappable(f"{join.refusal}: {CHAIN_CHANGE_GAP}")
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
        "status_to_facts": {
            status: {"time_alignment_unverified": facts.time_alignment_unverified,
                     "receiver_state_stale": facts.receiver_state_stale}
            for status, facts in _STATUS_FACTS.items()},
        "refused_facts": {
            "time_alignment_unverified": _REFUSED_FACTS.time_alignment_unverified,
            "receiver_state_stale": _REFUSED_FACTS.receiver_state_stale},
        "accepts_expected_hashes": False,
        "chain_change_gap": CHAIN_CHANGE_GAP,
        "decides_metadata_facts": False,
        "decides_surface_update": False,
        "side_effects": "NONE",
    }
