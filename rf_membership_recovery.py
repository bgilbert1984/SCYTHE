"""§5.20 3d, piece 2: final-dependent membership recovery.

The membership journal core (``rf_membership_journal``) establishes intrinsic
log consistency but, in its own words, "does not inspect capture files and
therefore does not decide whether a final is verified. That authority arrives
with §5.20 steps 5--8; the six final-dependent recovery classifications remain
in slice 3d." This module is that authority. On reopen it reads each intent's
expected final, recomputes its file digest, and classifies the window into
exactly one of six states, then acts on the classification so a reopened corpus
reflects what actually survived a crash.

The hole entry 14 names is the instant **between a verified final and its
COMMIT record**: step 6 has linked the final, step 8 has read it back, and the
process dies before the journal's COMMIT is appended. The bytes are durable and
the member is reachable, yet the journal still reads INTENT. Two wrong answers
bracket the right one:

  * starting every stratum from zero on reopen loses the member -- continuity
    by amnesia, the defect piece 1's durable sequence exists to make visible;
  * turning every open INTENT into a member manufactures a *verified-but-
    unaccounted final*, precisely the state the journal exists to refuse.

Reconciliation is the third answer. A present, verified final under an open
intent is **adopted**: its COMMIT is appended, making durable a membership that
was already true. An intent with no such final is **discarded**: its ABANDON is
appended. Only then is the journal a faithful account of the finals on disk, and
only then may a sequence be reconstructed from it (piece 1's
``reconstruct_stratum_sequence``), which the reopen path does immediately after.

Every act here is relative to an already-held corpus directory descriptor, under
the exclusive ``flock`` the reopen path holds before this runs. No path leaves
this module and no production namespace is named. Verification returns a plain
outcome rather than raising: a member name that reaches the wrong bytes is a
window to discard, not an error to abort a reopen with. A genuine contradiction
between the journal and the disk -- a COMMIT whose final is gone, an ABANDON
whose final is present, a member no intent records, a member object that is not
a proper member -- refuses through ``RecoveryRefused``, which is this module's
own regime.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
import struct
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

import numpy as _np

from rf_capture_admission import (
    CAPTURED_STRATA, CapturedStratumSequence, CommittedWindow,
    reconstruct_stratum_sequence,
)
from rf_capture_format import (
    IQC_FRAMING_PREFIX_BYTES, IQC_MAGIC, IQC_MAX_HEADER_BYTES,
    IQC_FORMAT_VERSION, canonical_member_name, is_partial_member_name,
    partial_member_name,
)
from rf_corpus_namespace import CORPUS_FILE_MODE
from rf_membership_journal import (
    ABANDON, COMMIT, JournalState, append_abandon, append_commit,
)
from rf_promotion_geometry import PROMOTION_DTYPE, PROMOTION_WINDOW_SAMPLES

SCHEMA = "scythe.rf-membership-recovery.v1"

# The readback is bounded so a member that is too long is caught rather than
# read without limit: a valid member holds at most one promotion window of
# samples, and the file digest -- recomputed over exactly what was read --
# refuses anything longer, so the bound only has to be safe, not exact.
_SAMPLE_BYTES = _np.dtype(PROMOTION_DTYPE).itemsize
IQC_MAX_PAYLOAD_BYTES = PROMOTION_WINDOW_SAMPLES * _SAMPLE_BYTES
IQC_READBACK_LIMIT = (IQC_FRAMING_PREFIX_BYTES + IQC_MAX_HEADER_BYTES
                      + IQC_MAX_PAYLOAD_BYTES + 1)


# -- the six final-dependent classifications --------------------------------
#
# One classification per window, decided from its journal terminal state and the
# outcome of verifying its expected final. Named for what the window IS, not for
# the action taken, because the action follows from the state and two windows in
# the same state take the same action.
SETTLED_MEMBER = "SETTLED_MEMBER"              # COMMIT + a verified final
ADOPTED = "ADOPTED"                            # INTENT-only + a verified final
DISCARDED_UNWRITTEN = "DISCARDED_UNWRITTEN"    # INTENT-only + no final at all
DISCARDED_UNVERIFIED = "DISCARDED_UNVERIFIED"  # INTENT-only + a final that fails
SETTLED_ABANDON = "SETTLED_ABANDON"            # ABANDON + no verified final
UNACCOUNTED_FINAL = "UNACCOUNTED_FINAL"        # a member name no intent records
RECOVERY_CLASSIFICATIONS: Tuple[str, ...] = (
    SETTLED_MEMBER, ADOPTED, DISCARDED_UNWRITTEN, DISCARDED_UNVERIFIED,
    SETTLED_ABANDON, UNACCOUNTED_FINAL,
)

# The two classes that add a member, and the two that spend an intent. Kept as
# sets so the reconcile loop reads as the classification deciding the action.
_ADMITTING = frozenset((SETTLED_MEMBER, ADOPTED))
_DISCARDING = frozenset((DISCARDED_UNWRITTEN, DISCARDED_UNVERIFIED))


# -- membership contradictions, this module's own refusals ------------------
RECOVERY_COMMITTED_FINAL_MISSING = "RECOVERY_COMMITTED_FINAL_MISSING"
RECOVERY_ABANDONED_FINAL_PRESENT = "RECOVERY_ABANDONED_FINAL_PRESENT"
RECOVERY_UNACCOUNTED_FINAL = "RECOVERY_UNACCOUNTED_FINAL"
RECOVERY_STRAY_ENTRY = "RECOVERY_STRAY_ENTRY"
RECOVERY_INTENT_MANIFEST_MISMATCH = "RECOVERY_INTENT_MANIFEST_MISMATCH"
RECOVERY_MEMBER_OBJECT_REFUSED = "RECOVERY_MEMBER_OBJECT_REFUSED"
RECOVERY_INTENT_SELF_CONTRADICTORY = "RECOVERY_INTENT_SELF_CONTRADICTORY"
RECOVERY_MIXED_LIFETIME = "RECOVERY_MIXED_LIFETIME"
RECOVERY_DESCRIPTOR_REFUSED = "RECOVERY_DESCRIPTOR_REFUSED"
RECOVERY_REFUSALS: Tuple[str, ...] = (
    RECOVERY_COMMITTED_FINAL_MISSING, RECOVERY_ABANDONED_FINAL_PRESENT,
    RECOVERY_UNACCOUNTED_FINAL, RECOVERY_STRAY_ENTRY,
    RECOVERY_INTENT_MANIFEST_MISMATCH, RECOVERY_MEMBER_OBJECT_REFUSED,
    RECOVERY_INTENT_SELF_CONTRADICTORY, RECOVERY_MIXED_LIFETIME,
    RECOVERY_DESCRIPTOR_REFUSED,
)


class RecoveryRefused(RuntimeError):
    """Journal state and finals on disk that cannot form a coherent membership.

    Distinct from ``JournalRefused`` (the log is not intrinsically valid) and
    from ``PublicationFailed`` (a final name's bytes do not verify): those two
    are established without cross-referencing the journal against the finals,
    and this is exactly that cross-reference failing.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class FinalOutcome:
    """The result of looking for one intent's expected final on disk.

    ``verified`` is true only when a file at the intent's canonical member name
    read back to the intent's ``file_sha256``. When verified, the window's
    sample interval -- taken from the header those exact bytes carry -- is here
    so the caller need not read the file again; when not, both indices are
    ``None``. ``present`` distinguishes a final that is absent (a crash before
    the link) from one whose bytes are wrong (a name reaching the wrong file).
    """

    present: bool
    verified: bool
    first_sample_index: Optional[int] = None
    last_sample_index: Optional[int] = None


@dataclass(frozen=True)
class ReconciledMembership:
    """What a reopen found, after the journal and the finals agree.

    ``members_by_stratum`` maps each captured stratum to the tuple of its
    committed windows, oldest first, ready for
    ``reconstruct_stratum_sequence``. ``counts`` records how many windows fell
    into each classification, for the status surface and for a sweep's witness.
    """

    members_by_stratum: Mapping[str, Tuple[CommittedWindow, ...]]
    counts: Mapping[str, int]


def _classify(terminal: Optional[str], outcome: FinalOutcome) -> str:
    """The pure core: journal terminal state plus final outcome to a class.

    Takes no descriptor and reads no file. Every contradiction it can name is a
    disagreement between two facts it is handed, so a test states the pair
    rather than arranging a crash. The impure caller establishes ``outcome`` and
    performs the action the class implies.
    """
    if terminal == COMMIT:
        if not outcome.verified:
            raise RecoveryRefused(
                RECOVERY_COMMITTED_FINAL_MISSING,
                "a window the journal committed has no verified final; a "
                "COMMIT is the claim that step 8 read one back, and reopening "
                "over its absence would trust a record the bytes do not support")
        return SETTLED_MEMBER
    if terminal == ABANDON:
        if outcome.verified:
            raise RecoveryRefused(
                RECOVERY_ABANDONED_FINAL_PRESENT,
                "a window the journal abandoned has a verified final; abandon "
                "precedes the link, so a member under an abandoned intent is a "
                "final the journal disowns and cannot silently keep")
        return SETTLED_ABANDON
    # No terminal: the crash fell inside the intent's bracket.
    if outcome.verified:
        return ADOPTED
    if outcome.present:
        return DISCARDED_UNVERIFIED
    return DISCARDED_UNWRITTEN


def _check_directory_descriptor(dir_fd: int) -> os.stat_result:
    if type(dir_fd) is not int:
        raise RecoveryRefused(
            RECOVERY_DESCRIPTOR_REFUSED,
            f"the corpus directory is a descriptor; got {type(dir_fd).__name__}")
    try:
        info = os.fstat(dir_fd)
    except OSError as exc:
        raise RecoveryRefused(
            RECOVERY_DESCRIPTOR_REFUSED,
            "the corpus directory descriptor is not open") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise RecoveryRefused(
            RECOVERY_DESCRIPTOR_REFUSED,
            "the supplied descriptor does not name a directory")
    return info


def _member_object_ok(info: os.stat_result, directory_device: int) -> bool:
    """The step-8 object checks, as a boolean rather than a raise.

    A recovered member has exactly one name: the retained-temporary case is a
    live-path outcome that does not persist across a reopen, so two names here
    is a foreign hard link, not an accounted second name.
    """
    return (stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == CORPUS_FILE_MODE
            and info.st_dev == directory_device
            and info.st_nlink == 1)


def _read_member_image(final_fd: int) -> bytes:
    chunks = []
    received = 0
    while received < IQC_READBACK_LIMIT:
        block = os.read(final_fd, IQC_READBACK_LIMIT - received)
        if not block:
            break
        chunks.append(block)
        received += len(block)
    return b"".join(chunks)


def _header_geometry(image: bytes) -> Tuple[Mapping[str, Any], bytes]:
    """Locate and decode the header, returning it and the payload bytes.

    Framing is parsed exactly as the publisher parses it at step 8 -- magic,
    format version, header length -- so a recovered final and a freshly
    published one are read through the same shape. The header is decoded here
    (the publisher needs only its length) because recovery reads the window
    geometry the sequence reconstruction needs. Raises ``ValueError`` on a
    frame it cannot locate; the one caller reaches this only after the file
    digest already matched the intent, so an unframable image there is an
    intent self-contradiction, not an ordinary unverified final.
    """
    if len(image) < IQC_FRAMING_PREFIX_BYTES:
        raise ValueError("shorter than the framing prefix")
    if image[:len(IQC_MAGIC)] != IQC_MAGIC:
        raise ValueError("no .iqc magic")
    version = struct.unpack("<H", image[len(IQC_MAGIC):len(IQC_MAGIC) + 2])[0]
    if version != IQC_FORMAT_VERSION:
        raise ValueError(f"format version {version}")
    header_length = struct.unpack(
        "<I", image[len(IQC_MAGIC) + 2:IQC_FRAMING_PREFIX_BYTES])[0]
    if header_length > IQC_MAX_HEADER_BYTES:
        raise ValueError("header longer than the format allows")
    body_start = IQC_FRAMING_PREFIX_BYTES + header_length
    if len(image) < body_start:
        raise ValueError("shorter than the header it declares")
    header = json.loads(image[IQC_FRAMING_PREFIX_BYTES:body_start]
                        .decode("utf-8"))
    return header, image[body_start:]


def _verify_final(dir_fd: int, directory_device: int,
                  intent: Mapping[str, Any]) -> FinalOutcome:
    """Look for one intent's expected final and decide whether it verifies.

    The name is the intent's ``file_sha256`` (a member's name is its own file
    digest). The file-digest match is the pivot: bytes that hash to the intent's
    ``file_sha256`` are exactly the bytes the intent bound, so everything the
    header then says is fixed by that digest, and a disagreement between those
    bytes and the intent's own other fields is the intent contradicting itself
    (a refusal), never an ordinary unverified final. Bytes that do NOT hash to
    the name are simply the wrong file at that name: present, unverified,
    discarded.
    """
    file_sha256 = intent["file_sha256"]
    final_name = canonical_member_name(file_sha256)
    try:
        final_fd = os.open(final_name, os.O_RDONLY | os.O_NOFOLLOW,
                           dir_fd=dir_fd)
    except FileNotFoundError:
        return FinalOutcome(present=False, verified=False)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise RecoveryRefused(
                RECOVERY_MEMBER_OBJECT_REFUSED,
                f"{final_name} is a symbolic link where a member is named")
        raise RecoveryRefused(
            RECOVERY_MEMBER_OBJECT_REFUSED,
            f"{final_name} could not be opened: {exc.strerror}")
    try:
        info = os.fstat(final_fd)
        if not _member_object_ok(info, directory_device):
            raise RecoveryRefused(
                RECOVERY_MEMBER_OBJECT_REFUSED,
                f"{final_name} is not a proper member object (regular, this "
                f"uid, mode {CORPUS_FILE_MODE:04o}, one name, on device)")
        image = _read_member_image(final_fd)
    finally:
        os.close(final_fd)

    if hashlib.sha256(image).hexdigest() != file_sha256:
        return FinalOutcome(present=True, verified=False)

    # The bytes are the intent's file, so the header and payload are fixed:
    # anything below that disagrees is the intent contradicting itself.
    try:
        header, payload = _header_geometry(image)
    except ValueError as exc:
        raise RecoveryRefused(
            RECOVERY_INTENT_SELF_CONTRADICTORY,
            f"the bytes named by {final_name} are not a framed member: {exc}")
    if hashlib.sha256(payload).hexdigest() != intent["payload_sha256"]:
        raise RecoveryRefused(
            RECOVERY_INTENT_SELF_CONTRADICTORY,
            "the intent's payload digest disagrees with the file its own file "
            "digest names")
    first = header.get("first_sample_index")
    sample_count = header.get("sample_count")
    if (type(first) is not int or type(sample_count) is not int
            or first < 0 or sample_count <= 0):
        raise RecoveryRefused(
            RECOVERY_INTENT_SELF_CONTRADICTORY,
            "the member the intent names carries no usable sample geometry")
    if first != intent["first_sample_index"]:
        raise RecoveryRefused(
            RECOVERY_INTENT_SELF_CONTRADICTORY,
            f"the member begins at sample {first} and the intent that bound "
            f"its digest recorded {intent['first_sample_index']}")
    # The half-open convention rf_iq_ring counts in: the successor begins at or
    # after `first + sample_count`, so that is the predecessor's last index.
    return FinalOutcome(present=True, verified=True,
                        first_sample_index=first,
                        last_sample_index=first + sample_count)


def _unlink_if_present(dir_fd: int, name: str) -> None:
    """Remove a name if it is there, treating absence as success.

    A discarded intent's temporary is an orphan the crash left behind, and a
    published member's temporary is a redundant leftover the step-6 unlink
    would have removed had it run: either way the partial is not the member,
    and reconciliation frees the name. Absence is the ordinary case -- most
    intents have no partial -- so it is not an error.
    """
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        pass


def reconcile(dir_fd: int, *, journal: JournalState, entries: Tuple[str, ...],
              reserved_names: Tuple[str, ...],
              manifest_sha256: str) -> ReconciledMembership:
    """Reconcile the journal against the finals on disk, and act on it.

    ``entries`` is the corpus directory listing and ``reserved_names`` the
    corpus's own declarations (manifest, eligible, journal), which are not
    members. Every remaining entry must be a member named by exactly one intent,
    or it is a stray this module will not adopt. For each intent the expected
    final is verified and classified; the class's action -- adopt (append
    COMMIT) or discard (append ABANDON) -- is performed here, so the journal
    this returns beside is a faithful account of the finals, and every verified
    member carries its window geometry for sequence reconstruction.

    The order is deliberate: strays are refused before any terminal is written,
    so a directory holding something unaccountable leaves the journal untouched.
    """
    directory = _check_directory_descriptor(dir_fd)
    directory_device = directory.st_dev
    counts = {name: 0 for name in RECOVERY_CLASSIFICATIONS}
    members: Dict[str, list] = {stratum: [] for stratum in CAPTURED_STRATA}

    # Every member name an intent accounts for; a member-named file outside this
    # set is unaccounted, and any other stray is refused. Checked first, so no
    # terminal is appended if the directory holds anything unaccountable.
    accounted = {canonical_member_name(intent["file_sha256"])
                 for intent in journal.intents.values()}
    accounted_partials = {partial_member_name(intent["file_sha256"])
                          for intent in journal.intents.values()}
    reserved = frozenset(reserved_names)
    for entry in entries:
        if entry in reserved or entry in accounted or entry in accounted_partials:
            continue
        if is_partial_member_name(entry):
            raise RecoveryRefused(
                RECOVERY_STRAY_ENTRY,
                f"{entry!r} is a partial member sibling no intent accounts "
                "for; a temporary exists only under the intent that named it")
        if entry.endswith(".iqc"):
            raise RecoveryRefused(
                RECOVERY_UNACCOUNTED_FINAL,
                f"{entry!r} is a member name no journal intent records; a "
                "member the journal does not account for cannot be adopted")
        raise RecoveryRefused(
            RECOVERY_STRAY_ENTRY,
            f"{entry!r} is neither a declaration nor a member named by an "
            "intent; recovery adopts finals, not stray files")

    for window_id, intent in journal.intents.items():
        if intent["manifest_sha256"] != manifest_sha256:
            raise RecoveryRefused(
                RECOVERY_INTENT_MANIFEST_MISMATCH,
                f"intent for window {window_id!r} was bound under manifest "
                f"{intent['manifest_sha256']!r}, not this corpus's "
                f"{manifest_sha256!r}")
        terminal = journal.terminals.get(window_id)
        outcome = _verify_final(dir_fd, directory_device, intent)
        classification = _classify(terminal, outcome)
        counts[classification] += 1

        if classification == ADOPTED:
            append_commit(dir_fd, window_id)
        elif classification in _DISCARDING:
            append_abandon(dir_fd, window_id)

        if classification in _ADMITTING:
            members[intent["stratum"]].append(CommittedWindow(
                window_id=window_id,
                first_sample_index=outcome.first_sample_index,
                last_sample_index=outcome.last_sample_index,
                ring_lifetime_id=intent["ring_lifetime_id"]))
        # Free the temporary once the window's fate is settled: an orphan for a
        # discarded intent, a redundant leftover for a member. The final name,
        # if any, is a different inode and is untouched.
        _unlink_if_present(dir_fd, partial_member_name(intent["file_sha256"]))

    members_by_stratum = {
        stratum: tuple(sorted(windows, key=lambda w: w.first_sample_index))
        for stratum, windows in members.items()
    }
    return ReconciledMembership(members_by_stratum=members_by_stratum,
                                counts=dict(counts))


def corpus_ring_lifetime(reconciled: ReconciledMembership) -> Optional[str]:
    """The one ring lifetime a corpus's members share, or ``None`` if empty.

    §5.26 is single-lifetime capture: every member of a corpus was captured
    under one ring lifetime, across all strata, so a corpus that mixes lifetimes
    is not one this slice can have produced. A corpus with no committed members
    has no lifetime yet -- the reopen of a corpus that has captured nothing --
    and its sequences are created when capture first begins under a live ring.
    """
    lifetimes = {window.ring_lifetime_id
                 for windows in reconciled.members_by_stratum.values()
                 for window in windows}
    if not lifetimes:
        return None
    if len(lifetimes) > 1:
        raise RecoveryRefused(
            RECOVERY_MIXED_LIFETIME,
            "this corpus's members span more than one ring lifetime "
            f"({sorted(lifetimes)}); §5.26 captures a corpus under exactly one")
    (ring_lifetime_id,) = lifetimes
    return ring_lifetime_id


def reconstruct_sequences(reconciled: ReconciledMembership, *, corpus_id: str
                          ) -> Dict[str, CapturedStratumSequence]:
    """One reconstructed sequence per stratum, from reconciled membership.

    The bridge from this module to piece 1: the corpus's single ring lifetime is
    derived from its committed members, and each stratum's windows -- oldest
    first, already lifetime-checked at verification -- are handed to
    ``reconstruct_stratum_sequence`` bound to it. A corpus with no members yet
    has no lifetime, so there is nothing to reconstruct and the mapping is
    empty; the first capture under a live ring creates the sequences then. The
    ``corpus_id`` is the manifest's; the reopen path holds it and passes it in.
    """
    ring_lifetime_id = corpus_ring_lifetime(reconciled)
    if ring_lifetime_id is None:
        return {}
    return {
        stratum: reconstruct_stratum_sequence(
            corpus_id=corpus_id, stratum=stratum,
            ring_lifetime_id=ring_lifetime_id, committed_windows=windows)
        for stratum, windows in reconciled.members_by_stratum.items()
    }


def recovery_declaration() -> Dict[str, Any]:
    """Stable scalar diagnostics for a status surface. No record contents."""
    return {
        "schema": SCHEMA,
        "classifications": list(RECOVERY_CLASSIFICATIONS),
        "refusals": list(RECOVERY_REFUSALS),
    }
