"""The append-only membership journal core accepted by §5.26.

This module owns only facts that can be established from ``membership.iqj``
itself: durable empty creation, the three exact record schemas, canonical
framing, append durability, torn-tail truncation, bounds, and intrinsic log
consistency.  It does not inspect capture files and therefore does not decide
whether a final is verified.  That authority arrives with §5.20 steps 5--8;
the six final-dependent recovery classifications remain in slice 3d.

All filesystem acts are relative to an already-held corpus directory
descriptor.  No path leaves this module and no production namespace is named.
"""

from __future__ import annotations

import hashlib
import json
import errno
import os
import stat
import struct
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from rf_capture_admission import CAPTURED_STRATA
from rf_validation_manifest import MINIMUM_WINDOWS_PER_STRATUM


JOURNAL_NAME = "membership.iqj"
JOURNAL_FILE_MODE = 0o600

IQJ_RECORD_LENGTH_BYTES = 4
IQJ_RECORD_DIGEST_BYTES = 32
IQJ_MAX_RECORD_BYTES = 65_536

CORPUS_MEMBER_CAPACITY = (
    len(CAPTURED_STRATA) * MINIMUM_WINDOWS_PER_STRATUM
)
MAX_ABANDONED_ATTEMPTS = 2 * CORPUS_MEMBER_CAPACITY
IQJ_MAX_RECORDS = 2 * (
    CORPUS_MEMBER_CAPACITY + MAX_ABANDONED_ATTEMPTS
)

INTENT = "INTENT"
COMMIT = "COMMIT"
ABANDON = "ABANDON"
RECORD_TYPES: Tuple[str, ...] = (INTENT, COMMIT, ABANDON)

INTENT_FIELDS = frozenset((
    "record_type", "manifest_sha256", "corpus_id", "stratum",
    "window_id", "ring_lifetime_id", "expected_final_filename",
    "file_sha256", "payload_sha256", "previous_window_id",
    "first_sample_index", "envelope_digest", "capture_plan_digest",
))
TERMINAL_FIELDS = frozenset(("record_type", "window_id"))


JOURNAL_NOT_FOUND = "JOURNAL_NOT_FOUND"
JOURNAL_ALREADY_PRESENT = "JOURNAL_ALREADY_PRESENT"
JOURNAL_DESCRIPTOR_REFUSED = "JOURNAL_DESCRIPTOR_REFUSED"
JOURNAL_SYMLINK_REFUSED = "JOURNAL_SYMLINK_REFUSED"
JOURNAL_HARD_LINKED = "JOURNAL_HARD_LINKED"
JOURNAL_OWNER_MISMATCH = "JOURNAL_OWNER_MISMATCH"
JOURNAL_MODE_PERMISSIVE = "JOURNAL_MODE_PERMISSIVE"
JOURNAL_DEVICE_MISMATCH = "JOURNAL_DEVICE_MISMATCH"
JOURNAL_RECORD_TOO_LARGE = "JOURNAL_RECORD_TOO_LARGE"
JOURNAL_NOT_CANONICAL = "JOURNAL_NOT_CANONICAL"
JOURNAL_RECORD_TYPE_REFUSED = "JOURNAL_RECORD_TYPE_REFUSED"
JOURNAL_FIELD_NOT_EMITTED = "JOURNAL_FIELD_NOT_EMITTED"
JOURNAL_FIELD_NO_AUTHORITY = "JOURNAL_FIELD_NO_AUTHORITY"
JOURNAL_VALUE_REFUSED = "JOURNAL_VALUE_REFUSED"
JOURNAL_RECORD_LIMIT_REACHED = "JOURNAL_RECORD_LIMIT_REACHED"
JOURNAL_ABANDONMENT_LIMIT_REACHED = "JOURNAL_ABANDONMENT_LIMIT_REACHED"
JOURNAL_DUPLICATE_INTENT = "JOURNAL_DUPLICATE_INTENT"
JOURNAL_TERMINAL_WITHOUT_INTENT = "JOURNAL_TERMINAL_WITHOUT_INTENT"
JOURNAL_DUPLICATE_TERMINAL = "JOURNAL_DUPLICATE_TERMINAL"
JOURNAL_CONTRADICTORY_TERMINAL = "JOURNAL_CONTRADICTORY_TERMINAL"
JOURNAL_STRATUM_CAP_REACHED = "JOURNAL_STRATUM_CAP_REACHED"

