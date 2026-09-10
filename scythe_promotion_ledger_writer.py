"""The durable promotion ledger, write path -- behind a gate it cannot open.

§17 slice 6. Every append here requires a **live ownership scope**, and this
module contains no way to produce one. Slice 6b supplies that, by acquiring
`fcntl.flock` and yielding a scope from inside the span where the lock is
actually held. Until then the only caller that can reach an append is a test
that injects a scope through the `ownership` seam -- the same shape `mounts` and
`refused_prefixes` already use in the reader.

This is deliberately stronger than *the lock comes next* (§13c D.4). A write
path that works and merely lacks its guard is a write path someone can call.

Implements PROMOTION_EXECUTION_CONTRACT.md §9 (framing, fsync discipline,
sequence), §10 (restart), §13 and §13c D.1-D.4.

What this module refuses to do, all four deliberately:

  It never appends to a torn ledger, and never repairs one (§13c D.2). Not the
  tail, not the records before it, not the file's length.

  It never establishes a generation as a side effect of a write (§13c D.3).
  Creating the file and declaring a generation are two explicit operations, and
  neither happens because the other did.

  It never reinterprets what the reader already reads. Records are framed by the
  reader's own `frame_of`, so a byte this module writes is a byte
  `scythe_promotion_ledger_store` already accepts -- there is no second opinion
  about the format, because there is no second implementation of it.

  It never hands out an append that takes an ownership flag. A parameter a
  caller can pass is a parameter a caller can pass wrongly, and the reviewer of
  the call site cannot see whether the claim was true.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from typing import Any, Callable, Dict, Iterator, Optional, Sequence, Tuple

from scythe_promotion_ledger_store import (
    COMMITTED, FAILED, FRAME_VERSION, HEADER, LEDGER_SCHEMA, LEDGER_TORN,
    LEDGER_UNAVAILABLE, LEDGER_UNREADABLE, OWNER_FIELDS, RESERVED,
    LedgerRead, frame_of, read_ledger, refuse_location,
)

SCHEMA = "scythe.promotion-ledger-writer.v1"

# -- why an append is refused ---------------------------------------------
#
# Executability codes (§5). LEDGER_NOT_OWNED the contract already names;
# LEDGER_GENERATION_UNDECLARED is §13c D.3's, and says the file is perfectly
# readable and correct and simply has no generation for C1 to total over.
LEDGER_NOT_OWNED = "LEDGER_NOT_OWNED"
LEDGER_GENERATION_UNDECLARED = "LEDGER_GENERATION_UNDECLARED"
APPEND_REFUSALS: Tuple[str, ...] = (
    LEDGER_NOT_OWNED, LEDGER_GENERATION_UNDECLARED, LEDGER_TORN,
    LEDGER_UNAVAILABLE, LEDGER_UNREADABLE,
)


class LedgerWriteRefused(RuntimeError):
    """An append that did not happen. Carries the code, never a repair."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# -- the ownership proof (§13c D.4) ---------------------------------------

class OwnershipScope:
    """Live proof that this process owns this ledger, for as long as it lives.

    Not a boolean and not a recorded attestation, because either permits
    attest -> release -> append against a ledger the process no longer owns:
    the check passes and the guarantee is already gone. A scope answers *do I
    own it now*, and a reference kept past the owning span answers no.

    `path` is the resolved ledger this proof is for. A proof for one file does
    not admit a write to another, which is the second half of the same rule --
    a scope that authorised any append would be a boolean wearing a class.
    """

    __slots__ = ("_path", "_live")

    def __init__(self, path: str) -> None:
        self._path = os.path.realpath(path)
        self._live = True

    @property
    def path(self) -> str:
        return self._path

    @property
    def live(self) -> bool:
        return self._live

    def _expire(self) -> None:
        self._live = False

    def __repr__(self) -> str:      # pragma: no cover - diagnostics only
        state = "live" if self._live else "expired"
        return f"<OwnershipScope {state} {self._path!r}>"


def no_ownership(path: str) -> Optional[OwnershipScope]:
    """The only producer slice 6 has, and it produces nothing.

    Slice 6b replaces this with one that takes `fcntl.flock(LOCK_EX | LOCK_NB)`
    and yields a scope for the lock's lifetime. Until then every append refuses
    with LEDGER_NOT_OWNED, which is what makes the write path unreachable rather
    than merely unguarded.
    """
    return None


# -- the writer -----------------------------------------------------------

