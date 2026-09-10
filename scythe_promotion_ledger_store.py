"""The durable promotion ledger, read path only.

SCYTHE may learn whether the fence exists and what it says. It may not yet move
the fence. This module creates no file, appends no record, acquires no lock and
reaches no graph -- those are later slices, and their absence here is the point
rather than an omission.

Implements PROMOTION_EXECUTION_CONTRACT.md: location validation and capability
attestation (§9 Amendment A), framed-record parsing and header validation (§9),
identity reconstruction (§7, §10), torn-tail detection (§13), generation totals
(§11) and SHADOW seeding (§12).

Two things this module refuses to do, both deliberately:

  It never creates the ledger. A missing ledger is a missing fence (§10), and a
  read path that created one would answer the question by erasing it.

  It never treats an unrecognised record kind as ignorable. A ledger written by
  a coordinator that knows more record kinds than this one is not a ledger this
  one may read past: skipping a kind it does not understand is exactly how a
  fence stops fencing.

And one it refuses to guess at: a populated ledger with no valid header. Schema,
framing version, generation and owner are declared by the header or they are not
declared at all, and a reader that supplied any of them would be reconstructing
a durable identity set from records whose meaning it had assumed.
"""

from dataclasses import dataclass, field
import binascii
import json
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "scythe.promotion-ledger-store.v1"

# What the ledger on disk declares about itself, in its header record. Distinct
# from SCHEMA above: that names this reader, this names the file. A reader that
# accepted a file whose schema it had never heard of would be asserting that the
# fence means what this module assumes, on no evidence.
LEDGER_SCHEMA = "scythe.promotion-ledger.v1"
FRAME_VERSION = "pl1"

# The header's own field set, closed. §9 requires the holder to write its
# ProcessIdentity into a header record so a reader can *name* the owner rather
# than infer one; a header that omits it leaves the ledger attributable to
# nobody, which is the inference §9 exists to forbid.
HEADER_FIELDS = frozenset(("kind", "seq", "schema", "frame_version",
                           "generation", "owner"))
OWNER_FIELDS = frozenset(("boot_id", "pid", "start_ticks"))

# -- record kinds this slice understands ----------------------------------
#
# Closed, and closed hard: RECONCILED_COMMITTED and RECONCILED_RELEASED are
# defined by §8 and are NOT here, because the slice that writes them has not
# landed. A ledger containing one is unreadable to this module rather than
# partially readable, which is the fail-closed direction.
HEADER = "HEADER"
RESERVED = "RESERVED"
COMMITTED = "COMMITTED"
FAILED = "FAILED"
KNOWN_KINDS: Tuple[str, ...] = (HEADER, RESERVED, COMMITTED, FAILED)
TERMINAL_KINDS: Tuple[str, ...] = (COMMITTED, FAILED)

# -- readability ----------------------------------------------------------
AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
LEDGER_UNAVAILABLE = "LEDGER_UNAVAILABLE"
LEDGER_UNREADABLE = "LEDGER_UNREADABLE"
LEDGER_TORN = "LEDGER_TORN"

# -- capability attestation (§9 Amendment A) ------------------------------
ATTESTED_BY_FILESYSTEM_POLICY = "ATTESTED_BY_FILESYSTEM_POLICY"
UNATTESTED = "UNATTESTED"
LOCK_EXCLUSION_UNATTESTED = "LOCK_EXCLUSION_UNATTESTED"
RESERVATION_DURABILITY_UNATTESTED = "RESERVATION_DURABILITY_UNATTESTED"
LOCATION_REFUSED = "LEDGER_LOCATION_REFUSED"

# Narrow on purpose. It will refuse sound configurations, and that is the
# correct direction: a refused ARMED on a good host is recoverable by extending
# this tuple after testing, while an accepted ARMED on a mount that does not
# exclude IS the race the coordinator's mutex cannot see.
ALLOWLISTED_FILESYSTEMS: Tuple[str, ...] = ("ext4",)
KNOWN_REFUSED_FILESYSTEMS: Tuple[str, ...] = (
    "drvfs", "9p", "cifs", "nfs", "nfs4", "fuse", "fuseblk", "overlay",
    "tmpfs", "vboxsf", "smbfs")

