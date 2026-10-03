#!/usr/bin/env python3
"""§5.21 catalogue run, §7.1: the tuner-sequencing module.

Drives the NESDR through the declared visit schedule over rtl_tcp's control
channel, acquires R = 8 byte-exact 256 ms windows per visit, detects candidate
features, measures their persistence with the retention estimator, associates
detections across a tuning's retune visits, fits slopes, classifies, and writes
``catalogue.json`` + ``chain.json`` + ``README-provenance.txt``.

Runs capture-local on the host with the dongle (scythe-1): the measured
tailnet path (10.5 Mbps) cannot sustain the 32.8 Mbps stream, and rtl_tcp
drops samples silently under backpressure. The byte-exact window accounting
here is the guard the streaming path cannot give.

Design decisions, all recorded where they bite:

* A window is 256 ms at 2.048 MS/s: exactly 1,048,576 bytes of offset-binary
  uint8 I/Q. A short read is a refused visit, re-acquired whole (up to
  ``MAX_VISIT_ATTEMPTS``); a visit that cannot be acquired is recorded as
  refused and the schedule moves on. The schedule is the declared one either
  way -- a refused visit is a gap in the record, not a rescheduling.
* Detection is per visit: the median spectrum across the 8 windows, local
  maxima above the tuning-local median by ``PLAN_PERSISTENCE_MARGIN_DB``,
  inside the usable half-span (the slope fit would refuse folded offsets, so
  detecting them is wasted estimator calls). The DC bin is kept: LO leakage
  is a genuine internal product and the slope analysis says so itself.
* Association seeds from the tuning's earliest visit (schedule order) with
  retained candidates, strongest first, over integer slope hypotheses in
  ``[-PLAN_MAX_MIXING_SLOPE, PLAN_MAX_MIXING_SLOPE]``. A seed explained by two
  slopes is ambiguous and refused, not picked. A detection claimed by one
  track cannot seed or join another. A candidate at a later visit that no
  seeded track explains is unassociated: a product persistent at 7/8 within
  each visit shows at every visit, and one that does not is not slope-fittable.
* Classification is the §4 table read off ``matched_slope``: None ->
  ``SPUR_CANDIDATE_UNRESOLVED``; -1 -> the comb is searched for a harmonic and
  a miss is an ingress *finding*, not a product; otherwise
  ``CONSISTENT_WITH_INTERNAL_MIXING``. ``VANISHES_ON_DECLARED_TERMINATION``
  is decided by the load and this run is fully terminated, so the sequencer
  never assigns it.
* Stability is ``SESSION_SCOPED`` for every entry of a one-epoch catalogue.
  Final stability (``RECONNECT_STABLE`` / ``POWER_CYCLE_STABLE``) is assigned
  by the cross-epoch synthesis matching entries across epochs, which is a
  later step, not this module.
* The catalogue entry carries the persistence observation of its seed visit:
  the earliest visit, never the strongest. Picking the strongest would be
  selection wearing measurement's clothes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from rf_promotion_envelope import (
    Band,
    CataloguedSpur,
    ReferenceComb,
    SpurSlopeEstimate,
    generate_tunings,
    generate_visit_schedule,
    matched_mixing_slope,
    usable_half_span_hz,
    CONSISTENT_WITH_INTERNAL_MIXING,
    CONSISTENT_WITH_INTERNAL_REFERENCE,
    PLAN_MAX_MIXING_SLOPE,
    PLAN_PERSISTENCE_MARGIN_DB,
    PROMOTION_SAMPLE_RATE_HZ,
    SESSION_SCOPED,
    SPUR_CANDIDATE_UNRESOLVED,
)
from rf_spur_retention_estimator import measure_persistence


SEQUENCER_REVISION = "seq-7.1-1"
SAMPLE_RATE_HZ = 2048000
assert SAMPLE_RATE_HZ == PROMOTION_SAMPLE_RATE_HZ, (
    "the sequencer samples at the promotion rate the plan analyses; "
    "a mismatch here is a different instrument")
WINDOW_S = 0.256
WINDOW_BYTES = int(WINDOW_S * SAMPLE_RATE_HZ) * 2  # uint8 I/Q
assert WINDOW_BYTES == 1_048_576
WINDOWS_PER_VISIT = 8
SETTLE_S = 0.3          # post-retune discard before the first window
MAX_VISIT_ATTEMPTS = 3  # the first acquisition plus two re-acquisitions
WINDOW_DEADLINE_S = 3.0
BIN_HZ = 1.0 / WINDOW_S  # 3.90625 Hz
ASSOCIATION_TOL_BINS = 3
ASSOCIATION_TOL_HZ = ASSOCIATION_TOL_BINS * BIN_HZ

# rtl_tcp control opcodes (1 opcode byte + 4-byte big-endian value).
_RTL_TCP_SET_FREQ = 0x01
_RTL_TCP_SET_GAIN_MODE = 0x03
_RTL_TCP_SET_GAIN = 0x04
_GAIN_MODE_MANUAL = 1

# The R820T2 gain table rtl_tcp clamps to. The declared gain must be a member:
# a clamped gain is a different chain wearing the declared chain's hash.
_R820T2_GAINS_DB = (
    0.0, 0.9, 1.4, 2.7, 3.7, 7.7, 8.7, 12.5, 14.4, 15.7, 16.6, 19.7, 20.7,
    23.1, 25.0, 27.8, 29.7, 32.8, 33.8, 36.4, 37.2, 38.6, 40.2, 42.1, 43.4,
    43.9, 44.5, 48.0, 49.6,
)


class SequencerRefused(Exception):
    """The run stops rather than proceeds on a compromised premise."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code


