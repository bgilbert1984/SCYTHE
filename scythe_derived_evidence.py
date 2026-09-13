"""Reading derived evidence, and refusing what cannot be read safely.

§17 slice 10b, implementing PROMOTION_EXECUTION_CONTRACT.md §13i Amendment J and
§13j Amendment K.

**A closed coordinate schema proves conformance to a representation. It does not
prove semantic origin** (§13i). What this reader establishes is that an artefact
passed the accepted structural exclusion profile -- five refusals, each closing
one way samples arrive -- and nothing about whether a dishonest producer encoded
them anyway. Origin is attested by a producer that does not exist yet, and the
reader validates an attestation rather than reconstructing provenance from
values.

There is no writer here. Slice 10 gains the ability to consume an artefact and
nothing gains the ability to produce one, so §13h I.3's *no acquisition* is
untouched rather than carefully preserved.

Four things shape the read:

  **One descriptor, opened once.** A path is a name and can be repointed between
  opens, so a digest taken from a second open attests bytes the verdicts did not
  come from. Attest the descriptor, not the path -- §13d E.2's lesson one layer
  down.

  **The digest is not recursive.** A digest cannot cover the header that carries
  it, so the content digest covers the framed evidence records and the artefact
  identity is derived from schema, provenance and that digest.

  **Refusal precedes both checkers.** A verdict derived from evidence whose
  sample status is unknown would carry that uncertainty into a promotion
  identity, where nothing downstream could recover it.

  **The bounds are the contract's**, not a caller's, because an unbounded
  artefact is an unbounded run wearing a file.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import stat
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from rf_walk_survey_metadata import find_sample_bearing_field
from scythe_promotion_ledger_store import frame_of
from scythe_promotion_policy import CapsuleIdentity

SCHEMA = "scythe.derived-evidence.v1"
ARTEFACT_SCHEMA = "scythe.derived-evidence-artefact.v2"
#                                                       ^^
# Bumped by §13l M.4, which made `measurement_status` and `instrument_state`
# required. Adding a required field to a closed set changes what the old name
# means, so the name changes with it: a v1 artefact is refused as foreign rather
# than read with defaults. There is no compatibility path and none is needed --
# no v1 artefact has ever been produced, and inventing a migration for a
# population of zero would be inventing the defaults the amendment forbids.
FRAME_VERSION = "de1"

# -- record kinds, disjoint from the ledger's -----------------------------
#
# Deliberately not HEADER: an artefact must never be readable as a generation,
# and the ledger's reader refuses a kind it does not know rather than skipping
# it. Two closed sets that share no member cannot be confused for one another.
ARTEFACT_PROVENANCE = "ARTEFACT_PROVENANCE"
WALK_STEP_PAIR = "WALK_STEP_PAIR"
ARTEFACT_KINDS: Tuple[str, ...] = (ARTEFACT_PROVENANCE, WALK_STEP_PAIR)

# -- the three-state sample assessment (§13i J.3) -------------------------
SAMPLE_BEARING_DETECTED = "SAMPLE_BEARING_DETECTED"
DERIVED_SCHEMA_CONFORMANT = "DERIVED_SCHEMA_CONFORMANT"
SAMPLE_STATUS_UNVERIFIABLE = "SAMPLE_STATUS_UNVERIFIABLE"
SAMPLE_ASSESSMENTS: Tuple[str, ...] = (SAMPLE_BEARING_DETECTED,
                                       DERIVED_SCHEMA_CONFORMANT,
                                       SAMPLE_STATUS_UNVERIFIABLE)

# -- refusals --------------------------------------------------------------
ARTEFACT_UNREADABLE = "ARTEFACT_UNREADABLE"
ARTEFACT_TOO_LARGE = "ARTEFACT_TOO_LARGE"
COORDINATE_NOT_IN_SCHEMA = "COORDINATE_NOT_IN_SCHEMA"
NON_SCALAR_COORDINATE = "NON_SCALAR_COORDINATE"
OVERSIZED_COORDINATE = "OVERSIZED_COORDINATE"
RECORD_INTERVAL_REFUSED = "RECORD_INTERVAL_REFUSED"
CONTENT_DIGEST_MISMATCH = "CONTENT_DIGEST_MISMATCH"
ARTEFACT_REFUSALS: Tuple[str, ...] = (
    ARTEFACT_UNREADABLE, ARTEFACT_TOO_LARGE, COORDINATE_NOT_IN_SCHEMA,
    NON_SCALAR_COORDINATE, OVERSIZED_COORDINATE, RECORD_INTERVAL_REFUSED,
    CONTENT_DIGEST_MISMATCH, SAMPLE_BEARING_DETECTED,
    SAMPLE_STATUS_UNVERIFIABLE,
)

# -- contract-declared bounds (§13i J.8, §13j K.2) ------------------------
MAX_ARTEFACT_RECORDS = 1_000
MAX_ARTEFACT_BYTES = 4_194_304
MAX_RECORD_BYTES = 16_384
MAX_STRING_BYTES = 256
MAX_NUMERIC_MAGNITUDE = 1e12
MAX_NUMERIC_DECIMALS = 9
MINIMUM_RECORD_INTERVAL_NS = 1_000_000

# -- the closed schema, per series ----------------------------------------
#
# Closed like the ledger's record kinds. A name not here is refused rather than
# ignored, because a coordinate this reader does not understand is a coordinate
# whose contents it cannot bound.
WALK_COORDINATES: Tuple[str, ...] = (
    "device_id", "receiver_state_chain_hash", "monotonic_source_id",
    "signal_chain_hash", "configuration_epoch", "latitude", "longitude",
    "observed_monotonic_ns", "pose_uncertainty_m", "surface_rows_contributed",
)
SERIES_SCHEMA: Dict[str, Tuple[str, ...]] = {WALK_STEP_PAIR: WALK_COORDINATES}

# -- what the instrument did, declared and never inferred (§13l M.4) ------
#
# Both sets have exactly one member, and that is the honest size. This tree can
# produce one kind of artefact -- a walk over attested positions with the
# receiver idle -- and a second member would be a name for a capability that
# does not exist, readable by a reader that has never seen one. When a
# measurement path is contracted, its amendment adds its value.
RF_MEASUREMENT_NOT_PERFORMED = "RF_MEASUREMENT_NOT_PERFORMED"
MEASUREMENT_STATUSES: Tuple[str, ...] = (RF_MEASUREMENT_NOT_PERFORMED,)

INSTRUMENT_CONFIGURED_IDLE = "INSTRUMENT_CONFIGURED_IDLE"
INSTRUMENT_STATES: Tuple[str, ...] = (INSTRUMENT_CONFIGURED_IDLE,)

# The label every populated instrument setting must carry. Named
# CONFIGURED_NOT_EXERCISED rather than DECLARED_NOT_EXERCISED, which is a
# negation pair with UNDECLARED and with LEDGER_GENERATION_UNDECLARED (M.9).
CONFIGURED_NOT_EXERCISED = "CONFIGURED_NOT_EXERCISED"
EXERCISE_LABELS: Tuple[str, ...] = (CONFIGURED_NOT_EXERCISED,)

# Settings the manifest may declare. Each is optional and each is carried flat,
# beside its own label, rather than nested: a setting and the claim about
# whether it was exercised are two facts, and burying the second inside a
# structure is how it becomes something nobody reads.
INSTRUMENT_SETTINGS: Tuple[str, ...] = ("sample_rate_hz", "gain_db")
EXERCISE_SUFFIX = "_exercise"
INSTRUMENT_SETTING_FIELDS = frozenset(
    INSTRUMENT_SETTINGS + tuple(name + EXERCISE_SUFFIX
                                for name in INSTRUMENT_SETTINGS))

PROVENANCE_FIELDS = frozenset((
    "kind", "schema", "frame_version", "artifact_id", "content_digest",
    "record_count", "device_id", "signal_chain_hash", "configuration_epoch",
    "monotonic_source_id", "producer_attestation",
    "measurement_status", "instrument_state",
))


class ArtefactRefused(RuntimeError):
    """An artefact that is not read. Carries a code, never a repair."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- scalar admission (§13i J.8) -------------------------------------------

