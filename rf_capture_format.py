"""The `.iqc` framing, declared where both halves of §5.20 can reach it.

§5.20 fixed the representation: magic, a fixed format version, a fixed-width
header length, a canonical UTF-8 JSON header, the payload, and EOF immediately
after it. This module holds **only that** -- the constants and the two
serialisation rules -- and knows nothing about admission, corpora, locks or
rings.

It is separate from `rf_capture_admission` on purpose. The boundary decides
whether a window belongs; the format decides what a file made of one looks
like. §5.20's unbuilt persistence slice needs the second without the first, and
a reader needs it without either.

**Nothing here creates, opens or writes a file.** `canonical_header_bytes` and
`framing_prefix` return bytes; what a caller does with them is elsewhere.
"""

from __future__ import annotations

import json
import struct
from typing import Any, Dict, Mapping, Tuple

from rf_promotion_geometry import PROMOTION_DTYPE

# -- the fixed values, declared rather than described (§5.20) ----------------

# PNG's construction for PNG's reasons: byte 0 has its high bit set, so a
# seven-bit-clean transfer path corrupts it detectably, and the trailing CR LF
# catches line-ending translation. A file that survived a text-mode copy is not
# a corpus member and should not be readable as one.
IQC_MAGIC = b"\x89SCYIQ\r\n"                        # exactly 8 bytes
IQC_FORMAT_VERSION = 1                              # uint16, little-endian
IQC_HEADER_SCHEMA = "scythe.iq-capture-window.v1"   # the JSON `schema` token
IQC_MAX_HEADER_BYTES = 65_536

# The storage representation, named in the header so a reader never infers it.
# `complex64` is the promotion geometry's dtype; the byte order is declared
# because "native" is not a representation, it is a property of whoever wrote
# the file.
IQC_SAMPLE_DTYPE = PROMOTION_DTYPE
IQC_BYTE_ORDER = "little"

# magic (8) + format_version (uint16 LE) + header_length (uint32 LE)
IQC_FRAMING_PREFIX_BYTES = len(IQC_MAGIC) + 2 + 4

# The header carries `payload_sha256` and never `file_sha256`: a digest cannot
# cover the header that carries it (§13i J.7a, §16.53b, §5.20). The filename
# derives from `file_sha256`, which is why it lives outside the header rather
# than being omitted by an oversight somebody could correct.
FILE_DIGEST_FIELD_FORBIDDEN_IN_HEADER = "file_sha256"

assert len(IQC_MAGIC) == 8


class FramingRefused(ValueError):
    """A header that cannot be framed. Not a capture refusal -- a format one."""


def canonical_header_bytes(header: Mapping[str, Any]) -> bytes:
    """The one serialisation. Two spellings of one header are two files.

    `allow_nan=False` is load-bearing rather than tidy: Python's `json` emits
    bare `NaN` and `Infinity` by default, which no conforming parser accepts --
    and a `NaN` that reached the header would compare unequal to itself, so
    every digest over it would verify correctly while the value it named meant
    nothing.
    """
    try:
        text = json.dumps(dict(header), sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except ValueError as exc:                      # NaN, Infinity, -Infinity
        raise FramingRefused(f"the header is not canonically serialisable: {exc}")
    except TypeError as exc:                       # a value the schema did not
        raise FramingRefused(f"the header holds a value JSON cannot carry: {exc}")
    encoded = text.encode("utf-8")
    if len(encoded) > IQC_MAX_HEADER_BYTES:
        raise FramingRefused(
            f"the header is {len(encoded)} bytes and the format allows "
            f"{IQC_MAX_HEADER_BYTES}")
    return encoded


def framing_prefix(header_bytes: bytes) -> bytes:
    """Magic, format version and header length -- located, never scanned for.

    `header_length` is fixed-width, unsigned and little-endian so that a reader
    finds the header before reading it, and so that it can reject a file it
    cannot parse **without parsing it**.
    """
    if type(header_bytes) is not bytes:
        raise FramingRefused(
            f"the header is framed as bytes; got {type(header_bytes).__name__}")
    if len(header_bytes) > IQC_MAX_HEADER_BYTES:
        raise FramingRefused(
            f"the header is {len(header_bytes)} bytes and the format allows "
            f"{IQC_MAX_HEADER_BYTES}")
    return (IQC_MAGIC
            + struct.pack("<H", IQC_FORMAT_VERSION)
            + struct.pack("<I", len(header_bytes)))


IQC_MEMBER_SUFFIX = ".iqc"
IQC_FILE_DIGEST_HEX_CHARS = 64


def canonical_member_name(file_sha256: str) -> str:
    """The member's one filename: its `file_sha256`, then `.iqc`.

    The name carries the digest and **nothing else**. A name that also spelled
    the stratum or the window id would repeat facts the header already fixes,
    inside a string a reader compares against the file's own digest --- and a
    redundant discriminator can disagree with the fact it repeats, which is the
    objection that removed `attestation_kind` from the header. One file, one
    name, and the name is checkable from the bytes.

    Nominal and shape-checked, because a digest is the one argument here and a
    caller that passed something else would produce a name that no readback
    could ever agree with.
    """
    if type(file_sha256) is not str:
        raise FramingRefused(
            "a member name derives from file_sha256 as a string; got "
            f"{type(file_sha256).__name__}")
    if len(file_sha256) != IQC_FILE_DIGEST_HEX_CHARS:
        raise FramingRefused(
            f"file_sha256 is {len(file_sha256)} characters, not "
            f"{IQC_FILE_DIGEST_HEX_CHARS}")
    if any(c not in "0123456789abcdef" for c in file_sha256):
        raise FramingRefused(
            "file_sha256 is lowercase hexadecimal; this is not")
    return file_sha256 + IQC_MEMBER_SUFFIX


def framing_declaration() -> Dict[str, Any]:
    """What the format declares about itself, for a status surface to read."""
    return {
        "schema": IQC_HEADER_SCHEMA,
        "format_version": IQC_FORMAT_VERSION,
        "magic_bytes": len(IQC_MAGIC),
        "max_header_bytes": IQC_MAX_HEADER_BYTES,
        "sample_dtype": IQC_SAMPLE_DTYPE,
        "byte_order": IQC_BYTE_ORDER,
        "prefix_bytes": IQC_FRAMING_PREFIX_BYTES,
    }


FRAMING_FIELDS: Tuple[str, ...] = tuple(sorted(framing_declaration()))