class VisitRefused(Exception):
    """One visit could not be acquired; the schedule moves on without it."""


def _decode_iq(raw: bytes) -> np.ndarray:
    """Offset-binary uint8 I/Q -> complex64 in [-1, 1]."""
    if len(raw) != WINDOW_BYTES:
        raise VisitRefused(
            f"window is {len(raw)} bytes, not the byte-exact {WINDOW_BYTES}")
    u8 = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
    iq = u8.reshape(-1, 2)
    return ((iq[:, 0] - 127.5) / 127.5
            + 1j * (iq[:, 1] - 127.5) / 127.5).astype(np.complex64)


class RtlTcpTuner:
    """The dongle, commanded over rtl_tcp's control channel.

    The sample stream and the control channel are one socket, as rtl_tcp
    serves them. `socket_factory` exists so tests can serve synthetic bytes
    without hardware; production passes nothing.
    """

    def __init__(self, host: str, port: int,
                 socket_factory=None) -> None:
        self._host = host
        self._port = port
        self._socket_factory = socket_factory or socket.create_connection
        self._sock = None
        self.tuner_type: Optional[int] = None
        self.gain_count: Optional[int] = None

    def __enter__(self) -> "RtlTcpTuner":
        sock = self._socket_factory((self._host, self._port))
        info = self._read_exactly(sock, 12)
        magic, tuner_type, gain_count = (
            info[0:4], int.from_bytes(info[4:8], "big"),
            int.from_bytes(info[8:12], "big"))
        if magic != b"RTL0":
            sock.close()
            raise SequencerRefused(
                "TUNER_NOT_RTL_TCP",
                f"dongle_info magic is {magic!r}, not b'RTL0'")
        self.tuner_type = tuner_type
        self.gain_count = gain_count
        self._sock = sock
        return self

    def __exit__(self, *exc) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _read_exactly(self, sock, nbytes: int,
                      deadline_s: float = 10.0) -> bytes:
        buf = bytearray()
        sock.settimeout(deadline_s)
        while len(buf) < nbytes:
            try:
                chunk = sock.recv(nbytes - len(buf))
            except socket.timeout as exc:
                raise VisitRefused(
                    f"only {len(buf)} of {nbytes} bytes inside "
                    f"{deadline_s} s; the visit is refused") from exc
            if not chunk:
                raise VisitRefused(
                    f"stream closed after {len(buf)} of {nbytes} bytes; "
                    "the visit is refused")
            buf += chunk
        return bytes(buf)

    def _command(self, opcode: int, value: int) -> None:
        if self._sock is None:
            raise SequencerRefused("TUNER_NOT_CONNECTED",
                                   "no IQ socket is open")
        self._sock.sendall(
            bytes([opcode]) + int(value).to_bytes(4, "big", signed=False))

    def set_lo_hz(self, lo_hz: float) -> None:
        """Set the tuner frequency. The ring invalidates with RETUNE."""
        if not math.isfinite(lo_hz) or lo_hz <= 0:
            raise SequencerRefused("TUNER_BAD_LO",
                                   f"LO of {lo_hz!r} Hz is not a tuning")
        self._command(_RTL_TCP_SET_FREQ, int(round(lo_hz)))

    def set_manual_gain_db(self, gain_db: float) -> None:
        """Pin the gain for the whole run. One gain, one chain, one catalogue."""
        if gain_db not in _R820T2_GAINS_DB:
            raise SequencerRefused(
                "GAIN_NOT_SUPPORTED",
                f"{gain_db} dB is not in the R820T2 table; rtl_tcp would "
                "clamp it and the chain hash would describe a gain the "
                "receiver is not at")
        self._command(_RTL_TCP_SET_GAIN_MODE, _GAIN_MODE_MANUAL)
        self._command(_RTL_TCP_SET_GAIN, int(round(gain_db * 10.0)))

    def read_window(self) -> np.ndarray:
        """One byte-exact 256 ms window, decoded."""
        return _decode_iq(self._read_exactly(self._sock, WINDOW_BYTES,
                                             WINDOW_DEADLINE_S))

    def discard(self, seconds: float) -> None:
        """Read and drop post-retune bytes: the PLL's settling is not data."""
        self._read_exactly(self._sock,
                           int(seconds * SAMPLE_RATE_HZ) * 2,
                           deadline_s=seconds + 5.0)


