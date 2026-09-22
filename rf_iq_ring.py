"""Phase 1: a bounded, process-local raw-IQ working buffer.

This is the first place in SCYTHE where raw IQ is retained beyond a single FFT
block.  It ships alone -- no channelizer, no cyclic detector, no DSP of any kind
-- because the retention mechanism and the thing that consumes it deserve
independently reviewable boundaries.

The operator authority this module implements, in full::

    PROCESS-LOCAL - VOLATILE - FIXED-CAPACITY
    NON-PERSISTENT - NON-TRANSPORTABLE - NON-MODEL-CONTEXT
    BRIDGE-OWNED - INVALIDATED ON SIGNAL-CHAIN CHANGE

    This is permission for a DSP working buffer, not permission for an IQ archive.

That last sentence is the specification.  Everything below is an attempt to make
the buffer structurally incapable of becoming an archive:

* the allocation is made once, at a fixed capacity, and never grows;
* samples are never serialized, logged, returned by a status API, or placed in an
  exception message -- ``__repr__`` and ``__reduce__`` are overridden on both the
  ring and the window to make the accidental cases fail rather than leak;
* there is no disk fallback, no swap-oriented buffering and no crash-dump
  facility, and none may be added under this approval;
* a child process cannot construct one at all;
* every consumer receives a read-only copy, never the writable ring.

Why invalidation is the load-bearing part
-----------------------------------------
A size limit is not a boundary.  Samples captured before and after a retune, a
sample-rate change, a gain-regime change or a reconnect were produced by
different signal chains, and a cyclic statistic computed across that seam is a
measurement of the seam.  ``invalidate`` therefore clears the ring outright and
advances ``configuration_epoch``; a window issued under an earlier epoch can
never be verified again.  ``append`` also invalidates on its own when the
declared ``signal_chain_hash`` changes, so the guarantee does not depend on a
caller remembering to call it.

Windows and their digests
-------------------------
``acquire_window`` issues an ``IQWindow`` with a bridge-generated identifier and a
bridge-computed digest.  The digest is over the samples, bound to the signal
chain and the epoch, so it is reproducible for identical content and worthless
across a configuration change.  ``verify_window`` answers whether a claimed
window is still real: issued here, matching digest, same epoch, and not yet
overwritten by later appends.

This closes the gap named in ``docs/RF_Signal_Family_Classifier_Scope.md`` 5.4.
Phase 0 validates that a ``source_window_hash`` is *shaped* like a digest, which
proves a caller knows the format and nothing more.  With a ring that owns window
records, a claim can be checked against a window that actually existed.  Wiring
that check into the classification gate is a separate change; this module ships
the capability, not the policy.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import os
import threading
import time
import weakref
from typing import Any, Dict, Optional, Tuple

import numpy as np

from rf_promotion_geometry import (
    PROMOTION_BYTES_PER_SAMPLE,
    PROMOTION_CYCLE_RESOLUTION_HZ,
    PROMOTION_DTYPE,
    PROMOTION_SAMPLE_RATE_HZ,
    PROMOTION_WINDOW_MS,
    PROMOTION_WINDOW_OVERLAP,
    PROMOTION_WINDOW_SAMPLES,
)
from rf_signal_chain_identity import UNDECLARED


SCHEMA = "scythe.rf-iq-ring.v1"

# The approved configuration. 256 ms at 2.048 MS/s, complex64.
#
# These are a *registered detector configuration*, not a universal optimum. A
# later validation may authorise longer or overlapping windows, and when it does
# it must register that as a new configuration rather than silently changing what
# squared-envelope-cyclic.v1 was validated against.
#
# **Re-exported, not declared.** §5.20 correction A gave the geometry one home
# in `rf_promotion_geometry`, which imports nothing but `typing`. The names here
# keep their spelling because every caller already uses them and a rename is not
# a correction; what changed is that there is no longer a second place the
# numbers could drift from.
DEFAULT_SAMPLE_RATE_HZ = PROMOTION_SAMPLE_RATE_HZ
DEFAULT_WINDOW_MS = PROMOTION_WINDOW_MS
DEFAULT_CAPACITY_SAMPLES = PROMOTION_WINDOW_SAMPLES
STORAGE_DTYPE = PROMOTION_DTYPE
BYTES_PER_SAMPLE = PROMOTION_BYTES_PER_SAMPLE
# 1 / 0.256 s. The finest cycle-frequency spacing a 256 ms window can resolve.
NOMINAL_CYCLE_RESOLUTION_HZ = PROMOTION_CYCLE_RESOLUTION_HZ

# Windows are non-overlapping initially: overlap multiplies computation and
# correlates consecutive verdicts before there is any evidence it buys detection
# performance.
#
# Non-overlap is a property of the **capture rule**, not of this ring:
# `acquire_window` copies and removes nothing, so two calls with no appends
# between them return the same samples under two window IDs. See
# `samples_since` and §5.20 correction A.
WINDOW_OVERLAP = PROMOTION_WINDOW_OVERLAP

# Every reason a ring may be cleared. The set is closed on purpose -- an
# unrecognised reason raises rather than clearing quietly, because a clear whose
# cause nobody named is a clear nobody can audit.
INVALIDATION_REASONS: Tuple[str, ...] = (
    "RETUNE",
    "SAMPLE_RATE_CHANGE",
    "GAIN_CHANGE",
    "DIRECT_SAMPLING_CHANGE",
    "SIGNAL_CHAIN_CHANGE",
    "DISCONNECT",
    "RECONNECT",
    "CAPTURE_OWNER_CHANGE",
    "ORCHESTRATOR_STOP",
    "CLOCK_DISCONTINUITY",
    "SOURCE_STARVED",
)

RING_STATES: Tuple[str, ...] = ("INVALIDATED", "FILLING", "READY", "CLOSED")

# What `verify_window` actually binds, stated because the name overpromises.
#
# It takes two strings. It proves that **this ring issued a window with that ID
# and that digest, under the current epoch, and has not evicted it**. It does
# not see an `IQWindow` object at all, so it cannot and does not prove that any
# particular object in a caller's hand is the one that was issued: a caller
# holding a genuine ID and digest can construct a different `IQWindow` carrying
# different samples, different metadata, or a different interval, and
# `verify_window` will return WINDOW_VERIFIED for the pair of strings it was
# given.
#
# That is sufficient for its existing job -- refusing a `source_window_hash`
# that no window ever carried. It is **not** sufficient to attest an object
# before publishing its bytes, which is what §5.20's capture writer will need:
# an authoritative ring operation over the exact nominal object, all of its
# metadata, its sample interval, and a digest recomputed from the bytes being
# published. That operation does not exist yet and this constant exists so that
# nobody mistakes the two-string check for it.
VERIFICATION_BINDS = "WINDOW_ID_AND_DIGEST_ONLY"
VERIFICATION_DOES_NOT_BIND: Tuple[str, ...] = (
    "the IQWindow object",
    "its samples",
    "its metadata",
    "its sample interval",
)
# §5.24, accepted 2026-09-16 and implemented here. `attest_window` is the
# operation the paragraph above says does not exist. It is a **second**
# operation with a stronger guarantee, not a widening of `verify_window`:
# the two-string check keeps its narrower job and its narrower name.
FULL_OBJECT_ATTESTATION = "ATTEST_WINDOW_RETURNS_A_LIVE_SCOPE"

# -- attestation refusals ---------------------------------------------------
#
# These raise rather than returning a result object, which is the one place
# this module departs from "a refusal is a result, not an exception". The
# reason is the shape of the operation: `with ring.attest_window(w) as a:` has
# to be **impossible to enter** when attestation fails, and a falsy result
# object entered by a caller who did not check is the hole again.
ATTESTATION_NOT_AN_IQ_WINDOW = "ATTESTATION_NOT_AN_IQ_WINDOW"
ATTESTATION_RING_CLOSED = "ATTESTATION_RING_CLOSED"
ATTESTATION_WINDOW_NOT_ISSUED = "ATTESTATION_WINDOW_NOT_ISSUED"
ATTESTATION_EPOCH_CHANGED = "ATTESTATION_EPOCH_CHANGED"
ATTESTATION_WINDOW_EVICTED = "ATTESTATION_WINDOW_EVICTED"
ATTESTATION_METADATA_MISMATCH = "ATTESTATION_METADATA_MISMATCH"
ATTESTATION_REPRESENTATION_INVALID = "ATTESTATION_REPRESENTATION_INVALID"
ATTESTATION_DIGEST_MISMATCH = "ATTESTATION_DIGEST_MISMATCH"
ATTESTATION_RING_LIFETIME_MISMATCH = "ATTESTATION_RING_LIFETIME_MISMATCH"
ATTESTATION_SCOPE_NOT_ACTIVE = "ATTESTATION_SCOPE_NOT_ACTIVE"
ATTESTATION_SCOPE_NOT_MINTED = "ATTESTATION_SCOPE_NOT_MINTED"
ATTESTATION_WRITE_INCOMPLETE = "ATTESTATION_WRITE_INCOMPLETE"
ATTESTATION_WRITE_TARGET_INVALID = "ATTESTATION_WRITE_TARGET_INVALID"
ATTESTATION_REFUSALS: Tuple[str, ...] = (
    ATTESTATION_NOT_AN_IQ_WINDOW, ATTESTATION_RING_CLOSED,
    ATTESTATION_WINDOW_NOT_ISSUED, ATTESTATION_EPOCH_CHANGED,
    ATTESTATION_WINDOW_EVICTED, ATTESTATION_METADATA_MISMATCH,
    ATTESTATION_REPRESENTATION_INVALID, ATTESTATION_DIGEST_MISMATCH,
    ATTESTATION_SCOPE_NOT_ACTIVE, ATTESTATION_SCOPE_NOT_MINTED,
    ATTESTATION_WRITE_INCOMPLETE, ATTESTATION_WRITE_TARGET_INVALID,
)

# Metadata compared field by field against the ring's own record. Declared as a
# tuple rather than written into the comparison, so "every stored metadata
# field" is a list somebody can read rather than a claim about a loop body.
ATTESTED_METADATA_FIELDS: Tuple[str, ...] = (
    "configuration_epoch", "first_sample_index", "last_sample_index",
    "sample_count", "sample_rate_hz", "start_time", "end_time",
    "signal_chain_hash", "digest", "ring_lifetime_id",
)

# §5.26. Two rings each count from zero, so indices from different rings
# compare cleanly and mean nothing. `_total_appended` is zeroed only in
# `__init__` and `configuration_epoch` restarts at zero too, so before this
# nothing in a header distinguished one lifetime from another.
RING_LIFETIME_ID_BYTES = 16


def _mint_ring_lifetime_id() -> str:
    """Unpredictable and collision-resistant, minted once per ring instance.

    A module-level function rather than a constructor argument on purpose: a
    parameter is a caller's claim, and this is the identity that attestation
    compares against. A test needing determinism patches this; it cannot pass
    a value in.
    """
    return os.urandom(RING_LIFETIME_ID_BYTES).hex()

WINDOW_INTERVAL_OVERLAP = "WINDOW_INTERVAL_OVERLAP"


def window_interval_disjoint(previous: "IQWindow", current: "IQWindow") -> bool:
    """Interval arithmetic over two windows of **one ring**, and nothing more::

        current.first_sample_index >= previous.last_sample_index

    **It assumes what it cannot establish.** A sample index is meaningful only
    relative to the ring that assigned it, and this function does not and cannot
    check that both windows came from the same one -- two rings each count from
    zero, so indices from different rings compare cleanly and mean nothing. The
    caller must have established common provenance first; this then answers the
    arithmetic question and only that one.

    Under the promotion geometry, where every window is the full 524 288
    samples, it is exactly §5.20 correction A's rule --
    ``current.first >= previous.first + 524_288`` -- and it stays correct for
    the shorter windows a development configuration may take, which the
    constant-subtraction form does not.

    This is the whole reason the indices are exposed. `acquire_window` copies
    and removes nothing, so two calls with no appends between them return **the
    same samples** under two window IDs, two digests and two timestamps, all of
    which differ. The indices are the only part that does not.
    """
    return current.first_sample_index >= previous.last_sample_index

# Outcomes of a window request. Same shape as the signal-family contract: a small
# stable vocabulary, and a refusal is a result rather than a missing value.
ACQUISITION_REASONS: Dict[str, str] = {
    "WINDOW_ACQUIRED": "A COMPLETE WINDOW WAS COPIED OUT OF THE RING",
    "INSUFFICIENT_WINDOW": (
        "FEWER RETAINED SAMPLES THAN THE REQUESTED WINDOW. THE RING HAS NOT "
        "REFILLED SINCE THE LAST INVALIDATION"
    ),
    "WINDOW_TOO_LARGE": "THE REQUESTED WINDOW EXCEEDS THE RING'S FIXED CAPACITY",
    "RING_CLOSED": "THE RING HAS BEEN CLOSED AND HOLDS NOTHING",
}

VERIFICATION_REASONS: Dict[str, str] = {
    "WINDOW_VERIFIED": "THE WINDOW WAS ISSUED BY THIS RING AND ITS SAMPLES ARE STILL HELD",
    "WINDOW_NOT_ISSUED": (
        "NO SUCH WINDOW WAS ISSUED BY THIS RING. A CORRECTLY SHAPED IDENTIFIER IS "
        "NOT AN ISSUED ONE"
    ),
    "DIGEST_MISMATCH": "THE WINDOW EXISTS BUT THE CLAIMED DIGEST IS NOT THE ONE ISSUED",
    "EPOCH_CHANGED": (
        "THE WINDOW PREDATES AN INVALIDATION. ITS SAMPLES AND THE CURRENT ONES "
        "CAME FROM DIFFERENT SIGNAL CHAINS"
    ),
    "WINDOW_EVICTED": (
        "THE WINDOW'S SAMPLES HAVE BEEN OVERWRITTEN BY LATER APPENDS AND NO "
        "LONGER EXIST TO BE RE-EXAMINED"
    ),
    "RING_CLOSED": "THE RING HAS BEEN CLOSED, SO NOTHING CAN BE VERIFIED AGAINST IT",
}

DIGEST_ALGORITHM = "blake2s"

# Only metadata is kept, never samples, so the record table costs nothing that
# matters and can outlive the samples it describes.
MAX_TRACKED_WINDOWS = 32

# An explicitly declared non-orchestrator role may not hold raw IQ. An empty role
# is a test or a developer shell, which is not a child process; the orchestrator
# always stamps 'child' on the ones it spawns (scythe_orchestrator.py).
ALLOWED_PROCESS_ROLES: Tuple[str, ...] = ("", "orchestrator")

# §5.25: the attested scope supplies "capture times and the named clock
# authority". The ring stamps `start_time` and `end_time` from an injectable
# `now`, so *which* clock produced them is a property of the ring instance and
# nothing recorded it. A capture header that carried the times without naming
# their source would be asserting a quality it never established.
#
# `POSIX_REALTIME` is what `time.time` reads. It is named rather than praised:
# it is settable, it is not monotonic, and nothing here has disciplined it
# against a reference. A ring handed some other `now` and not told what it is
# records `UNDECLARED` -- the named absence, never a guess -- and the header
# carries that rather than the default it would have been convenient to assume.
CLOCK_AUTHORITY_POSIX_REALTIME = "POSIX_REALTIME"
CLOCK_AUTHORITIES: Tuple[str, ...] = (CLOCK_AUTHORITY_POSIX_REALTIME, UNDECLARED)


def _window_digest(signal_chain_hash: str, configuration_epoch: int,
                   sample_count: int, payload: Any) -> str:
    """The window digest, from explicit inputs and the payload bytes.

    One implementation, two callers: issuance computes it over the bytes it is
    about to freeze, and attestation recomputes it over the bytes bound into the
    scope. **The schema and revision do not change** -- an amendment that
    quietly moved a digest would invalidate every product already carrying one,
    so this is a refactor of where the inputs come from and of nothing else.

    `payload` is anything supporting the buffer protocol. A read-only
    `memoryview` over an immutable array hashes identically to the `bytes` it
    views, which is why attestation needs no copy of its own.
    """
    hasher = hashlib.blake2s(digest_size=32)
    hasher.update(signal_chain_hash.encode())
    hasher.update(b"|")
    hasher.update(str(configuration_epoch).encode())
    hasher.update(b"|")
    hasher.update(str(sample_count).encode())
    hasher.update(b"|")
    hasher.update(payload)
    return f"{DIGEST_ALGORITHM}:{hasher.hexdigest()}"


class RawIQRetentionRefused(RuntimeError):
    """Raised when a process that may not hold raw IQ tries to allocate a ring."""


class RawIQNotTransportable(TypeError):
    """Raised on any attempt to serialize a ring or a window."""


def _process_role() -> str:
    return os.getenv("SCYTHE_PROCESS_ROLE", "").strip().lower()


def _text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class IQWindow:
    """A read-only copy of a contiguous span of retained samples.

    ``samples`` is a non-writeable ``complex64`` array that does not alias the
    ring, so a consumer can neither corrupt the buffer nor keep a live view of it
    after the samples have been overwritten.
    """

    window_id: str
    configuration_epoch: int
    # Where this window sits in the ring's own count of appended samples --
    # authoritative because the ring assigns it, not the consumer. §5.20
    # correction A: non-overlap has to be checkable from two windows rather
    # than promised by whoever acquired them, and timestamps cannot do it
    # (two acquisitions a millisecond apart over identical samples have
    # different times and the same content).
    first_sample_index: int
    start_time: float
    end_time: float
    sample_count: int
    sample_rate_hz: float
    digest: str
    signal_chain_hash: str
    # The ring instance this window came from. Two rings count from zero
    # independently, so without it two sample-index domains look comparable.
    ring_lifetime_id: str
    samples: np.ndarray

    def to_dict(self) -> Dict[str, Any]:
        """Metadata only. There is no code path that serializes the samples."""
        return {
            "window_id": self.window_id,
            "configuration_epoch": self.configuration_epoch,
            "first_sample_index": self.first_sample_index,
            "last_sample_index": self.last_sample_index,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "sample_count": self.sample_count,
            "sample_rate_hz": self.sample_rate_hz,
            "digest": self.digest,
            "ring_lifetime_id": self.ring_lifetime_id,
            "signal_chain_hash": self.signal_chain_hash,
            "duration_s": self.duration_s,
            "raw_iq_exposed": False,
        }

    @property
    def last_sample_index(self) -> int:
        """One past the last sample, half-open like a slice.

        Derived from `first_sample_index` and `sample_count` rather than stored
        beside them. A stored end index is a third number that can disagree with
        the other two, and the disagreement would be invisible: every digest
        here covers the samples, not the bookkeeping.
        """
        return self.first_sample_index + self.sample_count

    @property
    def duration_s(self) -> float:
        return self.end_time - self.start_time

    def __repr__(self) -> str:
        # The default dataclass repr would print the samples, and reprs end up in
        # logs, tracebacks and debugger transcripts.
        return (f"IQWindow(window_id={self.window_id!r}, "
                f"configuration_epoch={self.configuration_epoch}, "
                f"sample_count={self.sample_count}, digest={self.digest!r}, "
                f"samples=<{self.sample_count} complex64 samples withheld>)")

    def __reduce__(self):
        raise RawIQNotTransportable(
            "an IQWindow holds raw IQ and is not serializable: it is process-local "
            "by the terms of the retention approval")


class AttestationRefused(RuntimeError):
    """An attestation that did not happen, and which check refused it."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# Mint-time state lives HERE, not on the scope instance. §5.24: immutable bytes
