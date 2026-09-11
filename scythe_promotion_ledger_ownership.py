"""Who owns the ledger, and for how long.

§17 slice 6b. Supplies the one thing slice 6 deliberately lacked: a producer of
live ownership scopes. Everything about `fcntl` lives here, so the write path
stays ignorant of *how* ownership is obtained and keeps its own assertion that
it takes no lock.

Implements PROMOTION_EXECUTION_CONTRACT.md §9 and §13d Amendment E.

Three things shape this module:

  **The lock is on a derived sidecar** (E.1), and it is derived from the
  **lineage root** rather than from any one generation file (§13e F.10).
  Acquiring ownership requires opening something, creating the ledger requires
  ownership, and opening a file that does not exist fails -- so the lock cannot
  be on a ledger. The name is derived and never configured, because a configured
  lock reproduces §9 Amendment A's failure in a new place: two coordinators, one
  lineage, two locks, both acquired, both satisfied.

  There is **no compatibility path** to E.1's per-generation sidecar. Consulting
  whichever of two files exists, or migrating one to the other, would manufacture
  exactly the ambiguity this derivation removes -- and the old path was never
  production-reachable, so there is no deployment to preserve.

  **A successful flock is not ownership** (E.2). On a mount that does not
  exclude, every acquisition succeeds and none of them means anything. The
  filesystem attestation is checked here, where scopes are minted, rather than
  at arming -- a gate checked somewhere other than where it is relied on is not
  a gate.

  **Ownership is acquired once and never reacquired** (E.3). A scope is a window
  onto that holding, so its liveness is derived from this object rather than
  tracked locally: a scope that outlived the lock would keep authorising appends
  after ownership ended.

And one thing the attestation alone does not establish. A path-based check
answers a question about a *name*, and the object that ends up locked is reached
through that name at four separate moments: attestation, open, `flock`, and
minting. A mount arriving or departing anywhere in that window leaves the
producer holding a lock on something other than what it attested, with every
check having reported success. So the attestation runs twice: once on the path
before anything is created, and once **on the open descriptor** immediately
before the first scope exists. The second is the authoritative one, because a
descriptor cannot be remounted out from under itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from typing import Any, Dict, Optional, Sequence, Tuple

from scythe_promotion_ledger_store import (
    ALLOWLISTED_FILESYSTEMS, LOCK_EXCLUSION_UNATTESTED,
    RESERVATION_DURABILITY_UNATTESTED, attest,
)
from scythe_promotion_ledger_writer import (
    LEDGER_NOT_OWNED, OWNERSHIP_LOST, RESERVATION_NOT_DURABLE,
    LedgerWriteRefused, OwnershipScope,
)

SCHEMA = "scythe.promotion-ledger-ownership.v1"

# Derived, never configured (§13d E.1). The derivation is the load-bearing part:
# it forecloses two coordinators locking different files for one ledger, rather
# than documenting against it.
SIDECAR_SUFFIX = ".lock"

HELD = "HELD"
NOT_HELD = "NOT_HELD"
HALTED = "APPENDS_HALTED"


def sidecar_path(lineage_root: str) -> str:
    """One lineage, one lock (§13e F.10).

    Derived from the **root**, so every generation beneath it contends for the
    same file. Locks on two generation files of one lineage exclude nobody who
    matters, which is what F.10 corrected in E.1.
    """
    return lineage_root + SIDECAR_SUFFIX


def device_filesystems(source: str = "/proc/self/mountinfo") -> Dict[int, str]:
    """st_dev -> filesystem type, from the mount table's major:minor field.

    Keyed by device rather than by mount point, because this is what an open
    descriptor can be checked against. A path lookup answers *what is mounted at
    this name right now*; `os.fstat(fd).st_dev` answers *what is this object
    actually on*, and only the second survives a remount.
    """
    devices: Dict[int, str] = {}
    try:
        with open(source, "r", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if " - " not in line or len(fields) < 6:
                    continue
                separator = fields.index("-")
                if len(fields) <= separator + 1:
                    continue
                major, _, minor = fields[2].partition(":")
                try:
                    devices[os.makedev(int(major), int(minor))] = \
                        fields[separator + 1]
                except (ValueError, OverflowError):
                    continue
    except OSError:
        return {}
    return devices


@dataclass
class LedgerOwnership:
    """Process-lifetime ownership of one ledger, and the source of its scopes.

    Not reusable across ledgers and not reacquirable: ownership is taken once,
    and an owner that has lost or released it stays lost. Reacquisition would
    mean a window during which another process could have written, and nothing
    in this design can tell whether it did.
    """

    lineage_root: str
    mounts: Optional[Sequence[Tuple[str, str]]] = None
    repo_root: Optional[str] = None
    refused_prefixes: Optional[Sequence[str]] = None
    # st_dev -> filesystem type, for the post-open check. Injectable for the
    # same reason `mounts` is: a test needs to state what the kernel would say.
    devices: Optional[Dict[int, str]] = None

    def __post_init__(self) -> None:
        self._fd: Optional[int] = None
        self._held = False
        # Never held and no-longer-held are different facts. The first is
        # LEDGER_NOT_OWNED -- go and look at who does hold it. The second is
        # OWNERSHIP_LOST and is terminal, because ownership is taken once and a
        # window between losing it and taking it again is one nothing here can
        # see across.
        self._ever_held = False
        self._halt_reason: Optional[str] = None
        self._refusals: Tuple[str, ...] = ()
        # A mount that changed under an acquisition is not a condition to retry
        # through. The owner is finished, and a later acquire() refuses without
        # touching the filesystem again.
        self._terminal = False
        self._preflight_dev: Optional[int] = None

    # -- acquisition ------------------------------------------------------

    @property
    def sidecar(self) -> str:
        return sidecar_path(self.lineage_root)

    @property
    def holds(self) -> bool:
        """What every scope's liveness is derived from (§13d E.3)."""
        return self._held

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    def attestation_refusals(self) -> Tuple[str, ...]:
        directory = os.path.dirname(os.path.abspath(self.lineage_root))
        attestation = attest(directory, repo_root=self.repo_root,
                             mounts=self.mounts,
                             refused_prefixes=self.refused_prefixes)
        return attestation.refusals()

    def acquire(self) -> bool:
        """Attest, then lock. Returns False rather than raising: failing to
        acquire refuses ARMED and does **not** refuse SHADOW (§9).

        The attestation runs first and the sidecar is **not created** when it
        fails. Creating a lock file on a mount where locking means nothing would
        leave the one artefact that makes the next reader believe someone owned
        this.
        """
        if self._held:
            return True
        if self._terminal:
            return False

        # Stage one: the path, before anything is created. Its only job is to
        # stop a sidecar appearing on a mount already known to be unacceptable.
        refusals = self.attestation_refusals()
        if refusals:
            self._refusals = refusals
            return False
        directory = os.path.dirname(os.path.abspath(self.lineage_root))
        if not os.path.isdir(directory):
            self._refusals = (LEDGER_NOT_OWNED,)
            return False
        self._preflight_dev = os.stat(directory).st_dev

        # O_EXCL first, so the answer to *did this process create it* is a fact
        # rather than an inference. It decides whether the file may be removed
        # if stage two refuses.
        created = False
        try:
            fd = os.open(self.sidecar, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            created = True
        except FileExistsError:
            fd = os.open(self.sidecar, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            self._refusals = (LEDGER_NOT_OWNED,)
            return False

        # Stage two: the descriptor, and this is the authoritative one. The
        # lock is already held, so nothing can move underneath the answer.
        mismatch = self._verify_descriptor(fd)
        if mismatch is not None:
            self._abandon(fd, created, mismatch)
            return False

        self._fd = fd
        self._held = True
        self._ever_held = True
        self._refusals = ()
        self._write_diagnostics()
        return True

    def _device_map(self) -> Dict[int, str]:
        return device_filesystems() if self.devices is None else dict(self.devices)

    def _verify_descriptor(self, fd: int) -> Optional[str]:
        """What the locked object is actually on. None if it is what we attested.

        Two checks, because they fail differently. The device changing means the
        name now reaches a different object than the one attested. The device
        being the same but off the allowlist means the mount itself changed
        underneath a stable name -- and a path lookup cannot see either.
        """
        observed = os.fstat(fd).st_dev
        if self._preflight_dev is not None and observed != self._preflight_dev:
            return (f"the locked descriptor is on device {observed}, and "
                    f"{self._preflight_dev} was attested")
        found = self._device_map().get(observed)
        if found not in ALLOWLISTED_FILESYSTEMS:
            return (f"the locked descriptor is on {found!r}, which is not an "
                    f"attested filesystem, whatever the path said")
        return None

    def _abandon(self, fd: int, created: bool, detail: str) -> None:
        """Give the lock back and finish. The ledger is never touched.

        The sidecar is removed **only** if this process created it during this
        attempt -- proven by O_EXCL, not assumed. Removing one we found would
        destroy another process's lock file on the strength of our own bad luck.
        """
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        if created:
            try:
                os.unlink(self.sidecar)
            except OSError:              # pragma: no cover - best effort
                pass
        self._terminal = True
        self._refusals = (LOCK_EXCLUSION_UNATTESTED,
                          RESERVATION_DURABILITY_UNATTESTED)
        self._verification_detail = detail

    def _write_diagnostics(self) -> None:
        """Who holds it, for whoever is reading `lsof` at 3am.

        Diagnostic only. The authoritative owner is the ledger's header record
        (§9); a second authority would be a second answer to a question that has
        one, and this file is not fsynced because nothing depends on it.
        """
        record = {"schema": SCHEMA, "boot_id": _boot_id(), "pid": os.getpid(),
                  "lineage_root": self.lineage_root,
                  "note": "DIAGNOSTIC ONLY. THE HEADER RECORD NAMES THE OWNER"}
        body = json.dumps(record, sort_keys=True).encode("utf-8") + b"\n"
        os.ftruncate(self._fd, 0)
        os.pwrite(self._fd, body, 0)

    def release(self) -> None:
        """Give up ownership. Every outstanding scope goes dead with it."""
        self._held = False
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    # -- the producer the writer calls ------------------------------------

    def scope(self, path: str) -> Optional[OwnershipScope]:
        """Mint a scope, or refuse. This is `LedgerWriter.ownership`.

        Three refusals, and they are three different facts:

        **Never acquired** -> None, which the writer reports as
        LEDGER_NOT_OWNED. Absent authority: go and find who does hold it.

        **Acquired and since lost or released** -> OWNERSHIP_LOST, and terminal.
        Invalidated authority, which is not the same as never having had it, and
        the difference is the one already drawn elsewhere in this contract.

        **Halted after an unsyncable write** -> RESERVATION_NOT_DURABLE. Raised
        rather than returned, because reporting it as *not owned* would send an
        operator to the lock when the uncertainty is about the file.
        """
        if self._halt_reason is not None:
            raise LedgerWriteRefused(
                self._halt_reason,
                "this process stopped appending after a reservation whose "
                "durability could not be established; a new session does not "
                "clear that, because the uncertainty is about the file")
        if self._terminal:
            return None
        if not self._held:
            if self._ever_held:
                raise LedgerWriteRefused(
                    OWNERSHIP_LOST,
                    "this owner held the ledger and no longer does; ownership "
                    "is not reacquired")
            return None
        if not self.covers(path):
            return None
        return OwnershipScope(path, holder=self)

    def covers(self, path: str) -> bool:
        """Does this lock authorise writing that file?

        A generation beneath the root, rather than the root itself -- the root
        is an identity, not a file anything is written to. The check is on the
        derived prefix, so a scope for one lineage cannot admit a write to a
        neighbouring one that happens to sit in the same directory.
        """
        root = os.path.realpath(self.lineage_root)
        target = os.path.realpath(path)
        return target.startswith(root + ".")

    def halt_appends(self, reason: str) -> None:
        """Stop appending in this process, through **every** session (§13d E.6).

        Expiring only the live scope would leave a back door: the caller opens
        another session and continues. A process that cannot make a reservation
        durable should stop making them, and the halt therefore lives on the
        owner rather than on the scope that discovered it.
        """
        self._halt_reason = reason

    def status(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "lineage_root": self.lineage_root,
            "sidecar": self.sidecar,
            "sidecar_name_is_derived": True,
            "ownership": (HALTED if self.halted
                          else HELD if self._held else NOT_HELD),
            "refusals": list(self._refusals),
            "halt_reason": self._halt_reason,
            "reacquires": False,
            "ever_held": self._ever_held,
            "terminal": self._terminal,
            "verification_detail": getattr(self, "_verification_detail", None),
            "attested_twice": True,
            "note": (
                "THE LOCK IS TAKEN ONCE AND NEVER REACQUIRED. A WINDOW BETWEEN "
                "LOSING IT AND TAKING IT AGAIN IS ONE NOTHING HERE CAN SEE "
                "ACROSS"),
        }


def _boot_id() -> str:
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as h:
            return h.read().strip()
    except OSError:                      # pragma: no cover - diagnostics only
        return "unknown"