def refuse_scalar(name: str, value: Any) -> None:
    """Bounded, finite, and unambiguous, or refused.

    The five numeric shapes are each a way to carry more than a measurement.
    Excessive precision is the one this profile is least able to see: a float's
    mantissa is sixty-odd bits of anywhere the producer likes.
    """
    if isinstance(value, bool):
        # bool before int, always: True is not a configuration epoch.
        raise ArtefactRefused(NON_SCALAR_COORDINATE,
                              f"{name}: a boolean is not a measurement")
    if isinstance(value, (list, tuple, dict, set, bytes, bytearray)):
        raise ArtefactRefused(NON_SCALAR_COORDINATE,
                              f"{name}: a container is how samples arrive")
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) > MAX_STRING_BYTES:
            # base64 is a string, and a long one is an archive.
            raise ArtefactRefused(
                OVERSIZED_COORDINATE,
                f"{name}: {len(encoded)} bytes exceeds {MAX_STRING_BYTES}")
        if _looks_numeric(value):
            raise ArtefactRefused(
                NON_SCALAR_COORDINATE,
                f"{name}: a numeric string lets the string bound and the "
                f"numeric bound each assume the other applied")
        return
    if isinstance(value, int):
        if abs(value) > 2 ** 63:
            raise ArtefactRefused(OVERSIZED_COORDINATE, f"{name}: out of range")
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ArtefactRefused(
                NON_SCALAR_COORDINATE,
                f"{name}: a non-finite coordinate is an undeclared absence "
                f"wearing a lab coat")
        if abs(value) > MAX_NUMERIC_MAGNITUDE:
            raise ArtefactRefused(OVERSIZED_COORDINATE, f"{name}: out of range")
        if _decimals(value) > MAX_NUMERIC_DECIMALS:
            raise ArtefactRefused(
                OVERSIZED_COORDINATE,
                f"{name}: precision beyond {MAX_NUMERIC_DECIMALS} places is far "
                f"past any instrument and is where a covert archive fits")
        return
    raise ArtefactRefused(NON_SCALAR_COORDINATE, f"{name}: not a scalar")


