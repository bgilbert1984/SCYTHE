"""scythe_mission.py — Mission, Session, and ObservationSet domain types.

PR A per docs/research/scythe_mission_definition.md: types and serialization only. As of PR A,
this module imports nothing from, and is not imported by, graphops_rf_ingest.py, pcap_ingest.py,
hypergraph_engine.py, capture_policy.py, or scythe_invariant_ledger.py. PR B (graphops_rf_ingest.py
accepting an optional mission_id) is the first module to import this one -- that is the planned
direction of coupling (Collection may reference Mission identifiers; Mission never references
Collection back), not a violation of the PR A boundary.

Domain objects are plain, serializer-independent classes. to_dict()/from_dict() (a general
projection) and digest() (a canonical, identity-only projection) are two independent views of
the same object, not the object itself -- the "domain object -> serializer A -> serializer B"
shape called for in docs/research/scythe_architectural_invariants.md section 13
(serialization-agnostic discipline), not "JSON schema -> everything else".

Two invariants from that document are *enforced* here, not just represented:
  - section 3: mission_id/session_id are immutable after creation (Mission and Session are
    frozen dataclasses; any attempted mutation raises dataclasses.FrozenInstanceError).
    This is TOP-LEVEL immutability only. Attribute reassignment is blocked; a dict-typed field
    (MissionParameters.frequency_plan, Mission.region) is stored as a types.MappingProxyType so
    `mission.mission_parameters.frequency_plan["start"] = ...` raises too -- but that's still
    shallow: a mutable value nested one level further down (e.g. a list inside region) is not
    protected. Deep immutability is a known, deliberately deferred gap, not claimed solved.
    API surprise worth knowing, not a bug: isinstance(mission.region, dict) is False --
    MappingProxyType is not a dict subclass, even though .get()/in/len()/iteration/equality all
    behave exactly like one. Nothing in this codebase currently does an isinstance(..., dict)
    check on these fields; if something ever does, it needs to check Mapping (or just use the
    value directly) instead.
  - section 5: an ObservationSet is append-only after sealing (ObservationSet.record_observation()
    raises SealedObservationSetError once sealed() has been called).
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import types
from typing import Any, Dict, List, Optional

__all__ = [
    "MissionError", "SealedObservationSetError",
    "MISSION_TYPES", "RF_POSTURES",
    "MissionParameters", "Mission", "Session", "ObservationSet",
    "is_valid_mission_id",
]


class MissionError(ValueError):
    """Raised for a malformed or invalid Mission, Session, or ObservationSet."""


class SealedObservationSetError(MissionError):
    """Raised when an observation is recorded against an already-sealed ObservationSet."""


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# docs/research/scythe_mission_definition.md section 6: only categories with existing evidence
# in this repository (graphops_rf_ingest.py / graphops_rf_antenna.py, pcap_ingest.py, the ADS-B
# pipeline, the AIS endpoint, the Gate C/D governance series, mission_runner.py). WARDRIVE,
# LTE_COLLECTION, and OSINT_COLLECTION were considered and left out: no corresponding module
# exists yet. Add a type here only once something real backs it.
MISSION_TYPES = frozenset({
    "RF_SURVEY", "PCAP_COLLECTION", "ADSB_COLLECTION", "AIS_COLLECTION",
    "REPLAY", "CALIBRATION", "SIMULATION", "CUSTOM",
})

# Sub-classification of RF_SURVEY only -- downstream code asks mission.mission_type, never a
# separate top-level type per posture (section 6).
RF_POSTURES = frozenset({"WALK", "STATIONARY", "FOX_HUNT", "LONG_TERM_MONITOR"})


def is_valid_mission_id(value: Any) -> bool:
    """Public per PR B (docs/research/scythe_mission_definition.md): lets a collection module
    (e.g. graphops_rf_ingest.py) validate an optional mission_id against the exact same rule
    Mission.mission_id itself enforces, instead of duplicating the pattern -- the fix for the
    "mission vocabulary single source" gap applied to identifiers, not just MISSION_TYPES."""
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _canonical_json(obj: Any) -> str:
    """The one serialization used for identity: sorted keys, fixed separators. Never the
    general-purpose serializer -- see scythe_architectural_invariants.md sections 4 and 13.
    Structural determinism only: this solves key order and omitted-field consistency, not
    numeric canonicalization (-0.0 vs 0.0, NaN, Infinity, float repr edge cases). No field in
    this module currently carries a float, so that gap is deferred, not solved -- revisit this
    the moment one does."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256_of(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


