"""What a walking receiver may and may not do between two observations.

For a mobile receiver the conserved object is **not** position. Position is
supposed to move. What is conserved is the apparatus and the lineage around the
movement:

    receiver apparatus identity   preserved
    RF signal-chain identity      preserved unless the configuration changed
    monotonic source identity     preserved -- it defines the comparison domain
    configuration epoch           preserved by a step, required by a change
    position                      permitted to change
    surface contribution          permitted only behind an admitting join

A note on naming
----------------
The displacement check was proposed as ``IMPOSSIBLE_DISPLACEMENT``. It is named
``DISPLACEMENT_EXCEEDS_DECLARED_BOUNDS`` here, because "impossible" invites
"therefore spoofed" and what is established is narrower: **the two states cannot
both describe one continuous trajectory under the bounds that were declared.**
That is consistent with a spoofed fix, a wrong speed, an understated accuracy, a
clock error, or an operator who put the phone in a car. The finding does not
choose among them, and its name should not either.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Optional, Tuple

from scythe_invariant_ledger import (
    BoundedCeiling, Coordinate, InvariantVerdict, TransitionContract,
    check_transition, signature,
)


SCHEMA = "scythe.rf-walk-transitions.v1"

EARTH_RADIUS_M = 6_371_008.8

# Used only when no speed was declared for either end of the step. Deliberately
# generous: it is the speed a bound must assume when nobody said, and assuming
# a walking pace there would manufacture violations out of an omission.
UNDECLARED_MAX_SPEED_MPS = 60.0

DISPLACEMENT_BASIS: Tuple[str, ...] = (
    "MAX_DECLARED_SPEED_TIMES_ELAPSED_MONOTONIC_TIME",
    "POSE_UNCERTAINTY_AT_BOTH_ENDS",
    "GREAT_CIRCLE_DISPLACEMENT_ON_A_SPHERE",
)
DISPLACEMENT_SCOPE = (
    "A VIOLATION ESTABLISHES THAT THE TWO STATES CANNOT BOTH DESCRIBE ONE "
    "CONTINUOUS TRAJECTORY UNDER THE DECLARED BOUNDS. IT DOES NOT ESTABLISH "
    "SPOOFING, AND IS EQUALLY CONSISTENT WITH A WRONG SPEED, AN UNDERSTATED "
    "ACCURACY, A CLOCK ERROR, OR A RECEIVER THAT CHANGED VEHICLE"
)

# The coordinates a walk signature carries.
SIGNATURE_FIELDS: Tuple[str, ...] = (
    "device_id", "receiver_state_chain_hash", "monotonic_source_id",
    "signal_chain_hash", "configuration_epoch",
    "latitude", "longitude", "observed_monotonic_ns",
    "displacement_m", "kinematic_budget_m",
    "pose_uncertainty_before_m", "pose_uncertainty_after_m",
    "surface_rows_contributed",
)

DISPLACEMENT_CEILING = BoundedCeiling(
    name="displacement",
    value_field="displacement_m",
    budget_fields=("kinematic_budget_m", "pose_uncertainty_before_m",
                   "pose_uncertainty_after_m"),
    accounting_basis=DISPLACEMENT_BASIS,
)

_COMMON_PRESERVED = ("device_id", "receiver_state_chain_hash")


def great_circle_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine displacement in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def kinematic_budget_m(elapsed_s: float, speed_before_mps: Optional[float],
                       speed_after_mps: Optional[float]) -> float:
    """How far the receiver could have travelled, from declared speeds.

    The faster of the two ends is used: a step that began at a walk and ended in
    a vehicle was a vehicle for part of it, and taking the slower end would
    manufacture a violation out of the acceleration.
    """
    speeds = [abs(float(s)) for s in (speed_before_mps, speed_after_mps)
              if s is not None and math.isfinite(float(s))]
    speed = max(speeds) if speeds else UNDECLARED_MAX_SPEED_MPS
    return speed * max(0.0, float(elapsed_s))


def walk_signature(*, device_id: str, receiver_state_chain_hash: str,
                   monotonic_source_id: str, signal_chain_hash: str,
                   configuration_epoch: int, latitude: float, longitude: float,
                   observed_monotonic_ns: int,
                   pose_uncertainty_m: Optional[float],
                   surface_rows_contributed: int = 0,
                   displacement_m: Any = None,
                   kinematic_budget_m: Any = None,
                   pose_uncertainty_before_m: Any = None,
                   pose_uncertainty_after_m: Any = None
                   ) -> Dict[str, Coordinate]:
    """One end of a step. The step-level coordinates are filled by `walk_step`."""
    absent = Coordinate("ABSENT")
    return signature(
        device_id=device_id,
        receiver_state_chain_hash=receiver_state_chain_hash,
        monotonic_source_id=monotonic_source_id,
        signal_chain_hash=signal_chain_hash,
        configuration_epoch=configuration_epoch,
        latitude=latitude, longitude=longitude,
        observed_monotonic_ns=observed_monotonic_ns,
        surface_rows_contributed=surface_rows_contributed,
        displacement_m=displacement_m if displacement_m is not None else absent,
        kinematic_budget_m=(kinematic_budget_m if kinematic_budget_m is not None
                            else absent),
        pose_uncertainty_before_m=(pose_uncertainty_before_m
                                   if pose_uncertainty_before_m is not None else absent),
        pose_uncertainty_after_m=(pose_uncertainty_after_m
                                  if pose_uncertainty_after_m is not None else absent),
    )


def with_step_measures(before: Dict[str, Coordinate], after: Dict[str, Coordinate],
                       *, speed_before_mps: Optional[float] = None,
                       speed_after_mps: Optional[float] = None,
                       pose_before_m: Optional[float] = None,
                       pose_after_m: Optional[float] = None
                       ) -> Tuple[Dict[str, Coordinate], Dict[str, Coordinate]]:
    """Fill the step-level coordinates the ceiling reads.

    Elapsed time is computed only when both ends are on the same monotonic
    source. Across domains there is no subtraction to perform, and the
    coordinates are left ABSENT so the domain finding is what a reader sees
    rather than a displacement computed from incomparable clocks.
    """
    same_domain = (before["monotonic_source_id"].value
                   == after["monotonic_source_id"].value)
    if not same_domain:
        return before, after
    elapsed_s = (after["observed_monotonic_ns"].value
                 - before["observed_monotonic_ns"].value) / 1e9
    distance = great_circle_m(before["latitude"].value, before["longitude"].value,
                              after["latitude"].value, after["longitude"].value)
    budget = kinematic_budget_m(elapsed_s, speed_before_mps, speed_after_mps)
    filled = dict(after)
    filled["displacement_m"] = Coordinate.of(distance)
    filled["kinematic_budget_m"] = Coordinate.of(budget)
    filled["pose_uncertainty_before_m"] = Coordinate.of(float(pose_before_m or 0.0))
    filled["pose_uncertainty_after_m"] = Coordinate.of(float(pose_after_m or 0.0))
    seeded = dict(before)
    for name in ("displacement_m", "kinematic_budget_m",
                 "pose_uncertainty_before_m", "pose_uncertainty_after_m"):
        seeded[name] = Coordinate("ABSENT")
    return seeded, filled


# -- the transitions ------------------------------------------------------

# One step of a survey. Nothing about the apparatus moves; the receiver does.
WALK_STEP = TransitionContract(
    name="WALK_STEP",
    must_preserve=_COMMON_PRESERVED + ("signal_chain_hash", "configuration_epoch"),
    may_change=("latitude", "longitude", "observed_monotonic_ns",
                "displacement_m", "kinematic_budget_m",
                "pose_uncertainty_before_m", "pose_uncertainty_after_m",
                "surface_rows_contributed"),
    # The clock is the comparison domain. Two monotonic sources cannot be
    # subtracted, so a change here suppresses every other finding rather than
    # producing a displacement computed from incomparable instants.
    domain_fields=("monotonic_source_id",),
    ceilings=(DISPLACEMENT_CEILING,),
)

# A step during which the RF configuration changed. The chain must move, and so
# must the epoch: products either side were taken through a different chain.
WALK_STEP_WITH_RECONFIGURATION = TransitionContract(
    name="WALK_STEP_WITH_RECONFIGURATION",
    must_preserve=_COMMON_PRESERVED,
    must_change=("signal_chain_hash", "configuration_epoch"),
    may_change=("latitude", "longitude", "observed_monotonic_ns",
                "displacement_m", "kinematic_budget_m",
                "pose_uncertainty_before_m", "pose_uncertainty_after_m",
                "surface_rows_contributed"),
    domain_fields=("monotonic_source_id",),
    ceilings=(DISPLACEMENT_CEILING,),
)

CONTRACTS: Dict[str, TransitionContract] = {
    contract.name: contract
    for contract in (WALK_STEP, WALK_STEP_WITH_RECONFIGURATION)
}


def surface_contract(join_admits: bool) -> TransitionContract:
    """The contract for a step, selected by whether the join admits.

    Surface eligibility is not a coordinate rule; it is which rule applies. When
    the join does not admit, ``surface_rows_contributed`` moves into
    ``must_preserve`` and any contribution is a prohibited change. Choosing the
    contract by the join's own verdict is what makes
    "contributed without an eligible join" unrepresentable rather than merely
    discouraged.
    """
    if join_admits:
        return WALK_STEP
    return TransitionContract(
        name="WALK_STEP",
        must_preserve=WALK_STEP.must_preserve + ("surface_rows_contributed",),
        may_change=tuple(f for f in WALK_STEP.may_change
                         if f != "surface_rows_contributed"),
        domain_fields=WALK_STEP.domain_fields,
        ceilings=WALK_STEP.ceilings,
    )


def check_walk_step(before: Dict[str, Coordinate], after: Dict[str, Coordinate],
                    *, join_admits: bool = True,
                    reconfigured: bool = False) -> InvariantVerdict:
    """Check one step. Pure: no clock, no I/O, no mutation."""
    if reconfigured:
        return check_transition(before, "WALK_STEP_WITH_RECONFIGURATION", after,
                                WALK_STEP_WITH_RECONFIGURATION)
    return check_transition(before, "WALK_STEP", after,
                            surface_contract(join_admits))


def transitions_status() -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "transitions": sorted(CONTRACTS),
        "signature_fields": list(SIGNATURE_FIELDS),
        "conserved": {
            "receiver apparatus identity": "PRESERVED",
            "RF signal-chain identity": "PRESERVED_UNLESS_RECONFIGURED",
            "monotonic source identity": "COMPARISON_DOMAIN",
            "configuration epoch": "PRESERVED_BY_A_STEP_REQUIRED_BY_A_CHANGE",
            "position": "PERMITTED_TO_CHANGE",
            "surface contribution": "PERMITTED_ONLY_BEHIND_AN_ADMITTING_JOIN",
        },
        "displacement_basis": list(DISPLACEMENT_BASIS),
        "displacement_scope": DISPLACEMENT_SCOPE,
        "displacement_finding_name": "DISPLACEMENT_EXCEEDS_DECLARED_BOUNDS",
        "renamed_from": "IMPOSSIBLE_DISPLACEMENT",
        "rename_reason": (
            "'IMPOSSIBLE' INVITES 'THEREFORE SPOOFED'. WHAT IS ESTABLISHED IS "
            "NARROWER AND THE NAME SHOULD SAY SO"),
        "undeclared_max_speed_mps": UNDECLARED_MAX_SPEED_MPS,
        "undeclared_speed_note": (
            "THE SPEED A BOUND MUST ASSUME WHEN NOBODY SAID. ASSUMING A WALKING "
            "PACE WOULD MANUFACTURE VIOLATIONS OUT OF AN OMISSION"),
        "mutates": False,
        "side_effects": "NONE",
    }
