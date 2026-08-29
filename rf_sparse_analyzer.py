"""NESDR sparse residual analysis (M0 residual + M1 carrier recovery).

Transplant from MIMO-FMCW sparse recovery, not a radar drop-in:

  bounded FFT frames -> median background -> residual -> candidate bins
  -> peak-track carrier/drift, plus OMP-assisted periodic-amplitude recovery

The NESDR remains a passive single-channel receiver. This module never claims
range, AoA, or blade length. Peak FFT measurements stay OBSERVED elsewhere;
records emitted here are DERIVED_INFERENCE. Noise and null windows are valid
outcomes: NO_SUPPORT, INSUFFICIENT_EVIDENCE, NOISE_COMPATIBLE.

Raw IQ and full waterfalls never leave the edge.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import os
import threading
import time
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


SCHEMA_SUPPORT = "scythe.rf-sparse-support.v1"
SCHEMA_RESIDUAL = "scythe.rf-residual-window.v1"
DICTIONARY_REVISION = "scythe.rf-sparse-dict.m1.v1"
ATOM_FAMILIES = ("stationary_carrier", "linear_drift", "periodic_amplitude")
RESERVED_ATOM_FAMILIES = ("periodic_sideband",)
NULL_OUTCOMES = ("NO_SUPPORT", "INSUFFICIENT_EVIDENCE", "NOISE_COMPATIBLE")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _digest(*parts: Any) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.blake2s(payload, digest_size=8).hexdigest()


def _pattern_hash(sequences: Sequence[int], seed: int) -> str:
    hasher = hashlib.sha256()
    hasher.update(f"{seed}:".encode("ascii"))
    hasher.update(",".join(str(item) for item in sequences).encode("ascii"))
    return "sha256:" + hasher.hexdigest()


@dataclass(frozen=True)
class SparseAnalyzerConfig:
    enabled: bool = True
    window_seconds: float = 4.0
    max_frames: int = 40
    min_frames: int = 8
    compression_ratio: float = 1.0
    sampling_seed: int = 20260827
    max_support: int = 3
    max_candidates: int = 4
    mad_k: float = 4.0
    max_records: int = 256
    min_residual_reduction: float = 0.35
    min_snr_db: float = 6.0
    min_persistence: float = 0.35
    tuner_ppm: float = 0.0
    gain_db: float = float("nan")
    antenna_id: str = "unspecified"
    clock_quality: str = "unknown"

    @classmethod
    def from_env(cls) -> "SparseAnalyzerConfig":
        return cls(
            enabled=_env_bool("SDRPP_SPARSE_ENABLED", True),
            window_seconds=float(os.getenv("SDRPP_SPARSE_WINDOW_S", "4")),
            max_frames=int(os.getenv("SDRPP_SPARSE_MAX_FRAMES", "40")),
            min_frames=int(os.getenv("SDRPP_SPARSE_MIN_FRAMES", "8")),
            compression_ratio=float(os.getenv("SDRPP_SPARSE_COMPRESSION", "1")),
            sampling_seed=int(os.getenv("SDRPP_SPARSE_SEED", "20260827")),
            max_support=int(os.getenv("SDRPP_SPARSE_MAX_SUPPORT", "3")),
            max_candidates=int(os.getenv("SDRPP_SPARSE_MAX_CANDIDATES", "4")),
            mad_k=float(os.getenv("SDRPP_SPARSE_MAD_K", "4")),
            max_records=int(os.getenv("SDRPP_SPARSE_MAX_RECORDS", "256")),
            min_residual_reduction=float(os.getenv("SDRPP_SPARSE_MIN_REDUCTION", "0.35")),
            min_snr_db=float(os.getenv("SDRPP_SPARSE_MIN_SNR_DB", "6")),
            min_persistence=float(os.getenv("SDRPP_SPARSE_MIN_PERSISTENCE", "0.35")),
            tuner_ppm=float(os.getenv("SDRPP_TUNER_PPM", "0")),
            gain_db=float(os.getenv("SDRPP_GAIN_DB", "nan")),
            antenna_id=os.getenv("SDRPP_ANTENNA_ID", "unspecified"),
            clock_quality=os.getenv("SDRPP_CLOCK_QUALITY", "unknown"),
        ).validated()

    def validated(self) -> "SparseAnalyzerConfig":
        if not 0.5 <= self.window_seconds <= 30:
            raise ValueError("SDRPP_SPARSE_WINDOW_S must be between 0.5 and 30")
        if not 8 <= self.max_frames <= 256:
            raise ValueError("SDRPP_SPARSE_MAX_FRAMES must be between 8 and 256")
        if not 4 <= self.min_frames <= self.max_frames:
            raise ValueError("SDRPP_SPARSE_MIN_FRAMES must be between 4 and max_frames")
        if not 0.1 <= self.compression_ratio <= 1.0:
            raise ValueError("SDRPP_SPARSE_COMPRESSION must be between 0.1 and 1.0")
        if not 1 <= self.max_support <= 8:
            raise ValueError("SDRPP_SPARSE_MAX_SUPPORT must be between 1 and 8")
        return self


@dataclass
class ResidualWindow:
    schema: str
    window_id: str
    sensor_id: str
    observed_start: float
    observed_end: float
    center_frequency_hz: float
    sample_rate_hz: float
    fft_size: int
    available_frames: int
    retained_frames: int
    dropped_frames: int
    compression_ratio: float
    sampling_pattern_hash: str
    background_method: str
    residual_energy_db: float
    candidate_regions: list[Dict[str, Any]]
    outcome: str = "NO_SUPPORT"
    chain: Dict[str, Any] = field(default_factory=dict)
    authority: str = "DERIVED_SIGNAL_PROCESSING"
    evidence_class: str = "DERIVED_INFERENCE"
    raw_iq_exposed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SparseSupport:
    schema: str
    support_id: str
    window_id: str
    sensor_id: str
    observed_start: float
    observed_end: float
    center_frequency_hz: float
    sample_rate_hz: float
    fft_size: int
    atom_family: str
    parameters: Dict[str, Any]
    fit: Dict[str, Any]
    measurement: Dict[str, Any]
    authority: str = "DERIVED_SIGNAL_PROCESSING"
    evidence_class: str = "DERIVED_INFERENCE"
    dictionary_revision: str = DICTIONARY_REVISION
    raw_iq_exposed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _linear_power(dbfs: np.ndarray) -> np.ndarray:
    return np.power(10.0, np.clip(dbfs, -240.0, 20.0) / 10.0)


def _db(power: np.ndarray | float) -> float:
    return float(10.0 * np.log10(max(float(np.asarray(power).mean()), 1e-18)))


def median_background(spectrogram_db: np.ndarray) -> np.ndarray:
    """Robust temporal median per frequency bin. Passive analogue of bulk subtraction."""
    return np.median(spectrogram_db, axis=0)


def residual_spectrogram(spectrogram_db: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    background = median_background(spectrogram_db)
    return spectrogram_db - background, background


def _mad(values: np.ndarray) -> float:
    median = float(np.median(values))
    return float(np.median(np.abs(values - median))) * 1.4826


def candidate_bins(residual_db: np.ndarray, mad_k: float, limit: int) -> List[int]:
    """Return bins that beat an explicit MAD threshold. Empty is a valid result."""
    energy = np.mean(np.abs(residual_db), axis=0)
    scale = max(_mad(energy), 0.05)
    threshold = float(np.median(energy)) + mad_k * scale
    ranked = np.argsort(energy)[::-1]
    selected = [int(index) for index in ranked if energy[index] >= threshold]
    return selected[:limit]


def _normalize(column: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(column))
    if norm <= 1e-12:
        return np.zeros_like(column)
    return column / norm


def _omp(y: np.ndarray, dictionary: np.ndarray, sparsity: int) -> Tuple[List[int], np.ndarray, np.ndarray]:
    residual = y.astype(np.float64, copy=True)
    selected: List[int] = []
    coefficients = np.zeros(dictionary.shape[1], dtype=np.float64)
    y_norm = max(float(np.linalg.norm(y)), 1e-12)
    for _ in range(max(1, sparsity)):
        correlation = dictionary.T @ residual
        index = int(np.argmax(np.abs(correlation)))
        if index in selected or abs(correlation[index]) < 1e-9:
            break
        selected.append(index)
        basis = dictionary[:, selected]
        fit, *_ = np.linalg.lstsq(basis, y.astype(np.float64), rcond=None)
        coefficients[selected] = fit
        residual = y - basis @ fit
        if float(np.linalg.norm(residual)) / y_norm < 0.05:
            break
    return selected, coefficients, residual


def _bin_hz(center_hz: float, sample_rate_hz: float, fft_size: int, bin_count: int, index: int) -> float:
    bandwidth = sample_rate_hz
    return center_hz - bandwidth / 2.0 + (index + 0.5) * bandwidth / bin_count


def _slow_time(residual_db: np.ndarray, bin_index: int) -> np.ndarray:
    lo = max(0, bin_index - 1)
    hi = min(residual_db.shape[1], bin_index + 2)
    return np.mean(residual_db[:, lo:hi], axis=1)


def _build_dictionary(slow_time: np.ndarray, dt: float) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    samples = slow_time.size
    t = np.arange(samples, dtype=np.float64) * dt
    atoms: List[np.ndarray] = []
    meta: List[Dict[str, Any]] = []

    atoms.append(_normalize(np.ones(samples)))
    meta.append({"atom_family": "stationary_carrier", "parameters": {}})

    duration = max(t[-1], dt)
    for slope in (-2.0, -1.0, 1.0, 2.0):
        drift = slope * (t / duration)
        atoms.append(_normalize(1.0 + drift))
        meta.append({
            "atom_family": "linear_drift",
            "parameters": {"drift_bins_per_window": slope},
        })

    mean = float(np.mean(slow_time))
    centered = slow_time - mean
    spectrum = np.fft.rfft(centered)
    freqs = np.fft.rfftfreq(samples, d=dt)
    if spectrum.size > 1:
        peak = int(np.argmax(np.abs(spectrum[1:])) + 1)
        repetition_hz = float(freqs[peak]) if peak < freqs.size else 0.0
    else:
        repetition_hz = 0.0
    if repetition_hz > 0:
        for harmonic in (1.0, 2.0):
            omega = 2.0 * math.pi * repetition_hz * harmonic
            atoms.append(_normalize(np.cos(omega * t)))
            atoms.append(_normalize(np.sin(omega * t)))
            meta.append({
                "atom_family": "periodic_amplitude",
                "parameters": {"modulation_rate_hz": round(repetition_hz * harmonic, 4)},
            })
            meta.append({
                "atom_family": "periodic_amplitude",
                "parameters": {"modulation_rate_hz": round(repetition_hz * harmonic, 4), "quadrature": True},
            })

    dictionary = np.column_stack(atoms) if atoms else np.ones((samples, 1))
    return dictionary, meta


def _peak_track_hz(spectrogram_db: np.ndarray, center_frequency_hz: float,
                   sample_rate_hz: float, fft_size: int) -> np.ndarray:
    bin_count = spectrogram_db.shape[1]
    peaks = np.argmax(spectrogram_db, axis=1)
    return np.array([
        _bin_hz(center_frequency_hz, sample_rate_hz, fft_size, bin_count, int(index))
        for index in peaks
    ], dtype=np.float64)


def _bin_widths(sample_rate_hz: float, fft_size: int, bin_count: int) -> Dict[str, float]:
    native = float(sample_rate_hz) / max(int(fft_size), 1)
    analysis = float(sample_rate_hz) / max(int(bin_count), 1)
    return {
        "native_fft_bin_width_hz": round(native, 6),
        "analysis_bin_width_hz": round(analysis, 6),
        "frequency_uncertainty_hz": round(analysis / 2.0, 6),
    }


def _signal_chain(*, sample_rate_hz: float, fft_size: int, bin_count: int,
                  center_frequency_hz: float, tuner_ppm: float, gain_db: float,
                  antenna_id: str, clock_quality: str, dropped_frames: int) -> Dict[str, Any]:
    widths = _bin_widths(sample_rate_hz, fft_size, bin_count)
    payload = {
        "tuner_frequency_hz": round(float(center_frequency_hz), 3),
        "tuner_ppm": round(float(tuner_ppm), 4),
        "gain_db": None if not math.isfinite(gain_db) else round(float(gain_db), 2),
        "antenna_id": antenna_id,
        "clock_quality": clock_quality,
        "dropped_usb_sample_count": None,
        "dropped_frames": int(dropped_frames),
        **widths,
    }
    payload["signal_chain_hash"] = "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return payload


def _candidate_persistence(residual_db: np.ndarray, bin_index: int, mad_k: float) -> float:
    column = residual_db[:, bin_index]
    scale = max(_mad(column), 0.05)
    threshold = float(np.median(column)) + mad_k * scale
    return float(np.mean(column >= threshold))


def _null_outcome(retained: int, min_frames: int, bins: Sequence[int], residual_energy_db: float) -> str:
    if retained < min_frames:
        return "INSUFFICIENT_EVIDENCE"
    if not bins:
        return "NO_SUPPORT"
    if residual_energy_db < -70.0:
        return "NOISE_COMPATIBLE"
    return "NO_SUPPORT"


def recover_support(
    spectrogram_db: np.ndarray,
    *,
    center_frequency_hz: float,
    sample_rate_hz: float,
    fft_size: int,
    timestamps: Sequence[float],
    sequences: Sequence[int],
    sensor_id: str,
    compression_ratio: float,
    available_frames: int,
    dropped_frames: int,
    sampling_seed: int,
    max_support: int,
    max_candidates: int,
    mad_k: float,
    min_residual_reduction: float = 0.35,
    min_snr_db: float = 6.0,
    min_persistence: float = 0.35,
    tuner_ppm: float = 0.0,
    gain_db: float = float("nan"),
    antenna_id: str = "unspecified",
    clock_quality: str = "unknown",
    min_frames: int = 8,
) -> Tuple[ResidualWindow, List[SparseSupport]]:
    residual_db, background = residual_spectrogram(spectrogram_db)
    retained = int(spectrogram_db.shape[0])
    bin_count = int(spectrogram_db.shape[1])
    dt = (timestamps[-1] - timestamps[0]) / max(retained - 1, 1)
    dt = dt if dt > 1e-6 else 0.1
    bins = candidate_bins(residual_db, mad_k, max_candidates)
    residual_energy_db = _db(_linear_power(residual_db))
    peak_hz = _peak_track_hz(spectrogram_db, center_frequency_hz, sample_rate_hz, fft_size)
    t = np.arange(retained, dtype=np.float64) * dt
    duration = max(float(timestamps[-1] - timestamps[0]), 1e-6)
    analysis_bin_hz = sample_rate_hz / max(bin_count, 1)
    regions = []
    supports: List[SparseSupport] = []
    window_id = f"rfwin-{_digest(sensor_id, timestamps[0], timestamps[-1], sequences[0], sequences[-1])}"
    chain = _signal_chain(
        sample_rate_hz=sample_rate_hz, fft_size=fft_size, bin_count=bin_count,
        center_frequency_hz=center_frequency_hz, tuner_ppm=tuner_ppm, gain_db=gain_db,
        antenna_id=antenna_id, clock_quality=clock_quality, dropped_frames=dropped_frames,
    )
    measurement = {
        "available_frames": available_frames,
        "retained_frames": retained,
        "dropped_frames": dropped_frames,
        "compression_ratio": compression_ratio,
        "sampling_pattern_hash": _pattern_hash(sequences, sampling_seed),
        "dictionary_revision": DICTIONARY_REVISION,
        "estimator": "peak_track_plus_omp_periodic_amplitude",
        **chain,
    }

    peak_bins = np.argmax(spectrogram_db, axis=1)
    dominant = int(np.bincount(peak_bins, minlength=bin_count).argmax())
    stationary_persistence = float(np.mean(np.abs(peak_bins - dominant) <= 1))
    continuity = 1.0 if retained < 2 else float(np.mean(np.abs(np.diff(peak_bins.astype(np.float64))) <= 2))
    carrier_db = float(np.median(np.max(spectrogram_db, axis=1)))
    noise_db = float(np.median(background))
    snr_db = carrier_db - noise_db
    slope, intercept = np.polyfit(t, peak_hz, 1)
    drift_line = intercept + slope * t
    bin_wander = peak_bins.astype(np.float64) - float(dominant)
    wander_rms = float(np.sqrt(np.mean(bin_wander ** 2)))
    drift_residual_bins = (peak_hz - drift_line) / max(analysis_bin_hz, 1e-9)
    drift_rms = float(np.sqrt(np.mean(drift_residual_bins ** 2)))
    stationary_reduction = max(0.0, 1.0 - wander_rms / 3.0)
    drift_reduction = max(0.0, 1.0 - drift_rms / 3.0)
    carrier_family = (
        "linear_drift"
        if abs(slope) > analysis_bin_hz / max(duration, 1.0) and drift_reduction >= stationary_reduction
        else "stationary_carrier"
    )
    reduction = drift_reduction if carrier_family == "linear_drift" else stationary_reduction
    persistence = continuity if carrier_family == "linear_drift" else stationary_persistence
    if (
        persistence >= min_persistence
        and snr_db >= min_snr_db
        and reduction >= min_residual_reduction
    ):
        carrier_hz = float(np.median(peak_hz))
        supports.append(SparseSupport(
            schema=SCHEMA_SUPPORT,
            support_id=f"rfss-{_digest(window_id, carrier_family, carrier_hz)}",
            window_id=window_id,
            sensor_id=sensor_id,
            observed_start=float(timestamps[0]),
            observed_end=float(timestamps[-1]),
            center_frequency_hz=float(center_frequency_hz),
            sample_rate_hz=float(sample_rate_hz),
            fft_size=int(fft_size),
            atom_family=carrier_family,
            parameters={
                "carrier_hz": round(carrier_hz, 3),
                "drift_hz_per_second": round(float(slope), 4),
            },
            fit={
                "residual_reduction": round(reduction, 4),
                "normalized_error": round(1.0 - reduction, 4),
                "snr_db": round(snr_db, 2),
                "persistence": round(persistence, 4),
                "support_rank": 1,
                "candidate_rank": 1,
                "null_model": "peak_bin_wander",
            },
            measurement=dict(measurement),
        ))

    for rank, bin_index in enumerate(bins, start=1):
        frequency = _bin_hz(center_frequency_hz, sample_rate_hz, fft_size, bin_count, bin_index)
        slow = _slow_time(residual_db, bin_index)
        persistence = _candidate_persistence(residual_db, bin_index, mad_k)
        regions.append({
            "peak_frequency_hz": round(frequency, 3),
            "residual_db": round(float(np.median(slow)), 2),
            "bin_index": bin_index,
            "persistence": round(persistence, 4),
        })
        y = slow - np.median(slow)
        y_norm = max(float(np.linalg.norm(y)), 1e-12)
        dictionary, meta = _build_dictionary(y, dt)
        selected, coefficients, leftover = _omp(y, dictionary, max_support)
        leftover_norm = float(np.linalg.norm(leftover))
        reduction = max(0.0, 1.0 - leftover_norm / y_norm)
        amplitude_score = float(np.std(y))
        if reduction < min_residual_reduction or amplitude_score < 1.0:
            continue
        for atom_index in selected:
            family = meta[atom_index]["atom_family"]
            if family != "periodic_amplitude":
                continue
            rate = abs(float(meta[atom_index]["parameters"].get("modulation_rate_hz", 0.0)))
            if rate <= 0:
                continue
            supports.append(SparseSupport(
                schema=SCHEMA_SUPPORT,
                support_id=f"rfss-{_digest(window_id, family, frequency, rate, rank)}",
                window_id=window_id,
                sensor_id=sensor_id,
                observed_start=float(timestamps[0]),
                observed_end=float(timestamps[-1]),
                center_frequency_hz=float(center_frequency_hz),
                sample_rate_hz=float(sample_rate_hz),
                fft_size=int(fft_size),
                atom_family=family,
                parameters={
                    "carrier_hz": round(frequency, 3),
                    "modulation_rate_hz": round(rate, 4),
                },
                fit={
                    "residual_reduction": round(reduction, 4),
                    "normalized_error": round(leftover_norm / y_norm, 4),
                    "persistence": round(persistence, 4),
                    "support_rank": 2,
                    "candidate_rank": rank,
                    "coefficient": round(float(coefficients[atom_index]), 4),
                    "null_model": "zero_mean_slow_time",
                },
                measurement=dict(measurement),
            ))
            break

    outcome = "SUPPORT" if supports else _null_outcome(retained, min_frames, bins, residual_energy_db)
    window = ResidualWindow(
        schema=SCHEMA_RESIDUAL,
        window_id=window_id,
        sensor_id=sensor_id,
        observed_start=float(timestamps[0]),
        observed_end=float(timestamps[-1]),
        center_frequency_hz=float(center_frequency_hz),
        sample_rate_hz=float(sample_rate_hz),
        fft_size=int(fft_size),
        available_frames=available_frames,
        retained_frames=retained,
        dropped_frames=dropped_frames,
        compression_ratio=compression_ratio,
        sampling_pattern_hash=measurement["sampling_pattern_hash"],
        background_method="temporal_median",
        residual_energy_db=round(residual_energy_db, 2),
        candidate_regions=regions,
        outcome=outcome,
        chain=chain,
    )
    return window, supports


class RFSparseAnalyzer:
    """Edge-only analyzer. Stores compact residual windows and OMP supports."""

    def __init__(self, config: Optional[SparseAnalyzerConfig] = None):
        self.config = (config or SparseAnalyzerConfig.from_env()).validated()
        self._lock = threading.RLock()
        self._frames: Deque[Dict[str, Any]] = deque(maxlen=self.config.max_frames)
        self._seen_sequences: Deque[int] = deque(maxlen=self.config.max_frames * 2)
        self._windows: Deque[Dict[str, Any]] = deque(maxlen=self.config.max_records)
        self._supports: Deque[Dict[str, Any]] = deque(maxlen=self.config.max_records)
        self._rng = np.random.default_rng(self.config.sampling_seed)
        self._axis: Optional[Tuple[float, float, int, int]] = None
        self._available = 0
        self._dropped = 0
        self._last_error: Optional[str] = None
        self._subscribers: list[Callable[[Dict[str, Any]], None]] = []

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def reset(self) -> None:
        with self._lock:
            self._frames.clear()
            self._seen_sequences.clear()
            self._axis = None
            self._available = 0
            self._dropped = 0

    def ingest_frame(self, frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.config.enabled:
            return None
        bins = frame.get("bins_dbfs")
        if not isinstance(bins, list) or len(bins) < 16:
            return None
        axis = (
            _finite(frame.get("center_frequency_hz")),
            _finite(frame.get("sample_rate_hz")),
            int(frame.get("fft_size") or 0),
            len(bins),
        )
        if axis[0] <= 0 or axis[1] <= 0 or axis[2] < 64:
            return None
        sequence = int(frame.get("sequence") or 0)
        with self._lock:
            if self._axis != axis:
                self.reset()
                self._axis = axis
            self._available += 1
            if self._seen_sequences:
                gap = sequence - self._seen_sequences[-1] - 1
                if gap > 0:
                    self._dropped += gap
            self._seen_sequences.append(sequence)
            if self._rng.random() > self.config.compression_ratio:
                return None
            self._frames.append({
                "timestamp": _finite(frame.get("timestamp"), time.time()),
                "sequence": sequence,
                "sensor_id": str(frame.get("sensor_id") or "unknown"),
                "bins_dbfs": np.asarray(bins, dtype=np.float32),
                "center_frequency_hz": axis[0],
                "sample_rate_hz": axis[1],
                "fft_size": axis[2],
            })
            if len(self._frames) < self.config.min_frames:
                return None
            span = self._frames[-1]["timestamp"] - self._frames[0]["timestamp"]
            if len(self._frames) < self.config.max_frames and span < self.config.window_seconds:
                return None
            return self._analyze_locked()

    def _analyze_locked(self) -> Optional[Dict[str, Any]]:
        frames = list(self._frames)
        spectrogram = np.stack([item["bins_dbfs"] for item in frames], axis=0)
        timestamps = [item["timestamp"] for item in frames]
        sequences = [item["sequence"] for item in frames]
        first = frames[0]
        try:
            window, supports = recover_support(
                spectrogram,
                center_frequency_hz=first["center_frequency_hz"],
                sample_rate_hz=first["sample_rate_hz"],
                fft_size=first["fft_size"],
                timestamps=timestamps,
                sequences=sequences,
                sensor_id=first["sensor_id"],
                compression_ratio=self.config.compression_ratio,
                available_frames=self._available,
                dropped_frames=self._dropped,
                sampling_seed=self.config.sampling_seed,
                max_support=self.config.max_support,
                max_candidates=self.config.max_candidates,
                mad_k=self.config.mad_k,
                min_residual_reduction=self.config.min_residual_reduction,
                min_snr_db=self.config.min_snr_db,
                min_persistence=self.config.min_persistence,
                tuner_ppm=self.config.tuner_ppm,
                gain_db=self.config.gain_db,
                antenna_id=self.config.antenna_id,
                clock_quality=self.config.clock_quality,
                min_frames=self.config.min_frames,
            )
        except Exception as exc:
            self._last_error = str(exc)
            return None
        window_dict = window.to_dict()
        support_dicts = [item.to_dict() for item in supports]
        self._windows.append(window_dict)
        for item in support_dicts:
            self._supports.append(item)
        self._available = 0
        self._dropped = 0
        self._frames.clear()
        payload = {"window": window_dict, "supports": support_dicts}
        subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(payload)
            except Exception:
                pass
        return payload

    def latest_window(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._windows[-1]) if self._windows else None

    def query_supports(self, *, since: Optional[float] = None, until: Optional[float] = None,
                       atom_family: Optional[str] = None, sensor_id: Optional[str] = None,
                       limit: int = 50) -> list[Dict[str, Any]]:
        limit = min(max(int(limit), 1), 200)
        with self._lock:
            items = list(self._supports)
        result = []
        for item in reversed(items):
            if since is not None and item["observed_end"] < float(since):
                continue
            if until is not None and item["observed_start"] > float(until):
                continue
            if atom_family and item["atom_family"] != atom_family:
                continue
            if sensor_id and item["sensor_id"] != sensor_id:
                continue
            result.append(dict(item))
            if len(result) >= limit:
                break
        return result

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            latest = self._windows[-1] if self._windows else None
            return {
                "enabled": self.config.enabled,
                "buffered_frames": len(self._frames),
                "window_count": len(self._windows),
                "support_count": len(self._supports),
                "compression_ratio": self.config.compression_ratio,
                "dictionary_revision": DICTIONARY_REVISION,
                "atom_families": list(ATOM_FAMILIES),
                "latest_window_id": None if latest is None else latest["window_id"],
                "latest_outcome": None if latest is None else latest.get("outcome"),
                "last_error": self._last_error,
                "authority": "DERIVED_SIGNAL_PROCESSING",
                "evidence_class": "DERIVED_INFERENCE",
                "raw_iq_exposed": False,
                "null_outcomes": list(NULL_OUTCOMES),
                "reserved_atom_families": list(RESERVED_ATOM_FAMILIES),
                "claims_withheld": ["range", "aoa", "blade_length", "periodic_sideband"],
            }


def compact_model_context(window: Optional[Dict[str, Any]], supports: Iterable[Dict[str, Any]],
                          limit: int = 6) -> Dict[str, Any]:
    """Bounded interpretation payload for local Ollama. No bins, no IQ, no control."""
    items = list(supports)[:limit]
    return {
        "window": None if window is None else {
            "id": window.get("window_id"),
            "outcome": window.get("outcome"),
            "observed_start": window.get("observed_start"),
            "observed_end": window.get("observed_end"),
            "center_frequency_hz": window.get("center_frequency_hz"),
            "retained_frames": window.get("retained_frames"),
            "compression_ratio": window.get("compression_ratio"),
            "residual_energy_db": window.get("residual_energy_db"),
        },
        "tracks": len(items),
        "supports": [{
            "family": item.get("atom_family"),
            "carrier_hz": (item.get("parameters") or {}).get("carrier_hz"),
            "modulation_rate_hz": (item.get("parameters") or {}).get("modulation_rate_hz"),
            "drift_hz_per_second": (item.get("parameters") or {}).get("drift_hz_per_second"),
            "normalized_error": (item.get("fit") or {}).get("normalized_error"),
            "residual_reduction": (item.get("fit") or {}).get("residual_reduction"),
        } for item in items],
        "falsifiers": [
            "repeat with gain reduced 10 dB",
            "repeat after shifting center frequency 250 kHz",
            "compare against a terminated-input capture",
        ],
        "evidence_class": "DERIVED_INFERENCE",
        "authority": "DERIVED_SIGNAL_PROCESSING",
        "hardware_authority": False,
        "raw_iq_exposed": False,
    }
