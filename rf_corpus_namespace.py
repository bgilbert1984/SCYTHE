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
§5.20 steps 3-8 and `ring_lifetime_id`. 3c-wire rewired §5.25's entrypoints
onto this scope: the scope acquires its clock internally when it opens, names
it as `corpus_clock_authority`, and answers admission's question through one
action, `admit_window`, while ownership is held. Nothing is yet compelled to
pass through it, so entry 14 stays open.

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
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from rf_corpus_manifest import (
    IQM_FRAMING_PREFIX_BYTES, IQM_MAX_BODY_BYTES, MANIFEST_ALREADY_PRESENT,
    MANIFEST_NOT_FOUND, ManifestRefused, frame_manifest, manifest_body,
    parse_manifest,
)
from rf_corpus_reconstruction import (
    ReconstructionRefused, capture_plan as _rebuild_plan,
    envelope as _rebuild_envelope,
)
from rf_eligible_trials_artefact import (
    ELIGIBLE_ARTEFACT_NOT_FOUND, ELIGIBLE_ARTEFACT_UNEXPECTED,
    ELIGIBLE_DIGEST_DISAGREES, EligibleSetRefused, digest_over_records,
    frame_records, parse_rows, read_records, sort_key as _eligible_sort_key,
)
from rf_membership_journal import (
    JOURNAL_NAME, JOURNAL_NOT_CANONICAL, JournalRefused,
    create_membership_journal,
    journal_declaration, read_membership_journal,
)
from rf_promotion_envelope import declaration_digest
from rf_signal_chain_identity import (
    CLOCK_AUTHORITIES, CLOCK_AUTHORITY_POSIX_REALTIME, UNDECLARED,
)
from rf_validation_manifest import PromotionCorpusLock, STRATA_DEFINITION_REVISION

SCHEMA = "scythe.rf-corpus-namespace.v1"

# §5.20's directory row, pinned. Resolved before use and disjoint from the
# repository, ledger, derived-evidence and observation namespaces.
PRODUCTION_CORPUS_ROOT = "/home/spectrcyde/scythe-validation-corpus/captured-v1"

MANIFEST_NAME = "manifest.iqm"
# §5.27. Present when and only when the compact capture plan has a spur
# allocation: the eligible rows are observations, and a seed cannot regenerate
# an observation.
ELIGIBLE_NAME = "eligible-spur-trials.iqe"

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
    # `realpath` resolves symlinks in whatever part of the path exists and
    # normalises the rest, which covers a symlinked parent and a not-yet-
    # created child alike. An earlier revision walked to the nearest existing
    # ancestor and re-resolved from there; reverting that walk failed no test,
    # because it was defending a case the standard library already handled.
    resolved = os.path.realpath(str(root))
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
                 "released", "envelope", "capture_plan", "lock",
                 "clock", "clock_authority")

    def __init__(self, dir_fd, device, body, digest, path,
                 envelope=None, capture_plan=None, lock=None,
                 clock: Callable[[], float] = time.time) -> None:
        self.dir_fd = dir_fd
        self.device = device
        self.body = body
        self.manifest_sha256 = digest
        self.path = path
        self.released = False
        # 3c-wire: the clock the scope acquired when it opened, and its named
        # authority, derived from the callable actually installed. Production
        # passes no clock; only the test factory injects one.
        self.clock = clock
        self.clock_authority = _clock_authority_of(clock)
        # The exact nominal objects, reconstructed once by the factory. §5.27:
        # admission must consume only this bound state, never a mapping and
        # never a reconstruction it performed itself.
        self.envelope = envelope
        self.capture_plan = capture_plan
        self.lock = lock


def _clock_authority_of(clock: Any) -> str:
    """Exactly `time.time` is `POSIX_REALTIME`; anything else is `UNDECLARED`.

    Identity, not behaviour: a wrapper that calls `time.time` reads the same
    clock and is still `UNDECLARED`, because nothing here can tell it from a
    wrapper that does not. Authority is never inferred from equivalence.
    """
    return CLOCK_AUTHORITY_POSIX_REALTIME if clock is time.time else UNDECLARED