# ---------------------------------------------------------------------------
# Acquisition: one visit = a retune, a settle, and R byte-exact windows.
# ---------------------------------------------------------------------------

@dataclass
class AcquiredVisit:
    position: int
    tuning_index: int
    tuning_id: str
    lo_hz: float
    retune_delta_hz: float
    windows: List[np.ndarray]
    elapsed_s: float
    attempt: int


def acquire_visit(tuner: RtlTcpTuner, position: int, tuning_id: str,
                  tuning_index: int, lo_hz: float,
                  retune_delta_hz: float) -> AcquiredVisit:
    """Acquire the visit's 8 windows, or raise VisitRefused.

    The whole visit is re-acquired on a short read: 8 windows at one LO
    setting are one observation, and 7 windows at this LO plus 1 at the next
    retune's are not.
    """
    last: Optional[VisitRefused] = None
    for attempt in range(1, MAX_VISIT_ATTEMPTS + 1):
        try:
            tuner.set_lo_hz(lo_hz)
            tuner.discard(SETTLE_S)
            started = time.monotonic()
            windows = [tuner.read_window()
                       for _ in range(WINDOWS_PER_VISIT)]
            return AcquiredVisit(
                position=position, tuning_index=tuning_index,
                tuning_id=tuning_id, lo_hz=lo_hz,
                retune_delta_hz=retune_delta_hz, windows=windows,
                elapsed_s=time.monotonic() - started, attempt=attempt)
        except VisitRefused as exc:
            last = exc
    raise VisitRefused(
        f"visit {position} refused after {MAX_VISIT_ATTEMPTS} attempts: {last}")


# ---------------------------------------------------------------------------
# Detection: the catalogue sweep, per visit.
# ---------------------------------------------------------------------------

@dataclass
class CandidateFeature:
    baseband_hz: float
    excess_db: float
    peak_bin: int


def _parabolic_peak_offset(mag: np.ndarray, k: int) -> float:
    """Sub-bin peak location by parabolic interpolation, in bins."""
    if k <= 0 or k >= len(mag) - 1:
        return float(k)
    a, b, c = mag[k - 1], mag[k], mag[k + 1]
    denom = a - 2.0 * b + c
    if denom == 0.0:
        return float(k)
    return float(k) + 0.5 * (a - c) / denom