# Never a ledger location, whatever the filesystem underneath reports.
REFUSED_PREFIXES: Tuple[str, ...] = ("/tmp", "/var/tmp", "/dev/shm", "/proc",
                                     "/sys", "/run")

# -- shadow fidelity (§12, §9 Amendment A row 3) --------------------------
FIDELITY_FULL = "FULL"
FIDELITY_DEGRADED_NOT_ARMABLE = "DEGRADED_FILESYSTEM_NOT_ARMABLE"
FIDELITY_UNSEEDED = "DEGRADED_LEDGER_UNREADABLE_NOT_SEEDED"


class LedgerFrameError(ValueError):
    """A frame that cannot be read. Never repaired, never guessed at."""


# -- framing --------------------------------------------------------------
#
# One record per line:  <len:08x> <crc32:08x> <payload>\n
#
# Length and CRC both, because they fail differently. A truncated payload is
# shorter than its declared length and is caught without reading it; corruption
# that preserves length is caught by the CRC. A tail truncated inside the header
# fails to parse at all. Framing is what makes a torn tail *detected* rather
# than inferred from a JSON error, which cannot tell truncation from a producer
# writing bad JSON.

def frame_of(payload: Mapping[str, Any]) -> bytes:
    """The canonical frame for a payload. Read path uses it only to verify."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return b"%08x %08x %s\n" % (len(body), binascii.crc32(body) & 0xFFFFFFFF, body)


def parse_frame(line: bytes) -> Dict[str, Any]:
    """One framed record, or LedgerFrameError. Never a partial result."""
    if len(line) < 19 or line[8:9] != b" " or line[17:18] != b" ":
        raise LedgerFrameError("frame header is malformed or truncated")
    try:
        declared = int(line[:8], 16)
        expected_crc = int(line[9:17], 16)
    except ValueError as exc:
        raise LedgerFrameError(f"frame header is not hexadecimal: {exc}") from exc
    body = line[18:]
    if len(body) != declared:
        raise LedgerFrameError(
            f"frame declares {declared} bytes and carries {len(body)}")
    if (binascii.crc32(body) & 0xFFFFFFFF) != expected_crc:
        raise LedgerFrameError("frame checksum does not match its payload")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerFrameError(f"frame payload is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LedgerFrameError("frame payload is not an object")
    kind = payload.get("kind")
    if kind not in KNOWN_KINDS:
        raise LedgerFrameError(
            f"record kind {str(kind)[:48]!r} is not one this slice reads; "
            f"a fence is not read past the first thing it does not understand")
    return payload


def _refuse_header(payload: Mapping[str, Any]) -> Optional[str]:
    """Why this header may not be believed, or None.

    A populated ledger that begins with a reservation is not a ledger with a
    tidy-up pending. It is a set of durable identities with no declared schema,
    no generation and no owner -- and a reader that accepted it would be
    reconstructing a fence from records whose format it had guessed. Every
    failure here is LEDGER_UNREADABLE rather than a repair, because the header
    is the only record that says what the others mean.
    """
    extra = set(payload) - HEADER_FIELDS
    missing = HEADER_FIELDS - set(payload)
    if extra or missing:
        return (f"header field set is not the declared one; missing "
                f"{sorted(missing)}, unexpected {sorted(extra)}")
    if payload["schema"] != LEDGER_SCHEMA:
        return (f"header declares schema {str(payload['schema'])[:64]!r}; this "
                f"reader reads {LEDGER_SCHEMA!r} only")
    if payload["frame_version"] != FRAME_VERSION:
        return (f"header declares frame version "
                f"{str(payload['frame_version'])[:32]!r}; this reader reads "
                f"{FRAME_VERSION!r} only")
    generation = payload["generation"]
    # Never inferred from the filename, never defaulted. A generation that the
    # reader supplied is a generation nobody committed to, and C1 is a lifetime
    # total over exactly this identifier (§11).
    if not isinstance(generation, str) or not generation:
        return "header carries no generation identifier"
    owner = payload["owner"]
    if not isinstance(owner, dict) or set(owner) != OWNER_FIELDS:
        return "header owner is not a ProcessIdentity field set"
    if not isinstance(owner["boot_id"], str) or not owner["boot_id"]:
        return "header owner carries no boot id"
    for numeric in ("pid", "start_ticks"):
        value = owner[numeric]
        # bool before int: bool subclasses int, and True is not a pid.
        if isinstance(value, bool) or not isinstance(value, int):
            return f"header owner {numeric} is not an integer"
    return None


# -- location and capability (§9 Amendment A) -----------------------------

def _mount_table(source: str = "/proc/self/mountinfo") -> List[Tuple[str, str]]:
    """(mount point, filesystem type), longest match wins at lookup."""
    entries: List[Tuple[str, str]] = []
    try:
        with open(source, "r", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if " - " not in line:
                    continue
                separator = fields.index("-")
                if len(fields) > separator + 1 and separator >= 5:
                    entries.append((fields[4], fields[separator + 1]))
    except OSError:
        return []
    return entries


def filesystem_type(path: str, *, mounts: Optional[Sequence[Tuple[str, str]]] = None
                    ) -> Optional[str]:
    """The filesystem backing `path`, by longest matching mount point.

    Deliberately not inferred from whether an operation on `path` succeeded:
    on a mount that does not exclude, every flock succeeds, including the two
    held by different coordinators in different directories. What is being
    attested is the mount.
    """
    table = list(mounts) if mounts is not None else _mount_table()
    resolved = os.path.realpath(path)
    best: Optional[str] = None
    best_len = -1
    for point, kind in table:
        if resolved == point or resolved.startswith(point.rstrip("/") + "/"):
            if len(point) > best_len:
                best, best_len = kind, len(point)
    return best


def refuse_location(path: str, *, repo_root: Optional[str] = None,
                    refused_prefixes: Optional[Sequence[str]] = None
                    ) -> Tuple[str, ...]:
    """Why this path may not hold a ledger. Empty means no objection.

    Not a filesystem question: a working tree on ext4 is still a working tree,
    where a checkout could silently move the fence.
    """
    refusals: List[str] = []
    if not os.path.isabs(path):
        refusals.append("PATH_NOT_ABSOLUTE")
    resolved = os.path.realpath(path)
    # Injectable for the same reason `mounts` is: a test needs a real file, and
    # every real temporary file is under a prefix this policy refuses. The
    # default is the strict tuple and a test pins that it is.
    for prefix in (REFUSED_PREFIXES if refused_prefixes is None
                   else tuple(refused_prefixes)):
        if resolved == prefix or resolved.startswith(prefix + "/"):
            refusals.append(f"PATH_UNDER_{prefix.strip('/').upper().replace('/', '_')}")
    if repo_root:
        root = os.path.realpath(repo_root)
        if resolved == root or resolved.startswith(root + "/"):
            refusals.append("PATH_INSIDE_WORKING_TREE")
    return tuple(refusals)


@dataclass(frozen=True)
class CapabilityAttestation:
    """Two independent claims that one lookup happens to answer here.

    They are kept apart because they come apart: a filesystem that fsyncs
    honestly and locks badly, or the reverse, is entirely ordinary. §7's
    reserve-before-write depends on durability and §3's mutex on exclusion, and
    one precondition covering both would tie two guarantees to whichever was
    checked.
    """

    directory: str
    observed_filesystem: Optional[str]
    lock_exclusion: str
    reservation_durability: str
    location_refusals: Tuple[str, ...] = ()

    @property
    def armable(self) -> bool:
        return (not self.location_refusals
                and self.lock_exclusion == ATTESTED_BY_FILESYSTEM_POLICY
                and self.reservation_durability == ATTESTED_BY_FILESYSTEM_POLICY)

    def refusals(self) -> Tuple[str, ...]:
        found: List[str] = list(self.location_refusals)
        if self.location_refusals:
            found.insert(0, LOCATION_REFUSED)
        if self.lock_exclusion != ATTESTED_BY_FILESYSTEM_POLICY:
            found.append(LOCK_EXCLUSION_UNATTESTED)
        if self.reservation_durability != ATTESTED_BY_FILESYSTEM_POLICY:
            found.append(RESERVATION_DURABILITY_UNATTESTED)
        return tuple(found)

    def as_dict(self) -> Dict[str, Any]:
        return {"directory": self.directory,
                # Recorded, not only the verdict: an operator needs to see what
                # was refused, not only that something was.
                "observed_filesystem": self.observed_filesystem,
                "lock_exclusion": self.lock_exclusion,
                "reservation_durability": self.reservation_durability,
                "allowlist": list(ALLOWLISTED_FILESYSTEMS),
                "armable": self.armable,
                "refusals": list(self.refusals())}


def attest(directory: str, *, repo_root: Optional[str] = None,
           mounts: Optional[Sequence[Tuple[str, str]]] = None,
           refused_prefixes: Optional[Sequence[str]] = None
           ) -> CapabilityAttestation:
    observed = filesystem_type(directory, mounts=mounts)
    attested = (ATTESTED_BY_FILESYSTEM_POLICY
                if observed in ALLOWLISTED_FILESYSTEMS else UNATTESTED)
    return CapabilityAttestation(
        directory=directory, observed_filesystem=observed,
        # One lookup, two claims. Written twice on purpose.
        lock_exclusion=attested, reservation_durability=attested,
        location_refusals=refuse_location(directory, repo_root=repo_root,
                                          refused_prefixes=refused_prefixes))


# -- reading --------------------------------------------------------------

@dataclass(frozen=True)
class LedgerRead:
    """What the ledger says, and how far it could be believed."""

    path: str
    readability: str
    generation: Optional[str] = None
    header_present: bool = False
    committed: Tuple[str, ...] = ()
    write_failed: Tuple[str, ...] = ()
    unresolved: Tuple[str, ...] = ()
    torn_tail: bool = False
    reservations_total: int = 0
    detail: Optional[str] = None
    records_read: int = 0

    @property
    def fenced(self) -> Tuple[str, ...]:
        """Every identity that may not be promoted again.

        All three states fence. A failed write fences because
        WriteResult(accepted=False) covers a rejection, a timeout and a landed
        write whose acknowledgement was lost, and those are indistinguishable
        from outside the bus (§2).
        """
        return tuple(sorted(set(self.committed) | set(self.write_failed)
                            | set(self.unresolved)))

    def as_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "readability": self.readability,
                "generation": self.generation,
                "header_present": self.header_present,
                "committed": list(self.committed),
                "write_failed": list(self.write_failed),
                "unresolved": list(self.unresolved),
                "fenced_total": len(self.fenced),
                "torn_tail": self.torn_tail,
                "reservations_total": self.reservations_total,
                "records_read": self.records_read,
                "detail": self.detail}


def read_ledger(path: str) -> LedgerRead:
    """Parse a ledger without creating, locking or modifying it.

    Opened read-only and read whole. A ledger that is absent is not an empty
    one: absent is a missing fence (§10) and empty is a fence with nothing
    behind it yet, and collapsing them would silently re-enable every promotion
    ever made.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        return LedgerRead(path=path, readability=LEDGER_UNAVAILABLE,
                          detail="no ledger at the configured path; this slice "
                                 "does not create one")
    except OSError as exc:
        return LedgerRead(path=path, readability=LEDGER_UNAVAILABLE,
                          detail=f"ledger could not be opened: {exc}")

    lines = data.split(b"\n")
    # A trailing newline yields a final empty element. Its absence is a tail
    # that was being written when the process stopped.
    torn = bool(lines and lines[-1] != b"")
    tail = lines[-1] if torn else b""
    complete = lines[:-1]

    generation: Optional[str] = None
    header_present = False
    reserved: Dict[int, str] = {}
    terminal: Dict[int, str] = {}
    order: List[int] = []
    # Strictly increasing across every record kind, header included. One
    # sequence, not one per kind: the writer's next_seq has to be answerable
    # from the last record of the file whatever that record is, and a per-kind
    # counter makes "the last record" a question with three answers.
    last_seq: Optional[int] = None

    for index, line in enumerate(complete):
        try:
            payload = parse_frame(line)
        except LedgerFrameError as exc:
            # A bad record with well-formed records after it is not a crash
            # artefact -- the writer got past it. That is corruption, and it is
            # not a torn tail however much it looks like one at this line.
            return LedgerRead(
                path=path, readability=LEDGER_UNREADABLE, records_read=index,
                detail=f"record {index}: {exc}")
        kind = payload["kind"]
        if kind == HEADER and index != 0:
            return LedgerRead(path=path, readability=LEDGER_UNREADABLE,
                              records_read=index,
                              detail="a HEADER appears after the first record")
        if index == 0 and kind != HEADER:
            return LedgerRead(
                path=path, readability=LEDGER_UNREADABLE, records_read=0,
                detail=f"the ledger holds records but begins with {kind}; a "
                       f"populated ledger begins with exactly one HEADER")
        seq = payload.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int):
            return LedgerRead(path=path, readability=LEDGER_UNREADABLE,
                              records_read=index,
                              detail=f"record {index}: seq is not an integer")
        if last_seq is not None and seq <= last_seq:
            return LedgerRead(
                path=path, readability=LEDGER_UNREADABLE, records_read=index,
                detail=f"record {index}: seq {seq} does not exceed the "
                       f"preceding {last_seq}")
        last_seq = seq
        if kind == HEADER:
            refusal = _refuse_header(payload)
            if refusal is not None:
                return LedgerRead(path=path, readability=LEDGER_UNREADABLE,
                                  records_read=index,
                                  detail=f"record {index}: {refusal}")
            generation = payload["generation"]
            header_present = True
            continue
        if kind == RESERVED:
            identity = payload.get("identity")
            if not isinstance(identity, str) or not identity:
                return LedgerRead(path=path, readability=LEDGER_UNREADABLE,
                                  records_read=index,
                                  detail=f"record {index}: RESERVED carries no identity")
            reserved[seq] = identity
            order.append(seq)
            continue
        reserves = payload.get("reserves")
        if reserves not in reserved:
            return LedgerRead(
                path=path, readability=LEDGER_UNREADABLE, records_read=index,
                detail=f"record {index}: {kind} resolves seq {reserves!r}, which "
                       f"no RESERVED record claimed")
        if reserves in terminal:
            return LedgerRead(path=path, readability=LEDGER_UNREADABLE,
                              records_read=index,
                              detail=f"record {index}: seq {reserves} resolved twice")
        terminal[reserves] = kind

    committed = tuple(reserved[s] for s in order if terminal.get(s) == COMMITTED)
    failed = tuple(reserved[s] for s in order if terminal.get(s) == FAILED)
    unresolved = tuple(reserved[s] for s in order if s not in terminal)

    return LedgerRead(
        path=path,
        readability=LEDGER_TORN if torn else AVAILABLE,
        generation=generation, header_present=header_present,
        committed=committed, write_failed=failed,
        unresolved=unresolved, torn_tail=torn,
        # C1 counts reservations, not confirmed writes (§11). A torn tail is a
        # reservation that may have been made, so it counts.
        reservations_total=len(reserved) + (1 if torn else 0),
        records_read=len(complete),
        detail=(f"tail of {len(tail)} bytes was being written; its identity "
                f"cannot be read" if torn else
                (None if header_present else
                 "zero-byte ledger: initialized and valid (\u00a710), and its "
                 "generation is not yet declared")))