@dataclass
class LedgerWriter:
    """Appends framed records to one ledger, under a scope it cannot make.

    Holds no file handle between sessions and takes no lock of its own: the
    coordinator's lock orders evaluations (§4) and the ownership scope orders
    processes (§9), and a third lock here would make the order between them a
    question nobody asked.
    """

    path: str
    ownership: Callable[[str], Optional[OwnershipScope]] = no_ownership
    repo_root: Optional[str] = None
    refused_prefixes: Optional[Sequence[str]] = None

    def location_refusals(self) -> Tuple[str, ...]:
        return refuse_location(os.path.dirname(self.path) or ".",
                               repo_root=self.repo_root,
                               refused_prefixes=self.refused_prefixes)

    @contextmanager
    def owned(self) -> Iterator["_WriteSession"]:
        """Open the allocate-and-append critical section, or refuse.

        The scope is obtained once and covers the whole span (§13c D.4): a
        session that re-proved ownership at each append would have a gap between
        the two proofs, and the gap is the hole.
        """
        scope = self.ownership(self.path)
        if scope is None:
            raise LedgerWriteRefused(
                LEDGER_NOT_OWNED,
                "no ownership scope; this slice has no producer and slice 6b "
                "supplies one by holding flock for the scope's lifetime")
        session = _WriteSession(self, scope)
        try:
            yield session
        finally:
            # Expired whatever happened, including on the exception path. A
            # session that survived its own failure would be a scope whose
            # lifetime is decided by the caller.
            scope._expire()

    def status(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "path": self.path,
            "append_refusals": list(APPEND_REFUSALS),
            "ownership_producer": self.ownership.__name__,
            "production_ownership_producer": "NOT_IMPLEMENTED",
            "ownership_note": (
                "EVERY APPEND REQUIRES A LIVE LEDGER-BOUND SCOPE. THIS SLICE "
                "HAS NO PRODUCER, SO NO PRODUCTION CALLER CAN REACH AN APPEND"),
            "location_refusals": list(self.location_refusals()),
            "repairs_torn_ledgers": False,
            "declares_generations_implicitly": False,
        }


