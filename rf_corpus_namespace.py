"""§5.26: the acts that make a corpus's terms a fact of its namespace.

`rf_corpus_manifest` is the artefact -- framing, canonical form, required
fields, reader. This is the **acts**: resolving the one authorised namespace,
holding it exclusively, creating a corpus exactly once, and reopening one.

`create_corpus_namespace` and `open_corpus_namespace` are two functions and not
one with a flag, because *"rejects a non-empty namespace"* is correct for
creation and false for reopening. A flag would make one of those two behaviours
a caller's choice.

**This slice is bounded.** §5.26 was accepted whole; the operator authorised
corpus creation and the manifest. Not here, and refused rather than omitted
where a caller could reach for them: the membership journal, the publisher,
§5.20 steps 3-8, `ring_lifetime_id`, and the rewiring of §5.25's entrypoints.

**No production corpus is creatable by this code.** §5.26's acceptance
authorises no production corpus creation, so `create_corpus_namespace` refuses
when asked to work in the production namespace, by a check rather than by a
convention. Lifting that is a later explicit act; it is not an edit someone
makes because it is in the way.

**And no test can reach the production namespace**, because a root that
resolves inside it is refused. The operator's temporary-directory authorisation
says tests must not read or write the production root, and that is enforced here
rather than left to whoever writes the next test.
"""

from __future__ import annotations

import errno
import fcntl
import os
import stat
from typing import Any, Dict, Optional, Tuple

from rf_corpus_manifest import (
    IQM_FRAMING_PREFIX_BYTES, IQM_MAX_BODY_BYTES, MANIFEST_ALREADY_PRESENT,
    ManifestRefused, frame_manifest, manifest_body, parse_manifest,
)
from rf_promotion_envelope import declaration_digest
from rf_validation_manifest import PromotionCorpusLock, STRATA_DEFINITION_REVISION

SCHEMA = "scythe.rf-corpus-namespace.v1"

# §5.20's directory row, pinned. Resolved before use and disjoint from the
# repository, ledger, derived-evidence and observation namespaces.
PRODUCTION_CORPUS_ROOT = "/home/spectrcyde/scythe-validation-corpus/captured-v1"

MANIFEST_NAME = "manifest.iqm"

# §5.20's permissions row.
CORPUS_DIRECTORY_MODE = 0o700
CORPUS_FILE_MODE = 0o600

# Read in one go with a bound, because the size is bounded by the format and a
# reader that streams an unbounded file is the allocation the bound exists to
# prevent.
MANIFEST_READ_LIMIT = IQM_FRAMING_PREFIX_BYTES + IQM_MAX_BODY_BYTES + 1

# -- refusals: the acts' side ----------------------------------------------
#
# Named disjointly from `rf_corpus_manifest`'s: a code there is about bytes, a
# code here is about a directory, a descriptor, or who holds it.
NAMESPACE_PRODUCTION_NOT_AUTHORISED = "NAMESPACE_PRODUCTION_NOT_AUTHORISED"
NAMESPACE_ROOT_INSIDE_PRODUCTION = "NAMESPACE_ROOT_INSIDE_PRODUCTION"
NAMESPACE_NOT_A_DIRECTORY = "NAMESPACE_NOT_A_DIRECTORY"
NAMESPACE_HOLDS_ENTRIES = "NAMESPACE_HOLDS_ENTRIES"
NAMESPACE_OWNER_MISMATCH = "NAMESPACE_OWNER_MISMATCH"
NAMESPACE_MODE_PERMISSIVE = "NAMESPACE_MODE_PERMISSIVE"
NAMESPACE_SYMLINK_REFUSED = "NAMESPACE_SYMLINK_REFUSED"
NAMESPACE_DEVICE_MISMATCH = "NAMESPACE_DEVICE_MISMATCH"
NAMESPACE_HARD_LINKED = "NAMESPACE_HARD_LINKED"
NAMESPACE_OWNED_ELSEWHERE = "NAMESPACE_OWNED_ELSEWHERE"
NAMESPACE_RECOVERY_UNBUILT = "NAMESPACE_RECOVERY_UNBUILT"
NAMESPACE_SCOPE_RELEASED = "NAMESPACE_SCOPE_RELEASED"
NAMESPACE_REFUSALS: Tuple[str, ...] = (
    NAMESPACE_PRODUCTION_NOT_AUTHORISED, NAMESPACE_ROOT_INSIDE_PRODUCTION,
    NAMESPACE_NOT_A_DIRECTORY, NAMESPACE_HOLDS_ENTRIES,
    NAMESPACE_OWNER_MISMATCH, NAMESPACE_MODE_PERMISSIVE,
    NAMESPACE_SYMLINK_REFUSED, NAMESPACE_DEVICE_MISMATCH,
    NAMESPACE_HARD_LINKED, NAMESPACE_OWNED_ELSEWHERE,
    NAMESPACE_RECOVERY_UNBUILT, NAMESPACE_SCOPE_RELEASED,
)