# stop the payload being modified, and do nothing about a scope attribute being
# replaced -- reflection reaches `scope._handle` exactly as it reaches
# `window.samples`. A scope therefore carries only an opaque handle, and every
# method re-derives its state from this registry. Replace the handle and the
# lookup resolves to nothing; replace it with another live scope's handle and
# the owner check refuses, because the entry names the object it was minted for.
_SCOPE_REGISTRY: Dict[str, "_ScopeState"] = {}
# Guards every insertion, lookup, prune and teardown of the mapping above.
# Rings do not share a lock, so without this two rings minting concurrently
# mutate one dictionary from two threads.
_REGISTRY_LOCK = threading.RLock()
_MINT_KEY = object()


class _ScopeState:
    """Bound at mint time and never re-read from the attested object."""

    __slots__ = ("owner", "payload", "samples", "metadata", "active", "lock")

    def __init__(self, owner, payload, samples, metadata) -> None:
        self.owner = owner              # weakref to the scope this belongs to
        self.payload = payload          # read-only memoryview over immutable bytes
        self.samples = samples          # the exact array reference, captured once
        self.metadata = metadata
        self.active = True
        # Held for the whole of a write, and acquired by teardown. A scope that
        # went inactive while a blocked write still held a local view would let
        # the evidence bytes cross the boundary after the scope reported ended,
        # which is the lifetime inversion §5.24 forbids, inverted in time.
        self.lock = threading.RLock()