@dataclass
class _WriteSession:
    """One allocate-and-append span, valid only while its scope is live.

    Every method re-checks the scope. Not because the scope can expire midway --
    `owned()` expires it only on the way out -- but because a *retained*
    session is the reuse D.4 forbids, and the check is what makes retention
    inert rather than merely discouraged.
    """

    writer: LedgerWriter
    scope: OwnershipScope
    _next_seq: Optional[int] = field(default=None, init=False)

    # -- the gate ---------------------------------------------------------

    def _require_scope(self) -> None:
        if not self.scope.live:
            raise LedgerWriteRefused(
                LEDGER_NOT_OWNED,
                "the ownership scope has expired; a session kept past its "
                "owning span proves nothing about now")
        if self.scope.path != os.path.realpath(self.writer.path):
            raise LedgerWriteRefused(
                LEDGER_NOT_OWNED,
                "the ownership scope is for a different ledger")

    def _read(self) -> LedgerRead:
        return read_ledger(self.writer.path)

    def _require_appendable(self, read: LedgerRead) -> None:
        """§13c D.2 and D.3, checked before every append and not cached.

        A torn ledger is append-ineligible, and this is where that is enforced
        rather than left to ARMED's refusal: appending past a torn tail turns a
        contained tail into LEDGER_UNREADABLE by the reader's own corruption
        rule, losing every identity in the file to save one record.
        """
        if read.readability == LEDGER_UNAVAILABLE:
            raise LedgerWriteRefused(LEDGER_UNAVAILABLE, read.detail or "")
        if read.readability == LEDGER_UNREADABLE:
            raise LedgerWriteRefused(LEDGER_UNREADABLE, read.detail or "")
        if read.torn_tail:
            raise LedgerWriteRefused(
                LEDGER_TORN,
                "a torn tail is evidence, and this writer neither appends past "
                "it nor repairs it; the repair is reconciliation's (slice 7)")

    # -- sequence (§13c D.1) ----------------------------------------------

    def next_seq(self) -> int:
        """`last_seq + 1`, over every record kind, read from the file.

        Never persisted separately: a counter kept beside the ledger is a second
        answer that can disagree with the first. Rebuilt once per session and
        then advanced in memory, which is safe only because the scope makes this
        process the sole writer for the session's whole span.

        Gaps are legal (§13c D.1), so a number taken and not written costs
        nothing but the number.
        """
        self._require_scope()
        if self._next_seq is None:
            read = self._read()
            self._require_appendable(read)
            self._next_seq = _last_seq(self.writer.path) + 1
        return self._next_seq

    def _take_seq(self) -> int:
        seq = self.next_seq()
        self._next_seq = seq + 1
        return seq

    # -- the two explicit operations §13c D.3 keeps apart ------------------

    def initialize(self) -> bool:
        """Create the ledger if absent. Writes no header and declares nothing.

        The parent directory is fsynced, because the file's *existence* is not
        durable until it is (§9): a crash in that window leaves a missing
        ledger, which correctly refuses ARMED for a reason nobody will diagnose.

        Returns True if it created the file. A zero-byte ledger is valid (§10)
        and refuses ARMED under LEDGER_GENERATION_UNDECLARED until a generation
        is declared -- deliberately, and by the operation below.
        """
        self._require_scope()
        if os.path.exists(self.writer.path):
            return False
        directory = os.path.dirname(os.path.abspath(self.writer.path))
        handle = os.open(self.writer.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)
        _fsync_directory(directory)
        return True

    def declare_generation(self, generation: str, owner: Dict[str, Any]) -> int:
        """Write the header. The one operation that establishes a generation.

        Explicit and governed (§13c D.3): never a side effect of the first
        append, because a generation that began as a side effect is one nobody
        authorized or dated, while C1 is a lifetime total over exactly that
        boundary.

        Refused if the ledger already has a header. Re-declaring would end one
        generation and start another, which is §11's operation and not this one.
        """
        self._require_scope()
        if not isinstance(generation, str) or not generation:
            raise LedgerWriteRefused(
                LEDGER_GENERATION_UNDECLARED,
                "a generation identifier is required and is never derived")
        if not isinstance(owner, dict) or set(owner) != OWNER_FIELDS:
            raise LedgerWriteRefused(
                LEDGER_NOT_OWNED,
                "the header records the owner's ProcessIdentity, so a reader "
                "can name the owner rather than infer one")
        read = self._read()
        self._require_appendable(read)
        if read.header_present:
            raise LedgerWriteRefused(
                LEDGER_GENERATION_UNDECLARED,
                "this ledger already declares a generation; starting a new one "
                "is §11's operation, under the same authority that ends one")
        seq = self._take_seq()
        self._append({"kind": HEADER, "seq": seq, "schema": LEDGER_SCHEMA,
                      "frame_version": FRAME_VERSION, "generation": generation,
                      "owner": dict(owner)}, durable=True)
        return seq

    # -- the two-phase records (§7) ---------------------------------------

    def append_reserved(self, payload: Dict[str, Any]) -> int:
        """RESERVED, fsynced before it returns (§3 step 4).

        Reserve-before-write only survives a crash if the reservation is on disk
        before the writer is called. An unsynced reservation is write-first
        semantics with extra steps, and write-first is the ordering that
        produces duplicates.
        """
        seq = self._prepare(require_generation=True)
        record = dict(payload, kind=RESERVED, seq=seq)
        self._append(record, durable=True)
        return seq

    def append_terminal(self, kind: str, reserves: int) -> int:
        """COMMITTED or FAILED. No fsync required (§7).

        Losing a terminal record degrades the reservation to unresolved, which
        is the safe direction: the identity stays fenced and nothing downstream
        reads it as a promotion that happened.
        """
        if kind not in (COMMITTED, FAILED):
            raise LedgerWriteRefused(
                LEDGER_UNREADABLE,
                f"{str(kind)[:32]!r} is not a terminal record kind")
        seq = self._prepare(require_generation=True)
        self._append({"kind": kind, "seq": seq, "reserves": int(reserves)},
                     durable=False)
        return seq

    # -- shared ------------------------------------------------------------

    def _prepare(self, *, require_generation: bool) -> int:
        self._require_scope()
        read = self._read()
        self._require_appendable(read)
        if require_generation and not read.header_present:
            raise LedgerWriteRefused(
                LEDGER_GENERATION_UNDECLARED,
                "this ledger declares no generation; declare_generation() is "
                "the operation that establishes one, and no append does it "
                "as a side effect")
        return self._take_seq()

    def _append(self, payload: Dict[str, Any], *, durable: bool) -> None:
        """The only place bytes reach the file.

        Framed by the reader's own `frame_of`, so there is no second opinion
        about the format -- there is no second implementation of it.
        """
        self._require_scope()
        line = frame_of(payload)
        handle = os.open(self.writer.path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(handle, line)
            if durable:
                os.fsync(handle)
        finally:
            os.close(handle)


def _fsync_directory(path: str) -> None:
    handle = os.open(path, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _last_seq(path: str) -> int:
    """The highest sequence number in the file, or -1 for an empty one.

    Read from the records themselves rather than from a count of them, because
    gaps are legal and counting would reissue a number a crashed writer already
    took (§13c D.1).
    """
    from scythe_promotion_ledger_store import parse_frame

    highest = -1
    with open(path, "rb") as handle:
        data = handle.read()
    lines = data.split(b"\n")
    complete = lines[:-1] if (lines and lines[-1] == b"") else lines[:-1]
    for line in complete:
        payload = parse_frame(line)
        seq = payload.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            highest = max(highest, seq)
    return highest
