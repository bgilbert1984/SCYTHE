"""The eligible-spur set, persisted because a seed cannot regenerate a measurement.

§5.27, accepted 2026-09-19. `SpurAllocation.to_dict()` represents two large
tuples by digest and count. `selected_trials` is **derived** -- reproducible
from the eligible set, the seed and the revision, and already refused by
`SpurAllocation.__post_init__` when it disagrees. `eligible_trials` is **not**:
each row carries an observed signed baseband offset at which a catalogued spur
was established as producible, and no seed regenerates an observation.

So the rows are persisted, in one separately framed artefact, bound by the
`eligible_trials_digest` the compact capture plan **already** carries. No second
digest is introduced: two stored digests for one tuple could disagree and would
need an authority rule of their own.

**Streaming, in both directions.** Writing materialises one record at a time and
digests incrementally; reading consumes one record at a time and never holds a
second encoded copy of the set. On the standard fixture the set is 5 700 rows
and about 1.5 MiB, which is exactly why it is not in the manifest.

This module resolves no path, opens nothing and creates nothing.
"""

from __future__ import annotations

import hashlib
import json
import struct
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Tuple

from rf_capture_format import IQC_MAGIC

# -- the framing ------------------------------------------------------------

IQE_MAGIC = b"\x89SCYET\r\n"                       # exactly 8 bytes
IQE_FORMAT_VERSION = 1                              # uint16, little-endian
IQE_SCHEMA = "scythe.iq-eligible-spur-trials.v1"
# A **per-record** bound, not one inferred from a fixture's total size: the
# largest record in the standard fixture is 268 bytes. A uint32 length can
# claim four gibibytes, and a corrupt or hostile one must become a refusal
# rather than an allocation.
IQE_MAX_RECORD_BYTES = 1_048_576

# magic (8) + format_version (uint16 LE) + record_count (uint64 LE)
IQE_FRAMING_PREFIX_BYTES = len(IQE_MAGIC) + 2 + 8
IQE_RECORD_LENGTH_BYTES = 4

# The total order the nominal object uses. Recovery requires it exactly, so one
# declared set has one byte representation.
ELIGIBLE_SORT_KEYS: Tuple[str, ...] = ("spur_id", "tuning_id", "epoch_id",
                                       "chain_hash")

if len(IQE_MAGIC) != 8:                                  # pragma: no cover
    raise ImportError(f"IQE_MAGIC is {len(IQE_MAGIC)} bytes, not 8")
if IQE_MAGIC == IQC_MAGIC:                               # pragma: no cover
    raise ImportError(
        "IQE_MAGIC and IQC_MAGIC are equal; a sidecar would be readable as an "
        "IQ window and a window as a sidecar")
# The IQE/IQM pair is checked in `rf_corpus_manifest`, which imports this
# module for its format declarations. Checking it here too would need an
# import back the other way, and a cycle between two format modules is a worse
# defect than one distinctness check living beside the other two.

# -- refusals ---------------------------------------------------------------

ELIGIBLE_ARTEFACT_NOT_FOUND = "ELIGIBLE_ARTEFACT_NOT_FOUND"
ELIGIBLE_ARTEFACT_UNEXPECTED = "ELIGIBLE_ARTEFACT_UNEXPECTED"
ELIGIBLE_FRAMING_REFUSED = "ELIGIBLE_FRAMING_REFUSED"
ELIGIBLE_RECORD_TOO_LARGE = "ELIGIBLE_RECORD_TOO_LARGE"
ELIGIBLE_COUNT_DISAGREES = "ELIGIBLE_COUNT_DISAGREES"
ELIGIBLE_NOT_CANONICAL = "ELIGIBLE_NOT_CANONICAL"
ELIGIBLE_TRAILING_BYTES = "ELIGIBLE_TRAILING_BYTES"
ELIGIBLE_ORDER_NOT_CANONICAL = "ELIGIBLE_ORDER_NOT_CANONICAL"
ELIGIBLE_KEY_REPEATED = "ELIGIBLE_KEY_REPEATED"
ELIGIBLE_DIGEST_DISAGREES = "ELIGIBLE_DIGEST_DISAGREES"
ELIGIBLE_REFUSALS: Tuple[str, ...] = (
    ELIGIBLE_ARTEFACT_NOT_FOUND, ELIGIBLE_ARTEFACT_UNEXPECTED,
    ELIGIBLE_FRAMING_REFUSED, ELIGIBLE_RECORD_TOO_LARGE,
    ELIGIBLE_COUNT_DISAGREES, ELIGIBLE_NOT_CANONICAL,
    ELIGIBLE_TRAILING_BYTES, ELIGIBLE_ORDER_NOT_CANONICAL,
    ELIGIBLE_KEY_REPEATED, ELIGIBLE_DIGEST_DISAGREES,
)


