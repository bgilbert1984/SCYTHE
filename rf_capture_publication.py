"""§5.20 steps 5-8: durability, no-replacement publication, and the readback.

3c-core, and deliberately **unreachable**. Nothing in this module is wired to
§5.25's typed entrypoints, and it must stay that way until journal sequencing
exists. A reachable publisher without intent and commit manufactures
*verified-but-unaccounted finals* --- precisely the state the membership journal
exists to classify rather than to normalise. Tests exercise the primitive
directly; that is the whole of its production surface.

What is here, and what is not:

  step 5   `fsync` the file                       -- the BYTES survive
  step 6   `link` the final, then `unlink` the temporary name
  step 7   `fsync` the directory                  -- the NAMES survive
  step 8   read the final back through a FRESH open of its name, and reconcile

Steps 5 and 7 are two different durability facts and neither implies the other,
which is why they are two refusals and two controls: one "durability" check
passes whenever either still works.

Not here: durable publication intent, the membership `commit`, counting, the two
abandon actions, sequence state, the clock, and the header. Intent and commit
bracket this primitive and belong to 3d; the header is written at step 4 and
belongs to admission.

Every failure in this module is **post-open**, so it raises `PublicationFailed`
rather than `CaptureRefused`. The two regimes differ in what they leave on disk,
and one exception type for both would let a caller decide an orphan does not
exist.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import struct
from typing import Any, Dict, Optional, Tuple

from rf_capture_admission import PublicationFailed
from rf_capture_format import (
    IQC_FRAMING_PREFIX_BYTES, IQC_MAGIC, IQC_MAX_HEADER_BYTES,
    IQC_FORMAT_VERSION, canonical_member_name,
)
from rf_corpus_namespace import CORPUS_FILE_MODE

SCHEMA = "scythe.capture-verified-final.v1"

# -- post-open failures, steps 5-8 ------------------------------------------
#
# Named by the step that produced them, because "publication failed" is not a
# fact anyone can act on: what a caller must know is whether a final name now
# exists, and these say so.
PUBLICATION_FILE_FSYNC_FAILED = "PUBLICATION_FILE_FSYNC_FAILED"
PUBLICATION_FINAL_NAME_EXISTS = "PUBLICATION_FINAL_NAME_EXISTS"
PUBLICATION_LINK_FAILED = "PUBLICATION_LINK_FAILED"
PUBLICATION_DIRECTORY_FSYNC_FAILED = "PUBLICATION_DIRECTORY_FSYNC_FAILED"
PUBLICATION_FINAL_NOT_READABLE = "PUBLICATION_FINAL_NOT_READABLE"
PUBLICATION_FINAL_IS_A_SYMLINK = "PUBLICATION_FINAL_IS_A_SYMLINK"
PUBLICATION_FINAL_OBJECT_REFUSED = "PUBLICATION_FINAL_OBJECT_REFUSED"
PUBLICATION_FINAL_NAME_COUNT_WRONG = "PUBLICATION_FINAL_NAME_COUNT_WRONG"
PUBLICATION_FRAMING_UNREADABLE = "PUBLICATION_FRAMING_UNREADABLE"
PUBLICATION_TRAILING_BYTES = "PUBLICATION_TRAILING_BYTES"
PUBLICATION_DECLARED_LENGTH_DISAGREES = "PUBLICATION_DECLARED_LENGTH_DISAGREES"
PUBLICATION_PAYLOAD_DIGEST_MISMATCH = "PUBLICATION_PAYLOAD_DIGEST_MISMATCH"
PUBLICATION_FILE_DIGEST_MISMATCH = "PUBLICATION_FILE_DIGEST_MISMATCH"
PUBLICATION_FILENAME_DISAGREES = "PUBLICATION_FILENAME_DISAGREES"
PUBLICATION_ARGUMENT_TYPE_WRONG = "PUBLICATION_ARGUMENT_TYPE_WRONG"
PUBLICATION_RESULT_UNCONSTRUCTIBLE = "PUBLICATION_RESULT_UNCONSTRUCTIBLE"

DURABILITY_FAILURES: Tuple[str, ...] = (
    PUBLICATION_FILE_FSYNC_FAILED, PUBLICATION_FINAL_NAME_EXISTS,
    PUBLICATION_LINK_FAILED, PUBLICATION_DIRECTORY_FSYNC_FAILED,
    PUBLICATION_FINAL_NOT_READABLE, PUBLICATION_FINAL_IS_A_SYMLINK,
    PUBLICATION_FINAL_OBJECT_REFUSED, PUBLICATION_FINAL_NAME_COUNT_WRONG,
    PUBLICATION_FRAMING_UNREADABLE, PUBLICATION_TRAILING_BYTES,
    PUBLICATION_DECLARED_LENGTH_DISAGREES,
    PUBLICATION_PAYLOAD_DIGEST_MISMATCH, PUBLICATION_FILE_DIGEST_MISMATCH,
    PUBLICATION_FILENAME_DISAGREES, PUBLICATION_ARGUMENT_TYPE_WRONG,
    PUBLICATION_RESULT_UNCONSTRUCTIBLE,
)

_MINT_KEY = object()


class VerifiedFinal:
    """Step 8's result, which a caller cannot construct.

    The mint-key construction `AttestedIQWindowScope` and
    `CorpusOwnershipScope` already use. A `bool`, a count or a path handed
    across this boundary would be a caller's claim; only the verification that
    actually read the final name can produce one of these.

    **It grants no filesystem authority.** No absolute path, no directory
    descriptor, no file descriptor, no header mapping, no caller-supplied
    timestamp and no mutable container --- a descriptor or path here would be a
    capability outliving the scope it came from, which is what §5.24 established
    a returned `memoryview` already was.

    Private state binds the verification and the expected intent identity for
    3d to reconcile against. No accessor exposes it.
    """

    __slots__ = ("_final_name", "_payload_sha256", "_file_sha256",
                 "_declared_payload_bytes", "_temporary_name_retained",
                 "_verified_by_step", "_intent_file_sha256")

    def __init__(self, mint_key: Any = None, *, final_name: str = "",
                 payload_sha256: str = "", file_sha256: str = "",
                 declared_payload_bytes: int = 0,
                 temporary_name_retained: bool = False,
                 verified_by_step: str = "",
                 intent_file_sha256: str = "") -> None:
        if mint_key is not _MINT_KEY:
            raise PublicationFailed(
                PUBLICATION_RESULT_UNCONSTRUCTIBLE,
                "a VerifiedFinal is minted by step 8's verification and by "
                "nothing else; a constructed one would be a caller's claim "
                "that a file was read back")
        self._final_name = final_name
        self._payload_sha256 = payload_sha256
        self._file_sha256 = file_sha256
        self._declared_payload_bytes = declared_payload_bytes
        self._temporary_name_retained = temporary_name_retained
        self._verified_by_step = verified_by_step
        self._intent_file_sha256 = intent_file_sha256

    @property
    def final_name(self) -> str:
        """The relative basename. Never a path, and never a directory."""
        return self._final_name

    @property
    def payload_sha256(self) -> str:
        return self._payload_sha256

    @property
    def file_sha256(self) -> str:
        return self._file_sha256

    @property
    def declared_payload_bytes(self) -> int:
        return self._declared_payload_bytes

    @property
    def temporary_name_retained(self) -> bool:
        """The temporary name still exists, because its `unlink` failed.

        Not a failure and not a second member: another name for the member's
        inode, left for the separately governed deletion path. Publication
        succeeded --- the final name exists and the bytes behind it verified.
        """
        return self._temporary_name_retained

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "final_name": self._final_name,
            "payload_sha256": self._payload_sha256,
            "file_sha256": self._file_sha256,
            "declared_payload_bytes": self._declared_payload_bytes,
            "temporary_name_retained": self._temporary_name_retained,
            "raw_iq_exposed": False,
            "publication_steps_completed": "5.20 STEPS 5-8",
        }


def _read_to_end(fd: int, limit: int) -> bytes:
    """Accumulating, because `os.read` may return fewer bytes than asked for.

    A bare `os.read` here would read a short chunk of an intact file and then
    disagree with every digest over it. The membership journal had exactly this
    defect at one of three call sites, and a fragmented-read test is what found
    it; the same mistake is available one subsystem over.

    `limit` is one byte beyond what the file should hold, so a file that is too
    long is detected rather than silently truncated into agreement.
    """
    chunks = []
    received = 0
    while received < limit:
        block = os.read(fd, limit - received)
        if not block:
            break
        chunks.append(block)
        received += len(block)
    return b"".join(chunks)


def _check_member_object(fd: int, directory_device: int,
                         expected_names: int) -> None:
    """Checks on the descriptor the final NAME reached, never on a path.

    `expected_names` is the link count the `unlink` outcome permits: one when
    the temporary name was removed, two while it is retained. A third name is a
    foreign hard link to a corpus member and refuses --- the retained temporary
    is accounted for, and anything beyond it is not.
    """
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise PublicationFailed(
            PUBLICATION_FINAL_OBJECT_REFUSED,
            "the final name does not reach a regular file")
    if info.st_uid != os.getuid():
        raise PublicationFailed(
            PUBLICATION_FINAL_OBJECT_REFUSED,
            f"the final is owned by uid {info.st_uid}, this process is "
            f"{os.getuid()}")
    if stat.S_IMODE(info.st_mode) != CORPUS_FILE_MODE:
        raise PublicationFailed(
            PUBLICATION_FINAL_OBJECT_REFUSED,
            f"the final is mode {stat.S_IMODE(info.st_mode):04o}, not "
            f"{CORPUS_FILE_MODE:04o}")
    if info.st_dev != directory_device:
        raise PublicationFailed(
            PUBLICATION_FINAL_OBJECT_REFUSED,
            "the final is on a different device from its corpus directory")
    if info.st_nlink != expected_names:
        raise PublicationFailed(
            PUBLICATION_FINAL_NAME_COUNT_WRONG,
            f"the final's inode has {info.st_nlink} names and this "
            f"publication accounts for {expected_names}")


def _parse_framing(image: bytes) -> int:
    """Return the header length, having located it rather than scanned for it."""
    if len(image) < IQC_FRAMING_PREFIX_BYTES:
        raise PublicationFailed(
            PUBLICATION_FRAMING_UNREADABLE,
            f"the final holds {len(image)} bytes, fewer than the "
            f"{IQC_FRAMING_PREFIX_BYTES}-byte framing prefix")
    if image[:len(IQC_MAGIC)] != IQC_MAGIC:
        raise PublicationFailed(
            PUBLICATION_FRAMING_UNREADABLE,
            "the final does not begin with the .iqc magic")
    version = struct.unpack("<H", image[len(IQC_MAGIC):len(IQC_MAGIC) + 2])[0]
    if version != IQC_FORMAT_VERSION:
        raise PublicationFailed(
            PUBLICATION_FRAMING_UNREADABLE,
            f"the final declares format version {version}, not "
            f"{IQC_FORMAT_VERSION}")
    header_length = struct.unpack(
        "<I", image[len(IQC_MAGIC) + 2:IQC_FRAMING_PREFIX_BYTES])[0]
    if header_length > IQC_MAX_HEADER_BYTES:
        raise PublicationFailed(
            PUBLICATION_FRAMING_UNREADABLE,
            f"the final declares a {header_length}-byte header and the format "
            f"allows {IQC_MAX_HEADER_BYTES}")
    if len(image) < IQC_FRAMING_PREFIX_BYTES + header_length:
        raise PublicationFailed(
            PUBLICATION_FRAMING_UNREADABLE,
            "the final is shorter than the header it declares")
    return header_length


def _publish_and_verify(*, fd: Any, dir_fd: Any, temporary_name: Any,
                        publication: Any, intent_file_sha256: Any,
                        ) -> VerifiedFinal:
    """§5.20 steps 5-8 over an already-written temporary. **Private.**

    `fd` is the descriptor step 3 created and step 4 wrote; this closes nothing
    it did not open, so the caller still owns it. `intent_file_sha256` is the
    identity 3d's durable intent bound *before* creation, which step 8
    recomputes from the file it reads back rather than trusting.
    """
    for name, value, kind in (("fd", fd, int), ("dir_fd", dir_fd, int),
                              ("temporary_name", temporary_name, str),
                              ("intent_file_sha256", intent_file_sha256, str)):
        if type(value) is not kind:
            raise PublicationFailed(
                PUBLICATION_ARGUMENT_TYPE_WRONG,
                f"{name} is {type(value).__name__}, not {kind.__name__}")

    # ---- step 5: the BYTES survive ---------------------------------------
    try:
        os.fsync(fd)
    except OSError as exc:
        raise PublicationFailed(
            PUBLICATION_FILE_FSYNC_FAILED,
            f"the temporary's bytes were not made durable: {exc.strerror}"
        ) from exc

    final_name = canonical_member_name(intent_file_sha256)

    # ---- step 6: publish WITHOUT replacement ------------------------------
    # `os.rename` was measured to replace an existing name silently, and Python
    # exposes neither `renameat2` nor `RENAME_NOREPLACE`. `os.link` refuses with
    # EEXIST, which is the whole reason the step names this primitive.
    try:
        os.link(temporary_name, final_name,
                src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise PublicationFailed(
                PUBLICATION_FINAL_NAME_EXISTS,
                f"{final_name} already exists; publication never replaces"
            ) from exc
        raise PublicationFailed(
            PUBLICATION_LINK_FAILED,
            f"the final name could not be created: {exc.strerror}") from exc

    # The final name now exists, so THIS IS THE POINT OF NO ABANDON. Nothing
    # below removes or replaces it, and no failure below may be reported as an
    # abandonment: the member is reachable and its bytes are the ones step 5
    # made durable. A failed `unlink` leaves a second NAME for one inode, not a
    # second member, and treating that as a publication failure would discard a
    # verifiable member.
    temporary_name_retained = False
    try:
        os.unlink(temporary_name, dir_fd=dir_fd)
    except OSError:
        temporary_name_retained = True

    # ---- step 7: the NAMES survive ---------------------------------------
    # After both names have settled, so the directory entry this `fsync`
    # persists is the final state and not an intermediate one.
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        raise PublicationFailed(
            PUBLICATION_DIRECTORY_FSYNC_FAILED,
            f"the namespace's names were not made durable: {exc.strerror}"
        ) from exc

    # ---- step 8: read the FINAL NAME back --------------------------------
    # A fresh open of the name, not the descriptor already held. Verifying the
    # held descriptor proves the bytes were written and says nothing about which
    # name reaches them, and filename agreement is exactly what is required.
    directory_device = os.fstat(dir_fd).st_dev
    try:
        final_fd = os.open(final_name, os.O_RDONLY | os.O_NOFOLLOW,
                           dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PublicationFailed(
                PUBLICATION_FINAL_IS_A_SYMLINK,
                f"{final_name} is a symbolic link") from exc
        raise PublicationFailed(
            PUBLICATION_FINAL_NOT_READABLE,
            f"{final_name} could not be reopened: {exc.strerror}") from exc
    try:
        _check_member_object(
            final_fd, directory_device,
            expected_names=2 if temporary_name_retained else 1)
        declared = int(publication.declared_payload_bytes)
        limit = (IQC_FRAMING_PREFIX_BYTES + IQC_MAX_HEADER_BYTES
                 + declared + 1)
        image = _read_to_end(final_fd, limit)
    finally:
        os.close(final_fd)

    header_length = _parse_framing(image)
    body_start = IQC_FRAMING_PREFIX_BYTES + header_length
    payload = image[body_start:]

    # Trailing bytes are their own refusal. The framing puts EOF immediately
    # after the payload, which is what makes one digest over one prefix enough;
    # a longer file is a different file that agrees on its first bytes.
    if len(payload) != declared:
        if len(payload) > declared:
            raise PublicationFailed(
                PUBLICATION_TRAILING_BYTES,
                f"the final carries {len(payload) - declared} bytes beyond the "
                f"{declared} it declares")
        raise PublicationFailed(
            PUBLICATION_DECLARED_LENGTH_DISAGREES,
            f"the final carries {len(payload)} payload bytes and declares "
            f"{declared}")

    payload_sha256 = hashlib.sha256(payload).hexdigest()
    file_sha256 = hashlib.sha256(image).hexdigest()

    if payload_sha256 != publication.payload_sha256:
        raise PublicationFailed(
            PUBLICATION_PAYLOAD_DIGEST_MISMATCH,
            "the payload read back does not digest to the value the header "
            "carries")
    if file_sha256 != intent_file_sha256:
        raise PublicationFailed(
            PUBLICATION_FILE_DIGEST_MISMATCH,
            "the file read back does not digest to the value the publication "
            "intent bound")
    # Separate from the digest agreement above: that one asks whether the bytes
    # are the intended bytes, and this asks whether the NAME they were reached
    # through is the one those bytes derive. A mutation that fixes the name to a
    # constant satisfies the first and fails this.
    if final_name != canonical_member_name(file_sha256):
        raise PublicationFailed(
            PUBLICATION_FILENAME_DISAGREES,
            f"the final was reached as {final_name}, which is not the "
            f"canonical name of the bytes it holds")

    return VerifiedFinal(
        _MINT_KEY,
        final_name=final_name,
        payload_sha256=payload_sha256,
        file_sha256=file_sha256,
        declared_payload_bytes=declared,
        temporary_name_retained=temporary_name_retained,
        verified_by_step="5.20 STEP 8",
        intent_file_sha256=intent_file_sha256,
    )
