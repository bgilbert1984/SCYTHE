"""The corpus manifest: what a corpus recorded about itself before its first window.

§5.26, accepted 2026-09-17. §5.25's admission recomputed a caller's lock digests
and refused a lock that disagreed with the objects beside it -- and that is all a
check on **one object** can establish. A caller who built a lock from its own
envelope passed every one of them, so the caller still supplied the set the
answer was drawn from, wrapped in a valid object.

    The lock is a fact of the corpus namespace, not an argument.

This module is the artefact half of that: the framing, the canonical form, the
body's required fields and the reader. It resolves no path, opens nothing,
creates nothing and writes nothing -- `rf_corpus_namespace` performs the acts,
and §5.20's unbuilt reader needs this without them.

**What a manifest is authority for, and what it is not.** It is authority
against a **caller**: no code path can present admission with a lock this file
did not record. It is not authority against someone holding the filesystem, and
a digest stored beside mutable content in the same writable namespace proves
canonical identity and detects corruption -- it does not prove the namespace
owner did not replace both. §5.20 is already this honest about encryption.
"""

from __future__ import annotations

import hashlib
import json
import struct
from typing import Any, Dict, Mapping, Tuple

from rf_capture_format import IQC_FORMAT_VERSION, IQC_HEADER_SCHEMA, IQC_MAGIC
from rf_eligible_trials_artefact import (
    IQE_FORMAT_VERSION, IQE_MAGIC, IQE_SCHEMA,
)
from rf_validation_manifest import STRATA_DEFINITION_REVISION

# -- the framing, declared rather than described (§5.26) --------------------

# Same construction as `.iqc` and for the same reasons -- high bit set on byte
# 0 so a seven-bit-clean path corrupts it detectably, trailing CR LF so a
# text-mode copy is caught -- and **distinct from `IQC_MAGIC`**, so a manifest
# presented as a window, or a window presented as a manifest, is refused
# without either being parsed.
IQM_MAGIC = b"\x89SCYMF\r\n"                      # exactly 8 bytes
IQM_FORMAT_VERSION = 2                             # uint16, little-endian
# v2. §5.27 makes a second namespace artefact necessary and adds two required
# fields, which a v1 manifest does not carry -- so v1 REFUSES as unsupported
# rather than being upgraded by inventing the rows it never held.
IQM_SCHEMA = "scythe.iq-corpus-manifest.v2"
# The body carries a whole capture plan -- 64 tunings, a spur catalogue and a
# selection -- so the bound is larger than the header's. Checked BEFORE
# allocating: a uint32 length can claim four gibibytes, and a corrupt or
# hostile one must become a refusal rather than an allocation.
IQM_MAX_BODY_BYTES = 1_048_576

IQM_FRAMING_PREFIX_BYTES = len(IQM_MAGIC) + 2 + 4

# Raised, not asserted. `python -O` strips `assert`, so an invariant that must
# hold in every interpreter cannot be one -- and this is the invariant that
# stops a manifest being readable as a window. The import detonates rather than
# letting the collision exist, which is coarse enforcement on purpose;
# `test_the_module_refuses_colliding_magic_values` is its isolated witness.
if len(IQM_MAGIC) != 8:                                  # pragma: no cover
    raise ImportError(f"IQM_MAGIC is {len(IQM_MAGIC)} bytes, not 8")
if IQM_MAGIC == IQC_MAGIC:                               # pragma: no cover
    raise ImportError(
        "IQM_MAGIC and IQC_MAGIC are equal; a corpus manifest would be "
        "readable as an IQ window and a window as a manifest")
# Checked here because this is the one module that imports all three formats.
if IQM_MAGIC == IQE_MAGIC:                               # pragma: no cover
    raise ImportError(
        "IQM_MAGIC and IQE_MAGIC are equal; a corpus manifest would be "
        "readable as an eligible-trial artefact, and the reverse")

