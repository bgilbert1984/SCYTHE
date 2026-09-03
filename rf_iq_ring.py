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
from typing import Any, Dict, Optional, Tuple

import numpy as np


SCHEMA = "scythe.rf-iq-ring.v1"

# The approved configuration. 256 ms at 2.048 MS/s, complex64.
#
# These are a *registered detector configuration*, not a universal optimum. A
# later validation may authorise longer or overlapping windows, and when it does
# it must register that as a new configuration rather than silently changing what
# squared-envelope-cyclic.v1 was validated against.
DEFAULT_SAMPLE_RATE_HZ = 2_048_000.0
DEFAULT_WINDOW_MS = 256.0
DEFAULT_CAPACITY_SAMPLES = 524_288
STORAGE_DTYPE = "complex64"
BYTES_PER_SAMPLE = 8
# 1 / 0.256 s. The finest cycle-frequency spacing a 256 ms window can resolve.
NOMINAL_CYCLE_RESOLUTION_HZ = 3.90625

# Windows are non-overlapping initially: overlap multiplies computation and
# correlates consecutive verdicts before there is any evidence it buys detection
# performance.
WINDOW_OVERLAP = "NONE"

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
)

RING_STATES: Tuple[str, ...] = ("INVALIDATED", "FILLING", "READY", "CLOSED")

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
    start_time: float
    end_time: float
    sample_count: int
    sample_rate_hz: float
    digest: str
    signal_chain_hash: str
    samples: np.ndarray

    def to_dict(self) -> Dict[str, Any]:
        """Metadata only. There is no code path that serializes the samples."""
        return {
            "window_id": self.window_id,
            "configuration_epoch": self.configuration_epoch,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "sample_count": self.sample_count,
            "sample_rate_hz": self.sample_rate_hz,
            "digest": self.digest,
            "signal_chain_hash": self.signal_chain_hash,
            "duration_s": self.duration_s,
            "raw_iq_exposed": False,
        }

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
    start_time: float
    end_time: float
    # Index of the window's first sample in the ring's monotonic append stream.
    # Compared against the eviction frontier to tell whether it still exists.
    first_index: int


class BoundedIQRing:
    """A fixed-capacity, process-local ring of complex64 IQ samples.

    Thread-safe.  The bridge appends from its capture thread while a consumer
    acquires windows; both take the same lock, and a consumer never holds a
    reference into the buffer once ``acquire_window`` has returned.
    """

    def __init__(self, *, capacity_samples: int = DEFAULT_CAPACITY_SAMPLES,
                 sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
                 signal_chain_hash: str = "UNDECLARED",
                 now=time.time) -> None:
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
        self._signal_chain_hash = _text(signal_chain_hash) or "UNDECLARED"
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
        self._closed = False

    # -- properties ---------------------------------------------------------

    @property
    def capacity_samples(self) -> int:
        return self._capacity

    @property
    def configuration_epoch(self) -> int:
        return self._configuration_epoch

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

            samples = self._ordered_tail_locked(requested)
            # Copy-isolated and frozen: the consumer cannot write through it, and
            # it will not silently change under them as the ring is overwritten.
            samples.setflags(write=False)

            end_time = (self._newest_sample_time
                        if self._newest_sample_time is not None else float(self._now()))
            start_time = end_time - requested / self._sample_rate_hz
            digest = self._digest_locked(samples)
            self._issued_windows += 1
            window_id = (f"iqw-{self._configuration_epoch}-{self._issued_windows}-"
                         f"{hashlib.blake2s(digest.encode(), digest_size=6).hexdigest()}")
            first_index = self._total_appended - requested

            window = IQWindow(
                window_id=window_id,
                configuration_epoch=self._configuration_epoch,
                start_time=start_time,
                end_time=end_time,
                sample_count=requested,
                sample_rate_hz=self._sample_rate_hz,
                digest=digest,
                signal_chain_hash=self._signal_chain_hash,
                samples=samples,
            )
            self._windows[window_id] = _WindowRecord(
                window_id=window_id,
                configuration_epoch=self._configuration_epoch,
                digest=digest,
                signal_chain_hash=self._signal_chain_hash,
                sample_count=requested,
                start_time=start_time,
                end_time=end_time,
                first_index=first_index,
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
        """
        hasher = hashlib.blake2s(digest_size=32)
        hasher.update(self._signal_chain_hash.encode())
        hasher.update(b"|")
        hasher.update(str(self._configuration_epoch).encode())
        hasher.update(b"|")
        hasher.update(str(samples.size).encode())
        hasher.update(b"|")
        hasher.update(samples.tobytes())
        return f"{DIGEST_ALGORITHM}:{hasher.hexdigest()}"

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
                "retention_ms": round(self.retention_ms, 3),
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