def _require_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise MissionError(f"{field} must be a non-empty identifier matching {_ID_RE.pattern}")
    return value


def _require_nonempty_str(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MissionError(f"{field} must be a non-empty string")
    return value


def _check_unknown(d: Dict[str, Any], known: set, context: str) -> None:
    unknown = set(d) - known
    if unknown:
        raise MissionError(f"unknown {context} field(s): {sorted(unknown)}")


@dataclasses.dataclass(frozen=True)
class MissionParameters:
    """What a mission intends (scythe_mission_definition.md section 5) -- named "Mission
    Parameters", not "Capture Policy": capture_policy.py already owns that name for an
    unrelated capture-command authorization DSL. This is intent, not authorization."""
    sdr: Optional[str] = None
    antenna_sha256: Optional[str] = None
    gain: Optional[str] = None
    expected_environment: Optional[str] = None
    frequency_plan: Optional[Dict[str, Any]] = None
    gps_cadence: Optional[str] = None
    operator_notes: Optional[str] = None
    capture_profile: Optional[str] = None
    rf_posture: Optional[str] = None

    def __post_init__(self) -> None:
        if self.antenna_sha256 is not None and not _SHA256_RE.match(self.antenna_sha256):
            raise MissionError("antenna_sha256 must be a 64-character lowercase hex sha256")
        if self.rf_posture is not None and self.rf_posture not in RF_POSTURES:
            raise MissionError(f"rf_posture must be one of {sorted(RF_POSTURES)}")
        if self.frequency_plan is not None:
            if not isinstance(self.frequency_plan, dict):
                raise MissionError("frequency_plan must be an object")
            # frozen=True only blocks reassigning the *attribute*; it does nothing to stop
            # frequency_plan["start"] = ... mutating the dict object in place, which would
            # silently change digest() afterwards -- the same bug class ObservationSet's
            # preprocessing_history had. Storing an immutable mapping closes it here too.
            object.__setattr__(self, "frequency_plan", types.MappingProxyType(dict(self.frequency_plan)))

    def to_dict(self) -> Dict[str, Any]:
        d = {}
        for f in dataclasses.fields(self):
            v = getattr(self, f.name)
            if v is None:
                continue
            d[f.name] = dict(v) if f.name == "frequency_plan" else v
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MissionParameters":
        known = {f.name for f in dataclasses.fields(cls)}
        _check_unknown(d, known, "mission_parameters")
        return cls(**d)


@dataclasses.dataclass(frozen=True)
class Mission:
    """mission-definition/1 (scythe_mission_definition.md section 4). mission_id is immutable
    after creation -- enforced by frozen=True, not just documented."""
    mission_id: str
    mission_type: str
    purpose: str
    operator: str
    mission_parameters: MissionParameters = dataclasses.field(default_factory=MissionParameters)
    classification: Optional[str] = None
    region: Optional[Dict[str, Any]] = None
    start_after: Optional[str] = None
    expire_after: Optional[str] = None
    gate_d_request_sha256: Optional[str] = None  # only meaningful when mission_type == REPLAY

    schema: str = dataclasses.field(default="mission-definition/1", init=False)

    def __post_init__(self) -> None:
        _require_id(self.mission_id, "mission_id")
        if self.mission_type not in MISSION_TYPES:
            raise MissionError(f"mission_type must be one of {sorted(MISSION_TYPES)}")
        _require_nonempty_str(self.purpose, "purpose")
        _require_nonempty_str(self.operator, "operator")
        if self.mission_parameters.rf_posture is not None and self.mission_type != "RF_SURVEY":
            raise MissionError("rf_posture is only meaningful when mission_type is RF_SURVEY")
        if self.gate_d_request_sha256 is not None:
            if self.mission_type != "REPLAY":
                raise MissionError("gate_d_request_sha256 is only meaningful when mission_type is REPLAY")
            if not _SHA256_RE.match(self.gate_d_request_sha256):
                raise MissionError("gate_d_request_sha256 must be a 64-character lowercase hex sha256")
        if self.region is not None:
            if not isinstance(self.region, dict):
                raise MissionError("region must be an object")
            # Shallow immutability only, by the same MappingProxyType mechanism as
            # MissionParameters.frequency_plan: region["type"] = ... now raises. A nested
            # mutable value (e.g. region["coordinates"][0].append(...), a list one level
            # down) is NOT protected -- deep immutability is a known, deliberately deferred
            # gap, not claimed solved here.
            object.__setattr__(self, "region", types.MappingProxyType(dict(self.region)))

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "schema": self.schema,
            "mission_id": self.mission_id,
            "mission_type": self.mission_type,
            "purpose": self.purpose,
            "operator": self.operator,
            "mission_parameters": self.mission_parameters.to_dict(),
        }
        for k, v in (("classification", self.classification), ("region", self.region),
                     ("start_after", self.start_after), ("expire_after", self.expire_after),
                     ("gate_d_request_sha256", self.gate_d_request_sha256)):
            if v is not None:
                d[k] = dict(v) if k == "region" else v
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Mission":
        d = dict(d)
        schema = d.pop("schema", "mission-definition/1")
        if schema != "mission-definition/1":
            raise MissionError(f"unknown schema {schema!r}")
        mp = MissionParameters.from_dict(d.pop("mission_parameters", {}))
        known = {f.name for f in dataclasses.fields(cls) if f.init}
        _check_unknown(d, known, "mission")
        return cls(mission_parameters=mp, **d)

    @classmethod
    def from_json(cls, s: str) -> "Mission":
        return cls.from_dict(json.loads(s))

    def digest(self) -> str:
        """mission_sha256: the canonical identity derivation (architectural invariants section
        4). Stable for the lifetime of this (immutable) instance."""
        return _sha256_of(self.to_dict())