@dataclass(frozen=True)
class CorpusAdmissionTerms:
    """What the ownership scope tells admission, and nothing more.

    The answer to one action, taken while ownership is held: bounded,
    immutable, non-capability facts. Strings, floats and one boolean. No lock,
    no envelope, no plan, no descriptor and no path -- a caller holding this
    holds comparison results, not the authority that produced them, which is
    the same construction as the verified-final result and for the same
    reason. `now` is read from the scope's own clock, so a production caller
    supplies no timestamp.
    """

    corpus_id: str
    opened_at: float
    delete_not_after: float
    configuration_digest: str
    envelope_digest: str
    capture_plan_digest: str
    strata_definition_revision: str
    corpus_clock_authority: str
    now: float
    chain_admitted: bool


class CorpusOwnershipScope:
    """A held corpus namespace, and the terms it recorded about itself.

    **The descriptor and the path do not leave.** §5.24 spent a slice
    establishing that a returned `memoryview` is a capability that outlives its
    scope; a returned directory descriptor or path is the same thing one layer
    out, and would let a second writer reach creation without passing anything.
    There is no public accessor for either, and the static call-site check
    refuses a production reference to the private one.

    **What it does not yet carry**: per-stratum sequence state and the
    journal's intent, commit and abandon acts, which are 3d's. The typed
    `InstrumentChainEnvelope` / `CapturePlanDeclaration` / `PromotionCorpusLock`
    objects are reconstructed once by the factory and bound here (§5.27); the
    clock is acquired internally when the scope opens (3c-wire); and admission
    consumes both through `admit_window`, an action rather than an accessor.
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

    # There is deliberately NO accessor returning the reconstructed lock, the
    # envelope, the capture plan, the allocation or the eligible rows.
    #
    # An earlier revision of this slice had `_bound_lock()`, restricted by the
    # static call-site check, and that is not sufficient. Demonstrated before
    # it was removed: a caller inside the `with` block took the lock, the block
    # ended, the scope refused with NAMESPACE_SCOPE_RELEASED -- and the escaped
    # object still answered, all 5 700 eligible rows included. Immutability
    # does not help. The escaped object simply no longer proves the namespace
    # is held, unchanged or exclusively owned, and a static check governs who
    # may CALL an accessor, never what an allowed caller RETAINS afterwards.
    #
    # This is §5.24's lesson one layer out, where a returned `memoryview`
    # outlived the attestation that vouched for it.
    #
    # Admission is not rewired in this slice, so nothing needs to consume the
    # objects yet. When it is, it gets an ACTION -- re-establishing scope
    # identity and liveness and answering the admission question while
    # ownership is held -- exactly as §5.24 replaced its accessor with a write.
    # Until then `to_dict()` reports bounded diagnostics: digests, counts and
    # type names, which are comparison results rather than authority.

    # There is deliberately no `_directory_fd()` either. §5.26 shipped one,
    # "restricted for this module's acts", and it had **no production caller**:
    # every act here already holds the descriptor as a local. The runtime
    # surface walk added in §5.27 classified it as a DESCRIPTOR escape on its
    # first run, which is what it is -- a caller inside the `with` block could
    # retain the integer, and after release the fd is closed, so a retained
    # number either fails or names whatever the kernel handed out next.
    #
    # Entry 16's precedent applies unchanged: a method nobody calls is deleted
    # rather than rehabilitated.

    @property
    def corpus_clock_authority(self) -> str:
        """Which clock this scope acquired when it opened. §5.26 point 4.

        A property of the ownership scope, not of any window: the ring names
        its own clock for the capture times, and one field declared by two
        authorities is what stops `_merge` refusing a field claimed twice.
        """
        return self._live().clock_authority

    def admit_window(self, *, signal_chain_hash: str) -> CorpusAdmissionTerms:
        """The admission action. §5.25's gate, answered while ownership is held.

        Admission asks the scope whether the **attested** chain hash is a
        declared member of the frozen envelope, and receives the corpus terms
        the header binds -- as bounded facts, never as the objects. The caller
        supplies neither the answer nor the set the answer is drawn from: the
        envelope consulted is the one this factory reconstructed and verified
        against the manifest's frozen digest, and nothing a caller passes can
        substitute for it.

        An action rather than an accessor because §5.24 and §5.27 both found
        that a returned authority object outlives the scope that vouched for
        it. Every call re-establishes that the namespace is still held.
        """
        state = self._live()
        chain = str(signal_chain_hash)
        lock = state.lock
        return CorpusAdmissionTerms(
            corpus_id=str(state.body["corpus_id"]),
            opened_at=float(state.body["opened_at"]),
            delete_not_after=float(state.body["delete_not_after"]),
            configuration_digest=str(lock.configuration_digest),
            envelope_digest=str(lock.envelope_digest),
            capture_plan_digest=str(lock.capture_plan_digest),
            strata_definition_revision=str(
                state.body["strata_definition_revision"]),
            corpus_clock_authority=state.clock_authority,
            now=float(state.clock()),
            chain_admitted=bool(state.envelope.admits(chain)),
        )

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
            # Bounded diagnostics. Strings and integers only -- enough to
            # establish that reconstruction happened and agrees with what the
            # namespace froze, and not enough to be authority.
            "capture_plan_reconstructed": state.capture_plan is not None,
            "reconstructed_types": sorted(
                type(obj).__name__ for obj in
                (state.envelope, state.capture_plan, state.lock)
                if obj is not None),
            "reconstructed_envelope_digest": (
                None if state.envelope is None else state.envelope.digest()),
            "reconstructed_capture_plan_digest": (
                None if state.capture_plan is None
                else state.capture_plan.digest()),
            "eligible_trials_bound": (
                0 if state.capture_plan is None
                or state.capture_plan.spur_allocation is None
                else len(state.capture_plan.spur_allocation.eligible_trials)),
            "corpus_clock_authority": state.clock_authority,
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


def _write_file(dir_fd: int, name: str, chunks) -> int:
    """Create exclusively, write completely, set the mode, and `fsync`.

    The mode is set with `fchmod` rather than trusted to `open`'s argument,
    which the umask masks -- measured: under umask 0300 a requested 0600
    arrives as 0400.
    """
    try:
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     CORPUS_FILE_MODE, dir_fd=dir_fd)
    except FileExistsError as exc:
        raise ManifestRefused(
            MANIFEST_ALREADY_PRESENT,
            f"{name} appeared after this corpus's directory was created; "
            "another writer is in this namespace") from exc
    total = 0
    try:
        for chunk in chunks:
            written = 0
            while written < len(chunk):
                wrote = os.write(fd, chunk[written:])
                if wrote <= 0:
                    raise NamespaceRefused(
                        NAMESPACE_NOT_A_DIRECTORY,
                        f"wrote {total + written} bytes of {name} and stalled")
                written += wrote
            total += written
        os.fchmod(fd, CORPUS_FILE_MODE)
        os.fsync(fd)
    finally:
        os.close(fd)
    return total


def _open_member(dir_fd: int, name: str, device: int, absent_code: str) -> int:
    """Open a namespace file and check the OPENED OBJECT, never the path."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            if absent_code == ELIGIBLE_ARTEFACT_NOT_FOUND:
                raise EligibleSetRefused(
                    absent_code,
                    f"the capture plan declares a spur allocation and {name} "
                    "is not in this namespace") from exc
            raise ManifestRefused(
                absent_code, f"no {name} in this namespace") from exc
        if exc.errno == errno.ELOOP:
            raise NamespaceRefused(
                NAMESPACE_SYMLINK_REFUSED, f"{name} is a symbolic link") from exc
        raise
    try:
        _check_manifest_file(fd, device)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_eligible_rows(dir_fd: int, device: int, allocation: Any):
    """Stream the sidecar, or refuse. Returns the canonical rows.

    Presence is decided only by the compact plan's `spur_allocation`, so an
    artefact nobody declared is an unaccounted namespace entry rather than a
    decoration.
    """
    present = ELIGIBLE_NAME in set(os.listdir(dir_fd))
    if allocation is None:
        if present:
            raise EligibleSetRefused(
                ELIGIBLE_ARTEFACT_UNEXPECTED,
                f"{ELIGIBLE_NAME} is here and the capture plan declares no "
                "spur allocation to reconstruct")
        return []
    fd = _open_member(dir_fd, ELIGIBLE_NAME, device, ELIGIBLE_ARTEFACT_NOT_FOUND)
    try:
        records = list(read_records(
            lambda count: os.read(fd, count),
            expected_count=int(allocation["distinct_trial_units"])))
    finally:
        os.close(fd)
    recomputed = digest_over_records(records)
    if recomputed != allocation["eligible_trials_digest"]:
        raise EligibleSetRefused(
            ELIGIBLE_DIGEST_DISAGREES,
            "the artefact does not digest to the value the capture plan froze")
    return parse_rows(records)


