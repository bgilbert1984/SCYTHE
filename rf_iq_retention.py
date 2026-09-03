"""Phase 1c: who owns the bounded IQ ring, and when it is cleared.

Phase 1a built a ring that nothing allocated.  This module allocates one, decides
which bridge events clear it and with which reason, and publishes the retention
transition.  There is no DSP here and no channelizer call: the point of a
separate commit is that ownership, lifecycle and process-role enforcement can be
read without signal processing in the way.

A class existing is not active retention
----------------------------------------
``iq_retention`` stays ``NONE_BEYOND_ONE_FFT_BLOCK`` while no ring is allocated,
whatever modules happen to be importable.  The ring is allocated **lazily, on the
first block of samples**, so ``iq_retention_active`` becoming true means exactly
what it says: an allocation exists and samples are arriving into it.  A build
that imports ``rf_iq_ring`` and never captures reports no retention, correctly.

Four reasons there may be no ring, each reported rather than blanked::

    DISABLED_BY_CONFIGURATION   SCYTHE_RF_IQ_RETENTION is off
    NOT_CAPTURE_OWNER           another process owns the IQ socket
    PROCESS_ROLE_REFUSED        this process may not hold raw IQ at all
    NO_SAMPLES_YET              permitted, but nothing has arrived

Lifecycle
---------
The ring's guarantee is that no window spans a configuration change, and that
guarantee is only as good as the events wired to it:

===========================================  =========================
bridge event                                 reason
===========================================  =========================
``tune()`` / centre-frequency change         ``RETUNE``
sample-rate change                           ``SAMPLE_RATE_CHANGE`` *
sample-type change                           ``SIGNAL_CHAIN_CHANGE`` *
IQ socket established                        ``RECONNECT``
IQ socket lost or errored                    ``DISCONNECT``
``stop()``                                   ``ORCHESTRATOR_STOP``
capture owner changed                        ``CAPTURE_OWNER_CHANGE``
===========================================  =========================

\\* These two also change the ring's required capacity or its decode, so they
reallocate rather than clear.  Allocating a fresh ring for a new configuration is
not the same as growing one: within a configuration the allocation is made once
and never resized, which is what the approval requires.

One bridge action can produce several clears.  A retune clears for ``RETUNE``,
then the stream restart clears again for ``ORCHESTRATOR_STOP`` and once more for
``RECONNECT``.  The ring only remembers the last one, which would leave the audit
saying a reconnect caused a clear that a retune actually caused, so the owner
keeps a bounded ``invalidation_history``.  Suppressing the later clears would be
worse: they really did happen.

``GAIN_CHANGE``, ``DIRECT_SAMPLING_CHANGE`` and ``CLOCK_DISCONTINUITY`` remain in
the ring's vocabulary with **no bridge event wired to them**, because this bridge
has no gain control, no direct-sampling control and no clock-discontinuity
detector.  They are declared absences, not oversights; when those controls exist
they must be wired here before they are exposed anywhere else.
"""

from __future__ import annotations

from collections import deque
import hashlib
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import numpy as np

from rf_iq_ring import (
    DEFAULT_CAPACITY_SAMPLES, DEFAULT_WINDOW_MS, INVALIDATION_REASONS,
    BoundedIQRing, RawIQRetentionRefused,
)


LOG = logging.getLogger("scythe.rf.retention")

SCHEMA = "scythe.rf-iq-retention.v1"

RETENTION_NONE = "NONE_BEYOND_ONE_FFT_BLOCK"
RETENTION_RING = "PROCESS_LOCAL_BOUNDED_RING"

INACTIVE_REASONS: Dict[str, str] = {
    "DISABLED_BY_CONFIGURATION": (
        "RAW-IQ RETENTION IS SWITCHED OFF BY SCYTHE_RF_IQ_RETENTION. NO RING IS "
        "ALLOCATED AND NO SAMPLES ARE HELD BEYOND ONE FFT BLOCK"
    ),
    "NOT_CAPTURE_OWNER": (
        "THIS PROCESS DOES NOT OWN THE IQ SOCKET, SO IT HAS NO SAMPLES TO RETAIN"
    ),
    "PROCESS_ROLE_REFUSED": (
        "THIS PROCESS ROLE MAY NOT HOLD RAW IQ. CHILD PROCESSES AND THE SPECTRUM "
        "MCP RECEIVE DERIVED PRODUCTS ONLY"
    ),
    "NO_SAMPLES_YET": (
        "RETENTION IS PERMITTED BUT NO IQ HAS ARRIVED, SO NO RING HAS BEEN "
        "ALLOCATED. A PERMITTED RING IS NOT AN ACTIVE ONE"
    ),
}

# The channelizer module exists and is tested; nothing in the capture path calls
# it. Saying "NOT_IMPLEMENTED" without that distinction would be as misleading in
# one direction as claiming it runs would be in the other.
CHANNELIZER_STATE = "NOT_IMPLEMENTED"
CHANNELIZER_NOTE = (
    "rf_channelizer IS IMPLEMENTED AND TESTED BUT IS NOT WIRED INTO THE CAPTURE "
    "PATH. NO WINDOW IS CHANNELIZED AND NO DERIVED PRODUCT IS PRODUCED"
)