class EligibleSetRefused(RuntimeError):
    """A sidecar that is not the declared set. Raised, never returned."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- the canonical record ---------------------------------------------------


def canonical_record_bytes(row: Mapping[str, Any]) -> bytes:
    """One row's canonical JSON. **`ensure_ascii=True`, deliberately.**

    `manifest.iqm` and the `.iqc` header both use `ensure_ascii=False`; this
    does not, and that is not an inconsistency to tidy away.
    `rf_promotion_envelope._canonical_bytes` takes Python's default, so the
    **already-frozen** `eligible_trials_digest` is computed over escaped bytes.
    A sidecar written with `ensure_ascii=False` would not reproduce the digest
    the capture plan already carries, and the binding would fail for a reason
    that looked like corruption.

    Verified rather than reasoned: for the standard fixture, these per-record
    bytes joined by commas inside brackets are byte-identical to
    `_canonical_bytes(rows)`.
    """
    try:
        text = json.dumps(dict(row), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise EligibleSetRefused(
            ELIGIBLE_NOT_CANONICAL, f"a row is not canonically serialisable: {exc}")
    encoded = text.encode("utf-8")
    if len(encoded) > IQE_MAX_RECORD_BYTES:
        raise EligibleSetRefused(
            ELIGIBLE_RECORD_TOO_LARGE,
            f"a record is {len(encoded)} bytes and the format allows "
            f"{IQE_MAX_RECORD_BYTES}")
    return encoded


def sort_key(row: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(str(row[name]) for name in ELIGIBLE_SORT_KEYS)


def framing_prefix(count: int) -> bytes:
    return (IQE_MAGIC
            + struct.pack("<H", IQE_FORMAT_VERSION)
            + struct.pack("<Q", int(count)))


def frame_records(rows: Iterable[Mapping[str, Any]]) -> Iterator[bytes]:
    """Stream the complete artefact, one chunk at a time.

    A generator rather than a `bytes`: materialising 1.5 MiB to hand to a write
    call would be the second encoded copy this format exists to avoid.
    """
    materialised: List[bytes] = [canonical_record_bytes(row) for row in rows]
    yield framing_prefix(len(materialised))
    for encoded in materialised:
        yield struct.pack("<I", len(encoded))
        yield encoded


def digest_over_records(records: Iterable[bytes]) -> str:
    """`blake2s:` over `[` + the canonical record bytes joined by `,` + `]`.

    Computed incrementally, so the array this reconstructs never exists as one
    buffer. It is the **existing** `eligible_trials_digest`, recomputed -- not a
    second digest with a second authority.
    """
    hasher = hashlib.blake2s(digest_size=16)
    hasher.update(b"[")
    first = True
    for encoded in records:
        if not first:
            hasher.update(b",")
        hasher.update(encoded)
        first = False
    hasher.update(b"]")
    return f"blake2s:{hasher.hexdigest()}"


# -- the reader -------------------------------------------------------------


class _Stream:
    """Exact reads from a descriptor, refusing a short one."""

    __slots__ = ("_read", "_consumed")

    def __init__(self, read) -> None:
        self._read = read
        self._consumed = 0

    def exact(self, count: int, what: str) -> bytes:
        chunks, got = [], 0
        while got < count:
            block = self._read(count - got)
            if not block:
                raise EligibleSetRefused(
                    ELIGIBLE_FRAMING_REFUSED,
                    f"{what}: wanted {count} bytes and the artefact ended after "
                    f"{got}")
            chunks.append(block)
            got += len(block)
        self._consumed += got
        return b"".join(chunks)

    def at_end(self) -> bool:
        return self._read(1) == b""


def read_records(read, *, expected_count: int) -> Iterator[bytes]:
    """Stream the canonical record bytes, refusing anything that is not them.

    `read(n)` is any exact-ish reader -- `os.read` on a descriptor, or a
    `BytesIO`'s. The count is required to equal the compact plan's
    `distinct_trial_units` **before** rows are read, so a truncated or padded
    artefact is refused rather than partly believed.
    """
    stream = _Stream(read)
    prefix = stream.exact(IQE_FRAMING_PREFIX_BYTES, "the framing prefix")
    if prefix[:8] != IQE_MAGIC:
        raise EligibleSetRefused(
            ELIGIBLE_FRAMING_REFUSED,
            "the magic is not an eligible-trial artefact's"
            + (" -- this is an .iqc window" if prefix[:8] == IQC_MAGIC else ""))
    version = struct.unpack("<H", prefix[8:10])[0]
    if version != IQE_FORMAT_VERSION:
        raise EligibleSetRefused(
            ELIGIBLE_FRAMING_REFUSED,
            f"format version {version}, not {IQE_FORMAT_VERSION}")
    count = struct.unpack("<Q", prefix[10:18])[0]
    if count != int(expected_count):
        raise EligibleSetRefused(
            ELIGIBLE_COUNT_DISAGREES,
            f"the artefact frames {count} records and the capture plan declares "
            f"{expected_count} distinct trial units")
    for index in range(count):
        raw_length = stream.exact(IQE_RECORD_LENGTH_BYTES, f"record {index}")
        length = struct.unpack("<I", raw_length)[0]
        # Bounded BEFORE the read, not after it.
        if length > IQE_MAX_RECORD_BYTES:
            raise EligibleSetRefused(
                ELIGIBLE_RECORD_TOO_LARGE,
                f"record {index} claims {length} bytes and the format allows "
                f"{IQE_MAX_RECORD_BYTES}")
        yield stream.exact(length, f"record {index}")
    if not stream.at_end():
        raise EligibleSetRefused(
            ELIGIBLE_TRAILING_BYTES,
            "bytes follow the declared record count; a reader that ignores "
            "what it did not expect is a reader that can be appended to")


def parse_rows(records: Iterable[bytes]) -> List[Dict[str, Any]]:
    """Decode records, requiring canonical spelling, order and uniqueness."""
    rows: List[Dict[str, Any]] = []
    previous = None
    seen = set()
    for index, encoded in enumerate(records):
        try:
            row = json.loads(encoded.decode("utf-8"),
                             parse_constant=_refuse_constant)
        except (UnicodeDecodeError, ValueError) as exc:
            raise EligibleSetRefused(
                ELIGIBLE_NOT_CANONICAL, f"record {index} does not parse: {exc}")
        if type(row) is not dict:
            raise EligibleSetRefused(
                ELIGIBLE_NOT_CANONICAL,
                f"record {index} is a {type(row).__name__}, not an object")
        if canonical_record_bytes(row) != encoded:
            raise EligibleSetRefused(
                ELIGIBLE_NOT_CANONICAL,
                f"record {index} does not re-serialise to the bytes it was "
                "read from")
        key = sort_key(row)
        if previous is not None and key <= previous:
            raise EligibleSetRefused(
                ELIGIBLE_ORDER_NOT_CANONICAL if key != previous
                else ELIGIBLE_KEY_REPEATED,
                f"record {index} is not strictly after its predecessor in "
                f"{ELIGIBLE_SORT_KEYS}")
        if key in seen:                                   # pragma: no cover
            raise EligibleSetRefused(
                ELIGIBLE_KEY_REPEATED, f"{key} appears more than once")
        seen.add(key)
        previous = key
        rows.append(row)
    return rows


def _refuse_constant(name: str):
    raise EligibleSetRefused(
        ELIGIBLE_NOT_CANONICAL,
        f"{name} is not a value a conforming JSON parser accepts")


def format_declaration() -> Dict[str, Any]:
    return {
        "schema": IQE_SCHEMA,
        "format_version": IQE_FORMAT_VERSION,
        "magic_bytes": len(IQE_MAGIC),
        "distinct_from_iqc_magic": IQE_MAGIC != IQC_MAGIC,
        "max_record_bytes": IQE_MAX_RECORD_BYTES,
        "prefix_bytes": IQE_FRAMING_PREFIX_BYTES,
        "sort_keys": list(ELIGIBLE_SORT_KEYS),
        "second_digest_introduced": False,
    }