def _read_manifest(dir_fd: int, device: int) -> Tuple[Dict[str, Any], str]:
    fd = _open_member(dir_fd, MANIFEST_NAME, device, MANIFEST_NOT_FOUND)
    try:
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


def _reconstruct(body, rows):
    """The exact nominal objects, built once and returned together.

    §5.26 held the manifest's **mapping**, and admission consuming a mapping is
    the caller-supplied set §5.25 refused wearing a different shape. Everything
    below is checked by the two comparisons `rf_corpus_reconstruction` ends
    with: re-serialisation to the stored declaration, byte for byte, and the
    frozen digest.
    """
    rebuilt_envelope = _rebuild_envelope(body["envelope"],
                                         frozen_digest=body["envelope_digest"])
    rebuilt_plan = _rebuild_plan(body["capture_plan"], eligible_rows=rows,
                                 frozen_digest=body["capture_plan_digest"])
    fields = {name: body[name] for name in PromotionCorpusLock.__dataclass_fields__
              if name not in ("envelope", "capture_plan")}
    lock = PromotionCorpusLock(envelope=rebuilt_envelope,
                               capture_plan=rebuilt_plan, **fields)
    return rebuilt_envelope, rebuilt_plan, lock


def _eligible_rows_for(plan) -> list:
    allocation = getattr(plan, "spur_allocation", None)
    if allocation is None:
        return []
    return sorted((trial.to_dict() for trial in allocation.eligible_trials),
                  key=_eligible_sort_key)