class AttestedIQWindowScope:
    """A live, process-local attestation over one exact `IQWindow`.

    **Not a verdict.** `attest(window) -> bool` leaves a window between the
    answer and the write, with the object caller-supplied on both sides, so a
    verdict about a mutable object is a statement about the past. This binds one
    immutable sample reference and the authoritative metadata for its whole
    lifetime and never re-reads the attested object, so replacing a frozen
    dataclass field by reflection after attestation changes nothing it will
    write.

    **The ring may advance underneath it, and that is correct.** §5.20 already
    says the ring may have evicted or invalidated the source by publication step
    8. This is a point-in-time attested snapshot, not a lease on the buffer.
    """

    __slots__ = ("_handle", "__weakref__")

    def __init__(self, handle: str, mint_key: Any = None) -> None:
        # Unconstructible through the public API: a scope a caller can build is
        # a caller's claim, which is the thing attestation exists to replace.
        if mint_key is not _MINT_KEY:
            raise AttestationRefused(
                ATTESTATION_SCOPE_NOT_MINTED,
                "an AttestedIQWindowScope is minted by BoundedIQRing.attest_window")
        self._handle = handle

    # -- state, re-derived on every access ---------------------------------

    def _state(self) -> "_ScopeState":
        """Ordinary resolution, through the handle."""
        with _REGISTRY_LOCK:
            state = _SCOPE_REGISTRY.get(getattr(self, "_handle", None))
            if state is None or state.owner() is not self:
                raise AttestationRefused(
                    ATTESTATION_SCOPE_NOT_MINTED,
                    "this scope's handle does not resolve to state minted for it")
            if not state.active:
                raise AttestationRefused(
                    ATTESTATION_SCOPE_NOT_ACTIVE,
                    "the attestation scope has ended; a reused scope attests "
                    "nothing")
            return state

    def _own_state(self) -> Optional["_ScopeState"]:
        """The entry minted for this object, found by **identity**.

        Teardown cannot go through the handle. The handle is a mutable field,
        and an exit resolving through it leaves the genuine entry live whenever
        the field has been replaced -- so replacing it before the `with` block
        ends, and restoring it afterwards, would resurrect a scope that had
        reported itself ended, with its payload intact. Field replacement has to
        stay a refusal; it must not become delayed capability recovery.

        The handle may govern ordinary resolution. It may not govern teardown.
        """
        with _REGISTRY_LOCK:
            for state in _SCOPE_REGISTRY.values():
                if state.owner() is self:
                    return state
        return None

    @property
    def active(self) -> bool:
        """Liveness without raising, for a caller deciding whether to proceed.

        Resolved by identity, not by the presented handle: a scope is live or
        ended as an object, and answering from a replaceable field would let the
        answer be steered by the thing it is supposed to report on.
        """
        state = self._own_state()
        if state is None or not state.active:
            return False
        # Ended scopes report False above; a live one still has to be reachable
        # through the handle it is presenting, or it cannot be used.
        with _REGISTRY_LOCK:
            return _SCOPE_REGISTRY.get(getattr(self, "_handle", None)) is state

    def __enter__(self) -> "AttestedIQWindowScope":
        self._state()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        # Terminal on exit, including by exception. The entry stays so that a
        # reused scope can be told it has **ended** rather than that it was
        # never minted -- two different failures deserve two different codes --
        # but the payload and the array are dropped, so an ended scope keeps no
        # raw IQ alive.
        #
        # Found by identity, so a replaced handle cannot skip teardown; and the
        # state lock is taken, so an in-progress write finishes before the scope
        # can report itself ended. Exit therefore blocks behind a blocked write
        # rather than racing it -- which is the contract, not a deadlock: §5.24
        # says the scope stays active across the whole write.
        state = self._own_state()
        if state is None:
            return False
        with state.lock:
            state.active = False
            state.payload = None
            state.samples = None
        return False

    # -- metadata, never payload -------------------------------------------

    @property
    def window_id(self) -> str:
        return self._state().metadata["window_id"]

    @property
    def digest(self) -> str:
        return self._state().metadata["digest"]

    @property
    def configuration_epoch(self) -> int:
        return self._state().metadata["configuration_epoch"]

    @property
    def signal_chain_hash(self) -> str:
        return self._state().metadata["signal_chain_hash"]

    @property
    def sample_count(self) -> int:
        return self._state().metadata["sample_count"]

    def to_dict(self) -> Dict[str, Any]:
        """Metadata only. There is no code path that serialises the payload."""
        data = dict(self._state().metadata)
        data["raw_iq_exposed"] = False
        data["attestation"] = FULL_OBJECT_ATTESTATION
        return data

    # -- the restricted action ---------------------------------------------
    #
    # An ACTION, not an accessor. Returning a read-only `memoryview` looked
    # sufficient -- the bytes are immutable and the registry entry is cleared on
    # exit -- and it is not: the returned view holds its own reference to the
    # backing object, so it stays readable after the scope ends. Dropping the
    # registry reference is bookkeeping; the capability has already left, and
    # `release()` on one view does not reach a slice a consumer made from it.
    # §5.24: "a byte handle that outlives the scope is a scope that ended
    # without ending".
    #
    # The zero-copy view inside `attest_window` is fine and stays: that
    # reference never crosses the operation boundary. Zero-copy hashing is
    # good; zero-copy capability escape is not.

    def _write_payload_to_fd(self, fd: Any) -> int:
        """Write the attested bytes to `fd` and return how many. **Restricted.**

        Returns a count and never the payload, the array, bytes or a view.
        Partial writes are completed here rather than handed back as a resumable
        offset, because a resumable offset is the handle again with an integer
        in front of it.

        Python module privacy enforces nothing -- an underscore is a convention
        and nothing stops an import. What is enforceable, and what §5.24
        narrowed the claim to, is that there is **no public accessor** and that
        every production reference to this one is statically restricted;
        `test_the_payload_action_is_statically_restricted` walks the repository
        by AST and refuses any call site outside the allowed set.
        """
        state = self._state()          # type, provenance and liveness, in order
        if type(fd) is not int:
            raise AttestationRefused(
                ATTESTATION_WRITE_TARGET_INVALID,
                f"a file descriptor is an int; got {type(fd).__name__}")
        # The whole write happens under the state lock, and teardown acquires
        # the same lock. A partial write to a full pipe blocks here for as long
        # as it must, and an exit on another thread waits rather than marking
        # the scope ended while these bytes are still crossing.
        with state.lock:
            state = self._state()      # re-resolved: the handle may have moved
            view = state.payload
            total = 0
            while total < view.nbytes:
                written = os.write(fd, view[total:])
                if written <= 0:
                    raise AttestationRefused(
                        ATTESTATION_WRITE_INCOMPLETE,
                        f"wrote {total} of {view.nbytes} bytes and then stalled")
                total += written
            return total

    def _payload_sha256(self) -> str:
        """SHA-256 over the attested bytes, as hex. **Restricted.**

        §5.20's header carries `payload_sha256`, and the header is written
        **before** the payload -- so the digest cannot be a by-product of the
        write and has to be taken while the scope is live and before anything
        is created. §5.25 names the payload action as the authority for that
        field, which is what this is.

        Returns a 64-character hex string and never the payload, the array,
        bytes or a view. It is not the deleted `_payload_nbytes()` returning
        under a new name: that method had no caller and answered a question the
        attested metadata already answers, and this one answers a question
        nothing else can -- only the bytes know their own digest.

        The ring digest is not a substitute. It is BLAKE2s over the chain, the
        epoch, the sample count **and** the payload, so it identifies the live
        source at issue time; `payload_sha256` is over the payload alone, which
        is what a reader recomputes from a file it has no ring for.
        """
        state = self._state()          # type, provenance and liveness, in order
        # Same discipline as the write: the whole read happens under the state
        # lock, and teardown acquires it. A digest taken while an exit was
        # clearing the payload would either raise incidentally or hash a
        # half-dropped view, and both are the lifetime inversion §5.24 forbids.
        with state.lock:
            state = self._state()      # re-resolved: the handle may have moved
            return hashlib.sha256(state.payload).hexdigest()

    # There is deliberately no `_payload_nbytes()`. It existed, had no
    # production caller, read `state.payload.nbytes` outside the state lock --
    # so a concurrent exit produced an incidental `AttributeError` rather than
    # the declared refusal -- and added a second private surface for the static
    # call-site check to police. `PENDING_AMENDMENTS` entry 16 offered two
    # repairs and this is the other one: **a method nobody calls is deleted
    # rather than rehabilitated.**
    #
    # A writer needing the declared payload length derives it from the
    # attested metadata -- `sample_count * BYTES_PER_SAMPLE` -- while building
    # its canonical header, and then requires `_write_payload_to_fd` to return
    # exactly that. That is strictly stronger than asking the payload object
    # how large it is: the expected length comes from the ring's authoritative
    # record, and the completed write reconciles against it independently.

    def __repr__(self) -> str:
        handle = getattr(self, "_handle", "<replaced>")
        return (f"AttestedIQWindowScope(handle={handle!r}, "
                f"active={self.active}, payload=<withheld>)")

    def __reduce__(self):
        raise RawIQNotTransportable(
            "an attestation scope binds raw IQ and is process-local by the "
            "terms of the retention approval")


