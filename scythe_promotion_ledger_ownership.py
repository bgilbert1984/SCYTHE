"""Who owns the ledger, and for how long.

§17 slice 6b. Supplies the one thing slice 6 deliberately lacked: a producer of
live ownership scopes. Everything about `fcntl` lives here, so the write path
stays ignorant of *how* ownership is obtained and keeps its own assertion that
it takes no lock.

Implements PROMOTION_EXECUTION_CONTRACT.md §9 and §13d Amendment E.

Three things shape this module:

  **The lock is on a derived sidecar** (E.1). Acquiring ownership requires
  opening the ledger, creating the ledger requires ownership, and opening one
  that does not exist fails -- so the lock cannot be on the ledger itself. The
  sidecar's name is derived from the ledger path and is never configured,
  because a configured lock file reproduces §9 Amendment A's failure in a new
  place: two coordinators, one ledger, two locks, both acquired, both satisfied.

  **A successful flock is not ownership** (E.2). On a mount that does not
  exclude, every acquisition succeeds and none of them means anything. The
  filesystem attestation is checked here, where scopes are minted, rather than
  at arming -- a gate checked somewhere other than where it is relied on is not
  a gate.

  **Ownership is acquired once and never reacquired** (E.3). A scope is a window
  onto that holding, so its liveness is derived from this object rather than
  tracked locally: a scope that outlived the lock would keep authorising appends
  after ownership ended.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import json
import os
from typing import Any, Dict, Optional, Sequence, Tuple

from scythe_promotion_ledger_store import (
    LOCK_EXCLUSION_UNATTESTED, RESERVATION_DURABILITY_UNATTESTED, attest,
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


def sidecar_path(ledger_path: str) -> str:
    return ledger_path + SIDECAR_SUFFIX


@dataclass
class LedgerOwnership:
    """Process-lifetime ownership of one ledger, and the source of its scopes.

    Not reusable across ledgers and not reacquirable: ownership is taken once,
    and an owner that has lost or released it stays lost. Reacquisition would
    mean a window during which another process could have written, and nothing
    in this design can tell whether it did.
    """

    ledger_path: str
    mounts: Optional[Sequence[Tuple[str, str]]] = None
    repo_root: Optional[str] = None
    refused_prefixes: Optional[Sequence[str]] = None

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

    # -- acquisition ------------------------------------------------------

    @property
    def sidecar(self) -> str:
        return sidecar_path(self.ledger_path)

    @property
    def holds(self) -> bool:
        """What every scope's liveness is derived from (§13d E.3)."""
        return self._held

    @property
    def halted(self) -> bool:
        return self._halt_reason is not None

    def attestation_refusals(self) -> Tuple[str, ...]:
        directory = os.path.dirname(os.path.abspath(self.ledger_path))
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
        refusals = self.attestation_refusals()
        if refusals:
            self._refusals = refusals
            return False
        directory = os.path.dirname(os.path.abspath(self.ledger_path))
        if not os.path.isdir(directory):
            self._refusals = (LEDGER_NOT_OWNED,)
            return False
        fd = os.open(self.sidecar, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            self._refusals = (LEDGER_NOT_OWNED,)
            return False
        self._fd = fd
        self._held = True
        self._ever_held = True
        self._refusals = ()
        self._write_diagnostics()
        return True

    def _write_diagnostics(self) -> None:
        """Who holds it, for whoever is reading `lsof` at 3am.

        Diagnostic only. The authoritative owner is the ledger's header record
        (§9); a second authority would be a second answer to a question that has
        one, and this file is not fsynced because nothing depends on it.
        """
        record = {"schema": SCHEMA, "boot_id": _boot_id(), "pid": os.getpid(),
                  "ledger": self.ledger_path,
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

        Returning None produces LEDGER_NOT_OWNED at the writer. The halted case
        raises instead, because RESERVATION_NOT_DURABLE is a different fact and
        reporting it as *not owned* would send an operator to the lock.
        """
        if self._halt_reason is not None:
            raise LedgerWriteRefused(
                self._halt_reason,
                "this process stopped appending after a reservation whose "
                "durability could not be established; a new session does not "
                "clear that, because the uncertainty is about the file")
        if not self._held:
            if self._ever_held:
                raise LedgerWriteRefused(
                    OWNERSHIP_LOST,
                    "this owner held the ledger and no longer does; ownership "
                    "is not reacquired")
            return None
        if os.path.realpath(path) != os.path.realpath(self.ledger_path):
            return None
        return OwnershipScope(path, holder=self)

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
            "ledger": self.ledger_path,
            "sidecar": self.sidecar,
            "sidecar_name_is_derived": True,
            "ownership": (HALTED if self.halted
                          else HELD if self._held else NOT_HELD),
            "refusals": list(self._refusals),
            "halt_reason": self._halt_reason,
            "reacquires": False,
            "ever_held": self._ever_held,
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
