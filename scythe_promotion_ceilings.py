"""Two ceilings over one durable structure.

§17 slice 8, implementing PROMOTION_EXECUTION_CONTRACT.md §11 and §13f
Amendment G.

`BoundedCeiling` is **not** reused. That class is merit-side apparatus -- it is
declared on a TransitionContract, evaluated by check_transition over coordinate
mappings, and a violation is a Finding about a subject. These refusals are
executability codes (§5), and importing the class would route a coordinator
condition through the merit vocabulary's finding type.

What is reused is its best idea, which is its refusal to exist without one:

    a ceiling with no published accounting basis cannot be checked; its
    headroom would absorb effects nobody named.

So a ceiling here declares five things -- subject, accounting source, scope,
reset rule and refusal -- and one missing any of them is not a ceiling.

The two are calibrated by opposite tests, which is why they are two:

  **C1 should never fire.** If it does during correct operation it was set
  wrong, and that is its calibration test (§11).

  **C2 is meant to fire.** It is the designed detector for an adapter that
  stops answering (§7), and firing is the mechanism working.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Dict, Tuple

SCHEMA = "scythe.promotion-ceilings.v1"

# -- refusals (§5) --------------------------------------------------------
DURABLE_CEILING_REACHED = "DURABLE_CEILING_REACHED"
OUTSTANDING_RESERVATION_CEILING_REACHED = "OUTSTANDING_RESERVATION_CEILING_REACHED"
CEILING_REFUSALS: Tuple[str, ...] = (DURABLE_CEILING_REACHED,
                                     OUTSTANDING_RESERVATION_CEILING_REACHED)

# -- contract-declared values (§13f G.4) ----------------------------------
#
# Not configurable: no constructor argument, no environment variable, no setter.
# A ceiling a deployment can raise is a ceiling that will be raised at the
# moment it first fires, which is the moment it is doing its job. Changing
# either requires a reviewed amendment, and the configuration identity below
# makes a change that skipped one visible rather than inferred.
RESERVATION_CEILING = 10_000
UNRESOLVED_CEILING = 32

# SHADOW cannot generate new unresolved reservations: it has no adapter that
# could fail to answer. It can still observe real ones.
NOT_SIMULABLE = "NOT_SIMULABLE"


class CeilingError(ValueError):
    """A ceiling that is not declared, or a value that was not declared here."""


@dataclass(frozen=True)
class Ceiling:
    """One one-sided bound on a durable total, with everything it counts on.

    One-sided, not a balance. A generation that made fewer reservations than its
    ceiling allows has done nothing wrong, and expressing that as a balance
    would make standing still a finding -- the same distinction BoundedCeiling
    draws, kept here rather than imported.
    """

    name: str
    subject: str
    accounting_source: str
    scope: str
    reset_rule: str
    refusal: str
    limit: int

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise CeilingError("a ceiling needs a name")
        # The four declarations must be present and not placeholders. This
        # catches the blank and the "TBD" and nothing subtler -- a first draft
        # demanded three words and rejected C1's own "one generation", which is
        # terse and complete. Any automatic test of whether prose says something
        # is a proxy; the real guard is that these are read in review, and this
        # one is here to make an empty field impossible rather than a weak one
        # unlikely.
        for field_name in ("subject", "accounting_source", "scope", "reset_rule"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or len(value) < 8:
                raise CeilingError(
                    f"{self.name}: a ceiling with no declared {field_name} "
                    f"cannot be checked; its headroom would absorb effects "
                    f"nobody named")
        if not isinstance(self.refusal, str) or not self.refusal:
            raise CeilingError(f"{self.name}: a ceiling needs a refusal")
        if self.refusal not in CEILING_REFUSALS:
            raise CeilingError(f"{self.name}: {self.refusal!r} is not a ceiling refusal")
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) \
                or self.limit < 1:
            raise CeilingError(f"{self.name}: a ceiling needs a positive limit")

    def exceeded_by(self, total: int) -> bool:
        """One-sided: at the limit is reached, and reached refuses.

        `>=` rather than `>`: the total is checked *before* the reservation it
        would authorise (§13f G.5), so a total already at the limit means the
        next one would pass it.
        """
        return total >= self.limit

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "subject": self.subject,
                "accounting_source": self.accounting_source,
                "scope": self.scope, "reset_rule": self.reset_rule,
                "refusal": self.refusal, "limit": self.limit}


C1 = Ceiling(
    name="C1",
    subject="every durable reservation in the authoritative generation, "
            "regardless of terminal outcome",
    accounting_source="RESERVED records in that generation's file, a torn tail "
                      "included",
    scope="one generation",
    reset_rule="publication of a valid successor generation (§13e F.6)",
    refusal=DURABLE_CEILING_REACHED,
    limit=RESERVATION_CEILING,
)

C2 = Ceiling(
    name="C2",
    subject="unresolved reservations: RESERVED with neither a valid terminal "
            "record nor a valid reconciliation record",
    accounting_source="those records across the complete validated lineage",
    scope="the lineage, not the generation",
    reset_rule="a valid reconciliation record only, under §13e F.2's authority "
               "rules; not a restart, not time, not generation closure",
    refusal=OUTSTANDING_RESERVATION_CEILING_REACHED,
    limit=UNRESOLVED_CEILING,
)

CEILINGS: Tuple[Ceiling, ...] = (C1, C2)


def configuration_identity() -> str:
    """A digest over everything declared, so a silent change is visible.

    Covers the limits and the five declarations, because a ceiling whose subject
    or scope changed while its number stayed the same is a different ceiling
    wearing the same value.
    """
    material = json.dumps([ceiling.as_dict() for ceiling in CEILINGS],
                          sort_keys=True, separators=(",", ":"))
    digest = hashlib.blake2s(material.encode("utf-8"), digest_size=16)
    return f"blake2s:{digest.hexdigest()}"


@dataclass
class CeilingTotals:
    """What the ceilings are counting, seeded from the ledger.

    Memory is a cache of the file (§13d E.4). What keeps a cache honest is not a
    promise: `recount` reads the durable state back and a test compares it to
    these, which is the guarantee.
    """

    reservations_in_generation: int = 0
    unresolved_in_lineage: int = 0

    def reserve(self) -> None:
        """One reservation advances both: it is a reservation in this
        generation, and until something resolves it, it is outstanding."""
        self.reservations_in_generation += 1
        self.unresolved_in_lineage += 1

    def resolve(self) -> None:
        """A terminal record arrived. C1 does not move -- it counts reservations
        regardless of outcome -- and the reservation is no longer unresolved."""
        self.unresolved_in_lineage = max(0, self.unresolved_in_lineage - 1)

    def reconcile(self) -> None:
        """§13f G.3: C2 decreases on a valid reconciliation only. For a
        reservation that was already resolved this is a no-op, because it was
        never counted as outstanding."""
        self.unresolved_in_lineage = max(0, self.unresolved_in_lineage - 1)

    def new_generation(self) -> None:
        """C1 resets and **C2 does not** (§13f G.3).

        Were C2 to reset here, an operator facing it could clear it by closing
        the generation -- turning *the graph boundary is not answering* into
        *close it and carry on*, which is §11's refill-through-the-reset-path
        failure arriving through the door §11 was written to shut.
        """
        self.reservations_in_generation = 0

    def refusal(self) -> Any:
        """The code this state refuses with, or None. C1 first: it is the
        catastrophe bound, and a generation past it should be closed before
        anything else is diagnosed."""
        if C1.exceeded_by(self.reservations_in_generation):
            return DURABLE_CEILING_REACHED
        if C2.exceeded_by(self.unresolved_in_lineage):
            return OUTSTANDING_RESERVATION_CEILING_REACHED
        return None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "durable_reservations_in_generation": self.reservations_in_generation,
            "durable_unresolved_in_lineage": self.unresolved_in_lineage,
        }


def recount(generation_read, lineage_fenced_unresolved: int) -> CeilingTotals:
    """Rebuild both totals from durable state (§13f G.6).

    C1 from the authoritative generation's own reservations; C2 from the whole
    lineage, because an unresolved reservation in a closed predecessor was never
    reconciled and is still outstanding.
    """
    return CeilingTotals(
        reservations_in_generation=generation_read.reservations_total,
        unresolved_in_lineage=lineage_fenced_unresolved,
    )


def status(totals: CeilingTotals, *, simulated_reservations: int = 0,
           mode: str = "SHADOW") -> Dict[str, Any]:
    """Durable and simulated published apart, under different authorities.

    A SHADOW coordinator reads a real lineage and may find unresolved
    reservations from an earlier authorized run. Reporting that as zero would be
    a false statement about the durable record rather than an honest one about
    simulation, so the durable fields are read under any mode and the simulated
    ones are named as SHADOW's own arithmetic.
    """
    published = {
        "schema": SCHEMA,
        "ceilings": [ceiling.as_dict() for ceiling in CEILINGS],
        "ceiling_configuration_identity": configuration_identity(),
        "ceilings_are_configurable": False,
        "calibration": {
            "C1": "SHOULD_NEVER_FIRE",
            "C2": "FIRES_WHEN_THE_BOUNDARY_STOPS_ANSWERING",
        },
    }
    published.update(totals.as_dict())
    published["shadow_simulated_reservations"] = simulated_reservations
    published["shadow_unresolved_simulation"] = NOT_SIMULABLE
    published["shadow_simulation_note"] = (
        "SHADOW HAS NO ADAPTER THAT COULD FAIL TO ANSWER. THE DURABLE COUNTS "
        "ABOVE ARE READ FROM THE LEDGER UNDER ANY MODE")
    return published