def detect_candidates(windows: Sequence[np.ndarray]) -> List[CandidateFeature]:
    """Local maxima above the tuning-local median by the persistence margin.

    The spectrum is the median across the 8 repeats, so a candidate that
    survives detection is already halfway to surviving retention. The floor
    is the median of that median spectrum: robust to the very spurs it
    hunts.
    """
    n = len(windows[0])
    spectra = np.abs(np.fft.fft(np.stack(windows), axis=1)) ** 2
    median_spec = np.median(spectra, axis=0)
    floor = float(np.median(median_spec))
    if not math.isfinite(floor) or floor <= 0.0:
        raise SequencerRefused("SPECTRUM_NOT_FINITE",
                               "the median spectrum has no usable floor")
    usable = usable_half_span_hz()
    freqs = np.fft.fftfreq(n, d=1.0 / SAMPLE_RATE_HZ)
    inner = median_spec[1:-1]
    is_peak = (inner > median_spec[:-2]) & (inner > median_spec[2:])
    excess_db = 10.0 * np.log10(inner / floor)
    in_span = np.abs(freqs[1:-1]) <= usable
    hits = np.where(is_peak
                    & (excess_db > PLAN_PERSISTENCE_MARGIN_DB)
                    & in_span)[0] + 1
    found = []
    for k in hits:
        k = int(k)
        # fftfreq, not the bin index: the upper half of the FFT is negative
        # frequency, and the bin index alone would alias it positive.
        f_hz = float(freqs[k]) + (_parabolic_peak_offset(median_spec, k) - k) * BIN_HZ
        found.append(CandidateFeature(
            baseband_hz=float(f_hz),
            excess_db=float(10.0 * math.log10(median_spec[int(k)] / floor)),
            peak_bin=int(k)))
    # Strongest first: association claims detections greedily, and the
    # strongest peak owns its bin.
    found.sort(key=lambda c: c.excess_db, reverse=True)
    return found


# ---------------------------------------------------------------------------
# Association: one product, three retunes, one track.
# ---------------------------------------------------------------------------

@dataclass
class TuningVisitData:
    visit: AcquiredVisit
    candidates: List[CandidateFeature]
    persistence: Dict[int, Any]
    retained: List[int]  # indices into candidates, strongest first


@dataclass
class AssociatedTrack:
    tuning_id: str
    anchor_center_hz: float
    deltas: Tuple[float, ...]
    offsets: Tuple[float, ...]
    visit_positions: Tuple[int, ...]
    slope_hypothesis: int
    seed_excess_db: float
    persistence: Any  # the seed visit's SpurPersistenceObservation