@dataclass(frozen=True)
class WindowAcquisition:
    """The outcome of a window request. A refusal is a result, not an exception."""

    window: Optional[IQWindow]
    reason_code: str
    detail: str

    def __bool__(self) -> bool:
        return self.window is not None

    @property
    def reason(self) -> str:
        return ACQUISITION_REASONS.get(self.reason_code, self.reason_code)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "reason": self.reason,
            "detail": self.detail,
            "window": self.window.to_dict() if self.window is not None else None,
        }


@dataclass(frozen=True)
class WindowVerification:
    """Whether a claimed window is one this ring actually issued and still holds."""

    verified: bool
    reason_code: str
    detail: str

    def __bool__(self) -> bool:
        return self.verified

    @property
    def reason(self) -> str:
        return VERIFICATION_REASONS.get(self.reason_code, self.reason_code)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verified": self.verified,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class _WindowRecord:
    """Bridge-side memory of an issued window. Metadata only -- never samples."""

    window_id: str
    configuration_epoch: int
    digest: str
    signal_chain_hash: str
    sample_count: int
    # §5.24. Without it, "every stored metadata field matches the record"
    # quietly means "every field the record happens to keep", and a window
    # claiming a rate the ring never issued would attest cleanly.
    sample_rate_hz: float
    start_time: float
    end_time: float
    # Index of the window's first sample in the ring's monotonic append stream.
    # Compared against the eviction frontier to tell whether it still exists.
    first_index: int
    # §5.26: the ring lifetime this record was issued under.
    ring_lifetime_id: str


