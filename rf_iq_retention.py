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

Configured is not effective
---------------------------
The approval fixes the allocation at 524,288 samples, and 256 ms is a *request*
against that ceiling.  The two agree only at the currently configured 2.048 MS/s.
At the device's nominal 2.4 MS/s the same allocation holds 218.453 ms, so a
status that kept saying ``256`` would be stating a duration the ring does not
have -- a precise-looking number that is wrong, which is worse than a vague one.
Both are published, along with ``capacity_limited`` saying which bound applied.

Phase 1d: the owner issues windows, the channelizer never reaches in
--------------------------------------------------------------------
The channelizer is not given the ring.  The owner issues a complete, immutable
``IQWindow`` -- already copy-isolated from the buffer -- and the channelizer
verifies it against the ring immediately before processing.  A channelizer that
periodically read "whatever is newest" out of a mutable buffer would produce
products whose contents depend on thread timing, which is not evidence.

The sequence, and every step of it is load-bearing::

    decoded IQ -> append -> complete window issued -> verify_window()
               -> epoch and signal-chain check -> channelize -> bounded product

The owner deliberately does **not** hold its lock across the DSP.  Holding it
would block a retune arriving on the API thread for the length of the transform,
and would make the retune-during-channelization case unobservable -- the very
race the verification exists to catch.  The window is a copy, so a concurrent
``invalidate()`` cannot corrupt it; it can only make it stale, and staleness is
exactly what ``verify_window`` reports.

One product per source window.  ``WINDOW_OVERLAP`` is ``NONE``, so a fresh
capacity's worth of samples must arrive before another window is issued; without
that a fast frame rate would emit near-identical products from overlapping spans
and the product count would describe the polling rate rather than the signal.

No classification happens here.  A product is measurements and verdict metadata;
nothing consumes it and nothing derives a family from it.