# -- the two acts -----------------------------------------------------------


def create_corpus_namespace(*, corpus_id: str, lock: Any, retention: Any,
                            root: Optional[str] = None
                            ) -> CorpusOwnershipScope:
    """Open a corpus for the first time. A **new, empty** namespace only.

    The scope acquires its clock **internally**: `time.time`, and nothing a
    caller passes. Only `_create_corpus_namespace_with_clock`, the test
    factory, accepts an injected one, so the seam sits at the boundary rather
    than on every write, and a production caller cannot reach it.

    §5.26's sequence, in order::

        resolve -> create the directory 0700 -> hold exclusively
        -> validate empty -> create and fsync the empty journal 0600
        -> O_CREAT|O_EXCL the manifest 0600
        -> write -> fsync the manifest -> fsync the directory
        -> reopen and verify -> mint the scope

    The reopen is not decoration. A manifest nobody has read once is a corpus
    whose terms have never been shown to parse, and every later act rests on
    them.
    """
    return _create(corpus_id=corpus_id, lock=lock, retention=retention,
                   root=root, clock=time.time)


def _create_corpus_namespace_with_clock(*, corpus_id: str, lock: Any,
                                        retention: Any, root: Optional[str],
                                        clock: Callable[[], float]
                                        ) -> CorpusOwnershipScope:
    """**The test factory.** Creation with an injected clock, and nothing else.

    Restricted: a production reference to this name is refused by the static
    call-site check, so production code obtains its clock only from
    `create_corpus_namespace` and reads `POSIX_REALTIME`. A scope minted here
    names its clock `UNDECLARED` unless the callable is exactly `time.time`.
    """
    return _create(corpus_id=corpus_id, lock=lock, retention=retention,
                   root=root, clock=clock)


def _create(*, corpus_id: str, lock: Any, retention: Any, root: Optional[str],
            clock: Callable[[], float]) -> CorpusOwnershipScope:
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

        # §5.27: the DEPENDENCY first. Writing the manifest first would publish
        # a corpus whose declared eligible set did not yet exist. A crash
        # before the manifest is durably named leaves an incomplete namespace
        # rather than a corpus: unopenable, unresumable, minting no scope, and
        # never adopted on retry.
        rows = _eligible_rows_for(lock.capture_plan)
        if rows:
            _write_file(dir_fd, ELIGIBLE_NAME, frame_records(rows))
            _read_eligible_rows(dir_fd, device,
                                body["capture_plan"]["spur_allocation"])

        # §5.26 amendment A/B: an empty journal is the canonical zero-record
        # journal.  It exists before the manifest so the directory fsync below
        # makes all three names durable together; creating it on first intent
        # would fsync its bytes and leave its name outside the accepted order.
        create_membership_journal(dir_fd)
        journal = read_membership_journal(dir_fd)
        if journal.records:                            # pragma: no cover
            raise JournalRefused(
                JOURNAL_NOT_CANONICAL,
                "a newly created membership journal is not empty")

        _write_file(dir_fd, MANIFEST_NAME, (framed,))
        # After every name exists. The directory fsync makes names durable,
        # and making one durable before another is what leaves a manifest
        # whose dependency or membership accounting can be lost.
        os.fsync(dir_fd)

        read_body, digest = _read_manifest(dir_fd, device)
        _check_declarations(read_body)
        if read_body != body:
            raise ManifestRefused(
                "MANIFEST_NOT_CANONICAL",
                "the manifest read back is not the manifest written")
        read_rows = _read_eligible_rows(
            dir_fd, device, read_body["capture_plan"]["spur_allocation"])
        rebuilt_envelope, rebuilt_plan, rebuilt_lock = _reconstruct(
            read_body, read_rows)
        state = _ScopeState(dir_fd, device, read_body, digest, path,
                            rebuilt_envelope, rebuilt_plan, rebuilt_lock,
                            clock=clock)
        return CorpusOwnershipScope(state, _MINT_KEY)
    except BaseException:
        os.close(dir_fd)
        raise


