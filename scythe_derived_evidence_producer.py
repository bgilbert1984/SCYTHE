"""Writing derived evidence, kept as far from a capture as the design allows.

§17 slice 10c, implementing PROMOTION_EXECUTION_CONTRACT.md §13k Amendment L.

**Disabled by default**, and that is the rule carrying the others. Everything
else here is a property of code that has to be reachable to matter, and a
producer that does nothing until someone turns it on cannot be reached by an
accident, a default, or a test that forgot where it was running.

This module produces bytes and publishes them. It does not acquire, does not
open a socket, does not start `rtl_tcp`, does not call the adapter, does not
schedule anything, and touches no process.

Two boundaries do the real work:

  **A runtime nominal-type gate, before any filesystem operation** (§13k L.1a).
  An annotation refuses nothing at a Python call boundary -- a bytes-like
  object, a mock or a structurally compatible impostor passes an annotated
  signature unremarked -- so the annotations here serve static analysis and the
  check below is what actually refuses. Writing the annotation and believing it
  is the whole guard is the failure L.1a exists to name.

  **The reader stays authoritative.** Every structural exclusion applied here is
  applied again by `scythe_derived_evidence`, and admission depends on the
  reader's answer. A check the reader did not repeat would be a guarantee held
  by the party with the most reason to be wrong about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from scythe_invariant_ledger import Coordinate
from scythe_derived_evidence import (
    ARTEFACT_PROVENANCE, ARTEFACT_SCHEMA, FRAME_VERSION, INSTRUMENT_SETTINGS,
    INSTRUMENT_SETTING_FIELDS, MAX_ARTEFACT_BYTES, MAX_ARTEFACT_RECORDS,
    MAX_RECORD_BYTES, MINIMUM_RECORD_INTERVAL_NS, WALK_COORDINATES,
    WALK_STEP_PAIR, artifact_identity, refuse_instrument_declaration,
    refuse_scalar,
)
from scythe_promotion_ledger_store import frame_of
from scythe_promotion_lineage import Syscalls

SCHEMA = "scythe.derived-evidence-producer.v1"

# -- reachability (§13k L.5) ----------------------------------------------
PRODUCER_NOT_ENABLED = "PRODUCER_NOT_ENABLED"
PRODUCER_INPUT_REFUSED = "PRODUCER_INPUT_REFUSED"
ARTEFACT_PUBLICATION_REFUSED = "ARTEFACT_PUBLICATION_REFUSED"
REQUEST_DIGEST_CONFLICT = "REQUEST_DIGEST_CONFLICT"
PRODUCER_BOUNDS_EXCEEDED = "PRODUCER_BOUNDS_EXCEEDED"
PRODUCER_REFUSALS: Tuple[str, ...] = (
    PRODUCER_NOT_ENABLED, PRODUCER_INPUT_REFUSED,
    ARTEFACT_PUBLICATION_REFUSED, REQUEST_DIGEST_CONFLICT,
    PRODUCER_BOUNDS_EXCEEDED,
)

MAX_PRODUCER_RECORDS = 1_000
MAX_PRODUCER_DURATION_S = 900.0

# Where a provenance claim came from. A claim whose source is unnamed is a claim
# nobody can later question (§13k L.2).
UPSTREAM_CLAIM = "UPSTREAM_CLAIM"
CLAIM_SOURCE = "CLAIM_SOURCE"

# Coordinates a legitimate walk signature carries that the **checker** computes:
# `with_step_measures` fills them from the pair. They are accepted as input and
# deliberately **never recorded** -- storing them would duplicate checker
# mathematics, which §13k L.1 forbids, and the reader recomputes them from the
# inputs exactly as the live path does.
STEP_MEASURE_COORDINATES: Tuple[str, ...] = (
    "displacement_m", "kinematic_budget_m", "pose_uncertainty_before_m",
    "pose_uncertainty_after_m",
)

PROVENANCE_CLAIMS: Tuple[str, ...] = (
    "device_id", "signal_chain_hash", "configuration_epoch",
    "monotonic_source_id",
)


class ProducerRefused(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- the runtime nominal gate (§13k L.1a, L.1b) ---------------------------

def require_signature(name: str, value: Any) -> Mapping[str, Coordinate]:
    """The check that actually refuses. Nominal, never `isinstance`.

    A subclass may override `keys`, `__getitem__` or `__iter__` to return
    anything **at the moment the serializer asks**, so what is protected is what
    the object *is* rather than what it can do. `dict` exactly, and `Coordinate`
    exactly, all the way down.

    Duck typing is what must not be honoured here. A mapping-like object with
    the right methods is precisely the impostor this gate exists for.
    """
    if type(value) is not dict:
        raise ProducerRefused(
            PRODUCER_INPUT_REFUSED,
            f"{name}: {type(value).__name__} is not a signature; the check is "
            f"nominal, so a subclass, proxy or mapping-like substitute is "
            f"refused whatever it can do")
    if not value:
        raise ProducerRefused(PRODUCER_INPUT_REFUSED, f"{name}: empty signature")
    for key, coordinate in value.items():
        if type(key) is not str:
            raise ProducerRefused(PRODUCER_INPUT_REFUSED,
                                  f"{name}: a coordinate name must be a string")
        if key not in WALK_COORDINATES and key not in STEP_MEASURE_COORDINATES:
            raise ProducerRefused(
                PRODUCER_INPUT_REFUSED,
                f"{name}: {key!r} is not a declared walk coordinate")
        if type(coordinate) is not Coordinate:
            raise ProducerRefused(
                PRODUCER_INPUT_REFUSED,
                f"{name}.{key}: {type(coordinate).__name__} is not a Coordinate")
    return value


def _scalar_of(name: str, coordinate: Coordinate) -> Any:
    """The value a coordinate carries, admitted by the reader's own rules.

    `refuse_scalar` is imported rather than reimplemented: the producer's checks
    are defence in depth and must be the *same* checks, or the depth is two
    different answers waiting to disagree.
    """
    from scythe_derived_evidence import ArtefactRefused

    value = coordinate.value
    try:
        refuse_scalar(name, value)
    except ArtefactRefused as refused:
        # The same check, re-raised as the producer's type so a caller has one
        # exception surface. The *code* is carried through unchanged -- wrapping
        # the check would be a second implementation, and translating the code
        # would be a second vocabulary.
        raise ProducerRefused(PRODUCER_INPUT_REFUSED,
                              f"{refused.code}: {refused.detail}") from None
    return value


# -- the producer ----------------------------------------------------------

@dataclass
class DerivedEvidenceProducer:
    """Records checker inputs. Acquires nothing, and is off unless enabled.

    There is no `write_record(payload)` here and there will not be one: a
    generic mapping parameter is a hole shaped like anything, and the named
    family entrypoints below are the only way in.
    """

    directory: str
    run_id: str
    device_id: str
    signal_chain_hash: str
    configuration_epoch: int
    monotonic_source_id: str
    claim_sources: Mapping[str, str]
    configuration_identity: str
    measurement_status: str
    instrument_state: str
    instrument_settings: Mapping[str, Any] = field(default_factory=dict)
    enabled: bool = False
    syscalls: Syscalls = field(default_factory=Syscalls)

    def __post_init__(self) -> None:
        self._records: List[Dict[str, Any]] = []
        self._last: Dict[str, int] = {}

    # -- family-specific entrypoints (§13k L.1) ---------------------------

    def record_walk_step(self, before: Mapping[str, Coordinate],
                         after: Mapping[str, Coordinate]) -> None:
        """One walk step, exactly as `check_walk_step` would receive it.

        The annotations above are for a reader and a static checker. The refusal
        is `require_signature`, and it runs before anything is created.
        """
        self._require_enabled()
        require_signature("before", before)
        require_signature("after", after)
        if len(self._records) >= MAX_PRODUCER_RECORDS:
            raise ProducerRefused(
                PRODUCER_BOUNDS_EXCEEDED,
                f"{MAX_PRODUCER_RECORDS} records is the bound")

        record = {"kind": WALK_STEP_PAIR,
                  "before": self._coordinates("before", before),
                  "after": self._coordinates("after", after)}
        self._require_cadence(record)
        framed = frame_of(record)
        if len(framed) > MAX_RECORD_BYTES:
            raise ProducerRefused(PRODUCER_BOUNDS_EXCEEDED,
                                  f"record exceeds {MAX_RECORD_BYTES} bytes")
        self._records.append(record)

    def _coordinates(self, side: str, signature: Mapping[str, Coordinate]
                     ) -> Dict[str, Any]:
        return {name: _scalar_of(f"{side}.{name}", coordinate)
                for name, coordinate in signature.items()
                if name in WALK_COORDINATES and coordinate.value is not None}

    def _require_cadence(self, record: Mapping[str, Any]) -> None:
        """§13j K.2, applied here as well as by the reader.

        Per series, integers only. Defence in depth: the reader repeats it and
        its answer is the one admission depends on.
        """
        series = record["kind"]
        observed = record["after"].get("observed_monotonic_ns")
        if type(observed) is not int:
            raise ProducerRefused(
                PRODUCER_INPUT_REFUSED,
                "observed_monotonic_ns must be an integer")
        previous = self._last.get(series)
        if previous is not None:
            if observed <= previous or \
                    observed - previous < MINIMUM_RECORD_INTERVAL_NS:
                raise ProducerRefused(
                    PRODUCER_INPUT_REFUSED,
                    f"records must advance by at least "
                    f"{MINIMUM_RECORD_INTERVAL_NS} ns within a series")
        self._last[series] = observed

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise ProducerRefused(
                PRODUCER_NOT_ENABLED,
                "the producer is disabled by default and is turned on "
                "explicitly, never by a configuration default")

    # -- publication (§13k L.3, by §13e F.6's five steps) ------------------

    def artefact_path(self) -> str:
        """Deterministic and request-addressed. No *current artefact* pointer.

        A mutable pointer would be a second answer to which artefact is which,
        and the run identity already answers it.
        """
        stamp = hashlib.blake2s(self.run_id.encode("utf-8"),
                                digest_size=8).hexdigest()
        return os.path.join(self.directory, f"derived.{stamp}.jsonl")

    def publish(self) -> Dict[str, Any]:
        """Publish once, or rediscover what this run already published.

        A repeated run identity finds the existing artefact; a differing digest
        under one identity is REQUEST_DIGEST_CONFLICT and refuses, because two
        contents under one name is the thing a request-addressed artefact exists
        to make impossible.
        """
        self._require_enabled()
        if not self._records:
            raise ProducerRefused(ARTEFACT_PUBLICATION_REFUSED,
                                  "an artefact with no records says nothing")
        body = b"".join(frame_of(record) for record in self._records)
        content_digest = "sha256:" + hashlib.sha256(body).hexdigest()
        provenance = self._provenance(content_digest, len(self._records))
        data = frame_of(provenance) + body
        if len(data) > MAX_ARTEFACT_BYTES:
            raise ProducerRefused(PRODUCER_BOUNDS_EXCEEDED,
                                  f"artefact exceeds {MAX_ARTEFACT_BYTES} bytes")

        final = self.artefact_path()
        if os.path.exists(final):
            return self._rediscover(final, provenance["artifact_id"])

        temporary = f"{final}.{provenance['artifact_id'][-16:]}.partial"
        fd = self.syscalls.open_exclusive(temporary)
        try:
            self.syscalls.write(fd, data)
            self.syscalls.fsync(fd)
        finally:
            self.syscalls.close(fd)
        self.syscalls.rename(temporary, final)
        self.syscalls.fsync_directory(self.directory)
        return {"schema": SCHEMA, "path": final, "rediscovered": False,
                "artifact_id": provenance["artifact_id"],
                "content_digest": content_digest,
                "records": len(self._records)}

    def _rediscover(self, final: str, artifact_id: str) -> Dict[str, Any]:
        with open(final, "rb") as handle:
            first = handle.readline()
        existing = json.loads(first[18:].decode("utf-8"))
        if existing.get("artifact_id") != artifact_id:
            raise ProducerRefused(
                REQUEST_DIGEST_CONFLICT,
                "this run identity already addresses an artefact with different "
                "content; a second one is not this process's to write")
        return {"schema": SCHEMA, "path": final, "rediscovered": True,
                "artifact_id": artifact_id,
                "content_digest": existing.get("content_digest"),
                "records": existing.get("record_count")}

    def _provenance(self, content_digest: str, count: int) -> Dict[str, Any]:
        """Upstream attestations, each naming the component that supplied it.

        The producer verified none of these. It cannot: a device identity and a
        signal-chain hash are facts about an instrument, and this module has
        never touched one. What it can do is say who told it (§13k L.2).
        """
        missing = [claim for claim in PROVENANCE_CLAIMS
                   if not self.claim_sources.get(claim)]
        if missing:
            raise ProducerRefused(
                ARTEFACT_PUBLICATION_REFUSED,
                f"every provenance claim names its source; {sorted(missing)} "
                f"name none, and an unsourced claim is one nobody can question")
        declared = dict(self.instrument_settings)
        unknown = sorted(set(declared) - INSTRUMENT_SETTING_FIELDS)
        if unknown:
            raise ProducerRefused(
                ARTEFACT_PUBLICATION_REFUSED,
                f"{unknown} are not declared instrument settings; the settings "
                f"are {sorted(INSTRUMENT_SETTINGS)}, each beside its own label")
        provenance = {
            "kind": ARTEFACT_PROVENANCE,
            "schema": ARTEFACT_SCHEMA,
            "frame_version": FRAME_VERSION,
            "device_id": self.device_id,
            "signal_chain_hash": self.signal_chain_hash,
            "configuration_epoch": self.configuration_epoch,
            "monotonic_source_id": self.monotonic_source_id,
            "producer_attestation": self._attestation(),
            "content_digest": content_digest,
            "record_count": count,
            "measurement_status": self.measurement_status,
            "instrument_state": self.instrument_state,
        }
        provenance.update(declared)
        # The reader's own function, imported rather than reimplemented. The
        # producer states what the instrument did; it never derives it, and it
        # never labels a setting the caller left unlabelled (§13l M.4).
        from scythe_derived_evidence import ArtefactRefused
        try:
            refuse_instrument_declaration(provenance)
        except ArtefactRefused as refused:
            raise ProducerRefused(
                ARTEFACT_PUBLICATION_REFUSED,
                f"{refused.code}: {refused.detail}") from None
        provenance["artifact_id"] = artifact_identity(provenance, content_digest)
        return provenance

    def _attestation(self) -> str:
        """What the producer itself attests: that every coordinate came from the
        declared checker-input pipeline, and which component supplied each
        upstream claim."""
        sources = {claim: self.claim_sources[claim] for claim in PROVENANCE_CLAIMS}
        material = json.dumps({"producer": SCHEMA,
                               "configuration_identity": self.configuration_identity,
                               "run_id": self.run_id,
                               "claim_sources": sources},
                              sort_keys=True, separators=(",", ":"))
        return material[:240]

    def status(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "enabled": self.enabled,
            "records": len(self._records),
            "run_id": self.run_id,
            "bounds": {"records": MAX_PRODUCER_RECORDS,
                       "duration_s": MAX_PRODUCER_DURATION_S},
            "refusals": list(PRODUCER_REFUSALS),
            "measurement_status": self.measurement_status,
            "instrument_state": self.instrument_state,
            "writes_carries_samples": False,
            "acquires": False,
            "schedules": False,
            "reader_is_authoritative": True,
            "note": ("PRODUCER CHECKS ARE DEFENCE IN DEPTH. ADMISSION DEPENDS "
                     "ON THE READER, WHICH REPEATS EVERY ONE OF THEM"),
        }