``GAIN_CHANGE``, ``DIRECT_SAMPLING_CHANGE`` and ``CLOCK_DISCONTINUITY`` remain in
the ring's vocabulary with **no bridge event wired to them**, because this bridge
has no gain control, no direct-sampling control and no clock-discontinuity
detector.  They are declared absences, not oversights; when those controls exist
they must be wired here before they are exposed anywhere else.
"""

from __future__ import annotations

from collections import deque
import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import numpy as np

from rf_channelizer import ChannelRequest, ChannelizedProduct, channelize
from rf_detector_contract import contract_status
from rf_symbol_clock import detector_status
from rf_validation_manifest import manifest_status
from rf_iq_ring import (
    DEFAULT_CAPACITY_SAMPLES, DEFAULT_WINDOW_MS, INVALIDATION_REASONS,
    BoundedIQRing, IQWindow, RawIQRetentionRefused, WindowAcquisition,
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

# Wired to the capture path as of Phase 1d. The state names what is still
# missing rather than stopping at "integrated", because a product being produced
# is not the same as a product being believed by anything.
CHANNELIZER_STATE = "INTEGRATED_NO_CLASSIFICATION"
CHANNELIZER_NOTE = (
    "COMPLETE WINDOWS ARE VERIFIED AND CHANNELIZED INTO BOUNDED PRODUCTS. NO "
    "DETECTOR CONSUMES THEM, NO CLASSIFICATION IS DERIVED FROM THEM, AND NO "
    "BASEBAND LEAVES THE PROCESS"
)
# Non-overlapping, matching the ring's declared WINDOW_OVERLAP. A product count
# must describe captured spans, not how often the bridge was asked.
WINDOW_POLICY = "ONE_PRODUCT_PER_NON_OVERLAPPING_WINDOW"
MAX_TRACKED_PRODUCTS = 8

# Reasons the ring declares that this bridge has no event for. Listed so the gap
# is visible rather than looking like full coverage.
# GAIN_CHANGE and CLOCK_DISCONTINUITY are wired as of 2026-09-03, which is what
# makes the GAIN_STEPS and DROPPED_FRAMES_TIMING_GAPS validation strata buildable:
# a corpus can now produce a gain step and observe a timing break rather than
# labelling windows from an assumption.
UNWIRED_REASONS = ("DIRECT_SAMPLING_CHANGE",)
WIRED_REASON_SOURCES = {
    "GAIN_CHANGE": "IQRetentionOwner.set_gain_db, called by SDRPPBridge.set_gain",
    "CLOCK_DISCONTINUITY": "ClockContinuityMonitor, on every append",
}
MAX_INVALIDATION_HISTORY = 16
UNWIRED_NOTE = (
    "THIS BRIDGE HAS NO DIRECT-SAMPLING CONTROL, SO NOTHING CALLS "
    "DIRECT_SAMPLING_CHANGE. IT MUST BE WIRED HERE BEFORE SUCH A CONTROL IS "
    "EXPOSED"
)


SIGNAL_CHAIN_SCHEMA = "scythe.rf-signal-chain.v2"
SIGNAL_CHAIN_REVISION = "v2"
# v1 was a positional hash over sensor, antenna, sample type and rate, with no
# feedline. It is not rescalable into v2 and nothing attempts to reinterpret it.
PRIOR_SIGNAL_CHAIN_REVISION_COMPARABLE = False
# Vendor figure for the SMArt v5, carried as a declaration rather than a
# measurement: nothing here has disciplined this oscillator against a reference.
CLOCK_QUALITY = "MODEL_DECLARED_0_5_PPM_TCXO"


# --- clock continuity ------------------------------------------------------
#
# Measured on the live NESDR stream before these were chosen. Over 2-second
# windows the arrival rate swings by up to 4.3%, which is TCP and USB buffering
# rather than the oscillator; over 20 seconds the cumulative drift was 0.007%.
# So the honest signal is cumulative and the check interval has to be long enough
# to average the buffering out. A 2% instantaneous tolerance would have fired
# continuously on a perfectly healthy stream and invalidated the ring for it.
CLOCK_CHECK_INTERVAL_S = 10.0
# ~150x the observed cumulative drift, and far below a real discontinuity, which
# is a whole dropped buffer rather than a slow slide.
CLOCK_DRIFT_TOLERANCE = 0.01
# Continuous data at 2 MS/s arrives many times a second. A second of silence
# inside a streaming connection is a stall, not slow going.
CLOCK_GAP_S = 1.0


class ClockContinuityMonitor:
    """Detects a break in the sample stream's timing, so it can be declared.

    Two different faults, kept apart because they mean different things:

    ``GAP``     nothing arrived for longer than a stream at this rate can be
                silent, so samples are missing between two that look adjacent.
    ``DRIFT``   samples arrived, but not as many as the elapsed time at the
                declared rate implies, so the count and the clock disagree.

    Both report ``CLOCK_DISCONTINUITY``. The distinction is kept in the detail
    because a gap is a transport fault and a drift is a rate fault, and a corpus
    labelling either as the other would be mislabelling its own strata.
    """

    def __init__(self, sample_rate_hz: float, *,
                 check_interval_s: float = CLOCK_CHECK_INTERVAL_S,
                 tolerance: float = CLOCK_DRIFT_TOLERANCE,
                 gap_s: float = CLOCK_GAP_S) -> None:
        self._rate = float(sample_rate_hz)
        self._interval = float(check_interval_s)
        self._tolerance = float(tolerance)
        self._gap_s = float(gap_s)
        self._reference_at: Optional[float] = None
        self._last_at: Optional[float] = None
        self._samples = 0
        self._discontinuities = 0
        self._last_detail: Optional[Dict[str, Any]] = None

    def reset(self, now: Optional[float] = None) -> None:
        """Start a new continuity claim. Called wherever the ring is cleared."""
        self._reference_at = now
        self._last_at = now
        self._samples = 0

    def observe(self, sample_count: int, now: float) -> Optional[Dict[str, Any]]:
        """Record an arrival. Returns a detail dict when continuity broke."""
        if sample_count <= 0:
            return None
        if self._reference_at is None:
            self.reset(now)
            self._last_at = now
            self._samples = int(sample_count)
            return None
        gap = now - (self._last_at if self._last_at is not None else now)
        self._last_at = now
        if gap > self._gap_s:
            return self._break("GAP", {"gap_s": round(gap, 4),
                                       "gap_limit_s": self._gap_s}, now)
        self._samples += int(sample_count)
        elapsed = now - self._reference_at
        if elapsed < self._interval:
            return None
        expected = self._rate * elapsed
        drift = (self._samples - expected) / expected if expected > 0 else 0.0
        detail = {"observed_samples": self._samples,
                  "expected_samples": round(expected, 1),
                  "drift": round(drift, 6), "elapsed_s": round(elapsed, 3),
                  "tolerance": self._tolerance}
        if abs(drift) > self._tolerance:
            return self._break("DRIFT", detail, now)
        # Healthy: start a fresh accumulation rather than integrating forever, so
        # one bad interval cannot be diluted by an hour of good ones.
        self.reset(now)
        self._last_at = now
        return None

    def _break(self, kind: str, detail: Dict[str, Any],
               now: float) -> Dict[str, Any]:
        self._discontinuities += 1
        record = {"kind": kind, "at": now, **detail}
        self._last_detail = record
        self.reset(now)
        self._last_at = now
        return record

    def status(self) -> Dict[str, Any]:
        return {
            "detector": "SAMPLE_COUNT_AGAINST_ELAPSED_TIME",
            "state": "WIRED",
            "check_interval_s": self._interval,
            "drift_tolerance": self._tolerance,
            "gap_limit_s": self._gap_s,
            "discontinuities": self._discontinuities,
            "last_discontinuity": self._last_detail,
        }


def retention_enabled() -> bool:
    """Default on: the operator approved bounded retention. Off is a kill switch."""
    raw = os.getenv("SCYTHE_RF_IQ_RETENTION", "enabled").strip().lower()
    return raw not in {"0", "off", "no", "false", "disabled", "none"}


def antenna_id() -> str:
    """UNDECLARED, never UNKNOWN: a metadata omission is not a puzzled inspection."""
    return (os.getenv("SDRPP_ANTENNA_ID", "") or "").strip() or "UNDECLARED"


def feedline_id() -> str:
    """UNDECLARED unless the operator said. A default cable is not a cable."""
    return (os.getenv("SDRPP_FEEDLINE_ID", "") or "").strip() or "UNDECLARED"


def _feedline_length_m(identifier: str) -> Optional[float]:
    try:
        from graphops_rf_antenna import FEEDLINES
    except Exception:                                   # pragma: no cover
        return None
    entry = FEEDLINES.get(identifier)
    return None if entry is None else entry.get("length_m")


def signal_chain_manifest(*, sensor_id: str, sample_type: str, sample_rate_hz: float,
                          antenna: Optional[str] = None,
                          feedline: Optional[str] = None,
                          gain_db: Optional[float] = None) -> Dict[str, Any]:
    """Everything the physical and decode path is made of, declared or not.

    A manifest rather than an argument list: the chain grew a feedline the moment
    someone asked what the antenna was plugged into, and it will grow a gain and a
    direct-sampling state when those are wired.  An expanding positional hash
    input makes every such addition a silent rewrite of what a hash meant, whereas
    a manifest is retained beside its hash and can simply be read.

    Every absence is named.  ``UNDECLARED`` is a metadata omission and says so; it
    is never ``UNKNOWN``, which would suggest the system looked and was puzzled.
    """
    antenna_identifier = antenna if antenna is not None else antenna_id()
    feedline_identifier = feedline if feedline is not None else feedline_id()
    declared_feedline = feedline_identifier not in ("UNDECLARED", "undeclared")
    return {
        "schema": SIGNAL_CHAIN_SCHEMA,
        "sensor_id": sensor_id,
        "sample_type": sample_type,
        "sample_rate_hz": float(sample_rate_hz),
        "antenna": {
            "id": antenna_identifier if antenna_identifier != "UNDECLARED" else None,
            "authority": ("OPERATOR_DECLARED" if antenna_identifier != "UNDECLARED"
                          else "UNDECLARED"),
        },
        "feedline": {
            "id": feedline_identifier if declared_feedline else None,
            "length_m": _feedline_length_m(feedline_identifier) if declared_feedline else None,
            "authority": "OPERATOR_DECLARED" if declared_feedline else "UNDECLARED",
        },
        # Declared only once something has actually set it. An automatic-gain
        # receiver has a gain, but not one this process knows, and reporting a
        # number for it would be inventing the instrument's own state.
        "gain": ({"value_db": float(gain_db), "authority": "OPERATOR_DECLARED"}
                 if gain_db is not None else
                 {"value_db": None, "authority": "UNDECLARED"}),
        "direct_sampling": "UNDECLARED",
        # Not a control and not a measurement: this receiver has no bias tee, so
        # there is nothing to switch and nothing to sense.
        "bias_tee": "NOT_FITTED",
        "clock_quality": CLOCK_QUALITY,
    }


def canonical_signal_chain_bytes(manifest: Dict[str, Any]) -> bytes:
    """The bytes that are hashed. Sorted keys and no incidental whitespace, so a
    reordered or reformatted manifest is the same chain and hashes the same."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def signal_chain_hash(*, sensor_id: str, sample_type: str, sample_rate_hz: float,
                      antenna: Optional[str] = None,
                      feedline: Optional[str] = None,
                      gain_db: Optional[float] = None) -> str:
    """Identity of the physical and decode path the samples came through.

    Deliberately excludes the centre frequency.  Retuning does not change the
    chain -- it is its own invalidation reason -- and folding it in here would
    make every retune look like a different antenna.

    Revision v2 hashes the canonical manifest and includes the feedline, so
    declaring a cable changes the hash.  That is the system noticing the analogue
    instrument changed, not breakage.  v1 hashes are not comparable with these and
    are not reinterpreted: ``prior_revision_comparable`` is false.
    """
    manifest = signal_chain_manifest(sensor_id=sensor_id, sample_type=sample_type,
                                     sample_rate_hz=sample_rate_hz, antenna=antenna,
                                     feedline=feedline, gain_db=gain_db)
    digest = hashlib.blake2s(canonical_signal_chain_bytes(manifest),
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
        self._gain_db: Optional[float] = None
        self._clock = ClockContinuityMonitor(sample_rate_hz)
        self._signal_chain_manifest = signal_chain_manifest(
            sensor_id=sensor_id, sample_type=sample_type, sample_rate_hz=sample_rate_hz)
        self._signal_chain_hash = signal_chain_hash(
            sensor_id=sensor_id, sample_type=sample_type, sample_rate_hz=sample_rate_hz)
        self._refused_reason: Optional[str] = None
        self._appended_blocks = 0
        # Bounded, metadata only. Kept on the owner rather than the ring so it
        # survives a reallocation and so a later clear cannot overwrite the cause
        # of an earlier one.
        self._history: deque = deque(maxlen=MAX_INVALIDATION_HISTORY)
        # Channelization bookkeeping. Bounded and metadata-only: products carry
        # measurements, never baseband, and the deque is capped.
        self._samples_since_window = 0
        self._windows_issued = 0
        self._products_total = 0
        self._products_by_outcome: Dict[str, int] = {}
        self._channelizer_errors = 0
        self._products: deque = deque(maxlen=MAX_TRACKED_PRODUCTS)

    # -- policy -------------------------------------------------------------

    def _requested_samples(self) -> int:
        """What the configured window would need at the configured rate."""
        return max(1, int(round(self._window_ms / 1000.0 * self._sample_rate_hz)))

    def _capacity(self) -> int:
        """The request, never above the approved allocation."""
        return min(DEFAULT_CAPACITY_SAMPLES, self._requested_samples())

    def _effective_retention_ms(self) -> float:
        """How much time the allocation actually holds at the configured rate.

        Equal to the configured window only while the rate keeps the request
        inside the ceiling.  Above that the ceiling wins and this is the honest
        number; ``configured_retention_ms`` keeps the request visible so the
        difference is a reported fact rather than a silent shortfall.
        """
        if self._sample_rate_hz <= 0:
            return 0.0
        return self._capacity() / self._sample_rate_hz * 1000.0

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
            # Counts every retained sample, not every block: the non-overlap
            # policy is measured in samples because that is what a window is.
            self._samples_since_window += kept
            # Timing is checked on the decoded count, not the byte count, so a
            # sample-type change cannot look like a clock fault.
            break_detail = self._clock.observe(
                kept, time.time() if timestamp is None else float(timestamp))
        if break_detail is not None:
            # Outside the lock: invalidate() takes it again, and a discontinuity
            # is exactly the case where the ring must be cleared rather than
            # silently spanning the gap.
            LOG.warning("clock discontinuity (%s): %s", break_detail["kind"], break_detail)
            self.invalidate("CLOCK_DISCONTINUITY")
        return kept

    def set_gain_db(self, gain_db: Optional[float]) -> Dict[str, Any]:
        """Record a gain change and clear the ring for it.

        Called by whatever actually moved the gain; this owns the consequence, not
        the control.  A gain step changes the amplitude relationship between
        samples either side of it, so a window spanning one would compare two
        different instruments -- which is why ``GAIN_CHANGE`` exists and why it
        clears rather than annotates.
        """
        with self._lock:
            previous = self._gain_db
            value = None if gain_db is None else float(gain_db)
            if value == previous:
                return {"changed": False, "gain_db": value,
                        "signal_chain_hash": self._signal_chain_hash}
            self._gain_db = value
            self._signal_chain_manifest = signal_chain_manifest(
                sensor_id=self._sensor_id, sample_type=self._sample_type,
                sample_rate_hz=self._sample_rate_hz, gain_db=value)
            self._signal_chain_hash = signal_chain_hash(
                sensor_id=self._sensor_id, sample_type=self._sample_type,
                sample_rate_hz=self._sample_rate_hz, gain_db=value)
        self.invalidate("GAIN_CHANGE")
        return {"changed": True, "previous_gain_db": previous, "gain_db": value,
                "signal_chain_hash": self._signal_chain_hash}

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
            # A cleared ring holds nothing, so no window is pending. Leaving the
            # counter high would let the first samples after a retune be issued
            # as a "complete" window they do not fill.
            self._samples_since_window = 0
            # A new continuity claim starts here too: samples either side of an
            # invalidation are not contiguous, so counting across it would
            # manufacture a drift that is really a deliberate discard.
            self._clock.reset()
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
            self._signal_chain_manifest = signal_chain_manifest(
                sensor_id=self._sensor_id, sample_type=self._sample_type,
                sample_rate_hz=self._sample_rate_hz, gain_db=self._gain_db)
            self._signal_chain_hash = signal_chain_hash(
                sensor_id=self._sensor_id, sample_type=self._sample_type,
                sample_rate_hz=self._sample_rate_hz, gain_db=self._gain_db)
            if self._ring is not None:
                LOG.info("bounded IQ ring discarded for %s; new chain %s",
                         reason, self._signal_chain_hash)
                self._ring.close()
                self._ring = None
                self._samples_since_window = 0
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
            self._samples_since_window = 0
            self._record_locked(str(reason or "").strip().upper(), None)

    # -- channelization (Phase 1d) ------------------------------------------

    def window_ready(self) -> bool:
        """Has a full, non-overlapping window's worth of samples arrived?"""
        with self._lock:
            return self._ring is not None and self._samples_since_window >= self._capacity()

    def acquire_window(self) -> Optional[WindowAcquisition]:
        """Issue one complete immutable window, or None when there is no ring.

        The counter resets only on success, so a refused acquisition does not
        silently consume the samples that would have formed the next window.
        """
        with self._lock:
            if self._ring is None:
                return None
            acquisition = self._ring.acquire_window()
            if acquisition:
                self._samples_since_window = 0
                self._windows_issued += 1
            return acquisition

    def channelize_window(self, window: IQWindow, *, capture_center_hz: float,
                          target_frequency_hz: float) -> ChannelizedProduct:
        """Verify and channelize one issued window into a bounded product.

        Returns measurements and verdict metadata.  The ``Channelization`` that
        carries the baseband is local to this method and is dropped before it
        returns, so there is no reference through which complex samples could
        reach a caller, a status payload or a log line.
        """
        with self._lock:
            ring = self._ring
            # Read under the lock, used outside it: this is the caller's belief
            # about the current configuration, and the point is that it can be
            # wrong by the time the channelizer checks it.
            epoch = ring.configuration_epoch if ring is not None else None
            expected_chain = self._signal_chain_hash
        request = ChannelRequest(
            capture_center_hz=float(capture_center_hz),
            target_frequency_hz=float(target_frequency_hz),
            expected_signal_chain_hash=expected_chain,
            expected_configuration_epoch=epoch,
        )
        # Deliberately outside the lock. See the module docstring: holding it
        # here would stall a retune for the length of the transform and would
        # make the retune-during-channelization race untestable.
        result = channelize(window, request, ring=ring)
        product = result.product
        self._record_product(product)
        return product

    def maybe_channelize(self, *, capture_center_hz: float,
                         target_frequency_hz: float) -> Optional[ChannelizedProduct]:
        """Channelize if a fresh complete window exists. Never raises.

        Returns None when there is nothing to do -- no ring, a partial window,
        or an acquisition the ring refused.  A None is not a refusal product:
        no window was ever issued, so there is nothing to render a verdict on.
        """
        try:
            if not self.window_ready():
                return None
            acquisition = self.acquire_window()
            if acquisition is None or not acquisition:
                return None
            return self.channelize_window(
                acquisition.window, capture_center_hz=capture_center_hz,
                target_frequency_hz=target_frequency_hz)
        except Exception:
            # Channelization is analysis. It must never be able to stop the
            # bridge publishing the spectrum products it published before it
            # existed, so the failure is counted and reported, not raised.
            LOG.exception("channelization failed")
            with self._lock:
                self._channelizer_errors += 1
            return None

    def _record_product(self, product: ChannelizedProduct) -> None:
        with self._lock:
            self._products_total += 1
            self._products_by_outcome[product.outcome] = (
                self._products_by_outcome.get(product.outcome, 0) + 1)
            self._products.append(product.to_dict())

    def channelizer_block(self) -> Dict[str, Any]:
        """Bounded channelization metadata for the status payload."""
        with self._lock:
            capacity = self._capacity()
            remaining = max(0, capacity - self._samples_since_window) if self._ring else capacity
            return {
                "state": CHANNELIZER_STATE,
                "note": CHANNELIZER_NOTE,
                "window_policy": WINDOW_POLICY,
                "windows_issued": self._windows_issued,
                "products_total": self._products_total,
                "products_by_outcome": dict(self._products_by_outcome),
                "channelizer_errors": self._channelizer_errors,
                "samples_until_next_window": remaining,
                "classification": "NOT_DERIVED_FROM_PRODUCTS",
                "baseband_retained": False,
                "last_product": self._products[-1] if self._products else None,
                # Frozen before the detector exists, so it constrains the detector
                # rather than describing one.
                "detector_input_contract": contract_status(),
                # Shadow: implemented, running on nothing, promoting nothing.
                "symbol_clock_detector": detector_status(),
                # The Q4 gate, executable and uncollected. Declared here so the
                # promotion rule is readable beside the products it will judge.
                "false_digital_gate": manifest_status(),
            }

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
                "configured_retention_ms": round(self._window_ms, 3),
                "effective_retention_ms": round(self._effective_retention_ms(), 3),
                "capacity_limited": self._requested_samples() > DEFAULT_CAPACITY_SAMPLES,
                "capacity_samples": capacity,
                "max_capacity_samples": DEFAULT_CAPACITY_SAMPLES,
                "raw_iq_exposed": False,
                "channelizer_state": CHANNELIZER_STATE,
                "channelizer_note": CHANNELIZER_NOTE,
                "channelizer": self.channelizer_block(),
                "signal_chain_hash": self._signal_chain_hash,
                # The manifest is retained beside the hash so a chain identity can
                # be read rather than reverse-engineered from an argument order.
                "signal_chain": dict(self._signal_chain_manifest),
                "signal_chain_hash_revision": SIGNAL_CHAIN_REVISION,
                "prior_revision_comparable": PRIOR_SIGNAL_CHAIN_REVISION_COMPARABLE,
                "antenna_id": antenna_id(),
                "feedline_id": feedline_id(),
                "owner": "ORCHESTRATOR_BRIDGE",
                "permitted": self._enabled and self._owns_capture and not self._refused_reason,
                "appended_blocks": self._appended_blocks,
                "invalidation_reasons": list(INVALIDATION_REASONS),
                "unwired_invalidation_reasons": list(UNWIRED_REASONS),
                "unwired_invalidation_note": UNWIRED_NOTE,
                "wired_invalidation_sources": dict(WIRED_REASON_SOURCES),
                "gain_db": self._gain_db,
                "clock_continuity": self._clock.status(),
                "invalidation_history": list(self._history),
                "inactive_reason": inactive,
                "inactive_reason_note": INACTIVE_REASONS.get(inactive) if inactive else None,
            }
            payload["ring"] = self._ring.status() if self._ring is not None else None
            return payload