class NamespaceRefused(RuntimeError):
    """A namespace that may not be held, or an act that may not happen.

    Raises rather than returning a result, for the reason §5.24 gave about
    attestation: `with create_corpus_namespace(...) as corpus:` has to be
    impossible to enter when the act was refused, and a falsy object entered by
    a caller who did not check is the hole again.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- resolution -------------------------------------------------------------


def production_corpus_root() -> str:
    """The one authorised root. No parameters: a root nobody can steer."""
    return os.path.realpath(PRODUCTION_CORPUS_ROOT)


def _inside(path: str, root: str) -> bool:
    """Containment over path components, never a bare prefix match.

    `captured-v1-scratch` is not inside `captured-v1`, and a check written with
    `startswith` alone says it is.
    """
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _fully_resolved(path: str) -> str:
    """`realpath`, and then the nearest existing ancestor resolved as well.

    `realpath` on a path whose tail does not exist still resolves the symlinks
    in the part that does, so a symlinked parent is caught. This walks up to
    the nearest existing ancestor anyway and re-resolves from there, because
    the containment question is about **where the path will land**, and a
    directory that does not exist yet is exactly the case a creation call is
    making.
    """
    resolved = os.path.realpath(path)
    ancestor = resolved
    while ancestor and not os.path.exists(ancestor):
        parent = os.path.dirname(ancestor)
        if parent == ancestor:
            break
        ancestor = parent
    if ancestor and os.path.exists(ancestor):
        tail = os.path.relpath(resolved, ancestor)
        resolved = os.path.normpath(
            os.path.join(os.path.realpath(ancestor), tail))
    return resolved


def resolve_corpus_directory(corpus_id: str, *, root: Optional[str]) -> str:
    """The one resolver. `root=None` means production.

    `root` is a **declared test seam**, the same shape
    `scythe_position_act._first_party_closure` already uses and for the same
    reason: the alternative was a test that wrote into a real namespace and
    tidied up afterwards, which is a hazard whether or not it passes.

    A seam is not a hole. A supplied root that resolves inside the production
    namespace is refused, so the operator's condition -- no test reads or writes
    the production root -- is a check rather than a habit.
    """
    name = str(corpus_id)
    if not name or os.sep in name or name in (".", ".."):
        raise NamespaceRefused(
            NAMESPACE_NOT_A_DIRECTORY,
            f"{corpus_id!r} is not a single directory name")
    if root is None:
        return os.path.join(production_corpus_root(), name)
    resolved = _fully_resolved(str(root))
    if _inside(resolved, production_corpus_root()):
        raise NamespaceRefused(
            NAMESPACE_ROOT_INSIDE_PRODUCTION,
            "a supplied root inside the production namespace is the production "
            "namespace with a longer path")
    return os.path.join(resolved, name)


# -- the opaque scope -------------------------------------------------------


class _ScopeState:
    """Held by the scope and reachable through no public attribute."""

    __slots__ = ("dir_fd", "device", "body", "manifest_sha256", "path",
                 "released")

    def __init__(self, dir_fd, device, body, digest, path) -> None:
        self.dir_fd = dir_fd
        self.device = device
        self.body = body
        self.manifest_sha256 = digest
        self.path = path
        self.released = False


class CorpusOwnershipScope:
    """A held corpus namespace, and the terms it recorded about itself.

    **The descriptor and the path do not leave.** §5.24 spent a slice
    establishing that a returned `memoryview` is a capability that outlives its
    scope; a returned directory descriptor or path is the same thing one layer
    out, and would let a second writer reach creation without passing anything.
    There is no public accessor for either, and the static call-site check
    refuses a production reference to the private one.

    **What it does not yet carry**: per-stratum sequence state, the membership
    journal, a clock provider and typed `InstrumentChainEnvelope` /
    `CapturePlanDeclaration` objects. This slice verifies the stored
    declarations by **recomputing their digests from the stored form**; turning
    them back into objects is owed by the slice that rewires admission, and is
    refused here rather than half-done.
    """

    __slots__ = ("_state",)

    def __init__(self, state: _ScopeState, mint_key: Any = None) -> None:
        if mint_key is not _MINT_KEY:
            raise NamespaceRefused(
                NAMESPACE_SCOPE_RELEASED,
                "a CorpusOwnershipScope is minted by create_corpus_namespace "
                "or open_corpus_namespace")
        self._state = state

    def _live(self) -> _ScopeState:
        state = self._state
        if state is None or state.released:
            raise NamespaceRefused(
                NAMESPACE_SCOPE_RELEASED,
                "the corpus ownership scope has been released; the namespace "
                "is no longer held and nothing may be read through it")
        return state

    # -- what it will answer ------------------------------------------------

    @property
    def corpus_id(self) -> str:
        return self._live().body["corpus_id"]

    @property
    def manifest_sha256(self) -> str:
        """Canonical identity of the terms this corpus recorded.

        §5.26 binds this in the membership journal's intent records. The journal
        is unbuilt, so today it is computed on read and reported -- which is
        canonical identity and corruption detection, and is not authority.
        """
        return self._live().manifest_sha256

    def _verified_body(self) -> Dict[str, Any]:
        """**Restricted.** The verified manifest body, as a copy.

        Not a public accessor, and not caller-consumable authority. A caller
        holding these dictionaries would be holding the envelope and the
        capture plan as **mappings**, and admission consuming a mapping is the
        caller-supplied set §5.25 refused wearing a different shape -- the same
        defect that produced §5.26, one layer further in.

        What must happen instead, and is **not** built here: one scope factory
        reconstructs the exact nominal `InstrumentChainEnvelope`,
        `CapturePlanDeclaration`, `PromotionCorpusLock` and retention objects
        **once**, validates them, and binds them in this opaque state. Admission
        then consumes only that bound state. Reconstructing types
        opportunistically inside each entrypoint would put the same fragile
        step in several places and let them drift.
        """
        return dict(self._live().body)

    @property
    def opened_at(self) -> float:
        return float(self._live().body["opened_at"])

    @property
    def delete_not_after(self) -> float:
        return float(self._live().body["delete_not_after"])

    def _directory_fd(self) -> int:
        """**Restricted.** The held descriptor, for this module's acts only.

        Python privacy enforces nothing; what is enforceable, and what §5.24
        narrowed the equivalent claim to, is that there is no public accessor
        and that every production reference is statically restricted.
        """
        return self._live().dir_fd

    def release(self) -> None:
        state = self._state
        if state is None or state.released:
            return
        state.released = True
        try:
            fcntl.flock(state.dir_fd, fcntl.LOCK_UN)
        finally:
            os.close(state.dir_fd)
            state.dir_fd = -1

    def __enter__(self) -> "CorpusOwnershipScope":
        self._live()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False

    def to_dict(self) -> Dict[str, Any]:
        state = self._live()
        return {
            "schema": SCHEMA,
            "corpus_id": state.body["corpus_id"],
            "manifest_sha256": state.manifest_sha256,
            "held": True,
            "path_exposed": False,
            "descriptor_exposed": False,
            "sequence_state": "NOT BUILT",
            "membership_journal": "NOT BUILT",
            "publisher": "NOT BUILT",
        }

    def __repr__(self) -> str:
        state = self._state
        held = state is not None and not state.released
        return (f"CorpusOwnershipScope(corpus_id="
                f"{state.body['corpus_id']!r}, held={held}, "
                "path=<withheld>, fd=<withheld>)")


_MINT_KEY = object()


# -- the opened-object checks ----------------------------------------------


def _check_directory(dir_fd: int, path: str) -> int:
    """Checks on the **descriptor**, never on the path that reached it.

    A check on a path is a check on what the path meant a moment ago -- the
    same TOCTOU reading §5.24 recorded about verdicts over mutable objects, one
    subsystem over. `fstat` answers about the object actually held.
    """
    info = os.fstat(dir_fd)
    if not stat.S_ISDIR(info.st_mode):
        raise NamespaceRefused(NAMESPACE_NOT_A_DIRECTORY,
                               f"{path} is not a directory")
    if info.st_uid != os.getuid():
        raise NamespaceRefused(
            NAMESPACE_OWNER_MISMATCH,
            f"owned by uid {info.st_uid}, this process is {os.getuid()}")
    if stat.S_IMODE(info.st_mode) != CORPUS_DIRECTORY_MODE:
        raise NamespaceRefused(
            NAMESPACE_MODE_PERMISSIVE,
            f"mode {stat.S_IMODE(info.st_mode):04o}, not "
            f"{CORPUS_DIRECTORY_MODE:04o}")
    return info.st_dev


def _check_manifest_file(fd: int, device: int) -> None:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise NamespaceRefused(NAMESPACE_NOT_A_DIRECTORY,
                               "the manifest is not a regular file")
    if info.st_nlink != 1:
        raise NamespaceRefused(
            NAMESPACE_HARD_LINKED,
            f"the manifest has {info.st_nlink} names; a corpus's terms are not "
            "shared with another name")
    if info.st_uid != os.getuid():
        raise NamespaceRefused(
            NAMESPACE_OWNER_MISMATCH,
            f"the manifest is owned by uid {info.st_uid}")
    if stat.S_IMODE(info.st_mode) != CORPUS_FILE_MODE:
        raise NamespaceRefused(
            NAMESPACE_MODE_PERMISSIVE,
            f"the manifest is mode {stat.S_IMODE(info.st_mode):04o}, not "
            f"{CORPUS_FILE_MODE:04o}")
    if info.st_dev != device:
        raise NamespaceRefused(
            NAMESPACE_DEVICE_MISMATCH,
            "the manifest is on a different device from its corpus directory")


def _open_directory(path: str) -> int:
    """`O_NOFOLLOW`, so a symlinked namespace is refused rather than followed.

    The refusal comes from the open. The **classification** afterwards does
    look at the path, and that is sound only because the decision has already
    been made: nothing is opened, held or trusted on the strength of it, and it
    exists so an operator reads "this is a symlink" instead of "this is not a
    directory". Measured rather than assumed: Linux answers `O_DIRECTORY |
    O_NOFOLLOW` on a symlink with **ENOTDIR**, not the ELOOP POSIX describes,
    so an errno-only classification would report the wrong cause on the one
    platform §5.20 declares.
    """
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        # Classified by **inspecting the object**, not by reading the errno.
        # POSIX says O_NOFOLLOW gives ELOOP; Linux with O_DIRECTORY gives
        # ENOTDIR for the same symlink. An errno table would have reported the
        # wrong cause on the one platform §5.20 declares, and the next platform
        # is another table nobody has.
        try:
            info = os.lstat(path)
        except OSError:
            raise exc from None
        if stat.S_ISLNK(info.st_mode):
            raise NamespaceRefused(
                NAMESPACE_SYMLINK_REFUSED,
                f"{path} is a symbolic link") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise NamespaceRefused(
                NAMESPACE_NOT_A_DIRECTORY,
                f"{path} is not a directory") from exc
        raise


def _hold_exclusively(dir_fd: int, path: str) -> None:
    """OS-backed, not an in-process registry.

    Two scopes on one corpus means two sequence states and a cap counted twice,
    and the case that matters is a **second process**, which an in-process
    registry cannot see. `flock` on the directory descriptor holds without
    creating a file, so the namespace stays empty for creation's own check.
    """
    try:
        fcntl.flock(dir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise NamespaceRefused(
            NAMESPACE_OWNED_ELSEWHERE,
            f"{path} is held by another owner; one writer, §5.20") from exc


def _between_directory_and_manifest() -> None:
    """An internal seam, called once between creating the directory and
    exclusively creating the manifest.

    It exists so a test can interpose a **competing writer** in the one window
    where `O_EXCL` is load-bearing: `mkdir` has already guaranteed the
    directory was new, so on any ordinary path the manifest cannot exist, and a
    control that removed `O_EXCL` would fail nothing. The race is the only
    thing it defends, so the race is what has to be reproduced.

    Deliberately **not** a parameter. A caller-supplied hook would be exactly
    the injectable authority §5.26 exists to remove; this is module-internal
    and a test reaches it by patching the module, not by calling an API.
    """
    return None


def _read_manifest(dir_fd: int, device: int) -> Tuple[Dict[str, Any], str]:
    try:
        fd = os.open(MANIFEST_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise ManifestRefused(
                "MANIFEST_NOT_FOUND",
                f"no {MANIFEST_NAME} in this namespace") from exc
        if exc.errno == errno.ELOOP:
            raise NamespaceRefused(
                NAMESPACE_SYMLINK_REFUSED,
                f"{MANIFEST_NAME} is a symbolic link") from exc
        raise
    try:
        _check_manifest_file(fd, device)
        framed = os.read(fd, MANIFEST_READ_LIMIT)
    finally:
        os.close(fd)
    return parse_manifest(framed)


def _check_declarations(body: Dict[str, Any]) -> None:
    """The stored declarations must digest to the values frozen beside them.

    Recomputed through `rf_promotion_envelope.declaration_digest`, the one
    implementation the envelope and the capture plan use, so this is the same
    rule and not a second copy of it.

    It establishes that the manifest does not disagree with itself. It does not
    establish who wrote it -- a namespace owner who rewrote the declarations and
    their digests together produces a manifest that agrees perfectly, and no
    arrangement of files in one writable directory changes that.
    """
    for declaration, frozen in (("envelope", "envelope_digest"),
                                ("capture_plan", "capture_plan_digest")):
        recomputed = declaration_digest(body[declaration])
        if recomputed != body[frozen]:
            raise ManifestRefused(
                "MANIFEST_DECLARATION_DISAGREES",
                f"{frozen} is {body[frozen]} and {declaration} digests to "
                f"{recomputed}")
    if body["strata_definition_revision"] != STRATA_DEFINITION_REVISION:
        raise ManifestRefused(
            "MANIFEST_DECLARATION_DISAGREES",
            f"the corpus was opened under {body['strata_definition_revision']} "
            f"and the strata now mean {STRATA_DEFINITION_REVISION}")


# -- the two acts -----------------------------------------------------------


def create_corpus_namespace(*, corpus_id: str, lock: Any, retention: Any,
                            root: Optional[str] = None
                            ) -> CorpusOwnershipScope:
    """Open a corpus for the first time. A **new, empty** namespace only.

    §5.26's sequence, in order::

        resolve -> create the directory 0700 -> hold exclusively
        -> validate empty -> O_CREAT|O_EXCL the manifest 0600
        -> write -> fsync the manifest -> fsync the directory
        -> reopen and verify -> mint the scope

    The reopen is not decoration. A manifest nobody has read once is a corpus
    whose terms have never been shown to parse, and every later act rests on
    them.
    """
    if root is None:
        raise NamespaceRefused(
            NAMESPACE_PRODUCTION_NOT_AUTHORISED,
            "§5.26's acceptance authorises no production corpus creation. "
            "Creating one is a later explicit operator act, not an edit to "
            "this check")
    if type(lock) is not PromotionCorpusLock:
        raise NamespaceRefused(
            NAMESPACE_NOT_A_DIRECTORY,
            "a corpus is created against a frozen PromotionCorpusLock; got "
            f"{type(lock).__name__}")
    path = resolve_corpus_directory(corpus_id, root=root)
    if lock.corpus_id != str(corpus_id):
        raise NamespaceRefused(
            NAMESPACE_NOT_A_DIRECTORY,
            f"the lock names corpus {lock.corpus_id!r} and the namespace is "
            f"{corpus_id!r}")

    body = manifest_body(lock=lock, retention=retention)
    framed = frame_manifest(body)          # refuses before anything is created

    try:
        os.mkdir(path, CORPUS_DIRECTORY_MODE)
    except FileExistsError as exc:
        raise NamespaceRefused(
            NAMESPACE_HOLDS_ENTRIES,
            f"{path} already exists; creation is for a new namespace and "
            "reopening is open_corpus_namespace") from exc
    # mkdir's mode is masked by umask, so it is set explicitly rather than
    # assumed -- a 0755 corpus directory would satisfy nothing §5.20 asked for.
    os.chmod(path, CORPUS_DIRECTORY_MODE)

    dir_fd = _open_directory(path)
    try:
        device = _check_directory(dir_fd, path)
        _hold_exclusively(dir_fd, path)
        entries = sorted(os.listdir(dir_fd))
        if entries:
            raise NamespaceRefused(
                NAMESPACE_HOLDS_ENTRIES,
                f"{len(entries)} entr(y/ies) already here: {entries[:4]}")
        _between_directory_and_manifest()
        try:
            fd = os.open(MANIFEST_NAME,
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                         CORPUS_FILE_MODE, dir_fd=dir_fd)
        except FileExistsError as exc:
            # Someone created it between `mkdir` and here. `O_EXCL` turns that
            # into a refusal instead of a silent overwrite of a corpus's terms,
            # and it is reported as the declared code rather than as a bare
            # FileExistsError a caller might mistake for its own bug.
            raise ManifestRefused(
                MANIFEST_ALREADY_PRESENT,
                f"{MANIFEST_NAME} appeared after this corpus's directory was "
                "created; another writer is in this namespace") from exc
        try:
            written, total = 0, len(framed)
            while written < total:
                wrote = os.write(fd, framed[written:])
                if wrote <= 0:
                    raise NamespaceRefused(
                        NAMESPACE_NOT_A_DIRECTORY,
                        f"wrote {written} of {total} manifest bytes and stalled")
                written += wrote
            os.fsync(fd)                    # the bytes survive
        finally:
            os.close(fd)
        os.fsync(dir_fd)                    # the NAME survives; not the same fact
        read_body, digest = _read_manifest(dir_fd, device)
        _check_declarations(read_body)
        if read_body != body:
            raise ManifestRefused(
                "MANIFEST_NOT_CANONICAL",
                "the manifest read back is not the manifest written")
        state = _ScopeState(dir_fd, device, read_body, digest, path)
        return CorpusOwnershipScope(state, _MINT_KEY)
    except BaseException:
        os.close(dir_fd)
        raise


def open_corpus_namespace(*, corpus_id: str, root: Optional[str] = None
                          ) -> CorpusOwnershipScope:
    """Reopen an existing corpus. **Requires** a manifest rather than refusing one.

    Not `create_corpus_namespace` with a flag: *"rejects a non-empty
    namespace"* is right for creation and false here, and a flag would make
    which of the two applies a caller's choice.

    **Membership recovery is unbuilt**, so a namespace holding anything besides
    the manifest is refused rather than reopened with an unexamined member
    count. That is the whole of what §5.26's recovery would examine, and
    reopening over it silently would be the inert admission entry 11 was drained
    for closing.
    """
    if root is None:
        raise NamespaceRefused(
            NAMESPACE_PRODUCTION_NOT_AUTHORISED,
            "§5.26's acceptance authorises no production corpus access. No "
            "production corpus exists to open")
    path = resolve_corpus_directory(corpus_id, root=root)
    dir_fd = _open_directory(path)
    try:
        device = _check_directory(dir_fd, path)
        _hold_exclusively(dir_fd, path)
        entries = sorted(os.listdir(dir_fd))
        unexpected = [entry for entry in entries if entry != MANIFEST_NAME]
        if unexpected:
            raise NamespaceRefused(
                NAMESPACE_RECOVERY_UNBUILT,
                f"{len(unexpected)} entr(y/ies) besides the manifest, and "
                "membership recovery is not built. §5.26 requires every "
                "candidate final to be parsed, validated and reconciled "
                f"against the journal before a corpus is reopened: {unexpected[:4]}")
        body, digest = _read_manifest(dir_fd, device)
        _check_declarations(body)
        state = _ScopeState(dir_fd, device, body, digest, path)
        return CorpusOwnershipScope(state, _MINT_KEY)
    except BaseException:
        os.close(dir_fd)
        raise


def namespace_status() -> Dict[str, Any]:
    """What this slice is, for a status surface. No corpus, no filesystem."""
    return {
        "schema": SCHEMA,
        "production_root": PRODUCTION_CORPUS_ROOT,
        "production_creation_authorised": False,
        "manifest_name": MANIFEST_NAME,
        "built": ["THE MANIFEST", "CORPUS CREATION", "CORPUS REOPENING"],
        "not_built": ["MEMBERSHIP JOURNAL", "SEQUENCE STATE", "PUBLISHER",
                      "RING LIFETIME IDENTITY", "TYPED LOCK RECONSTRUCTION",
                      "ADMISSION CONSUMPTION OF THIS SCOPE", "CLOCK PROVIDER"],
        # Stated as its own key rather than left to be read off the list
        # above: this scope is not yet consumed by anything, so nothing is
        # compelled to pass through it -- which is §5.26's own reason that
        # entry 14 does not drain.
        "consumed_by_admission": False,
        "section_implemented": False,
        "exclusion": "FLOCK ON THE DIRECTORY DESCRIPTOR",
    }