def open_corpus_namespace(*, corpus_id: str, root: Optional[str] = None
                          ) -> CorpusOwnershipScope:
    """Reopen an existing corpus. **Requires** a manifest rather than refusing one.

    Acquires its clock internally, as creation does; the test factory is
    `_open_corpus_namespace_with_clock`.

    Not `create_corpus_namespace` with a flag: *"rejects a non-empty
    namespace"* is right for creation and false here, and a flag would make
    which of the two applies a caller's choice.

    **Membership recovery is unbuilt**, so a namespace holding anything besides
    the manifest is refused rather than reopened with an unexamined member
    count. That is the whole of what §5.26's recovery would examine, and
    reopening over it silently would be the inert admission entry 11 was drained
    for closing.
    """
    return _open(corpus_id=corpus_id, root=root, clock=time.time)


def _open_corpus_namespace_with_clock(*, corpus_id: str, root: Optional[str],
                                      clock: Callable[[], float]
                                      ) -> CorpusOwnershipScope:
    """**The test factory** for reopening. See `_create_corpus_namespace_with_clock`."""
    return _open(corpus_id=corpus_id, root=root, clock=clock)


def _open(*, corpus_id: str, root: Optional[str],
          clock: Callable[[], float]) -> CorpusOwnershipScope:
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
        unexpected = [entry for entry in entries
                      if entry not in (MANIFEST_NAME, ELIGIBLE_NAME,
                                       JOURNAL_NAME)]
        if unexpected:
            raise NamespaceRefused(
                NAMESPACE_RECOVERY_UNBUILT,
                f"{len(unexpected)} entr(y/ies) besides this corpus's own "
                "declarations, and membership recovery is not built. §5.26 "
                "requires every candidate final to be parsed, validated and "
                "reconciled against the journal before a corpus is reopened: "
                f"{unexpected[:4]}")
        body, digest = _read_manifest(dir_fd, device)
        _check_declarations(body)
        journal = read_membership_journal(dir_fd)
        if journal.records:
            raise NamespaceRefused(
                NAMESPACE_RECOVERY_UNBUILT,
                f"membership.iqj holds {len(journal.records)} record(s); "
                "slice 3d reconciles them against verified finals before a "
                "scope may be minted")
        rows = _read_eligible_rows(dir_fd, device,
                                   body["capture_plan"]["spur_allocation"])
        rebuilt_envelope, rebuilt_plan, rebuilt_lock = _reconstruct(body, rows)
        state = _ScopeState(dir_fd, device, body, digest, path,
                            rebuilt_envelope, rebuilt_plan, rebuilt_lock,
                            clock=clock)
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
        "eligible_artefact_name": ELIGIBLE_NAME,
        "membership_journal": journal_declaration(),
        "built": ["THE MANIFEST", "THE ELIGIBLE-TRIAL ARTEFACT",
                  "CORPUS CREATION", "CORPUS REOPENING",
                  "TYPED RECONSTRUCTION AND OPAQUE BINDING",
                  "MEMBERSHIP JOURNAL CORE",
                  "CLOCK PROVIDER", "ADMISSION CONSUMPTION OF THIS SCOPE"],
        "not_built": ["FINAL-DEPENDENT JOURNAL RECOVERY", "SEQUENCE STATE", "PUBLISHER",
                      "RING LIFETIME IDENTITY"],
        "clock_authorities": list(CLOCK_AUTHORITIES),
        # 3c-wire: admission consumes this scope, through `admit_window`. That
        # is consumption, not compulsion: no production path is yet obliged
        # to pass through it, which is §5.26's own reason that entry 14 does
        # not drain until 3d.
        "consumed_by_admission": True,
        "compelled_path_to_membership": False,
        "section_implemented": False,
        "exclusion": "FLOCK ON THE DIRECTORY DESCRIPTOR",
    }