# -- refusals ---------------------------------------------------------------
#
# The artefact side. `rf_corpus_namespace` declares the acts' side separately,
# and the two are named disjointly: a code here is about bytes, a code there is
# about a directory, a descriptor or who holds it.
MANIFEST_NOT_FOUND = "MANIFEST_NOT_FOUND"
MANIFEST_VERSION_REFUSED = "MANIFEST_VERSION_REFUSED"
MANIFEST_ALREADY_PRESENT = "MANIFEST_ALREADY_PRESENT"
MANIFEST_FRAMING_REFUSED = "MANIFEST_FRAMING_REFUSED"
MANIFEST_BODY_TOO_LARGE = "MANIFEST_BODY_TOO_LARGE"
MANIFEST_NOT_CANONICAL = "MANIFEST_NOT_CANONICAL"
MANIFEST_TRAILING_BYTES = "MANIFEST_TRAILING_BYTES"
MANIFEST_DECLARATION_DISAGREES = "MANIFEST_DECLARATION_DISAGREES"
MANIFEST_FIELD_NOT_EMITTED = "MANIFEST_FIELD_NOT_EMITTED"
MANIFEST_FIELD_NO_AUTHORITY = "MANIFEST_FIELD_NO_AUTHORITY"
MANIFEST_REFUSALS: Tuple[str, ...] = (
    MANIFEST_NOT_FOUND, MANIFEST_VERSION_REFUSED, MANIFEST_ALREADY_PRESENT,
    MANIFEST_FRAMING_REFUSED,
    MANIFEST_BODY_TOO_LARGE, MANIFEST_NOT_CANONICAL, MANIFEST_TRAILING_BYTES,
    MANIFEST_DECLARATION_DISAGREES, MANIFEST_FIELD_NOT_EMITTED,
    MANIFEST_FIELD_NO_AUTHORITY,
)