def associate_tracks(tuning_id: str, anchor_center_hz: float,
                     visits: List[TuningVisitData]
                     ) -> Tuple[List[AssociatedTrack], List[Dict],
                                List[Dict]]:
    """Bind one product's detections across a tuning's visits.

    Seeds are the earliest visit's retained candidates, strongest first. Each
    seed is tested against every integer slope in the mixing family; a seed
    explained by exactly one slope becomes a track, by two or more is
    ambiguous and refused, by none is unassociated. Detections are claimed
    greedily: one peak, one product.
    """
    ordered = sorted(visits, key=lambda v: v.visit.position)
    seed_visit = next((v for v in ordered if v.retained), None)
    unassociated: List[Dict] = []
    ambiguous: List[Dict] = []
    tracks: List[AssociatedTrack] = []
    if seed_visit is None:
        return tracks, unassociated, ambiguous

    by_position = {v.visit.position: v for v in ordered}
    claimed = set()  # (visit_position, candidate_index)

    def nearest(vd: TuningVisitData, predicted_hz: float
                ) -> Optional[int]:
        best, best_dist = None, ASSOCIATION_TOL_HZ
        for idx in vd.retained:
            if (vd.visit.position, idx) in claimed:
                continue
            dist = abs(vd.candidates[idx].baseband_hz - predicted_hz)
            if dist <= best_dist:
                best, best_dist = idx, dist
        return best

    for seed_idx in seed_visit.retained:
        if (seed_visit.visit.position, seed_idx) in claimed:
            continue
        seed = seed_visit.candidates[seed_idx]
        hypotheses = []
        for m in range(-PLAN_MAX_MIXING_SLOPE, PLAN_MAX_MIXING_SLOPE + 1):
            legs = []
            ok = True
            for vd in ordered:
                pred = (seed.baseband_hz
                        + m * (vd.visit.retune_delta_hz
                               - seed_visit.visit.retune_delta_hz))
                hit = nearest(vd, pred)
                if hit is None:
                    ok = False
                    break
                legs.append((vd, hit))
            if ok:
                hypotheses.append((m, legs))
        if len(hypotheses) == 1:
            m, legs = hypotheses[0]
            for vd, hit in legs:
                claimed.add((vd.visit.position, hit))
            order = sorted(range(len(legs)),
                           key=lambda i: legs[i][0].visit.position)
            tracks.append(AssociatedTrack(
                tuning_id=tuning_id,
                anchor_center_hz=anchor_center_hz,
                deltas=tuple(legs[i][0].visit.retune_delta_hz
                             for i in order),
                offsets=tuple(legs[i][0].candidates[legs[i][1]].baseband_hz
                              for i in order),
                visit_positions=tuple(legs[i][0].visit.position
                                      for i in order),
                slope_hypothesis=m,
                seed_excess_db=seed.excess_db,
                persistence=seed_visit.persistence[seed_idx]))
        elif len(hypotheses) > 1:
            claimed.add((seed_visit.visit.position, seed_idx))
            ambiguous.append({
                "tuning_id": tuning_id,
                "visit_position": seed_visit.visit.position,
                "baseband_hz": seed.baseband_hz,
                "slopes": sorted(m for m, _ in hypotheses),
                "reason": "one detection explained by more than one integer "
                          "slope; refused rather than picked",
            })
        else:
            unassociated.append({
                "tuning_id": tuning_id,
                "visit_position": seed_visit.visit.position,
                "baseband_hz": seed.baseband_hz,
                "excess_db": seed.excess_db,
                "reason": "no integer slope binds this detection across the "
                          "tuning's visits; not slope-fittable",
            })
    for vd in ordered:
        for idx in vd.retained:
            if (vd.visit.position, idx) in claimed:
                continue
            cand = vd.candidates[idx]
            unassociated.append({
                "tuning_id": tuning_id,
                "visit_position": vd.visit.position,
                "baseband_hz": cand.baseband_hz,
                "excess_db": cand.excess_db,
                "reason": "later-visit detection no seeded track explains; "
                          "a 7/8-persistent product shows at every visit",
            })
    return tracks, unassociated, ambiguous


# ---------------------------------------------------------------------------
# Classification: the §4 table, read off the fitted slope.
# ---------------------------------------------------------------------------

def classify_track(track: AssociatedTrack, comb: ReferenceComb,
                   track_no: int) -> Tuple[Optional[CataloguedSpur],
                                           Optional[Dict]]:
    """Fit the slope, read the class. Returns (spur, ingress_finding)."""
    slope_est = SpurSlopeEstimate(
        tuning_id=track.tuning_id,
        retune_delta_hz=track.deltas,
        signed_baseband_hz=track.offsets,
        anchor_center_frequency_hz=track.anchor_center_hz)
    matched = slope_est.matched_slope
    spur_id = f"{track.tuning_id}-trk{track_no:02d}"
    if matched is None:
        return (CataloguedSpur(
            spur_id=spur_id, classification=SPUR_CANDIDATE_UNRESOLVED,
            stability_class=SESSION_SCOPED, persistence=track.persistence,
            slope=slope_est, reference_harmonic=None), None)
    if matched == -1:
        # Slope -1 is a fixed RF position: LO + offset is constant.
        rf_hz = float(sum(
            track.anchor_center_hz + d + o
            for d, o in zip(track.deltas, track.offsets)) / len(track.deltas))
        harmonic = next(
            (n for n in range(1, comb.harmonic_cap + 1)
             if comb.matches(rf_hz, n)), None)
        if harmonic is None:
            return (None, {
                "tuning_id": track.tuning_id,
                "matched_slope": matched,
                "rf_hz": rf_hz,
                "reason": "slope -1 with no harmonic of the declared "
                          "reference inside the match window: an ingress "
                          "finding, not a product",
            })
        spur = CataloguedSpur(
            spur_id=spur_id,
            classification=CONSISTENT_WITH_INTERNAL_REFERENCE,
            stability_class=SESSION_SCOPED, persistence=track.persistence,
            slope=slope_est, reference_harmonic=harmonic)
        spur.reference_match(comb)  # the comb checks the claim, not us
        return (spur, None)
    return (CataloguedSpur(
        spur_id=spur_id, classification=CONSISTENT_WITH_INTERNAL_MIXING,
        stability_class=SESSION_SCOPED, persistence=track.persistence,
        slope=slope_est, reference_harmonic=None), None)