@dataclasses.dataclass(frozen=True)
class Session:
    """mission-session/1 (scythe_mission_definition.md section 8): purely factual, no intent.
    session_id is immutable after creation -- enforced by frozen=True."""
    session_id: str
    mission_id: str
    started_utc: str
    operator: str
    receiver: Optional[str] = None
    sensor_declaration_sha256: Optional[str] = None
    capture_profile_used: Optional[str] = None
    location_source: Optional[str] = None
    ended_utc: Optional[str] = None

    schema: str = dataclasses.field(default="mission-session/1", init=False)

    def __post_init__(self) -> None:
        _require_id(self.session_id, "session_id")
        _require_id(self.mission_id, "mission_id")
        _require_nonempty_str(self.started_utc, "started_utc")
        _require_nonempty_str(self.operator, "operator")
        if self.sensor_declaration_sha256 is not None and not _SHA256_RE.match(self.sensor_declaration_sha256):
            raise MissionError("sensor_declaration_sha256 must be a 64-character lowercase hex sha256")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "schema": self.schema, "session_id": self.session_id, "mission_id": self.mission_id,
            "started_utc": self.started_utc, "operator": self.operator,
        }
        for k, v in (("receiver", self.receiver), ("sensor_declaration_sha256", self.sensor_declaration_sha256),
                     ("capture_profile_used", self.capture_profile_used), ("location_source", self.location_source),
                     ("ended_utc", self.ended_utc)):
            if v is not None:
                d[k] = v
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Session":
        d = dict(d)
        schema = d.pop("schema", "mission-session/1")
        if schema != "mission-session/1":
            raise MissionError(f"unknown schema {schema!r}")
        known = {f.name for f in dataclasses.fields(cls) if f.init}
        _check_unknown(d, known, "session")
        return cls(**d)

    @classmethod
    def from_json(cls, s: str) -> "Session":
        return cls.from_dict(json.loads(s))

    def digest(self) -> str:
        """session_sha256: canonical identity derivation, stable for this instance's lifetime."""
        return _sha256_of(self.to_dict())


