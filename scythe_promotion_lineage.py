"""Generations of one ledger, and how a successor becomes real.

§17 slice 7, implementing PROMOTION_EXECUTION_CONTRACT.md §13e F.4-F.6c and
F.10. A lineage is a root path and the generation files beneath it; exactly one
generation is authoritative, and it is found rather than pointed at.

Three things this module exists to get right:

  **Publication is a protocol, not a moment.** "The header reaches disk" does
  not say what happens to a header half-written under its final name, and an
  ``fsync`` on a file says nothing about its directory entry. The five steps
  below are each load-bearing and are performed through an injectable syscall
  seam, so their *order* can be asserted rather than inferred from a file that
  looks right on a machine that did not crash.

  **A fork is refused, never resolved.** Timestamp, filename and directory order
  are properties of the filesystem's bookkeeping rather than of the evidence,
  and choosing by one would make the fence depend on which file happened to be
  written second.

  **A temporary supersedes nothing and is never removed.** Not by this module
  and not by a later attempt. A partial successor may be the only record of what
  someone was doing when the machine stopped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from scythe_promotion_ledger_store import (
    LEDGER_TORN, LEDGER_UNAVAILABLE, LEDGER_UNREADABLE, LedgerRead, read_ledger,
)

SCHEMA = "scythe.promotion-lineage.v1"

# -- naming, all derived -------------------------------------------------
#
# The root is configured; everything else is derived from it (§13e F.10). A
# configured generation name, or a configured lock, is a second way to say what
# this lineage is -- and §9 Amendment A is the record of what happens when two
# names for one thing both satisfy their own check.
GENERATION_SUFFIX = ".jsonl"
PARTIAL_SUFFIX = ".partial"
ORDINAL_DIGITS = 6

# -- refusals (§5) --------------------------------------------------------
GENERATION_LINEAGE_FORKED = "GENERATION_LINEAGE_FORKED"
GENERATION_CHAIN_BROKEN = "GENERATION_CHAIN_BROKEN"
GENERATION_PUBLICATION_UNCERTAIN = "GENERATION_PUBLICATION_UNCERTAIN"
LINEAGE_REFUSALS: Tuple[str, ...] = (
    GENERATION_LINEAGE_FORKED, GENERATION_CHAIN_BROKEN,
    GENERATION_PUBLICATION_UNCERTAIN,
)

# -- the publication steps, in order -------------------------------------
OPEN_EXCLUSIVE = "OPEN_EXCLUSIVE"
WRITE_HEADER = "WRITE_HEADER"
FSYNC_FILE = "FSYNC_FILE"
RENAME = "RENAME"
FSYNC_DIRECTORY = "FSYNC_DIRECTORY"
PUBLICATION_STEPS: Tuple[str, ...] = (OPEN_EXCLUSIVE, WRITE_HEADER, FSYNC_FILE,
                                      RENAME, FSYNC_DIRECTORY)


class LineageError(RuntimeError):
    """A lineage operation that did not happen. Carries a code, never a repair."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def generation_path(root: str, ordinal: int) -> str:
    return f"{root}.{ordinal:0{ORDINAL_DIGITS}d}{GENERATION_SUFFIX}"


def temporary_path(root: str, ordinal: int, request_id: str, nonce: str) -> str:
    """Never scanned as a generation, and unique per attempt.

    The request digest keeps an operator able to see which request left a
    partial behind; the nonce keeps every attempt O_EXCL-fresh, so a retry after
    a crash creates its own file instead of reusing or removing one whose
    contents nobody has established.
    """
    digest = hashlib.blake2s(request_id.encode("utf-8"), digest_size=6).hexdigest()
    return f"{generation_path(root, ordinal)}.{digest}.{nonce}{PARTIAL_SUFFIX}"


def ordinal_of(root: str, path: str) -> Optional[int]:
    prefix, suffix = root + ".", GENERATION_SUFFIX
    name = path
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    middle = name[len(prefix):-len(suffix)]
    return int(middle) if middle.isdigit() else None


# -- the syscall seam -----------------------------------------------------