class BoundedIQRing:
    """A fixed-capacity, process-local ring of complex64 IQ samples.

    Thread-safe.  The bridge appends from its capture thread while a consumer
    acquires windows; both take the same lock, and a consumer never holds a
    reference into the buffer once ``acquire_window`` has returned.
    """

    def __init__(self, *, capacity_samples: int = DEFAULT_CAPACITY_SAMPLES,
                 sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
                 signal_chain_hash: str = UNDECLARED,
                 now=time.time, clock_authority: Optional[str] = None) -> None:
        role = _process_role()
        if role not in ALLOWED_PROCESS_ROLES:
            # Refused before allocation, so a child never holds IQ even briefly.
            raise RawIQRetentionRefused(
                f"process role {role!r} may not retain raw IQ; only the "
                f"orchestrator-owned bridge may allocate a BoundedIQRing. Child "
                f"processes and the Spectrum MCP receive derived products only")
        capacity = int(capacity_samples)
        if capacity <= 0:
            raise ValueError("capacity_samples must be positive")
        rate = float(sample_rate_hz)
        if not rate > 0:
            raise ValueError("sample_rate_hz must be positive")

        self._lock = threading.RLock()
        self._now = now
        self._capacity = capacity
        self._sample_rate_hz = rate
        self._signal_chain_hash = _text(signal_chain_hash) or UNDECLARED
        # Derived from the clock actually installed, not from a parameter a
        # caller may leave at its default while replacing the clock beside it.
        if clock_authority is None:
            self._clock_authority = (CLOCK_AUTHORITY_POSIX_REALTIME
                                     if now is time.time else UNDECLARED)
        else:
            self._clock_authority = _text(clock_authority) or UNDECLARED
        # Allocated once. Nothing in this class reassigns or resizes it.
        self._buffer = np.zeros(capacity, dtype=STORAGE_DTYPE)
        self._write_index = 0
        self._held = 0
        # Monotonic across the ring's whole life, never reset by invalidation, so
        # window indices stay unambiguous.
        self._total_appended = 0
        self._configuration_epoch = 0
        self._last_invalidation_reason: Optional[str] = None
        self._last_invalidated_at: Optional[float] = None
        self._newest_sample_time: Optional[float] = None
        self._windows: "OrderedDict[str, _WindowRecord]" = OrderedDict()
        self._issued_windows = 0
        self._attestations = 0
        # §5.26: minted once, here, and never taken from the caller. It is
        # deliberately NOT reset by `_invalidate`: a configuration change
        # starts a new epoch, not a new index domain, and the identity tracks
        # the domain.
        self._ring_lifetime_id = _mint_ring_lifetime_id()
        self._closed = False

    # -- properties ---------------------------------------------------------

    @property
    def capacity_samples(self) -> int:
        return self._capacity

    @property
    def configuration_epoch(self) -> int:
        return self._configuration_epoch

    @property
    def ring_lifetime_id(self) -> str:
        """This ring instance's identity. Different for every new ring."""
        return self._ring_lifetime_id

    @property
    def clock_authority(self) -> str:
        """Which clock stamped this ring's capture times. §5.25."""
        return self._clock_authority

    @property
    def retention_ms(self) -> float:
        return 1000.0 * self._capacity / self._sample_rate_hz

    @property
    def allocated_bytes(self) -> int:
        return int(self._buffer.nbytes)

    # -- mutation -----------------------------------------------------------

    def append(self, samples, metadata: Optional[Dict[str, Any]] = None) -> int:
        """Append a block of IQ. Returns the number of samples retained from it.

        A declared ``signal_chain_hash`` that differs from the current one clears
        the ring first.  Making that automatic rather than the caller's duty is
        deliberate: the one invariant worth protecting hardest should not depend
        on every call site remembering it.
        """
        meta = metadata or {}
        with self._lock:
            if self._closed:
                raise RuntimeError("BoundedIQRing is closed and accepts no samples")

            declared_chain = _text(meta.get("signal_chain_hash"))
            if declared_chain and declared_chain != self._signal_chain_hash:
                self._invalidate_locked("SIGNAL_CHAIN_CHANGE")
                self._signal_chain_hash = declared_chain

            declared_rate = meta.get("sample_rate_hz")
            if declared_rate is not None:
                rate = float(declared_rate)
                if rate > 0 and rate != self._sample_rate_hz:
                    self._invalidate_locked("SAMPLE_RATE_CHANGE")
                    self._sample_rate_hz = rate

            block = np.ascontiguousarray(samples, dtype=STORAGE_DTYPE).ravel()
            if block.size == 0:
                return 0
            # A block larger than the ring keeps only its tail; the earlier part
            # was overwritten the moment it arrived. Saying so in the return
            # value is more honest than pretending the whole block was retained.
            if block.size > self._capacity:
                block = block[-self._capacity:]

            end = self._write_index + block.size
            if end <= self._capacity:
                self._buffer[self._write_index:end] = block
            else:
                head = self._capacity - self._write_index
                self._buffer[self._write_index:] = block[:head]
                self._buffer[:block.size - head] = block[head:]
            self._write_index = end % self._capacity
            self._held = min(self._capacity, self._held + block.size)
            self._total_appended += block.size

            observed_at = metadata.get("timestamp") if metadata else None
            self._newest_sample_time = (
                float(observed_at) + block.size / self._sample_rate_hz
                if observed_at is not None else float(self._now()))
            return int(block.size)

    def invalidate(self, reason: str) -> int:
        """Clear the ring and advance the configuration epoch.

        Returns the new epoch.  An unrecognised reason raises: a clear whose cause
        nobody named is a clear nobody can audit later.
        """
        code = _text(reason).upper()
        if code not in INVALIDATION_REASONS:
            raise ValueError(
                f"unknown invalidation reason {code[:48]!r}; expected one of "
                f"{', '.join(INVALIDATION_REASONS)}")
        with self._lock:
            return self._invalidate_locked(code)

    def _invalidate_locked(self, code: str) -> int:
        # Zero rather than merely reset the indices. Eviction already overwrites
        # in the steady state; an explicit clear should not leave the last 4 MB of
        # a previous signal chain sitting in the process image.
        self._buffer[:] = 0
        self._write_index = 0
        self._held = 0
        self._newest_sample_time = None
        # `_total_appended` is deliberately **not** reset. It is the ring's
        # lifetime count of appended samples and the only monotonic quantity
        # here; resetting it would make two windows from different epochs
        # compare as overlapping, which is the check §5.20 correction A depends
        # on. Eviction is tracked against it separately, by `frontier`.
        self._configuration_epoch += 1
        self._last_invalidation_reason = code
        self._last_invalidated_at = float(self._now())
        # Records are kept, not dropped: an outstanding window should verify as
        # EPOCH_CHANGED, which says what happened, rather than as never issued.
        return self._configuration_epoch

    def close(self) -> None:
        """Zero the allocation and refuse all further use."""
        with self._lock:
            if self._closed:
                return
            self._invalidate_locked("ORCHESTRATOR_STOP")
            self._closed = True

    # -- windows ------------------------------------------------------------

    def acquire_window(self, duration_samples: Optional[int] = None) -> WindowAcquisition:
        """Copy out the most recent ``duration_samples`` in chronological order."""
        requested = int(duration_samples) if duration_samples else self._capacity
        with self._lock:
            if self._closed:
                return WindowAcquisition(None, "RING_CLOSED",
                                         ACQUISITION_REASONS["RING_CLOSED"])
            if requested <= 0 or requested > self._capacity:
                return WindowAcquisition(
                    None, "WINDOW_TOO_LARGE",
                    f"REQUESTED {requested} SAMPLES, CAPACITY IS {self._capacity}")
            if self._held < requested:
                return WindowAcquisition(
                    None, "INSUFFICIENT_WINDOW",
                    f"HOLDING {self._held} OF {requested} SAMPLES SINCE EPOCH "
                    f"{self._configuration_epoch}")

            ordered = self._ordered_tail_locked(requested)
            # §5.24: immutable BACKING, not an immutable flag.
            # `setflags(write=False)` on an owning array is discouragement --
            # `setflags(write=True)` restores it. An array over immutable bytes
            # does not own its data, so the flag cannot be flipped at all.
            #
            # Peak here is two full-window copies, transiently: `ordered` is
            # still alive while `tobytes` allocates. That is authorised and
            # bounded at one window -- Python offers no way to fill an immutable
            # object in place -- and `test_acquisition_peaks_at_two_windows`
            # measures it rather than inferring it from final ownership.
            payload = ordered.tobytes(order="C")
            del ordered
            samples = np.frombuffer(payload, dtype=STORAGE_DTYPE)

            end_time = (self._newest_sample_time
                        if self._newest_sample_time is not None else float(self._now()))
            start_time = end_time - requested / self._sample_rate_hz
            digest = _window_digest(self._signal_chain_hash,
                                    self._configuration_epoch, requested, payload)
            self._issued_windows += 1
            window_id = (f"iqw-{self._configuration_epoch}-{self._issued_windows}-"
                         f"{hashlib.blake2s(digest.encode(), digest_size=6).hexdigest()}")
            first_index = self._total_appended - requested

            window = IQWindow(
                window_id=window_id,
                configuration_epoch=self._configuration_epoch,
                first_sample_index=first_index,
                start_time=start_time,
                end_time=end_time,
                sample_count=requested,
                sample_rate_hz=self._sample_rate_hz,
                digest=digest,
                signal_chain_hash=self._signal_chain_hash,
                ring_lifetime_id=self._ring_lifetime_id,
                samples=samples,
            )
            self._windows[window_id] = _WindowRecord(
                window_id=window_id,
                configuration_epoch=self._configuration_epoch,
                digest=digest,
                signal_chain_hash=self._signal_chain_hash,
                sample_count=requested,
                sample_rate_hz=self._sample_rate_hz,
                start_time=start_time,
                end_time=end_time,
                first_index=first_index,
                ring_lifetime_id=self._ring_lifetime_id,
            )
            while len(self._windows) > MAX_TRACKED_WINDOWS:
                self._windows.popitem(last=False)

            return WindowAcquisition(window, "WINDOW_ACQUIRED",
                                     ACQUISITION_REASONS["WINDOW_ACQUIRED"])

    def verify_window(self, window_id: str, digest: str) -> WindowVerification:
        """Is this a window this ring issued, and does it still exist?

        This is what makes a ``source_window_hash`` a binding rather than a label.
        A correctly shaped digest that no window ever carried fails here, which is
        the check Phase 0's shape validation structurally could not perform.
        """
        claimed_id = _text(window_id)
        claimed_digest = _text(digest).lower()
        with self._lock:
            if self._closed:
                return WindowVerification(False, "RING_CLOSED",
                                          VERIFICATION_REASONS["RING_CLOSED"])
            record = self._windows.get(claimed_id)
            if record is None:
                return WindowVerification(False, "WINDOW_NOT_ISSUED",
                                          VERIFICATION_REASONS["WINDOW_NOT_ISSUED"])
            if record.digest != claimed_digest:
                return WindowVerification(False, "DIGEST_MISMATCH",
                                          VERIFICATION_REASONS["DIGEST_MISMATCH"])
            if record.configuration_epoch != self._configuration_epoch:
                return WindowVerification(
                    False, "EPOCH_CHANGED",
                    f"WINDOW ISSUED UNDER EPOCH {record.configuration_epoch}, RING IS "
                    f"AT {self._configuration_epoch} AFTER "
                    f"{self._last_invalidation_reason}")
            # Everything before this index has been overwritten by later appends.
            frontier = self._total_appended - self._held
            if record.first_index < frontier:
                return WindowVerification(False, "WINDOW_EVICTED",
                                          VERIFICATION_REASONS["WINDOW_EVICTED"])
            return WindowVerification(True, "WINDOW_VERIFIED",
                                      VERIFICATION_REASONS["WINDOW_VERIFIED"])

    def attest_window(self, window: Any) -> AttestedIQWindowScope:
        """Attest one exact `IQWindow` and mint a scope over its bytes. §5.24.

        The operation §5.20's publication step 1 asks for, and the one
        `verify_window` is not: ten checks under the ring lock, over the object
        rather than over two strings, with the digest **recomputed from the
        bytes being bound** against the *record's* chain, epoch and sample
        count. An object that supplies its own comparands proves nothing.

        Raises rather than returning a result, because a refused attestation
        must be impossible to enter.
        """
        with self._lock:
            # 1. the exact nominal type. A subclass or a mapping carries the
            #    same two strings and is not the thing the ring issued.
            if type(window) is not IQWindow:
                raise AttestationRefused(
                    ATTESTATION_NOT_AN_IQ_WINDOW,
                    f"expected an exact IQWindow; got {type(window).__name__}")
            if self._closed:
                raise AttestationRefused(
                    ATTESTATION_RING_CLOSED, VERIFICATION_REASONS["RING_CLOSED"])
            record = self._windows.get(window.window_id)
            if record is None:
                raise AttestationRefused(
                    ATTESTATION_WINDOW_NOT_ISSUED,
                    VERIFICATION_REASONS["WINDOW_NOT_ISSUED"])
            if record.configuration_epoch != self._configuration_epoch:
                raise AttestationRefused(
                    ATTESTATION_EPOCH_CHANGED,
                    f"WINDOW ISSUED UNDER EPOCH {record.configuration_epoch}, RING "
                    f"IS AT {self._configuration_epoch} AFTER "
                    f"{self._last_invalidation_reason}")
            frontier = self._total_appended - self._held
            if record.first_index < frontier:
                raise AttestationRefused(
                    ATTESTATION_WINDOW_EVICTED,
                    VERIFICATION_REASONS["WINDOW_EVICTED"])

            # 6. §5.26. The RECORD against the ring itself, before the
            #    object is compared to the record. Placed here because the
            #    other order makes it unreachable: a tampered record fails the
            #    field-by-field comparison below first, and a check that
            #    cannot fire is not a check.
            if record.ring_lifetime_id != self._ring_lifetime_id:
                raise AttestationRefused(
                    ATTESTATION_RING_LIFETIME_MISMATCH,
                    f"the record was issued under ring lifetime "
                    f"{record.ring_lifetime_id!r}; this ring is "
                    f"{self._ring_lifetime_id!r}")

            # 7. every stored metadata field, against the ring's own record.
            authoritative = {
                "configuration_epoch": record.configuration_epoch,
                "first_sample_index": record.first_index,
                "last_sample_index": record.first_index + record.sample_count,
                "sample_count": record.sample_count,
                "sample_rate_hz": record.sample_rate_hz,
                "start_time": record.start_time,
                "end_time": record.end_time,
                "signal_chain_hash": record.signal_chain_hash,
                "digest": record.digest,
                "ring_lifetime_id": record.ring_lifetime_id,
            }
            for field in ATTESTED_METADATA_FIELDS:
                presented = getattr(window, field)
                if presented != authoritative[field]:
                    raise AttestationRefused(
                        ATTESTATION_METADATA_MISMATCH,
                        f"{field} is {presented!r} on the object and "
                        f"{authoritative[field]!r} in the record")

            # 7. the sample representation, exactly. A coerced array is a
            #    different file, so nothing here converts anything.
            samples = window.samples
            expected = np.dtype(STORAGE_DTYPE)
            problems = []
            if type(samples) is not np.ndarray:
                problems.append(f"samples are {type(samples).__name__}")
            else:
                if samples.ndim != 1:
                    problems.append(f"{samples.ndim} dimensions")
                if samples.dtype.str != expected.str:
                    problems.append(f"dtype {samples.dtype.str} not {expected.str}")
                if samples.size != record.sample_count:
                    problems.append(f"{samples.size} samples not {record.sample_count}")
                if samples.nbytes != record.sample_count * BYTES_PER_SAMPLE:
                    problems.append(f"{samples.nbytes} bytes")
                if not samples.flags.c_contiguous:
                    problems.append("not contiguous")
                if samples.flags.writeable:
                    problems.append("writeable")
                if samples.flags.owndata:
                    # An owning array can have its write flag restored, so a
                    # frozen owning array is discouragement rather than backing.
                    problems.append("owns its data, so its write flag is restorable")
            if problems:
                raise AttestationRefused(
                    ATTESTATION_REPRESENTATION_INVALID, "; ".join(problems))

            # 8/9. recomputed from the bytes being bound, against the record's
            #      comparands -- and equal to both the record and the object.
            payload = memoryview(samples).cast("B")
            recomputed = _window_digest(record.signal_chain_hash,
                                        record.configuration_epoch,
                                        record.sample_count, payload)
            if recomputed != record.digest:
                raise AttestationRefused(
                    ATTESTATION_DIGEST_MISMATCH,
                    "the bytes presented do not digest to the issued record")
            if recomputed != window.digest:
                raise AttestationRefused(
                    ATTESTATION_DIGEST_MISMATCH,
                    "the bytes presented do not digest to the object's own claim")

            metadata = dict(authoritative)
            metadata["window_id"] = record.window_id
            # Outside ATTESTED_METADATA_FIELDS deliberately: that tuple is the
            # field-by-field comparison of the *object* against the record, and
            # the clock authority is a property of the ring rather than of the
            # window. It is bound into the scope because §5.25's header needs
            # it, and it is bound at mint time so it cannot be re-read later.
            metadata["clock_authority"] = self._clock_authority
            # Already in `authoritative` above, and therefore in the scope's
            # metadata; named here only because §5.26 requires it exposed
            # through the attested scope and carried in the header.
            self._attestations += 1
            handle = (f"att-{record.window_id}-{self._attestations}-"
                      f"{os.urandom(8).hex()}")
            scope = AttestedIQWindowScope(handle, _MINT_KEY)
            with _REGISTRY_LOCK:
                # Prune entries whose scope has been collected: the registry is
                # process-local bookkeeping, not a cache, and it must not grow
                # without bound across a long capture.
                for dead in [h for h, st in _SCOPE_REGISTRY.items()
                             if st.owner() is None]:
                    del _SCOPE_REGISTRY[dead]
                _SCOPE_REGISTRY[handle] = _ScopeState(
                    owner=weakref.ref(scope), payload=payload, samples=samples,
                    metadata=metadata)
            return scope

    def recorded_window(self, window_id: str) -> Optional[Dict[str, Any]]:
        """What this ring recorded when it issued that window. Metadata only.

        Exposed for §5.20's eventual full-object attestation, which must compare
        an object against what was issued rather than compare two strings
        against a record. Returns `None` for an ID this ring never issued, and
        never returns samples -- there is no path here that serialises them.
        """
        with self._lock:
            record = self._windows.get(_text(window_id))
            if record is None:
                return None
            return {
                "window_id": record.window_id,
                "configuration_epoch": record.configuration_epoch,
                "digest": record.digest,
                "signal_chain_hash": record.signal_chain_hash,
                "sample_count": record.sample_count,
                "first_sample_index": record.first_index,
                "last_sample_index": record.first_index + record.sample_count,
                "start_time": record.start_time,
                "end_time": record.end_time,
                "raw_iq_exposed": False,
            }

    # -- internals ----------------------------------------------------------

    def _ordered_tail_locked(self, count: int) -> np.ndarray:
        """The newest ``count`` samples, oldest first, as a fresh array."""
        start = (self._write_index - count) % self._capacity
        if start + count <= self._capacity:
            return np.array(self._buffer[start:start + count], dtype=STORAGE_DTYPE)
        head = self._capacity - start
        return np.concatenate((self._buffer[start:], self._buffer[:count - head]))

    def _digest_locked(self, samples: np.ndarray) -> str:
        """Bridge-computed, reproducible, and bound to the signal chain and epoch.

        Identical samples captured under the same configuration digest
        identically.  The same samples under a different signal chain do not,
        because they are not the same evidence.

        A thin caller of `_window_digest` since §5.24: the arithmetic lives in
        one place so attestation cannot drift from issuance.
        """
        return _window_digest(self._signal_chain_hash, self._configuration_epoch,
                              int(samples.size), memoryview(samples).cast("B"))

    # -- published metadata -------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Operational metadata, and never contents.

        Every field here is a number or a label about the buffer.  Nothing in this
        dictionary is derived from sample values, so it is safe to serve, log and
        put in front of a model.
        """
        with self._lock:
            if self._closed:
                state = "CLOSED"
            elif self._held == 0:
                state = "INVALIDATED"
            elif self._held < self._capacity:
                state = "FILLING"
            else:
                state = "READY"
            oldest_age_ms = None
            if self._held and self._newest_sample_time is not None:
                oldest_age_ms = round(1000.0 * self._held / self._sample_rate_hz, 3)
            return {
                "schema": SCHEMA,
                "state": state,
                # Named "effective" because it is capacity over rate: what this
                # allocation holds, never a configured request it fell short of.
                "effective_retention_ms": round(self.retention_ms, 3),
                "capacity_samples": self._capacity,
                "held_samples": self._held,
                "storage_dtype": STORAGE_DTYPE,
                "allocated_bytes": self.allocated_bytes,
                "oldest_sample_age_ms": oldest_age_ms,
                "sample_rate_hz": self._sample_rate_hz,
                "cycle_resolution_hz": round(self._sample_rate_hz / self._capacity, 6),
                "window_overlap": WINDOW_OVERLAP,
                "configuration_epoch": self._configuration_epoch,
                "signal_chain_hash": self._signal_chain_hash,
                "last_invalidation_reason": self._last_invalidation_reason,
                "last_invalidated_at": self._last_invalidated_at,
                "invalidation_reasons": list(INVALIDATION_REASONS),
                "issued_windows": self._issued_windows,
                "tracked_windows": len(self._windows),
                "max_tracked_windows": MAX_TRACKED_WINDOWS,
                # Declared absences, in the style of the Phase 0 classifier block.
                "raw_iq_exposed": False,
                "persistence": "NONE",
                "disk_fallback": "NOT_IMPLEMENTED_AND_NOT_AUTHORIZED",
                "crash_dump_facility": "NOT_IMPLEMENTED_AND_NOT_AUTHORIZED",
                "transportable": False,
                "model_context_eligible": False,
                "owner": "ORCHESTRATOR_BRIDGE",
            }

    def __repr__(self) -> str:
        # Deliberately says nothing about content. A default repr on an object
        # holding a numpy buffer is one debugger session away from a 4 MB paste.
        return (f"BoundedIQRing(capacity_samples={self._capacity}, "
                f"held={self._held}, epoch={self._configuration_epoch}, "
                f"samples=<withheld>)")

    def __reduce__(self):
        raise RawIQNotTransportable(
            "a BoundedIQRing holds raw IQ and is not serializable: it is "
            "process-local by the terms of the retention approval")