# ---------------------------------------------------------------------------
# The run: declarations in, artefacts out.
# ---------------------------------------------------------------------------

_REQUIRED_DECLARATIONS = (
    "run_id", "epoch", "receiver", "reference_hz", "reference_ppm",
    "ppm_justification", "termination", "site", "bands", "seed", "gain_db",
    "rtl_tcp_host", "rtl_tcp_port", "output_dir",
)


def check_declaration(decl: Dict[str, Any]) -> Dict[str, Any]:
    """The software checks each declaration is present and well-formed.

    It cannot check that any of them is true. That is the operator's act,
    recorded in the provenance note.
    """
    missing = [k for k in _REQUIRED_DECLARATIONS if k not in decl]
    if missing:
        raise SequencerRefused("DECLARATION_ABSENT",
                               f"missing declarations: {missing}")
    if type(decl["seed"]) is not int:
        raise SequencerRefused("DECLARATION_MALFORMED",
                               "the seed is an integer or it reproduces nothing")
    for name in ("reference_hz", "reference_ppm"):
        value = decl[name]
        if not math.isfinite(value) or value <= 0:
            raise SequencerRefused("DECLARATION_MALFORMED",
                                   f"{name} is {value!r}")
    if not decl["ppm_justification"] or not str(
            decl["ppm_justification"]).strip():
        raise SequencerRefused("DECLARATION_ABSENT",
                               "the ppm justification is prose, not a default")
    term = decl["termination"]
    for key in ("part", "connector", "fitted"):
        if not term.get(key):
            raise SequencerRefused(
                "TERMINATION_UNDECLARED",
                f"termination {key!r} is undeclared; the load must be "
                "fitted before the first window and the act recorded")
    if not decl["site"] or not str(decl["site"]).strip():
        raise SequencerRefused("DECLARATION_ABSENT",
                               "the site is the operator's words, not ours")
    if not decl["receiver"].get("sensor_id"):
        raise SequencerRefused("DECLARATION_ABSENT",
                               "the receiver names its sensor_id")
    bands = [Band(band_id=b["band_id"], low_hz=b["low_hz"],
                  high_hz=b["high_hz"]) for b in decl["bands"]]
    if decl["gain_db"] not in _R820T2_GAINS_DB:
        raise SequencerRefused(
            "GAIN_NOT_SUPPORTED",
            f"{decl['gain_db']} dB is not in the R820T2 table")
    return {"bands": bands}