class ManifestRefused(RuntimeError):
    """A manifest that is not one. Raised, so a refused corpus cannot be held."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- the body, by authority -------------------------------------------------
#
# The same construction §5.25 used for the `.iqc` header: each authority
# declares what it supplies, the required set is the union, and the emitted set
# must EQUAL it. A field becomes required by being exported and an internal
# field stays internal by not being -- so adding a diagnostic attribute to
# `PromotionCorpusLock` does not silently become a format revision.

CORPUS_IDENTITY_FIELDS: Tuple[str, ...] = ("corpus_id", "opened_at")
# Every declared field of the frozen lock except the two above, which the
# corpus-identity authority owns. Derived from the dataclass rather than
# transcribed, so a field added to the lock becomes required without anyone
# editing a list -- §3's repair, applied to a second format.
LOCK_FIELDS_OWNED_BY_IDENTITY = frozenset(CORPUS_IDENTITY_FIELDS)
RETENTION_FIELDS: Tuple[str, ...] = ("delete_not_after",
                                     "retention_maximum_days")
FORMAT_FIELDS: Tuple[str, ...] = (
    "schema", "format_version", "strata_definition_revision",
    "iqc_header_schema", "iqc_format_version",
    # §5.27. Always present, never conditional on whether the plan has a spur
    # allocation: a conditional field would make the required set a handwritten
    # list with a branch in it, and the union equality below is the whole point.
    "iqe_schema", "iqe_format_version",
)


def lock_fields() -> Tuple[str, ...]:
    """Every declared lock field the manifest carries, derived from the lock."""
    from rf_validation_manifest import PromotionCorpusLock
    return tuple(sorted(name for name in PromotionCorpusLock.__dataclass_fields__
                        if name not in LOCK_FIELDS_OWNED_BY_IDENTITY))


def required_manifest_fields() -> frozenset:
    """The union of the four declarations. Derived, never transcribed."""
    return frozenset(CORPUS_IDENTITY_FIELDS + lock_fields()
                     + RETENTION_FIELDS + FORMAT_FIELDS)


def manifest_body(*, lock: Any, retention: Any) -> Dict[str, Any]:
    """The body, from the objects that own each part of it.

    `envelope` and `capture_plan` are carried as their own `to_dict()` forms,
    so the manifest holds the declarations rather than a digest of them and a
    reader can reconstruct what admission will consult.
    """
    body: Dict[str, Any] = {
        "corpus_id": lock.corpus_id,
        "opened_at": float(lock.opened_at),
        "schema": IQM_SCHEMA,
        "format_version": IQM_FORMAT_VERSION,
        "strata_definition_revision": STRATA_DEFINITION_REVISION,
        "iqc_header_schema": IQC_HEADER_SCHEMA,
        "iqc_format_version": IQC_FORMAT_VERSION,
        "iqe_schema": IQE_SCHEMA,
        "iqe_format_version": IQE_FORMAT_VERSION,
        "delete_not_after": float(retention.delete_not_after),
        "retention_maximum_days": retention.to_dict()[
            "maximum_days_from_opened_at"],
    }
    for name in lock_fields():
        value = getattr(lock, name)
        body[name] = value.to_dict() if hasattr(value, "to_dict") else value
    return body


def check_body_completeness(body: Mapping[str, Any]) -> None:
    """Emitted must **equal** required -- not contain it.

    Extra is refused for the reason §5.25 refused it in the header: the body is
    covered by `manifest_sha256` and bound by the membership journal, so a field
    no authority declared is caller-supplied decoration inside the canonical
    identity of a corpus.
    """
    required = required_manifest_fields()
    emitted = frozenset(body)
    missing = sorted(required - emitted)
    if missing:
        raise ManifestRefused(
            MANIFEST_FIELD_NOT_EMITTED,
            f"{len(missing)} declared field(s) absent: {', '.join(missing)}")
    extra = sorted(emitted - required)
    if extra:
        raise ManifestRefused(
            MANIFEST_FIELD_NO_AUTHORITY,
            f"{len(extra)} field(s) no authority declared: {', '.join(extra)}")


# -- canonical form and framing ---------------------------------------------


def canonical_body_bytes(body: Mapping[str, Any]) -> bytes:
    """One serialisation. Two spellings of one manifest are two corpora.

    `allow_nan=False` for the reason §5.20 gave: Python emits bare `NaN` and
    `Infinity` by default, no conforming parser accepts them, and a `NaN` in a
    manifest would compare unequal to itself while every digest over it
    verified.
    """
    try:
        text = json.dumps(dict(body), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise ManifestRefused(
            MANIFEST_NOT_CANONICAL,
            f"the body is not canonically serialisable: {exc}")
    except TypeError as exc:
        raise ManifestRefused(
            MANIFEST_NOT_CANONICAL,
            f"the body holds a value JSON cannot carry: {exc}")
    encoded = text.encode("utf-8")
    if len(encoded) > IQM_MAX_BODY_BYTES:
        raise ManifestRefused(
            MANIFEST_BODY_TOO_LARGE,
            f"the body is {len(encoded)} bytes and the format allows "
            f"{IQM_MAX_BODY_BYTES}")
    return encoded


def frame_manifest(body: Mapping[str, Any]) -> bytes:
    """The complete framed manifest: magic, version, length, body, EOF."""
    check_body_completeness(body)
    encoded = canonical_body_bytes(body)
    return (IQM_MAGIC
            + struct.pack("<H", IQM_FORMAT_VERSION)
            + struct.pack("<I", len(encoded))
            + encoded)


def manifest_sha256(framed: bytes) -> str:
    """SHA-256 over the **complete framed bytes**, magic through body.

    It is not in the body. A digest cannot cover the bytes that carry it --
    §13i J.7a, §16.53b, and §5.20's own rule for the `.iqc` header. §5.26 binds
    this value in the membership journal's intent records, which are outside the
    manifest; until that journal exists it is computed on read and reported, and
    its job is canonical identity and corruption detection, not authority.
    """
    return hashlib.sha256(framed).hexdigest()


def parse_manifest(framed: bytes) -> Tuple[Dict[str, Any], str]:
    """Parse a framed manifest, or refuse. Returns the body and its digest.

    The body is **re-serialised and compared byte-for-byte** with what was read.
    Without that, two files with identical meaning and different spacing are two
    digests and two identities for one corpus.
    """
    if type(framed) is not bytes:
        raise ManifestRefused(
            MANIFEST_FRAMING_REFUSED,
            f"a manifest is framed bytes; got {type(framed).__name__}")
    if len(framed) < IQM_FRAMING_PREFIX_BYTES:
        raise ManifestRefused(
            MANIFEST_FRAMING_REFUSED,
            f"{len(framed)} bytes cannot hold a {IQM_FRAMING_PREFIX_BYTES}-byte "
            "prefix")
    if framed[:8] != IQM_MAGIC:
        raise ManifestRefused(
            MANIFEST_FRAMING_REFUSED,
            "the magic is not a corpus manifest's"
            + (" -- this is an .iqc window" if framed[:8] == IQC_MAGIC else ""))
    version = struct.unpack("<H", framed[8:10])[0]
    if version != IQM_FORMAT_VERSION:
        # A v1 manifest is not corrupt; it is a complete declaration of an
        # earlier format that cannot reconstruct a capture plan, because the
        # eligible rows it commits to live in an artefact v1 never had. It is
        # refused as UNSUPPORTED rather than upgraded by inventing them.
        raise ManifestRefused(
            MANIFEST_VERSION_REFUSED,
            f"format version {version}, not {IQM_FORMAT_VERSION}"
            + ("; a v1 manifest carries no eligible-trial artefact and cannot "
               "reconstruct its capture plan" if version == 1 else ""))
    length = struct.unpack("<I", framed[10:14])[0]
    # Bounded BEFORE the slice, not after it.
    if length > IQM_MAX_BODY_BYTES:
        raise ManifestRefused(
            MANIFEST_BODY_TOO_LARGE,
            f"the length field claims {length} bytes and the format allows "
            f"{IQM_MAX_BODY_BYTES}")
    end = IQM_FRAMING_PREFIX_BYTES + length
    if len(framed) < end:
        raise ManifestRefused(
            MANIFEST_FRAMING_REFUSED,
            f"the length field claims {length} bytes and "
            f"{len(framed) - IQM_FRAMING_PREFIX_BYTES} are present")
    # EOF falls immediately after the body. A reader that ignores what it did
    # not expect is a reader that can be appended to.
    if len(framed) > end:
        raise ManifestRefused(
            MANIFEST_TRAILING_BYTES,
            f"{len(framed) - end} byte(s) follow the body")
    raw = framed[IQM_FRAMING_PREFIX_BYTES:end]
    try:
        body = json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ManifestRefused(MANIFEST_NOT_CANONICAL,
                              f"the body does not parse: {exc}")
    if type(body) is not dict:
        raise ManifestRefused(
            MANIFEST_NOT_CANONICAL,
            f"the body is a {type(body).__name__}, not an object")
    check_body_completeness(body)
    if canonical_body_bytes(body) != raw:
        raise ManifestRefused(
            MANIFEST_NOT_CANONICAL,
            "the body does not re-serialise to the bytes it was read from")
    return body, manifest_sha256(framed)


def _refuse_constant(name: str):
    """`NaN`, `Infinity` and `-Infinity` refused at parse as well as at write."""
    raise ManifestRefused(
        MANIFEST_NOT_CANONICAL,
        f"{name} is not a value a conforming JSON parser accepts")


def format_declaration() -> Dict[str, Any]:
    """What the manifest format declares about itself."""
    return {
        "schema": IQM_SCHEMA,
        "format_version": IQM_FORMAT_VERSION,
        "magic_bytes": len(IQM_MAGIC),
        "distinct_from_iqc_magic": IQM_MAGIC != IQC_MAGIC,
        "max_body_bytes": IQM_MAX_BODY_BYTES,
        "prefix_bytes": IQM_FRAMING_PREFIX_BYTES,
        "digest_covers": "MAGIC THROUGH BODY",
        "digest_in_body": False,
    }