JOURNAL_REFUSALS: Tuple[str, ...] = (
    JOURNAL_NOT_FOUND, JOURNAL_ALREADY_PRESENT, JOURNAL_DESCRIPTOR_REFUSED,
    JOURNAL_SYMLINK_REFUSED, JOURNAL_HARD_LINKED, JOURNAL_OWNER_MISMATCH,
    JOURNAL_MODE_PERMISSIVE, JOURNAL_DEVICE_MISMATCH,
    JOURNAL_RECORD_TOO_LARGE, JOURNAL_NOT_CANONICAL,
    JOURNAL_RECORD_TYPE_REFUSED, JOURNAL_FIELD_NOT_EMITTED,
    JOURNAL_FIELD_NO_AUTHORITY, JOURNAL_VALUE_REFUSED,
    JOURNAL_RECORD_LIMIT_REACHED, JOURNAL_ABANDONMENT_LIMIT_REACHED,
    JOURNAL_DUPLICATE_INTENT, JOURNAL_TERMINAL_WITHOUT_INTENT,
    JOURNAL_DUPLICATE_TERMINAL, JOURNAL_CONTRADICTORY_TERMINAL,
    JOURNAL_STRATUM_CAP_REACHED,
)


class JournalRefused(RuntimeError):
    """Bytes or journal state that cannot establish corpus membership."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class JournalState:
    """Validated intrinsic state; no claim about any final capture file."""

    records: Tuple[Mapping[str, Any], ...]
    intents: Mapping[str, Mapping[str, Any]]
    terminals: Mapping[str, str]
    committed_by_stratum: Mapping[str, int]
    abandoned_attempts: int
    valid_bytes: int
    torn_tail_truncated: bool


def required_fields(record_type: str) -> frozenset:
    if record_type == INTENT:
        return INTENT_FIELDS
    if record_type in (COMMIT, ABANDON):
        return TERMINAL_FIELDS
    raise JournalRefused(
        JOURNAL_RECORD_TYPE_REFUSED,
        f"record_type {record_type!r} is not one of {', '.join(RECORD_TYPES)}")


def _check_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(record, Mapping):
        raise JournalRefused(
            JOURNAL_NOT_CANONICAL,
            f"a journal record is a mapping; got {type(record).__name__}")
    body = dict(record)
    record_type = body.get("record_type")
    if type(record_type) is not str:
        raise JournalRefused(
            JOURNAL_RECORD_TYPE_REFUSED,
            f"record_type {record_type!r} is not one of {', '.join(RECORD_TYPES)}")
    required = required_fields(record_type)
    emitted = frozenset(body)
    missing = sorted(required - emitted)
    if missing:
        raise JournalRefused(
            JOURNAL_FIELD_NOT_EMITTED,
            f"{record_type} lacks: {', '.join(missing)}")
    extra = sorted(emitted - required)
    if extra:
        raise JournalRefused(
            JOURNAL_FIELD_NO_AUTHORITY,
            f"{record_type} carries undeclared fields: {', '.join(extra)}")

    window_id = body["window_id"]
    if type(window_id) is not str or not window_id:
        raise JournalRefused(
            JOURNAL_VALUE_REFUSED, "window_id must be a non-empty string")
    if record_type == INTENT:
        for name in (
            "manifest_sha256", "corpus_id", "stratum", "ring_lifetime_id",
            "expected_final_filename", "file_sha256", "payload_sha256",
            "envelope_digest", "capture_plan_digest",
        ):
            if type(body[name]) is not str or not body[name]:
                raise JournalRefused(
                    JOURNAL_VALUE_REFUSED,
                    f"{name} must be a non-empty string")
        if body["stratum"] not in CAPTURED_STRATA:
            raise JournalRefused(
                JOURNAL_VALUE_REFUSED,
                f"{body['stratum']!r} is not a captured stratum")
        previous = body["previous_window_id"]
        if previous is not None and (type(previous) is not str or not previous):
            raise JournalRefused(
                JOURNAL_VALUE_REFUSED,
                "previous_window_id must be null or a non-empty string")
        first = body["first_sample_index"]
        if type(first) is not int or first < 0:
            raise JournalRefused(
                JOURNAL_VALUE_REFUSED,
                "first_sample_index must be a non-negative exact integer")
    return body


def _encode_canonical(mapping: Mapping[str, Any]) -> bytes:
    """The `.iqm`/`.iqc` canonical JSON form, below record validation.

    A named seam rather than an inline `json.dumps`, because `allow_nan=False`
    is **unreachable through the public path**: every permitted field is an
    exact string, integer or `None` and extras refuse, so no valid record can
    carry a non-finite value this far. The parameter is the backstop for the day
    a numeric field is added --- and a backstop nothing can reach is a backstop
    nothing has tested. A sweep proved exactly that: flipping it to
    `allow_nan=True` discriminated nothing.

    Validation stays in front of this. The seam exists so the encoder's own
    refusal can be witnessed, not so a caller can skip the checks.
    """
    try:
        text = json.dumps(
            mapping, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise JournalRefused(
            JOURNAL_NOT_CANONICAL,
            f"the record is not canonically serialisable: {exc}") from exc
    return text.encode("utf-8")


def canonical_record_bytes(record: Mapping[str, Any]) -> bytes:
    """One validated record's canonical bytes, bounded by the record limit.

    `.iqe` uses ``ensure_ascii=True`` to reproduce a digest frozen elsewhere.
    Journal records are digested here, so there is no pre-existing escaped-byte
    identity to reproduce and the manifest/header form is the relevant one.
    """
    checked = _check_record(record)
    encoded = _encode_canonical(checked)
    if len(encoded) > IQJ_MAX_RECORD_BYTES:
        raise JournalRefused(
            JOURNAL_RECORD_TOO_LARGE,
            f"the record is {len(encoded)} bytes and the format allows "
            f"{IQJ_MAX_RECORD_BYTES}")
    return encoded


def frame_record(record: Mapping[str, Any]) -> bytes:
    body = canonical_record_bytes(record)
    return (struct.pack("<I", len(body)) + body
            + hashlib.sha256(body).digest())


def intent_record(**bindings: Any) -> Dict[str, Any]:
    """Build the exact INTENT mapping; validation occurs before it is returned."""
    record = {"record_type": INTENT, **bindings}
    return _check_record(record)


def terminal_record(record_type: str, window_id: str) -> Dict[str, Any]:
    """Build an exact terminal mapping without repeating intent-bound facts."""
    return _check_record({"record_type": record_type, "window_id": window_id})


def _check_directory_descriptor(dir_fd: int) -> os.stat_result:
    if type(dir_fd) is not int:
        raise JournalRefused(
            JOURNAL_DESCRIPTOR_REFUSED,
            f"the corpus directory is a descriptor; got {type(dir_fd).__name__}")
    try:
        info = os.fstat(dir_fd)
    except OSError as exc:
        raise JournalRefused(
            JOURNAL_DESCRIPTOR_REFUSED,
            "the corpus directory descriptor is not open") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise JournalRefused(
            JOURNAL_DESCRIPTOR_REFUSED,
            "the supplied descriptor does not name a directory")
    return info


def _check_opened_journal(fd: int, directory: os.stat_result) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise JournalRefused(
            JOURNAL_DESCRIPTOR_REFUSED, "membership.iqj is not a regular file")
    if info.st_nlink != 1:
        raise JournalRefused(
            JOURNAL_HARD_LINKED,
            f"membership.iqj has {info.st_nlink} names instead of one")
    if info.st_uid != os.getuid():
        raise JournalRefused(
            JOURNAL_OWNER_MISMATCH,
            f"membership.iqj is owned by uid {info.st_uid}")
    if stat.S_IMODE(info.st_mode) != JOURNAL_FILE_MODE:
        raise JournalRefused(
            JOURNAL_MODE_PERMISSIVE,
            f"membership.iqj is mode {stat.S_IMODE(info.st_mode):04o}, not "
            f"{JOURNAL_FILE_MODE:04o}")
    if info.st_dev != directory.st_dev:
        raise JournalRefused(
            JOURNAL_DEVICE_MISMATCH,
            "membership.iqj is on a different device from its corpus")


def create_membership_journal(dir_fd: int) -> None:
    """Create, mode and `fsync` the canonical zero-record journal.

    The caller's later directory `fsync` makes this new *name* durable.  This
    call makes the empty file itself durable before `manifest.iqm` is created.
    """
    directory = _check_directory_descriptor(dir_fd)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW
    try:
        fd = os.open(
            JOURNAL_NAME, flags, JOURNAL_FILE_MODE, dir_fd=dir_fd,
        )
    except FileExistsError as exc:
        raise JournalRefused(
            JOURNAL_ALREADY_PRESENT,
            "membership.iqj already exists; creation never replaces it") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise JournalRefused(
                JOURNAL_SYMLINK_REFUSED,
                "membership.iqj is a symbolic link") from exc
        raise
    try:
        os.fchmod(fd, JOURNAL_FILE_MODE)
        _check_opened_journal(fd, directory)
        os.fsync(fd)
    finally:
        os.close(fd)


def _open_journal(dir_fd: int, flags: int) -> int:
    directory = _check_directory_descriptor(dir_fd)
    try:
        fd = os.open(JOURNAL_NAME, flags | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise JournalRefused(
                JOURNAL_NOT_FOUND,
                "a v2 corpus has no membership.iqj") from exc
        if exc.errno == errno.ELOOP:
            raise JournalRefused(
                JOURNAL_SYMLINK_REFUSED,
                "membership.iqj is a symbolic link") from exc
        raise
    try:
        _check_opened_journal(fd, directory)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_exact_or_tail(fd: int, count: int) -> Optional[bytes]:
    chunks = []
    received = 0
    while received < count:
        block = os.read(fd, count - received)
        if not block:
            return None
        chunks.append(block)
        received += len(block)
    return b"".join(chunks)


def _validated_state(records: Tuple[Mapping[str, Any], ...], *,
                     valid_bytes: int, torn: bool) -> JournalState:
    if len(records) > IQJ_MAX_RECORDS:
        raise JournalRefused(
            JOURNAL_RECORD_LIMIT_REACHED,
            f"the journal holds {len(records)} records; the bound is "
            f"{IQJ_MAX_RECORDS}")
    intents: Dict[str, Mapping[str, Any]] = {}
    terminals: Dict[str, str] = {}
    committed = {stratum: 0 for stratum in CAPTURED_STRATA}
    abandoned = 0

    for record in records:
        checked = _check_record(record)
        window_id = checked["window_id"]
        kind = checked["record_type"]
        if kind == INTENT:
            if window_id in intents:
                raise JournalRefused(
                    JOURNAL_DUPLICATE_INTENT,
                    f"window {window_id!r} has more than one intent or was spent")
            intents[window_id] = checked
            continue
        if window_id not in intents:
            raise JournalRefused(
                JOURNAL_TERMINAL_WITHOUT_INTENT,
                f"{kind} for window {window_id!r} has no preceding intent")
        previous = terminals.get(window_id)
        if previous == kind:
            raise JournalRefused(
                JOURNAL_DUPLICATE_TERMINAL,
                f"window {window_id!r} has two {kind} records")
        if previous is not None:
            raise JournalRefused(
                JOURNAL_CONTRADICTORY_TERMINAL,
                f"window {window_id!r} is both {previous} and {kind}")
        terminals[window_id] = kind
        if kind == ABANDON:
            abandoned += 1
            if abandoned > MAX_ABANDONED_ATTEMPTS:
                raise JournalRefused(
                    JOURNAL_ABANDONMENT_LIMIT_REACHED,
                    f"the journal records {abandoned} abandoned attempts; "
                    f"the budget is {MAX_ABANDONED_ATTEMPTS}")
        else:
            stratum = intents[window_id]["stratum"]
            committed[stratum] += 1
            if committed[stratum] > MINIMUM_WINDOWS_PER_STRATUM:
                raise JournalRefused(
                    JOURNAL_STRATUM_CAP_REACHED,
                    f"{stratum} has {committed[stratum]} commits; the cap is "
                    f"{MINIMUM_WINDOWS_PER_STRATUM}")

    return JournalState(
        records=records, intents=dict(intents), terminals=dict(terminals),
        committed_by_stratum=dict(committed), abandoned_attempts=abandoned,
        valid_bytes=valid_bytes, torn_tail_truncated=torn,
    )


def read_membership_journal(dir_fd: int) -> JournalState:
    """Read exactly, truncate one torn tail, `fsync`, and validate the log."""
    fd = _open_journal(dir_fd, os.O_RDWR)
    records = []
    valid_end = 0
    torn = False
    try:
        file_size = os.fstat(fd).st_size
        while True:
            start = valid_end
            # Accumulating, like the body and digest reads below. A bare
            # `os.read` here treated a SHORT READ of an intact length prefix as
            # a torn append and truncated from the last good record --- silently
            # discarding committed members. `os.read` is permitted to return
            # fewer bytes than asked for, which is why the other two reads
            # already went through this helper; the prefix was the one that did
            # not, and a fragmented-read test is what found it.
            prefix = _read_exact_or_tail(fd, IQJ_RECORD_LENGTH_BYTES)
            if prefix is None:
                # A clean end leaves nothing over; a partial prefix does. The
                # helper cannot tell those apart and the file size can.
                if valid_end != file_size:
                    torn = True
                break
            if len(records) >= IQJ_MAX_RECORDS:
                raise JournalRefused(
                    JOURNAL_RECORD_LIMIT_REACHED,
                    f"the journal has another record after the bound of "
                    f"{IQJ_MAX_RECORDS}")
            (length,) = struct.unpack("<I", prefix)
            if length > IQJ_MAX_RECORD_BYTES:
                raise JournalRefused(
                    JOURNAL_RECORD_TOO_LARGE,
                    f"a record declares {length} bytes; the bound is "
                    f"{IQJ_MAX_RECORD_BYTES}")
            body = _read_exact_or_tail(fd, length)
            if body is None:
                torn = True
                break
            digest = _read_exact_or_tail(fd, IQJ_RECORD_DIGEST_BYTES)
            if digest is None or digest != hashlib.sha256(body).digest():
                record_end = (start + IQJ_RECORD_LENGTH_BYTES + length
                              + IQJ_RECORD_DIGEST_BYTES)
                if record_end >= file_size:
                    torn = True
                    break
                raise JournalRefused(
                    JOURNAL_NOT_CANONICAL,
                    "a digest disagreement before the journal tail is "
                    "corruption, not a torn append")
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                record_end = (start + IQJ_RECORD_LENGTH_BYTES + length
                              + IQJ_RECORD_DIGEST_BYTES)
                if record_end >= file_size:
                    torn = True
                    break
                raise JournalRefused(
                    JOURNAL_NOT_CANONICAL,
                    "an unparseable record before the journal tail is "
                    "corruption, not a torn append")
            checked = _check_record(parsed)
            if canonical_record_bytes(checked) != body:
                raise JournalRefused(
                    JOURNAL_NOT_CANONICAL,
                    "a complete journal record is not in canonical byte form")
            records.append(checked)
            valid_end = start + IQJ_RECORD_LENGTH_BYTES + length + IQJ_RECORD_DIGEST_BYTES

        if torn:
            os.ftruncate(fd, valid_end)
            os.fsync(fd)
        return _validated_state(
            tuple(records), valid_bytes=valid_end, torn=torn,
        )
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    written = 0
    while written < len(data):
        count = os.write(fd, data[written:])
        if count <= 0:
            raise JournalRefused(
                JOURNAL_DESCRIPTOR_REFUSED,
                f"journal append stalled after {written} of {len(data)} bytes")
        written += count


def _append_checked(dir_fd: int, record: Mapping[str, Any],
                    state: JournalState) -> JournalState:
    frame = frame_record(record)
    checked = _check_record(record)
    # Validate the prospective state before a byte becomes durable.  A
    # duplicate, contradictory or over-budget terminal discovered after this
    # write would corrupt the journal and then report that it had refused.
    next_state = _validated_state(
        state.records + (checked,),
        valid_bytes=state.valid_bytes + len(frame), torn=False,
    )
    fd = _open_journal(dir_fd, os.O_WRONLY | os.O_APPEND)
    try:
        _write_all(fd, frame)
        os.fsync(fd)
    finally:
        os.close(fd)
    return next_state


def append_intent(dir_fd: int, record: Mapping[str, Any]) -> JournalState:
    """Durably append one INTENT after reserving its terminal record.

    Reservation is a precondition: when this returns, one later COMMIT or
    ABANDON still fits even if the process dies at the next instruction.
    """
    checked = _check_record(record)
    if checked["record_type"] != INTENT:
        raise JournalRefused(
            JOURNAL_RECORD_TYPE_REFUSED,
            "append_intent accepts exactly an INTENT record")
    state = read_membership_journal(dir_fd)
    if len(state.records) + 2 > IQJ_MAX_RECORDS:
        raise JournalRefused(
            JOURNAL_RECORD_LIMIT_REACHED,
            "there is not room for both the intent and its terminal record")
    if state.abandoned_attempts >= MAX_ABANDONED_ATTEMPTS:
        raise JournalRefused(
            JOURNAL_ABANDONMENT_LIMIT_REACHED,
            "the abandonment budget is exhausted; publication is permanently "
            "refused for this corpus")
    window_id = checked["window_id"]
    if window_id in state.intents:
        raise JournalRefused(
            JOURNAL_DUPLICATE_INTENT,
            f"window {window_id!r} already has an intent or is spent")
    return _append_checked(dir_fd, checked, state)


def _append_terminal(dir_fd: int, record_type: str,
                     window_id: str) -> JournalState:
    """Private core for the two differently obligated 3d terminal actions."""
    if record_type not in (COMMIT, ABANDON):
        raise JournalRefused(
            JOURNAL_RECORD_TYPE_REFUSED,
            "a terminal record is COMMIT or ABANDON")
    checked = terminal_record(record_type, window_id)
    state = read_membership_journal(dir_fd)
    if len(state.records) + 1 > IQJ_MAX_RECORDS:
        # This should be unreachable after append_intent's reservation.  It is
        # still a refusal rather than an assertion: recovery runs after crashes.
        raise JournalRefused(
            JOURNAL_RECORD_LIMIT_REACHED,
            "the reserved terminal record no longer fits")
    return _append_checked(dir_fd, checked, state)


def append_commit(dir_fd: int, window_id: str) -> JournalState:
    """Durably append the COMMIT that turns an intent into a member.

    3d's membership decision, reachable at last: the journal core reserved the
    terminal slot when the intent was appended, and this spends it on the
    outcome that a verified final has been read back. `_validated_state` refuses
    a COMMIT for a window with no intent, a second terminal, or a stratum
    already at its cap, before a byte is written.
    """
    return _append_terminal(dir_fd, COMMIT, window_id)


def append_abandon(dir_fd: int, window_id: str) -> JournalState:
    """Durably append the ABANDON that spends an intent without a member.

    The other outcome the reserved slot pays for: an intent whose final never
    became a verified member. Distinct from COMMIT only in the record type, so
    both go through the one private core that validates the prospective state
    before it is made durable.
    """
    return _append_terminal(dir_fd, ABANDON, window_id)


def journal_declaration() -> Dict[str, Any]:
    """Stable scalar diagnostics; never record contents or a directory path."""
    return {
        "name": JOURNAL_NAME,
        "record_types": list(RECORD_TYPES),
        "max_record_bytes": IQJ_MAX_RECORD_BYTES,
        "max_records": IQJ_MAX_RECORDS,
        "max_abandoned_attempts": MAX_ABANDONED_ATTEMPTS,
        "corpus_member_capacity": CORPUS_MEMBER_CAPACITY,
    }