def _looks_numeric(text: str) -> bool:
    try:
        float(text)
    except (TypeError, ValueError):
        return False
    return True


def _decimals(value: float) -> int:
    rendered = repr(float(value))
    if "e" in rendered or "E" in rendered:
        return MAX_NUMERIC_DECIMALS + 1
    _whole, _, fraction = rendered.partition(".")
    return len(fraction.rstrip("0"))


# -- the read (§13i J.7) ---------------------------------------------------

def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def artifact_identity(provenance: Mapping[str, Any], content_digest: str) -> str:
    """Derived from schema, canonical provenance and the content digest.

    Never the reverse: a digest cannot cover the header that carries it, so the
    identity depends on the content rather than the content depending on the
    identity (§13i J.7a).
    """
    covered = PROVENANCE_FIELDS | INSTRUMENT_SETTING_FIELDS
    canonical = {k: provenance[k] for k in sorted(covered)
                 if k in provenance and k not in ("artifact_id", "content_digest")}
    material = json.dumps([ARTEFACT_SCHEMA, canonical, content_digest],
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class Artefact:
    """What one artefact says, once it has been read and admitted."""

    path: str
    artifact_id: str
    content_digest: str
    provenance: Dict[str, Any]
    records: Tuple[Dict[str, Any], ...]
    assessment: str
    measurement_status: str
    instrument_state: str

    @property
    def conformant(self) -> bool:
        return self.assessment == DERIVED_SCHEMA_CONFORMANT

    def capsule(self) -> CapsuleIdentity:
        """§13i J.3. Only DERIVED_SCHEMA_CONFORMANT derives carries_samples.

        There is no parameter here, and that is the point: a caller cannot
        supply the most important flag in the promotion path. What it means is
        that the artefact passed the accepted structural exclusion profile, not
        that a dishonest producer is impossible.
        """
        if not self.conformant:
            raise ArtefactRefused(
                self.assessment,
                "a capsule is derived only from a schema-conformant artefact")
        return CapsuleIdentity(schema="scythe.invariant-capsule.v1",
                               digest=self.content_digest,
                               within_bounds=True, carries_samples=False)


def read_artefact(path: str) -> Artefact:
    """One descriptor, bounded, digest over the bytes consumed.

    The file is opened once and never reopened: everything below -- the digest,
    the record count, the bounds -- is computed from the bytes this descriptor
    produced, so repointing the path afterwards changes nothing that was
    attested.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ArtefactRefused(
                ARTEFACT_UNREADABLE,
                "not a regular file; a FIFO or a device is a stream and this "
                "reader's bounds are about a file")
        if info.st_size > MAX_ARTEFACT_BYTES:
            raise ArtefactRefused(
                ARTEFACT_TOO_LARGE,
                f"{info.st_size} bytes exceeds {MAX_ARTEFACT_BYTES}")
        data = b""
        while len(data) <= MAX_ARTEFACT_BYTES:
            block = os.read(fd, 65536)
            if not block:
                break
            data += block
        if len(data) > MAX_ARTEFACT_BYTES:
            raise ArtefactRefused(ARTEFACT_TOO_LARGE, "read exceeded the bound")
    finally:
        os.close(fd)
    return _admit(path, data)


def _admit(path: str, data: bytes) -> Artefact:
    lines = data.split(b"\n")
    if not lines or lines[-1] != b"":
        raise ArtefactRefused(
            ARTEFACT_UNREADABLE,
            "trailing bytes after the last record; content outside the "
            "digest's reach")
    framed = lines[:-1]
    if not framed:
        raise ArtefactRefused(ARTEFACT_UNREADABLE, "no records")

    payloads: List[Dict[str, Any]] = []
    for index, line in enumerate(framed):
        if len(line) + 1 > MAX_RECORD_BYTES:
            raise ArtefactRefused(
                ARTEFACT_TOO_LARGE,
                f"record {index}: {len(line) + 1} bytes exceeds {MAX_RECORD_BYTES}")
        payloads.append(_parse(line, index))

    provenance = payloads[0]
    if provenance.get("kind") != ARTEFACT_PROVENANCE:
        raise ArtefactRefused(ARTEFACT_UNREADABLE,
                              "the first record is not the provenance record")
    for index, payload in enumerate(payloads[1:], start=1):
        if payload.get("kind") == ARTEFACT_PROVENANCE:
            raise ArtefactRefused(
                ARTEFACT_UNREADABLE,
                f"record {index}: a second provenance record; two headers and "
                f"two identities in one file")

    records = tuple(payloads[1:])
    if len(records) > MAX_ARTEFACT_RECORDS:
        raise ArtefactRefused(
            ARTEFACT_TOO_LARGE,
            f"{len(records)} records exceeds {MAX_ARTEFACT_RECORDS}")
    _refuse_provenance(provenance, len(records))

    # The content digest covers the framed evidence records exactly, and the
    # provenance record is excluded because it carries the digest.
    body = b"".join(line + b"\n" for line in framed[1:])
    content_digest = _digest(body)
    if content_digest != provenance["content_digest"]:
        raise ArtefactRefused(
            CONTENT_DIGEST_MISMATCH,
            "the bytes consumed are not the bytes attested")
    if artifact_identity(provenance, content_digest) != provenance["artifact_id"]:
        raise ArtefactRefused(CONTENT_DIGEST_MISMATCH,
                              "the artefact identity does not follow from its "
                              "schema, provenance and content")

    assessment = _assess(provenance, records)
    return Artefact(
        path=path, artifact_id=provenance["artifact_id"],
        content_digest=content_digest, provenance=dict(provenance),
        records=records, assessment=assessment,
        measurement_status=declared_measurement_status(provenance),
        instrument_state=declared_instrument_state(provenance))


def _parse(line: bytes, index: int) -> Dict[str, Any]:
    try:
        payload = _parse_frame(line)
    except ArtefactRefused:
        raise
    except Exception as exc:
        raise ArtefactRefused(ARTEFACT_UNREADABLE,
                              f"record {index}: {type(exc).__name__}") from None
    if payload.get("kind") not in ARTEFACT_KINDS:
        raise ArtefactRefused(
            ARTEFACT_UNREADABLE,
            f"record {index}: kind {str(payload.get('kind'))[:32]!r} is not one "
            f"this reader reads")
    return payload


def _parse_frame(line: bytes) -> Dict[str, Any]:
    """The ledger's framing discipline, with this module's own kind set.

    Shared encoder, separate vocabulary: an artefact must never be readable as a
    generation, and the ledger's reader refuses a kind it does not know rather
    than skipping it.
    """
    import binascii

    if len(line) < 19 or line[8:9] != b" " or line[17:18] != b" ":
        raise ArtefactRefused(ARTEFACT_UNREADABLE, "frame header malformed")
    declared = int(line[:8], 16)
    expected = int(line[9:17], 16)
    body = line[18:]
    if len(body) != declared:
        raise ArtefactRefused(ARTEFACT_UNREADABLE, "frame length mismatch")
    if (binascii.crc32(body) & 0xFFFFFFFF) != expected:
        raise ArtefactRefused(ARTEFACT_UNREADABLE, "frame checksum mismatch")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ArtefactRefused(ARTEFACT_UNREADABLE, "frame payload is not an object")
    return payload


def refuse_instrument_declaration(provenance: Mapping[str, Any]) -> None:
    """§13l M.4. What the instrument did is declared in closed values.

    Exported because the producer calls this exact function rather than writing
    its own: a second implementation is two answers waiting to disagree.

    Four properties, four checks below, each breakable on its own.
    """
    _refuse_declared_values(provenance)
    for setting in INSTRUMENT_SETTINGS:
        _refuse_setting_pairing(provenance, setting)
        if setting in provenance:
            _refuse_setting_value(provenance, setting)
            _refuse_setting_label(provenance, setting)


# Four checks rather than one body, because they are four properties and each
# has to be breakable alone. A single block would make every negative control a
# mutation that breaks all four, which proves only that the block runs.

def _refuse_declared_values(provenance: Mapping[str, Any]) -> None:
    """The two closed sets. A value outside them is refused, never mapped."""
    status = provenance.get("measurement_status")
    if status not in MEASUREMENT_STATUSES:
        raise ArtefactRefused(
            ARTEFACT_UNREADABLE,
            f"measurement_status {str(status)[:32]!r} is not one this reader "
            f"declares; {sorted(MEASUREMENT_STATUSES)} are")
    state = provenance.get("instrument_state")
    if state not in INSTRUMENT_STATES:
        raise ArtefactRefused(
            ARTEFACT_UNREADABLE,
            f"instrument_state {str(state)[:32]!r} is not one this reader "
            f"declares; {sorted(INSTRUMENT_STATES)} are")


def _refuse_setting_pairing(provenance: Mapping[str, Any], setting: str) -> None:
    """A setting and its label travel together, or neither is declared.

    Refused rather than repaired: a populated sample rate with no label is the
    shape that lets a configuration imply it was exercised, and supplying the
    label here would be this reader deciding the claim for the producer.
    """
    label_field = setting + EXERCISE_SUFFIX
    has_value = setting in provenance
    has_label = label_field in provenance
    if has_value != has_label:
        present, absent = ((setting, label_field) if has_value
                           else (label_field, setting))
        raise ArtefactRefused(
            ARTEFACT_UNREADABLE,
            f"{present} is present and {absent} is not; a setting and the "
            f"claim about whether it was exercised are declared together "
            f"or not at all")


def _refuse_setting_value(provenance: Mapping[str, Any], setting: str) -> None:
    """A setting is bounded like every other scalar this reader admits."""
    refuse_scalar(f"provenance.{setting}", provenance[setting])


def _refuse_setting_label(provenance: Mapping[str, Any], setting: str) -> None:
    """A populated setting carries CONFIGURED_NOT_EXERCISED, or is refused."""
    label_field = setting + EXERCISE_SUFFIX
    if provenance[label_field] not in EXERCISE_LABELS:
        raise ArtefactRefused(
            ARTEFACT_UNREADABLE,
            f"{setting} is populated and {label_field} is "
            f"{str(provenance[label_field])[:32]!r}; a populated setting "
            f"carries {CONFIGURED_NOT_EXERCISED} or is refused")


def declared_measurement_status(provenance: Mapping[str, Any]) -> str:
    """What the artefact says, never what the reader would guess.

    This reads one field and looks at nothing else. Not `device_id`: an RTL2838
    in the manifest is an instrument that was named, and naming one is not using
    one. Not the settings: a populated sample rate and a populated gain are a
    configuration, and §13l M.4 is precisely the rule that a declared
    configuration must not imply it was exercised.

    Both of those are plausible inferences, which is what makes them worth
    refusing here rather than trusting nobody will write them.
    """
    return provenance["measurement_status"]


def declared_instrument_state(provenance: Mapping[str, Any]) -> str:
    """What the artefact says about the instrument. Also never derived."""
    return provenance["instrument_state"]


def _refuse_provenance(provenance: Mapping[str, Any], counted: int) -> None:
    # Version before shape. An artefact from another schema is told that, not
    # told which fields it is missing -- the second reads as an invitation to
    # add them, which is the migration §13l M.4 does not have.
    if provenance.get("schema") != ARTEFACT_SCHEMA:
        raise ArtefactRefused(ARTEFACT_UNREADABLE, "foreign artefact schema")
    if provenance.get("frame_version") != FRAME_VERSION:
        raise ArtefactRefused(ARTEFACT_UNREADABLE, "unsupported frame version")
    extra = set(provenance) - (PROVENANCE_FIELDS | INSTRUMENT_SETTING_FIELDS)
    missing = PROVENANCE_FIELDS - set(provenance)
    if extra or missing:
        raise ArtefactRefused(
            ARTEFACT_UNREADABLE,
            f"provenance field set is not the declared one; missing "
            f"{sorted(missing)}, unexpected {sorted(extra)}")
    refuse_instrument_declaration(provenance)
    declared = provenance["record_count"]
    if isinstance(declared, bool) or not isinstance(declared, int) \
            or declared != counted:
        raise ArtefactRefused(
            ARTEFACT_UNREADABLE,
            f"record count {declared!r} does not match the {counted} present; "
            f"records were added or removed beneath the digest")


def _assess(provenance: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
            ) -> str:
    """The three-state assessment (§13i J.3), before any checker runs.

    UNVERIFIABLE is returned rather than raised where the profile could not be
    applied in full -- an artefact whose series this reader has no schema for is
    not refused as malformed, because it may be perfectly well formed and simply
    outside what this reader can bound.
    """
    if not provenance.get("producer_attestation"):
        return SAMPLE_STATUS_UNVERIFIABLE

    for index, record in enumerate(records):
        found = find_sample_bearing_field(record)
        if found is not None:
            return SAMPLE_BEARING_DETECTED
        schema = SERIES_SCHEMA.get(record.get("kind"))
        if schema is None:
            return SAMPLE_STATUS_UNVERIFIABLE
        for side in ("before", "after"):
            coordinates = record.get(side)
            if not isinstance(coordinates, dict):
                raise ArtefactRefused(ARTEFACT_UNREADABLE,
                                      f"record {index}: {side} is not a mapping")
            for name, value in coordinates.items():
                if name not in schema:
                    raise ArtefactRefused(
                        COORDINATE_NOT_IN_SCHEMA,
                        f"record {index}: {name!r} is not a declared coordinate")
                refuse_scalar(f"record {index}.{side}.{name}", value)

    _refuse_cadence(records)
    return DERIVED_SCHEMA_CONFORMANT


def _refuse_cadence(records: Sequence[Mapping[str, Any]]) -> None:
    """§13j K.2, per series and never globally.

    Two families may legitimately observe one monotonic instant -- a walk step
    and a decomposition are two observations of a moment, not a stream -- so a
    global floor would refuse an honest artefact for containing both.

    Integers only. A float conversion of a nanosecond count loses the low bits
    at exactly the magnitudes that matter, and a comparison that rounded two
    records into agreement would refuse nothing while appearing to check.
    """
    last: Dict[str, int] = {}
    for index, record in enumerate(records):
        series = record.get("kind")
        after = record.get("after")
        observed = after.get("observed_monotonic_ns") if isinstance(after, dict) else None
        if isinstance(observed, bool) or not isinstance(observed, int):
            raise ArtefactRefused(
                RECORD_INTERVAL_REFUSED,
                f"record {index}: observed_monotonic_ns must be an integer")
        previous = last.get(series)
        if previous is not None:
            if observed <= previous:
                raise ArtefactRefused(
                    RECORD_INTERVAL_REFUSED,
                    f"record {index}: timestamps must strictly increase within "
                    f"a series")
            if observed - previous < MINIMUM_RECORD_INTERVAL_NS:
                raise ArtefactRefused(
                    RECORD_INTERVAL_REFUSED,
                    f"record {index}: {observed - previous} ns is below the "
                    f"{MINIMUM_RECORD_INTERVAL_NS} ns floor; this is a "
                    f"high-rate record stream under the declared timeline")
        last[series] = observed


# -- the observation source ------------------------------------------------

def derived_walk_verdicts(path: str) -> Iterator[Tuple[Any, Any, CapsuleIdentity]]:
    """Real verdicts from a derived artefact (§13h I.3, §13i J.4).

    Refusal precedes both checkers: a non-conformant assessment yields **no
    verdicts at all**, because a verdict derived from evidence whose sample
    status is unknown would carry that uncertainty into a promotion identity
    where nothing downstream could recover it.

    There is no fallback to constructed evidence. §13h's
    DERIVED_EVIDENCE_UNAVAILABLE exists so absence is reported rather than
    filled.
    """
    from rf_walk_transitions import check_walk_step, walk_signature, with_step_measures
    from scythe_shadow_observation import ObservationSubject

    artefact = read_artefact(path)
    if not artefact.conformant:
        raise ArtefactRefused(
            artefact.assessment,
            "no verdict is derived from an artefact whose sample status is not "
            "schema-conformant")
    capsule = artefact.capsule()
    subject = ObservationSubject(requested_by="OPERATOR",
                                 target_graph="scythe.graphops.evidence",
                                 justification_source="OPERATOR")
    for record in artefact.records:
        if record.get("kind") != WALK_STEP_PAIR:
            continue
        before = walk_signature(pose_uncertainty_m=record["before"].get(
            "pose_uncertainty_m"), **_signature_args(record["before"]))
        after = walk_signature(pose_uncertainty_m=record["after"].get(
            "pose_uncertainty_m"), **_signature_args(record["after"]))
        before, after = with_step_measures(before, after,
                                           speed_before_mps=None,
                                           speed_after_mps=None)
        yield check_walk_step(before, after), subject, capsule


def _signature_args(coordinates: Mapping[str, Any]) -> Dict[str, Any]:
    return {name: coordinates[name] for name in WALK_COORDINATES
            if name in coordinates and name != "pose_uncertainty_m"}


def encode_for_test(provenance: Dict[str, Any],
                    records: Sequence[Mapping[str, Any]]) -> bytes:
    """Frame an artefact. **Not a writer**: it returns bytes and opens nothing.

    A writer is a separately authorized act (§13i J.4). This exists so a test
    can construct the bytes a producer would emit without this module gaining
    the ability to put them anywhere.
    """
    body = b"".join(frame_of(dict(record)) for record in records)
    complete = dict(provenance)
    complete["content_digest"] = _digest(body)
    complete["record_count"] = len(records)
    complete["artifact_id"] = artifact_identity(complete,
                                                complete["content_digest"])
    return frame_of(complete) + body