def _chain_digest(decl: Dict[str, Any]) -> str:
    """The terminated chain's identity, computed, never transcribed."""
    canonical = json.dumps({
        "sensor_id": decl["receiver"]["sensor_id"],
        "receiver": decl["receiver"].get("description", ""),
        "gain_db": decl["gain_db"],
        "termination_part": decl["termination"]["part"],
        "termination_connector": decl["termination"]["connector"],
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "sequencer_revision": SEQUENCER_REVISION,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def run_epoch(decl: Dict[str, Any], tuner: RtlTcpTuner,
              progress=None) -> Dict[str, Any]:
    """Perform the epoch. Returns the catalogue document."""
    checked = check_declaration(decl)
    bands = checked["bands"]
    seed = decl["seed"]
    tunings = generate_tunings(seed=seed, bands=tuple(bands))
    schedule = generate_visit_schedule(seed=seed, tunings=tuple(tunings))
    comb = ReferenceComb(reference_hz=decl["reference_hz"],
                         reference_ppm=decl["reference_ppm"])
    tuner.set_manual_gain_db(decl["gain_db"])

    schedule_digest = hashlib.sha256(json.dumps(
        {"tunings": [t.to_dict() for t in tunings],
         "visits": [v.to_dict() for v in schedule]},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    by_tuning: Dict[int, List[TuningVisitData]] = {}
    refused_visits: List[Dict] = []
    events: List[Dict] = []
    total = len(schedule)
    for number, visit in enumerate(schedule, start=1):
        tuning = tunings[visit.tuning_index]
        lo_hz = tuning.center_frequency_hz + visit.retune_delta_hz
        if progress:
            progress(number, total, visit, tuning.tuning_id, lo_hz)
        try:
            acquired = acquire_visit(
                tuner, visit.position, tuning.tuning_id, visit.tuning_index,
                lo_hz, visit.retune_delta_hz)
        except VisitRefused as exc:
            refused_visits.append({
                "position": visit.position,
                "tuning_id": tuning.tuning_id,
                "lo_hz": lo_hz,
                "reason": str(exc),
            })
            events.append({"type": "visit_refused",
                           "position": visit.position,
                           "reason": str(exc)})
            continue
        candidates = detect_candidates(acquired.windows)
        persistence = {}
        retained = []
        for idx, cand in enumerate(candidates):
            obs = measure_persistence(
                tuning_id=tuning.tuning_id, windows=acquired.windows,
                sample_rate_hz=SAMPLE_RATE_HZ,
                feature_baseband_hz=cand.baseband_hz)
            persistence[idx] = obs
            if obs.persistent():
                retained.append(idx)
        by_tuning.setdefault(visit.tuning_index, []).append(TuningVisitData(
            visit=acquired, candidates=candidates, persistence=persistence,
            retained=retained))
        events.append({"type": "visit_acquired",
                       "position": visit.position,
                       "tuning_id": tuning.tuning_id,
                       "lo_hz": lo_hz,
                       "attempt": acquired.attempt,
                       "elapsed_s": round(acquired.elapsed_s, 3),
                       "candidates": len(candidates),
                       "retained": len(retained)})

    entries: List[Dict] = []
    ingress_findings: List[Dict] = []
    unassociated: List[Dict] = []
    ambiguous: List[Dict] = []
    for tuning_index in sorted(by_tuning):
        tuning = tunings[tuning_index]
        tracks, una, amb = associate_tracks(
            tuning.tuning_id, tuning.center_frequency_hz,
            by_tuning[tuning_index])
        unassociated.extend(una)
        ambiguous.extend(amb)
        for track_no, track in enumerate(tracks):
            spur, finding = classify_track(track, comb, track_no)
            if finding is not None:
                ingress_findings.append(finding)
            else:
                assert spur is not None
                entries.append(spur.to_dict())

    return {
        "run_id": decl["run_id"],
        "epoch": decl["epoch"],
        "sequencer_revision": SEQUENCER_REVISION,
        "seed": seed,
        "schedule_digest": schedule_digest,
        "n_tunings": len(tunings),
        "n_visits_scheduled": len(schedule),
        "n_visits_acquired": sum(len(v) for v in by_tuning.values()),
        "n_visits_refused": len(refused_visits),
        "reference": {"reference_hz": decl["reference_hz"],
                      "reference_ppm": decl["reference_ppm"]},
        "chain_digest": _chain_digest(decl),
        "tuner_type": tuner.tuner_type,
        "entries": entries,
        "ingress_findings": ingress_findings,
        "refused_visits": refused_visits,
        "unassociated_detections": unassociated,
        "ambiguous_associations": ambiguous,
        "events": events,
    }


def write_artefacts(document: Dict[str, Any], decl: Dict[str, Any],
                    output_dir: str) -> List[str]:
    """catalogue.json, chain.json, README-provenance.txt, run-log.json."""
    import os
    os.makedirs(output_dir, exist_ok=True)
    paths = {}

    catalogue_path = os.path.join(output_dir, "catalogue.json")
    with open(catalogue_path, "w") as handle:
        json.dump({k: document[k] for k in document if k != "events"},
                  handle, indent=2, sort_keys=True)
    paths["catalogue"] = catalogue_path

    chain_path = os.path.join(output_dir, "chain.json")
    with open(chain_path, "w") as handle:
        json.dump({
            "chain_digest": document["chain_digest"],
            "receiver": decl["receiver"],
            "gain_db": decl["gain_db"],
            "termination": decl["termination"],
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "sequencer_revision": SEQUENCER_REVISION,
            "note": "the digest is computed from these fields; the capture "
                    "plan rebuilds the envelope ChainMember from the same "
                    "declarations and compares",
        }, handle, indent=2, sort_keys=True)
    paths["chain"] = chain_path

    readme_path = os.path.join(output_dir, "README-provenance.txt")
    with open(readme_path, "w") as handle:
        run_id = decl["run_id"]
        epoch_no = decl["epoch"]
        seed_no = decl["seed"]
        sensor_id = decl["receiver"]["sensor_id"]
        receiver_desc = decl["receiver"].get("description", "")
        site = decl["site"]
        term_part = decl["termination"]["part"]
        term_conn = decl["termination"]["connector"]
        term_fitted = decl["termination"]["fitted"]
        ref_hz = decl["reference_hz"]
        ref_ppm = decl["reference_ppm"]
        ppm_just = decl["ppm_justification"]
        gain_db = decl["gain_db"]
        sched = document["schedule_digest"][:16]
        tuner_type = document["tuner_type"]
        n_acq = document["n_visits_acquired"]
        n_sched = document["n_visits_scheduled"]
        n_ref = document["n_visits_refused"]
        n_entries = len(document["entries"])
        n_find = len(document["ingress_findings"])
        n_una = len(document["unassociated_detections"])
        n_amb = len(document["ambiguous_associations"])
        handle.write(
            f"§5.21 spur catalogue, run {run_id}, epoch {epoch_no}\n"
            f"sequencer {SEQUENCER_REVISION}, seed {seed_no}, "
            f"schedule {sched}\n"
            f"\nReceiver: {receiver_desc} (sensor {sensor_id}), "
            f"tuner type {tuner_type}\n"
            f"Site and enclosure: {site}\n"
            f"Termination: {term_part} via {term_conn}; "
            f"fitted {term_fitted}\n"
            f"Reference: {ref_hz:.0f} Hz, {ref_ppm:g} ppm. "
            f"Justification: {ppm_just}\n"
            f"Gain: {gain_db} dB pinned for the whole run. "
            f"Sample rate: {SAMPLE_RATE_HZ} Hz.\n"
            f"\nWindows are 256 ms / 1,048,576 bytes, byte-exact; a short "
            f"window refuses its visit. {n_acq} of {n_sched} visits "
            f"acquired, {n_ref} refused.\n"
            f"{n_entries} catalogue entries, {n_find} ingress findings, "
            f"{n_una} unassociated detections, {n_amb} ambiguous "
            f"associations.\n"
            f"\nStability is SESSION_SCOPED for every entry: final "
            f"stability is assigned by the cross-epoch synthesis, not "
            f"here.\n")
    paths["readme"] = readme_path

    log_path = os.path.join(output_dir, "run-log.json")
    with open(log_path, "w") as handle:
        json.dump({"run_id": document["run_id"],
                   "epoch": document["epoch"],
                   "events": document["events"]},
                  handle, indent=2, sort_keys=True)
    paths["log"] = log_path
    return [paths["catalogue"], paths["chain"], paths["readme"],
            paths["log"]]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="§5.21 catalogue run, §7.1: the tuner-sequencing module")
    parser.add_argument("--declaration", required=True,
                        help="JSON run declaration (the OPERATOR_DECLARED inputs)")
    args = parser.parse_args(argv)
    with open(args.declaration) as handle:
        decl = json.load(handle)

    def progress(number, total, visit, tuning_id, lo_hz):
        print(f"[{number:3d}/{total}] visit {visit.position}: "
              f"{tuning_id} LO {lo_hz:,.0f} Hz "
              f"(delta {visit.retune_delta_hz:+,.0f})", flush=True)

    with RtlTcpTuner(decl["rtl_tcp_host"], decl["rtl_tcp_port"]) as tuner:
        print(f"tuner type {tuner.tuner_type}, "
              f"{tuner.gain_count} gain steps reported", flush=True)
        document = run_epoch(decl, tuner, progress=progress)
    paths = write_artefacts(document, decl, decl["output_dir"])
    print(f"epoch {document[epoch]}: {len(document[entries])} entries, "
          f"{document[n_visits_refused]} refused visits")
    for path in paths:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