# -- the store ------------------------------------------------------------

@dataclass
class LedgerStore:
    """Read-only view of the ledger, and the startup capability assessment.

    Holds no file handle, takes no lock, and has no write method to forget to
    guard. The write path is a later slice and its absence is enforced by there
    being nothing here to call.
    """

    path: str
    repo_root: Optional[str] = None
    mounts: Optional[Sequence[Tuple[str, str]]] = None
    refused_prefixes: Optional[Sequence[str]] = None
    _attestation: Optional[CapabilityAttestation] = field(default=None, init=False)
    _read: Optional[LedgerRead] = field(default=None, init=False)

    def attestation(self) -> CapabilityAttestation:
        if self._attestation is None:
            self._attestation = attest(os.path.dirname(self.path) or ".",
                                       repo_root=self.repo_root, mounts=self.mounts,
                                       refused_prefixes=self.refused_prefixes)
        return self._attestation

    def snapshot(self) -> LedgerRead:
        """A fresh, complete read. What every evaluation must use.

        §12 permits SHADOW to run beside an ARMED writer, so the fence moves
        underneath a long-lived reader. A view taken once at startup would let
        SHADOW report WOULD_PROMOTE for an identity ARMED had already fenced --
        the observation would drift from the thing it exists to model, silently
        and in the direction that flatters it.

        One snapshot is internally consistent because the file is read whole
        before it is parsed; it does not follow the file while it is being read.
        A snapshot that ends mid-record is LEDGER_TORN, exactly as §13 says.
        """
        self._read = read_ledger(self.path)
        return self._read

    def cached_snapshot(self) -> LedgerRead:
        """The last snapshot taken, or a first one if none was.

        Named for what it is. A reader that wants the current fence calls
        snapshot(); this exists for reporting the same view twice without
        re-reading, and it must never stand in for the current one.
        """
        if self._read is None:
            return self.snapshot()
        return self._read

    def seed_for_shadow(self, read: Optional[LedgerRead] = None) -> Tuple[str, ...]:
        """Identities SHADOW must treat as already promoted.

        Seeded from real history, because a SHADOW starting empty under-counts
        WOULD_BE_REFUSED and over-counts WOULD_PROMOTE -- it would measure a
        system that does not exist (§12).
        """
        read = self.snapshot() if read is None else read
        if read.readability in (LEDGER_UNAVAILABLE, LEDGER_UNREADABLE):
            return ()
        return read.fenced

    def shadow_fidelity(self, read: Optional[LedgerRead] = None) -> str:
        """A SHADOW that cannot seed is not a SHADOW with nothing to seed from.

        Reporting it as full would make §12's argument false in the one case
        where it matters, so the third state does not collapse into the first.
        """
        read = self.snapshot() if read is None else read
        if read.readability in (LEDGER_UNAVAILABLE, LEDGER_UNREADABLE):
            return FIDELITY_UNSEEDED
        if not self.attestation().armable:
            return FIDELITY_DEGRADED_NOT_ARMABLE
        return FIDELITY_FULL

    def armed_capability(self, read: Optional[LedgerRead] = None
                         ) -> Tuple[str, Tuple[str, ...]]:
        """ARMED fails closed on every condition this slice can observe.

        This slice observes some and not all: exclusive ownership, generation
        ceilings and reconciliation are later slices, so an AVAILABLE here means
        nothing this module checked refuses ARMED -- never that ARMED is
        reachable.
        """
        refusals = list(self.attestation().refusals())
        read = self.snapshot() if read is None else read
        if read.readability == LEDGER_UNAVAILABLE:
            refusals.append(LEDGER_UNAVAILABLE)
        elif read.readability == LEDGER_UNREADABLE:
            refusals.append(LEDGER_UNREADABLE)
        elif read.torn_tail:
            refusals.append(LEDGER_TORN)
        return (UNAVAILABLE if refusals else AVAILABLE), tuple(refusals)

    def assessment(self, *, mode: str = "SHADOW") -> Dict[str, Any]:
        """Published at startup, not discovered at the moment of arming (§9 A).

        A mode gate that stayed silent until someone tried to arm would be the
        same design with the operator's cost moved later, for no gain.
        """
        # One snapshot, threaded through all three. Three separate fresh reads
        # would let the published assessment describe three different ledgers.
        read = self.snapshot()
        capability, refusals = self.armed_capability(read)
        return {
            "schema": SCHEMA,
            "mode": mode,
            "armed_capability": capability,
            "armed_refusals": list(refusals),
            "ledger_readability": (AVAILABLE if read.readability == AVAILABLE
                                   else read.readability),
            "shadow_fidelity": self.shadow_fidelity(read),
            "attestation": self.attestation().as_dict(),
            "ledger": read.as_dict(),
            "generation_totals": {
                "reservations": read.reservations_total,
                "unresolved": len(read.unresolved) + (1 if read.torn_tail else 0),
            },
            "writes": False,
            "creates": False,
            "locks": False,
            "scope_note": (
                "READ PATH ONLY. THIS SLICE MAY LEARN WHETHER THE FENCE EXISTS "
                "AND WHAT IT SAYS. IT MAY NOT MOVE THE FENCE"),
        }