# Reasons the ring declares that this bridge has no event for. Listed so the gap
# is visible rather than looking like full coverage.
UNWIRED_REASONS = ("GAIN_CHANGE", "DIRECT_SAMPLING_CHANGE", "CLOCK_DISCONTINUITY")
MAX_INVALIDATION_HISTORY = 16
UNWIRED_NOTE = (
    "THIS BRIDGE HAS NO GAIN CONTROL, NO DIRECT-SAMPLING CONTROL AND NO CLOCK-"
    "DISCONTINUITY DETECTOR, SO NOTHING CALLS THESE. THEY MUST BE WIRED HERE "
    "BEFORE ANY SUCH CONTROL IS EXPOSED"
)


def retention_enabled() -> bool:
    """Default on: the operator approved bounded retention. Off is a kill switch."""
    raw = os.getenv("SCYTHE_RF_IQ_RETENTION", "enabled").strip().lower()
    return raw not in {"0", "off", "no", "false", "disabled", "none"}


def antenna_id() -> str:
    """UNDECLARED, never UNKNOWN: a metadata omission is not a puzzled inspection."""
    return (os.getenv("SDRPP_ANTENNA_ID", "") or "").strip() or "UNDECLARED"


def signal_chain_hash(*, sensor_id: str, sample_type: str, sample_rate_hz: float,
                      antenna: Optional[str] = None) -> str:
    """Identity of the physical and decode path the samples came through.

    Deliberately excludes the centre frequency.  Retuning does not change the
    chain -- it is its own invalidation reason -- and folding it in here would
    make every retune look like a different antenna.
    """
    parts = (sensor_id, antenna if antenna is not None else antenna_id(),
             sample_type, f"{float(sample_rate_hz):.6f}")
    digest = hashlib.blake2s("|".join(str(part) for part in parts).encode(),
                             digest_size=16).hexdigest()
    return f"blake2s:{digest}"