class ObservationSet:
    """observation-set/1 (scythe_mission_definition.md section 9): one physical acquisition
    artifact, distinct from the Observations extracted from it. The one membership invariant
    (scythe_architectural_invariants.md section 5, "append-only after sealing") actually
    *enforced* here rather than merely described: record_observation() raises
    SealedObservationSetError once seal() has been called. Not a frozen dataclass like Mission
    and Session, because observation_count must legitimately grow before sealing."""
    schema = "observation-set/1"

    def __init__(self, observation_set_id: str, session_id: str, source_filename: str,
                 source_sha256: str, acquisition_tool_version: Optional[str] = None,
                 preprocessing_history: Optional[List[str]] = None) -> None:
        _require_id(observation_set_id, "observation_set_id")
        _require_id(session_id, "session_id")
        _require_nonempty_str(source_filename, "source_filename")
        if not _SHA256_RE.match(source_sha256):
            raise MissionError("source_sha256 must be a 64-character lowercase hex sha256")
        self.observation_set_id = observation_set_id
        self.session_id = session_id
        self.source_filename = source_filename
        self.source_sha256 = source_sha256
        self.acquisition_tool_version = acquisition_tool_version
        self._preprocessing_history: List[str] = list(preprocessing_history or [])
        self._observation_count = 0
        self._sealed = False

    @property
    def observation_count(self) -> int:
        return self._observation_count

    @property
    def sealed(self) -> bool:
        return self._sealed

    @property
    def preprocessing_history(self) -> List[str]:
        """A copy, not a live reference: mutating the returned list does not affect this
        object's state, sealed or not. The only way to add a step is add_preprocessing_step(),
        which is sealing-aware. (Found by review: an earlier version exposed this as a plain
        public list, so appending to it after seal() silently changed what digest() would
        compute next -- the seal was shallow. This closes that gap.)"""
        return list(self._preprocessing_history)

    def add_preprocessing_step(self, step: str) -> None:
        """The only way to extend preprocessing_history. Sealing-aware, same as
        record_observation() -- see _check_not_sealed()."""
        self._check_not_sealed("preprocessing_history")
        self._preprocessing_history.append(step)

    def _check_not_sealed(self, what: str) -> None:
        if self._sealed:
            raise SealedObservationSetError(
                f"ObservationSet {self.observation_set_id!r} is sealed; {what} never "
                "migrates into a sealed set (scythe_architectural_invariants.md section 5)"
            )

    def record_observation(self) -> int:
        """Increment observation_count by one. The append-only membership invariant: raises
        SealedObservationSetError once sealed, instead of silently accepting it."""
        self._check_not_sealed("observations")
        self._observation_count += 1
        return self._observation_count

    def seal(self) -> None:
        self._sealed = True

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "schema": self.schema, "observation_set_id": self.observation_set_id,
            "session_id": self.session_id, "source_filename": self.source_filename,
            "source_sha256": self.source_sha256, "preprocessing_history": list(self.preprocessing_history),
            "observation_count": self._observation_count, "sealed": self._sealed,
        }
        if self.acquisition_tool_version is not None:
            d["acquisition_tool_version"] = self.acquisition_tool_version
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ObservationSet":
        d = dict(d)
        schema = d.pop("schema", cls.schema)
        if schema != cls.schema:
            raise MissionError(f"unknown schema {schema!r}")
        sealed = bool(d.pop("sealed", False))
        count = d.pop("observation_count", 0)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise MissionError("observation_count must be a non-negative integer")
        obj = cls(
            observation_set_id=d.pop("observation_set_id", None),
            session_id=d.pop("session_id", None),
            source_filename=d.pop("source_filename", None),
            source_sha256=d.pop("source_sha256", None),
            acquisition_tool_version=d.pop("acquisition_tool_version", None),
            preprocessing_history=d.pop("preprocessing_history", []),
        )
        _check_unknown(d, set(), "observation_set")
        obj._observation_count = count
        if sealed:
            obj._sealed = True
        return obj

    @classmethod
    def from_json(cls, s: str) -> "ObservationSet":
        return cls.from_dict(json.loads(s))

    def digest(self) -> str:
        """observation_set_sha256: changes as observation_count grows before sealing (this is
        the one object in this module whose digest is NOT claimed stable for its whole
        lifetime -- only Mission and Session make that claim, per their frozen identity)."""
        return _sha256_of(self.to_dict())
