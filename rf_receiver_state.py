"""Where the instrument was, when, and how well any of that is known.

This is a second evidence chain, deliberately not folded into
``signal_chain_hash``.  The two answer different questions and fail
independently:

    signal_chain_hash        what instrument produced this measurement
    receiver_state_chain_hash  where, when and in what orientation that
                               instrument was *believed* to be

Folding them together would make a GPS fix change the identity of the receiver,
and a gain step change the identity of a position.  Neither is true, and a single
hash covering both would make every comparison between them meaningless in one
direction or the other.

Course is not heading
---------------------
The most expensive available mistake in this module is treating GNSS course as
device heading.  Course describes the direction the receiver is *translating*;
heading describes the direction the antenna is *pointing*.  At 1.1 m/s they
decouple completely, and they become unstable for entirely different reasons:
course from GNSS noise divided by a small velocity, heading from magnetic
disturbance and tilt.  A body-shadow experiment needs heading and gets nothing
useful from course.  ``orientation.heading_source`` is therefore ``UNDECLARED``
until something that actually measures orientation declares it, and
``motion.course_source`` can never be promoted into it.

The pose budget
---------------
A timestamp cutoff is the wrong gate.  What matters is how far the receiver could
have moved inside the timing uncertainty, which is a distance, not a duration::

    sigma_motion = v * sigma_t
    sigma_pose   = sqrt(sigma_gnss^2 + (v * sigma_t)^2 + sigma_mount^2)

At 1.1 m/s a 42 ms uncertainty contributes 4.6 cm and is irrelevant beside a
4.8 m GNSS circle; in a vehicle at 20 m/s the same 42 ms contributes 0.84 m and
starts to matter.  One rule covers both because it is expressed in metres.

Nothing here estimates a location.  This module produces receiver states, the
bounded join between a state and an RF observation, and the refusals for when
that join cannot be made.  The likelihood, the posterior and the planner are
separate and come later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Any, Dict, Optional, Tuple

SCHEMA = "scythe.rf-receiver-state.v1"
CHAIN_SCHEMA = "scythe.rf-receiver-state-chain.v1"
CHAIN_REVISION = "v1"

# --- authorities -----------------------------------------------------------
#
# Every one of these says who is making the claim, never how good it is. A
# 4.8 m accuracy figure from the device is DEVICE_GNSS whether the sky is clear
# or the operator is standing between two buildings; the accuracy number carries
# the quality and the authority carries the provenance.
POSITION_AUTHORITIES: Dict[str, str] = {
    "DEVICE_GNSS": "REPORTED BY THE DEVICE'S OWN GNSS RECEIVER",
    "DEVICE_FUSED": (
        "REPORTED BY A PLATFORM LOCATION SERVICE FUSING GNSS WITH NETWORK AND "
        "INERTIAL SOURCES. THE MIX IS NOT VISIBLE TO SCYTHE"
    ),
    "OPERATOR_DECLARED": "TYPED OR PLACED BY A PERSON, NOT MEASURED",
    "UNDECLARED": "NO POSITION WAS SUPPLIED",
}
COURSE_SOURCES: Dict[str, str] = {
    "GNSS_COURSE": (
        "DIRECTION OF TRANSLATION DERIVED FROM SUCCESSIVE FIXES. NOT AN "
        "ORIENTATION AND NEVER USABLE AS ONE"
    ),
    "DERIVED_FROM_TRACK": "COMPUTED BY SCYTHE FROM CONSECUTIVE POSITIONS",
    "UNDECLARED": "NO COURSE WAS SUPPLIED",
}
HEADING_SOURCES: Dict[str, str] = {
    "DEVICE_MAGNETOMETER": "COMPASS HEADING, SUBJECT TO LOCAL MAGNETIC DISTURBANCE",
    "DEVICE_IMU_FUSED": "ORIENTATION FROM A FUSED INERTIAL AND MAGNETIC SOLUTION",
    "OPERATOR_DECLARED": "STATED BY A PERSON",
    "UNDECLARED": (
        "NO ORIENTATION WAS MEASURED. GNSS COURSE IS NOT PROMOTED HERE UNDER ANY "
        "CIRCUMSTANCES: IT DESCRIBES TRANSLATION, NOT POINTING"
    ),
}

# --- time alignment --------------------------------------------------------
ALIGNMENT_METHODS: Dict[str, str] = {
    "BOUNDED_CLOCK_EXCHANGE": (
        "ROUND-TRIP EXCHANGE BETWEEN DEVICE AND ORCHESTRATOR BOUNDING THE OFFSET "
        "BY HALF THE ROUND-TRIP TIME"
    ),
    "SHARED_MONOTONIC_SOURCE": "BOTH TIMESTAMPS CAME FROM ONE CLOCK",
    "DEVICE_TIMESTAMP_TRUSTED": (
        "THE DEVICE'S CLOCK WAS TAKEN AT FACE VALUE. THIS IS NOT AN ALIGNMENT "
        "AND CANNOT PRODUCE A BOUNDED STATE"
    ),
    "NOT_ATTEMPTED": "NO ALIGNMENT WAS PERFORMED",
}

# The four states, and what each one is allowed to contribute. Breadcrumbs are
# always permitted: showing where the operator walked is a record of the survey,
# not an inference about an emitter. Everything that feeds a posterior requires
# the join to be at least bounded.
ALIGNMENT_STATES: Tuple[str, ...] = ("VERIFIED", "BOUNDED", "UNVERIFIED", "STALE")
ALIGNMENT_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    "VERIFIED": {"breadcrumbs": True, "heatmap_update": True,
                 "bearing_like_evidence": True,
                 "note": "SHARED CLOCK OR EXCHANGE TIGHT ENOUGH TO BE NEGLIGIBLE"},
    "BOUNDED": {"breadcrumbs": True, "heatmap_update": True,
                "bearing_like_evidence": "CONDITIONAL",
                "note": ("THE OFFSET IS BOUNDED AND ITS CONTRIBUTION TO POSE "
                         "UNCERTAINTY IS PROPAGATED, NOT IGNORED. BEARING-LIKE "
                         "EVIDENCE ADDITIONALLY REQUIRES A VERIFIED HEADING "
                         "SOURCE, WHICH TIME ALIGNMENT DOES NOT SUPPLY")},
    "UNVERIFIED": {"breadcrumbs": True, "heatmap_update": False,
                   "bearing_like_evidence": False,
                   "note": ("NOTHING JOINED THE OBSERVATION TO THE STATE. A "
                            "SURFACE BUILT ON THIS WOULD BE ASSERTING A "
                            "CONTEMPORANEITY NOBODY MEASURED")},
    "STALE": {"breadcrumbs": True, "heatmap_update": False,
              "bearing_like_evidence": False,
              "note": ("THE STATE IS TOO OLD FOR THE OBSERVATION AT THIS SPEED. "
                       "STALENESS IS MEASURED IN METRES OF POSSIBLE MOVEMENT, "
                       "NOT IN SECONDS")},
}

# A shared clock, or an exchange whose uncertainty is small enough that its
# spatial contribution is negligible at any survey speed, is VERIFIED.
VERIFIED_UNCERTAINTY_MS = 5.0
# Beyond this the join is STALE rather than BOUNDED: the receiver could have
# moved further than the GNSS circle inside the timing uncertainty alone, so the
# state no longer describes where the observation was made.
STALE_MOTION_RATIO = 1.0
# Floor on the GNSS circle used in that comparison, so a device reporting an
# implausibly small accuracy cannot make everything look stale.
MIN_POSITION_ACCURACY_M = 1.0
# Mount contribution: the antenna is on a 2 m magnetic base and the operator's
# relationship to it is UNDECLARED, so this is the declared unknown rather than
# a measured offset. It is in the budget so that it cannot be forgotten.
DEFAULT_MOUNT_UNCERTAINTY_M = 2.0

JOIN_REFUSALS: Dict[str, str] = {
    "NO_RECEIVER_STATE": "NO RECEIVER STATE WAS SUPPLIED FOR THIS OBSERVATION",
    "NO_POSITION": (
        "THE RECEIVER STATE CARRIES NO POSITION. A SURVEY POINT WITHOUT ONE IS "
        "A MEASUREMENT, NOT A GEOMETRY"
    ),
    "ALIGNMENT_NOT_ATTEMPTED": (
        "NO CLOCK ALIGNMENT WAS PERFORMED, SO THE OBSERVATION AND THE STATE ARE "
        "NOT KNOWN TO DESCRIBE THE SAME MOMENT"
    ),
    "ALIGNMENT_UNBOUNDED": (
        "THE ALIGNMENT METHOD PRODUCES NO UNCERTAINTY, SO NOTHING CAN BE "
        "PROPAGATED AND NOTHING MAY BE CLAIMED"
    ),
    "SIGNAL_CHAIN_CHANGED": (
        "THE OBSERVATION AND THE STATE BELONG TO DIFFERENT RF SIGNAL CHAINS"
    ),
    "RECEIVER_STATE_CHAIN_CHANGED": (
        "THE RECEIVER-STATE CHAIN MOVED BETWEEN THE STATE AND ITS USE. THE "
        "POSITION SOURCE OR MOUNT CHANGED, SO THE STATES ARE NOT COMPARABLE"
    ),
}


def canonical_bytes(manifest: Dict[str, Any]) -> bytes:
    """The bytes that are hashed. Same idiom as the signal chain, deliberately.

    Sorted keys and no incidental whitespace, so a reordered or reformatted
    manifest is the same chain and hashes the same.
    """
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def receiver_state_chain_manifest(*, device_id: str, position_authority: str,
                                  course_source: str, heading_source: str,
                                  alignment_method: str,
                                  mount_orientation: str = "UNDECLARED",
                                  mount_uncertainty_m: float = DEFAULT_MOUNT_UNCERTAINTY_M
                                  ) -> Dict[str, Any]:
    """What makes two receiver states comparable with each other.

    Deliberately excludes the position itself, exactly as the signal chain
    excludes the centre frequency: a chain identity that changed with every fix
    would make every state its own incomparable island. What is in here is the
    *apparatus* of positioning -- which device, which sources, which alignment
    method, which mount -- because a change to any of those changes what a
    position means.
    """
    return {
        "schema": CHAIN_SCHEMA,
        "device_id": device_id,
        "position_authority": position_authority,
        "course_source": course_source,
        "heading_source": heading_source,
        "alignment_method": alignment_method,
        "mount_orientation_relative_to_operator": mount_orientation,
        "mount_uncertainty_m": round(float(mount_uncertainty_m), 4),
    }


def receiver_state_chain_hash(manifest: Dict[str, Any]) -> str:
    digest = hashlib.blake2s(canonical_bytes(manifest), digest_size=16).hexdigest()
    return f"blake2s:{digest}"


def motion_uncertainty_m(speed_mps: Optional[float],
                         uncertainty_ms: Optional[float]) -> Optional[float]:
    """How far the receiver could have moved inside the timing uncertainty.

    ``v * sigma_t``. This is the whole reason a timestamp cutoff is the wrong
    gate: 42 ms is negligible on foot and material in a vehicle, and only the
    metres say which.
    """
    if speed_mps is None or uncertainty_ms is None:
        return None
    if not (math.isfinite(speed_mps) and math.isfinite(uncertainty_ms)):
        return None
    return abs(float(speed_mps)) * abs(float(uncertainty_ms)) / 1000.0


def pose_uncertainty_m(*, position_accuracy_m: Optional[float],
                       speed_mps: Optional[float],
                       alignment_uncertainty_ms: Optional[float],
                       mount_uncertainty_m: float = DEFAULT_MOUNT_UNCERTAINTY_M
                       ) -> Optional[float]:
    """The pose budget, in metres, combining the three independent contributions.

    Returns ``None`` when the position is unknown: a budget that silently
    substituted a default for a missing GNSS circle would publish a confident
    number about a position nobody supplied.
    """
    if position_accuracy_m is None or not math.isfinite(position_accuracy_m):
        return None
    motion = motion_uncertainty_m(speed_mps, alignment_uncertainty_ms) or 0.0
    mount = abs(float(mount_uncertainty_m))
    return math.sqrt(float(position_accuracy_m) ** 2 + motion ** 2 + mount ** 2)


@dataclass(frozen=True)
class ReceiverState:
    """One bounded belief about where the receiver was. Never a raw sample."""

    schema: str
    receiver_state_id: str
    receiver_state_chain_hash: str
    device_id: str

    latitude: Optional[float]
    longitude: Optional[float]
    horizontal_accuracy_m: Optional[float]
    altitude_m: Optional[float]
    position_authority: str

    speed_mps: Optional[float]
    course_deg: Optional[float]
    course_source: str

    # Separate from course, and separately sourced. Nothing in this module ever
    # writes a course value into a heading field.
    heading_deg: Optional[float]
    heading_source: str
    heading_accuracy_deg: Optional[float]

    device_timestamp: Optional[float]
    orchestrator_timestamp: Optional[float]
    offset_estimate_ms: Optional[float]
    alignment_uncertainty_ms: Optional[float]
    alignment_method: str
    alignment_status: str

    mount_orientation_relative_to_operator: str
    mount_uncertainty_m: float

    pose_uncertainty_m: Optional[float]
    motion_uncertainty_m: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        """Grouped as published. Each fact appears once, under what it is."""
        return {
            "schema": self.schema,
            "receiver_state_id": self.receiver_state_id,
            "receiver_state_chain_hash": self.receiver_state_chain_hash,
            "device_id": self.device_id,
            "position": {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "horizontal_accuracy_m": self.horizontal_accuracy_m,
                "altitude_m": self.altitude_m,
                "authority": self.position_authority,
            },
            "motion": {
                "speed_mps": self.speed_mps,
                "course_deg": self.course_deg,
                "course_source": self.course_source,
            },
            "orientation": {
                "heading_deg": self.heading_deg,
                "heading_source": self.heading_source,
                "heading_accuracy_deg": self.heading_accuracy_deg,
            },
            "time_alignment": {
                "device_timestamp": self.device_timestamp,
                "orchestrator_timestamp": self.orchestrator_timestamp,
                "offset_estimate_ms": self.offset_estimate_ms,
                "uncertainty_ms": self.alignment_uncertainty_ms,
                "method": self.alignment_method,
                "status": self.alignment_status,
            },
            "antenna_mount": {
                "orientation_relative_to_operator":
                    self.mount_orientation_relative_to_operator,
                "uncertainty_m": self.mount_uncertainty_m,
            },
            "uncertainty_budget": {
                "pose_uncertainty_m": self.pose_uncertainty_m,
                "motion_uncertainty_m": self.motion_uncertainty_m,
                "basis": "SQRT(GNSS^2 + (V*SIGMA_T)^2 + MOUNT^2)",
            },
        }


def _alignment_status(*, method: str, uncertainty_ms: Optional[float],
                      speed_mps: Optional[float],
                      position_accuracy_m: Optional[float]) -> str:
    """Which of the four states this join is in, decided in metres.

    STALE is not a duration. A state is stale when the receiver could have moved
    further than its own position circle inside the timing uncertainty, because
    at that point the state has stopped describing where the observation was
    made -- and that threshold arrives at a different number of milliseconds for
    a walker and for a vehicle.
    """
    if method == "NOT_ATTEMPTED":
        return "UNVERIFIED"
    if method == "DEVICE_TIMESTAMP_TRUSTED":
        # Taking a clock at face value is not an alignment, whatever number
        # accompanies it.
        return "UNVERIFIED"
    if uncertainty_ms is None or not math.isfinite(uncertainty_ms):
        return "UNVERIFIED"
    if method == "SHARED_MONOTONIC_SOURCE" or uncertainty_ms <= VERIFIED_UNCERTAINTY_MS:
        return "VERIFIED"
    motion = motion_uncertainty_m(speed_mps, uncertainty_ms)
    if motion is not None and position_accuracy_m is not None:
        circle = max(float(position_accuracy_m), MIN_POSITION_ACCURACY_M)
        if motion > circle * STALE_MOTION_RATIO:
            return "STALE"
    return "BOUNDED"


def build_receiver_state(*, device_id: str,
                         latitude: Optional[float] = None,
                         longitude: Optional[float] = None,
                         horizontal_accuracy_m: Optional[float] = None,
                         altitude_m: Optional[float] = None,
                         position_authority: str = "UNDECLARED",
                         speed_mps: Optional[float] = None,
                         course_deg: Optional[float] = None,
                         course_source: str = "UNDECLARED",
                         heading_deg: Optional[float] = None,
                         heading_source: str = "UNDECLARED",
                         heading_accuracy_deg: Optional[float] = None,
                         device_timestamp: Optional[float] = None,
                         orchestrator_timestamp: Optional[float] = None,
                         offset_estimate_ms: Optional[float] = None,
                         alignment_uncertainty_ms: Optional[float] = None,
                         alignment_method: str = "NOT_ATTEMPTED",
                         mount_orientation: str = "UNDECLARED",
                         mount_uncertainty_m: float = DEFAULT_MOUNT_UNCERTAINTY_M
                         ) -> ReceiverState:
    """One receiver state, with every declaration checked rather than accepted.

    An unrecognised authority or source becomes ``UNDECLARED`` rather than being
    carried through: a vocabulary that accepts anything is not a vocabulary, and
    a downstream consumer matching on ``DEVICE_GNSS`` would silently exclude a
    typo instead of refusing it.
    """
    if position_authority not in POSITION_AUTHORITIES:
        position_authority = "UNDECLARED"
    if course_source not in COURSE_SOURCES:
        course_source = "UNDECLARED"
    if heading_source not in HEADING_SOURCES:
        heading_source = "UNDECLARED"
    if alignment_method not in ALIGNMENT_METHODS:
        alignment_method = "NOT_ATTEMPTED"
    if latitude is None or longitude is None:
        position_authority = "UNDECLARED"
        latitude = longitude = horizontal_accuracy_m = None
    # A heading with no source that measures orientation is not a heading. This
    # is where GNSS course would get promoted if anything were careless enough
    # to try, and it is refused at the constructor rather than downstream.
    if heading_source == "UNDECLARED":
        heading_deg = None
        heading_accuracy_deg = None

    manifest = receiver_state_chain_manifest(
        device_id=device_id, position_authority=position_authority,
        course_source=course_source, heading_source=heading_source,
        alignment_method=alignment_method, mount_orientation=mount_orientation,
        mount_uncertainty_m=mount_uncertainty_m)
    chain_hash = receiver_state_chain_hash(manifest)
    status = _alignment_status(method=alignment_method,
                               uncertainty_ms=alignment_uncertainty_ms,
                               speed_mps=speed_mps,
                               position_accuracy_m=horizontal_accuracy_m)
    pose = pose_uncertainty_m(position_accuracy_m=horizontal_accuracy_m,
                              speed_mps=speed_mps,
                              alignment_uncertainty_ms=alignment_uncertainty_ms,
                              mount_uncertainty_m=mount_uncertainty_m)
    identity = canonical_bytes({
        "chain": chain_hash,
        "device_timestamp": device_timestamp,
        "orchestrator_timestamp": orchestrator_timestamp,
        "latitude": latitude, "longitude": longitude,
    })
    return ReceiverState(
        schema=SCHEMA,
        receiver_state_id=f"rsp-{hashlib.blake2s(identity, digest_size=8).hexdigest()}",
        receiver_state_chain_hash=chain_hash,
        device_id=device_id,
        latitude=latitude, longitude=longitude,
        horizontal_accuracy_m=horizontal_accuracy_m, altitude_m=altitude_m,
        position_authority=position_authority,
        speed_mps=speed_mps, course_deg=course_deg, course_source=course_source,
        heading_deg=heading_deg, heading_source=heading_source,
        heading_accuracy_deg=heading_accuracy_deg,
        device_timestamp=device_timestamp,
        orchestrator_timestamp=orchestrator_timestamp,
        offset_estimate_ms=offset_estimate_ms,
        alignment_uncertainty_ms=alignment_uncertainty_ms,
        alignment_method=alignment_method, alignment_status=status,
        mount_orientation_relative_to_operator=mount_orientation,
        mount_uncertainty_m=float(mount_uncertainty_m),
        pose_uncertainty_m=(round(pose, 4) if pose is not None else None),
        motion_uncertainty_m=(lambda m: round(m, 4) if m is not None else None)(
            motion_uncertainty_m(speed_mps, alignment_uncertainty_ms)),
    )


@dataclass(frozen=True)
class TimeAlignedJoin:
    """The edge, with its own verdict. A refusal here is a complete record."""

    schema: str = "scythe.rf-time-aligned-with.v1"
    joined: bool = False
    refusal: Optional[str] = None
    alignment_status: str = "UNVERIFIED"
    offset_estimate_ms: Optional[float] = None
    uncertainty_ms: Optional[float] = None
    method: str = "NOT_ATTEMPTED"
    authority: str = "DERIVED_MEASUREMENT"
    pose_uncertainty_m: Optional[float] = None
    capabilities: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {f: getattr(self, f) for f in self.__dataclass_fields__}
        payload["reason"] = (JOIN_REFUSALS.get(self.refusal, self.refusal)
                             if self.refusal else None)
        return payload


def time_align(observation: Dict[str, Any], state: Optional[ReceiverState], *,
               expected_receiver_state_chain_hash: Optional[str] = None
               ) -> TimeAlignedJoin:
    """Join one RF observation to one receiver state, or say why not.

    A survey point enters a posterior only through this edge. The edge carries
    the offset, its uncertainty, the method and the authority, so that a
    downstream consumer can propagate the uncertainty rather than discover after
    the fact that there was none to propagate.
    """
    if state is None:
        return TimeAlignedJoin(refusal="NO_RECEIVER_STATE")
    if state.latitude is None or state.longitude is None:
        return TimeAlignedJoin(refusal="NO_POSITION",
                               alignment_status=state.alignment_status)
    if (expected_receiver_state_chain_hash is not None
            and expected_receiver_state_chain_hash != state.receiver_state_chain_hash):
        return TimeAlignedJoin(refusal="RECEIVER_STATE_CHAIN_CHANGED",
                               alignment_status=state.alignment_status)
    observed_chain = observation.get("signal_chain_hash")
    expected_chain = observation.get("expected_signal_chain_hash")
    if (expected_chain is not None and observed_chain is not None
            and expected_chain != observed_chain):
        return TimeAlignedJoin(refusal="SIGNAL_CHAIN_CHANGED",
                               alignment_status=state.alignment_status)
    if state.alignment_method == "NOT_ATTEMPTED":
        return TimeAlignedJoin(refusal="ALIGNMENT_NOT_ATTEMPTED",
                               alignment_status="UNVERIFIED")
    if (state.alignment_uncertainty_ms is None
            and state.alignment_method != "SHARED_MONOTONIC_SOURCE"):
        return TimeAlignedJoin(refusal="ALIGNMENT_UNBOUNDED",
                               alignment_status="UNVERIFIED",
                               method=state.alignment_method)
    return TimeAlignedJoin(
        joined=True,
        alignment_status=state.alignment_status,
        offset_estimate_ms=state.offset_estimate_ms,
        uncertainty_ms=state.alignment_uncertainty_ms,
        method=state.alignment_method,
        pose_uncertainty_m=state.pose_uncertainty_m,
        capabilities=dict(ALIGNMENT_CAPABILITIES[state.alignment_status]),
    )


def may_update_posterior(join: TimeAlignedJoin) -> bool:
    """Whether this join may contribute to a location surface at all.

    Breadcrumbs are not gated here and never are: rendering where the operator
    walked is a record of the survey, not an inference about an emitter.
    """
    return bool(join.joined
                and ALIGNMENT_CAPABILITIES[join.alignment_status]["heatmap_update"])


def receiver_state_status() -> Dict[str, Any]:
    """The declared contract, for the status payload and for review."""
    return {
        "schema": SCHEMA,
        "chain_schema": CHAIN_SCHEMA,
        "chain_revision": CHAIN_REVISION,
        "state": "CONTRACT_ONLY_NO_COLLECTION_IMPLEMENTED",
        "folded_into_signal_chain": False,
        "separation_note": (
            "THE RF SIGNAL CHAIN ANSWERS WHAT INSTRUMENT PRODUCED A MEASUREMENT. "
            "THIS CHAIN ANSWERS WHERE, WHEN AND IN WHAT ORIENTATION THAT "
            "INSTRUMENT WAS BELIEVED TO BE. ONE HASH OVER BOTH WOULD MAKE A GPS "
            "FIX CHANGE THE IDENTITY OF THE RECEIVER"
        ),
        "position_authorities": dict(POSITION_AUTHORITIES),
        "course_sources": dict(COURSE_SOURCES),
        "heading_sources": dict(HEADING_SOURCES),
        "course_is_not_heading": (
            "COURSE DESCRIBES TRANSLATION AND HEADING DESCRIBES POINTING. AT "
            "WALKING SPEED THEY DECOUPLE AND DESTABILISE FOR DIFFERENT REASONS. "
            "GNSS COURSE IS NEVER PROMOTED INTO A HEADING FIELD"
        ),
        "alignment_methods": dict(ALIGNMENT_METHODS),
        "alignment_states": list(ALIGNMENT_STATES),
        "alignment_capabilities": dict(ALIGNMENT_CAPABILITIES),
        "verified_uncertainty_ms": VERIFIED_UNCERTAINTY_MS,
        "staleness_basis": "METRES_OF_POSSIBLE_MOVEMENT_NOT_SECONDS",
        "pose_budget": "SQRT(GNSS^2 + (V*SIGMA_T)^2 + MOUNT^2)",
        "default_mount_uncertainty_m": DEFAULT_MOUNT_UNCERTAINTY_M,
        "join_refusals": dict(JOIN_REFUSALS),
        "collection_implemented": False,
        "posterior_implemented": False,
        "planner_implemented": False,
        "body_shadow_implemented": False,
    }