class IQRetentionOwner:
    """Allocates, feeds, clears and publishes one bounded IQ ring.

    Every method is safe to call when retention is not permitted; they become
    no-ops that keep reporting why.  The capture thread must never take an
    exception from retention, and a refusal to retain must never be able to stop
    the bridge from producing its ordinary bounded spectrum products.
    """

    def __init__(self, *, sensor_id: str, sample_type: str, sample_rate_hz: float,
                 owns_capture: bool, window_ms: float = DEFAULT_WINDOW_MS,
                 enabled: Optional[bool] = None) -> None:
        self._lock = threading.RLock()
        self._sensor_id = sensor_id
        self._sample_type = sample_type
        self._sample_rate_hz = float(sample_rate_hz)
        self._owns_capture = bool(owns_capture)
        self._window_ms = float(window_ms)
        self._enabled = retention_enabled() if enabled is None else bool(enabled)
        self._ring: Optional[BoundedIQRing] = None
        self._signal_chain_hash = signal_chain_hash(
            sensor_id=sensor_id, sample_type=sample_type, sample_rate_hz=sample_rate_hz)
        self._refused_reason: Optional[str] = None
        self._appended_blocks = 0
        # Bounded, metadata only. Kept on the owner rather than the ring so it
        # survives a reallocation and so a later clear cannot overwrite the cause
        # of an earlier one.
        self._history: deque = deque(maxlen=MAX_INVALIDATION_HISTORY)

    # -- policy -------------------------------------------------------------

    def _capacity(self) -> int:
        """256 ms at the configured rate, never above the approved allocation."""
        samples = int(round(self._window_ms / 1000.0 * self._sample_rate_hz))
        return max(1, min(DEFAULT_CAPACITY_SAMPLES, samples))

    def _inactive_reason_locked(self) -> Optional[str]:
        if not self._enabled:
            return "DISABLED_BY_CONFIGURATION"
        if not self._owns_capture:
            return "NOT_CAPTURE_OWNER"
        if self._refused_reason:
            return self._refused_reason
        if self._ring is None:
            return "NO_SAMPLES_YET"
        return None

    @property
    def permitted(self) -> bool:
        with self._lock:
            return self._enabled and self._owns_capture and not self._refused_reason

    @property
    def active(self) -> bool:
        """An allocated ring receiving samples, not a class that exists."""
        with self._lock:
            return self._ring is not None

    @property
    def ring(self) -> Optional[BoundedIQRing]:
        with self._lock:
            return self._ring

    @property
    def signal_chain(self) -> str:
        with self._lock:
            return self._signal_chain_hash

    # -- lifecycle ----------------------------------------------------------

    def append(self, samples, timestamp: Optional[float] = None) -> int:
        """Feed decoded IQ. Allocates on first use; never raises into the caller."""
        with self._lock:
            if not (self._enabled and self._owns_capture) or self._refused_reason:
                return 0
            if self._ring is None:
                try:
                    self._ring = BoundedIQRing(
                        capacity_samples=self._capacity(),
                        sample_rate_hz=self._sample_rate_hz,
                        signal_chain_hash=self._signal_chain_hash)
                except RawIQRetentionRefused as exc:
                    # Recorded once and never retried: a process that may not hold
                    # raw IQ does not become eligible later in its own lifetime.
                    self._refused_reason = "PROCESS_ROLE_REFUSED"
                    LOG.warning("raw-IQ retention refused: %s", exc)
                    return 0
                except Exception:
                    LOG.exception("bounded IQ ring could not be allocated")
                    self._refused_reason = "PROCESS_ROLE_REFUSED"
                    return 0
                LOG.info("bounded IQ ring allocated: %d samples, %.1f ms, chain %s",
                         self._ring.capacity_samples, self._window_ms,
                         self._signal_chain_hash)
            try:
                kept = self._ring.append(
                    samples, {"timestamp": timestamp,
                              "signal_chain_hash": self._signal_chain_hash})
            except Exception:
                # Retention must never be able to stop the bridge producing its
                # ordinary bounded products.
                LOG.exception("bounded IQ ring append failed")
                return 0
            self._appended_blocks += 1
            return kept

    def invalidate(self, reason: str) -> Optional[int]:
        """Clear the ring for a named reason. A no-op when no ring is allocated."""
        code = str(reason or "").strip().upper()
        if code not in INVALIDATION_REASONS:
            raise ValueError(
                f"unknown invalidation reason {code[:48]!r}; expected one of "
                f"{', '.join(INVALIDATION_REASONS)}")
        with self._lock:
            if self._ring is None:
                return None
            LOG.info("bounded IQ ring invalidated: %s", code)
            epoch = self._ring.invalidate(code)
            self._record_locked(code, epoch)
            return epoch

    def _record_locked(self, reason: str, epoch: Optional[int]) -> None:
        self._history.append({"reason": reason, "at": time.time(), "epoch": epoch})

    def reconfigure(self, *, sample_type: Optional[str] = None,
                    sample_rate_hz: Optional[float] = None,
                    sensor_id: Optional[str] = None,
                    owns_capture: Optional[bool] = None,
                    reason: str = "SIGNAL_CHAIN_CHANGE") -> str:
        """Adopt a new capture configuration by discarding the ring, not resizing it.

        A rate or decode change alters the required capacity and the meaning of
        every retained sample, so the allocation is closed and the next block
        allocates a fresh one.  Within a configuration the ring is still allocated
        exactly once and never grown.
        """
        with self._lock:
            if sample_type is not None:
                self._sample_type = sample_type
            if sample_rate_hz is not None:
                self._sample_rate_hz = float(sample_rate_hz)
            if sensor_id is not None:
                self._sensor_id = sensor_id
            if owns_capture is not None:
                self._owns_capture = bool(owns_capture)
            self._signal_chain_hash = signal_chain_hash(
                sensor_id=self._sensor_id, sample_type=self._sample_type,
                sample_rate_hz=self._sample_rate_hz)
            if self._ring is not None:
                LOG.info("bounded IQ ring discarded for %s; new chain %s",
                         reason, self._signal_chain_hash)
                self._ring.close()
                self._ring = None
                self._record_locked(reason, None)
            return self._signal_chain_hash

    def close(self, reason: str = "ORCHESTRATOR_STOP") -> None:
        with self._lock:
            if self._ring is None:
                return
            try:
                self._ring.invalidate(reason)
            except ValueError:
                pass
            self._ring.close()
            self._ring = None
            self._record_locked(str(reason or "").strip().upper(), None)

    # -- published metadata -------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """The retention block. Metadata only; no field derives from a sample."""
        with self._lock:
            inactive = self._inactive_reason_locked()
            capacity = self._capacity()
            payload: Dict[str, Any] = {
                "schema": SCHEMA,
                "iq_retention": RETENTION_NONE if inactive else RETENTION_RING,
                "iq_retention_active": inactive is None,
                "retention_ms": round(self._window_ms, 3),
                "capacity_samples": capacity,
                "raw_iq_exposed": False,
                "channelizer_state": CHANNELIZER_STATE,
                "channelizer_note": CHANNELIZER_NOTE,
                "signal_chain_hash": self._signal_chain_hash,
                "antenna_id": antenna_id(),
                "owner": "ORCHESTRATOR_BRIDGE",
                "permitted": self._enabled and self._owns_capture and not self._refused_reason,
                "appended_blocks": self._appended_blocks,
                "invalidation_reasons": list(INVALIDATION_REASONS),
                "unwired_invalidation_reasons": list(UNWIRED_REASONS),
                "unwired_invalidation_note": UNWIRED_NOTE,
                "invalidation_history": list(self._history),
                "inactive_reason": inactive,
                "inactive_reason_note": INACTIVE_REASONS.get(inactive) if inactive else None,
            }
            payload["ring"] = self._ring.status() if self._ring is not None else None
            return payload