class Syscalls:
    """Every filesystem effect publication has, in one place.

    Injectable so a test can assert the **order** of the five steps rather than
    the shape of the file they leave behind. A correct-looking file on a machine
    that did not crash is evidence of nothing: every wrong ordering produces one.
    """

    def open_exclusive(self, path: str) -> int:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)

    def write(self, fd: int, data: bytes) -> None:
        os.write(fd, data)

    def fsync(self, fd: int) -> None:
        os.fsync(fd)

    def close(self, fd: int) -> None:
        os.close(fd)

    def rename(self, source: str, target: str) -> None:
        os.rename(source, target)

    def fsync_directory(self, path: str) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@dataclass
class Generation:
    """One published generation file and what its header says."""

    path: str
    ordinal: int
    read: LedgerRead
    supersedes: Optional[Dict[str, Any]] = None

    @property
    def identifier(self) -> Optional[str]:
        return self.read.generation


@dataclass
class Lineage:
    """A root, its published generations, and which one is authoritative."""

    root: str
    syscalls: Syscalls = field(default_factory=Syscalls)

    @property
    def directory(self) -> str:
        return os.path.dirname(os.path.abspath(self.root)) or "."

    # -- discovery --------------------------------------------------------

    def published(self) -> List[Generation]:
        """Every published generation, lowest ordinal first.

        Temporary files are not scanned -- they do not carry the name a
        generation is published under, which is the whole reason the protocol
        writes them somewhere else first. A file with no valid header is not a
        generation either (§13c D.3), so a crash before the header leaves the
        predecessor untouched in every sense that matters.
        """
        found: List[Generation] = []
        try:
            entries = sorted(os.listdir(self.directory))
        except OSError:
            return found
        base = os.path.basename(self.root)
        for entry in entries:
            if not entry.startswith(base + ".") or not entry.endswith(GENERATION_SUFFIX):
                continue
            path = os.path.join(self.directory, entry)
            ordinal = ordinal_of(os.path.join(self.directory, base), path)
            if ordinal is None:
                continue
            read = read_ledger(path)
            if not read.header_present:
                continue
            found.append(Generation(path=path, ordinal=ordinal, read=read,
                                    supersedes=_supersedes_of(path)))
        return found

    def authoritative(self) -> Generation:
        """The one generation nothing supersedes (§13e F.6a).

        Zero, one, many. Many is a refusal and never a choice: a fork means two
        processes believed they owned this lineage, and the right response to
        that is to stop.
        """
        generations = self.published()
        if not generations:
            raise LineageError(LEDGER_UNAVAILABLE,
                               "no published generation under this root")
        superseded = {g.supersedes.get("generation") for g in generations
                      if g.supersedes}
        heads = [g for g in generations if g.identifier not in superseded]
        if len(heads) > 1:
            raise LineageError(
                GENERATION_LINEAGE_FORKED,
                f"{len(heads)} generations are superseded by nothing: "
                f"{sorted(g.identifier or '?' for g in heads)}. Not resolved by "
                f"timestamp, filename or directory order, because none of those "
                f"is a property of the evidence")
        return heads[0]

    def chain(self) -> List[Generation]:
        """Root-first, from the oldest generation to the authoritative one.

        Walked rather than assumed, so a missing or altered predecessor is
        GENERATION_CHAIN_BROKEN instead of a silently smaller fence (§13e F.5).
        """
        generations = {g.identifier: g for g in self.published()}
        head = self.authoritative()
        order: List[Generation] = [head]
        seen = {head.identifier}
        current = head
        while current.supersedes:
            name = current.supersedes.get("generation")
            if name not in generations:
                raise LineageError(
                    GENERATION_CHAIN_BROKEN,
                    f"generation {str(name)[:48]!r} is named as a predecessor "
                    f"and is not present; the fence it holds cannot be read")
            if name in seen:
                raise LineageError(GENERATION_CHAIN_BROKEN,
                                   f"the chain revisits {str(name)[:48]!r}")
            current = generations[name]
            seen.add(name)
            order.append(current)
        return list(reversed(order))

    def fenced(self) -> Tuple[str, ...]:
        """Every identity the whole lineage refuses.

        The union over the chain, minus anything a later generation released. A
        torn predecessor is still read, up to its tear: torn means not
        appendable, never not readable.
        """
        blocked: Dict[str, str] = {}
        for generation in self.chain():
            read = generation.read
            if read.readability == LEDGER_UNREADABLE:
                raise LineageError(
                    GENERATION_CHAIN_BROKEN,
                    f"{generation.path} cannot be parsed, so the fence it holds "
                    f"cannot be read")
            for identity in read.fenced:
                blocked[identity] = generation.identifier or "?"
            for identity in read.released:
                blocked.pop(identity, None)
        return tuple(sorted(blocked))

    # -- publication (§13e F.6) -------------------------------------------

    def find_by_request(self, request_id: str) -> Optional[Generation]:
        """Any published generation this request already produced.

        Keyed on the **request**, not on the current head. Keying on the head
        was wrong and the rediscovery test caught it: after a successful close
        the head *is* the successor, so a retry looked for a successor of the
        file it had just created and found none -- then published a second
        generation for a request that had already succeeded, which is the exact
        failure idempotency exists to prevent.
        """
        for generation in self.published():
            block = generation.supersedes or {}
            if block.get("request_id") == request_id:
                return generation
        return None

    def find_successor(self, predecessor: Generation, request_id: str
                       ) -> Optional[Generation]:
        """Rediscovery, before anything is created (§13e F.6).

        An operator whose acknowledgement was lost should be told *this already
        happened*, not *you may not do that*.
        """
        for generation in self.published():
            block = generation.supersedes or {}
            if block.get("generation") != predecessor.identifier:
                continue
            if block.get("request_id") == request_id:
                return generation
            raise LineageError(
                GENERATION_LINEAGE_FORKED,
                f"{predecessor.identifier} is already superseded by a different "
                f"request; publishing a second successor is the fork this "
                f"protocol exists to prevent")
        return None

    def publish(self, predecessor: Generation, header: bytes, *,
                request_id: str, nonce: str) -> str:
        """The five steps, in order, through the seam.

        Returns the published path. Raises before the rename with nothing
        superseded; raises at the directory fsync with the publication's
        durability unknown, which is the caller's problem and not the ledger's.
        """
        ordinal = predecessor.ordinal + 1
        final = generation_path(self.root, ordinal)
        if os.path.exists(final):
            raise LineageError(
                GENERATION_LINEAGE_FORKED,
                f"{final} already exists; the successor ordinal is derived and "
                f"a second file at it is not this process's to overwrite")
        temporary = temporary_path(self.root, ordinal, request_id, nonce)

        fd = self.syscalls.open_exclusive(temporary)
        try:
            self.syscalls.write(fd, header)
            self.syscalls.fsync(fd)
        finally:
            self.syscalls.close(fd)
        self.syscalls.rename(temporary, final)
        try:
            self.syscalls.fsync_directory(self.directory)
        except Exception as exc:
            # After the rename and before the directory is durable. The rename
            # may be visible now and absent after a reboot, and this process
            # cannot tell. It stops; a later reader sees one of two well-defined
            # states, so the uncertainty belongs to the writer alone.
            raise LineageError(
                GENERATION_PUBLICATION_UNCERTAIN,
                f"the successor was renamed and the directory could not be "
                f"synced: {type(exc).__name__}") from None
        return final

    def status(self) -> Dict[str, Any]:
        try:
            head = self.authoritative()
            authoritative, refusal = head.identifier, None
        except LineageError as error:
            authoritative, refusal = None, error.code
        return {
            "schema": SCHEMA,
            "root": self.root,
            "published": [g.path for g in self.published()],
            "authoritative": authoritative,
            "refusal": refusal,
            "publication_steps": list(PUBLICATION_STEPS),
            "removes_temporaries": False,
            "resolves_forks": False,
            "note": (
                "THE AUTHORITATIVE GENERATION IS THE ONE NOTHING SUPERSEDES. "
                "THERE IS NO POINTER FILE, BECAUSE A POINTER IS A SECOND ANSWER "
                "THAT CAN DISAGREE WITH THE FIRST"),
        }


def _supersedes_of(path: str) -> Optional[Dict[str, Any]]:
    """The header's supersedes block, read straight from the first record."""
    from scythe_promotion_ledger_store import parse_frame

    try:
        with open(path, "rb") as handle:
            first = handle.readline()
    except OSError:
        return None
    if not first.endswith(b"\n"):
        return None
    try:
        payload = parse_frame(first.rstrip(b"\n"))
    except Exception:
        return None
    block = payload.get("supersedes")
    return block if isinstance(block, dict) else None
