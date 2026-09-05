"""SDR++ edge bridge for RF SCYTHE.

The bridge deliberately keeps SDR++ outside the web process.  It consumes the
unframed IQ byte stream produced by SDR++'s ``iq_exporter`` module, converts it
to bounded FFT frames, and uses SDR++'s Rigctl server for tune/mode commands.

Only the Flask integration in ``rf_scythe_api_server.py`` is allowed to expose
this object to browsers.  Neither native SDR++ socket should be internet-facing.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import hashlib
import logging
import math
import os
import socket
import threading
import time
from typing import Any, Callable, Deque, Dict, Iterable, Optional

import numpy as np

from rf_iq_retention import IQRetentionOwner
from rf_signal_family import (
    AXES, classifier_status, empty_axis_counts, empty_reason_counts,
    normalize_classification,
)


LOG = logging.getLogger(__name__)

_SAMPLE_DTYPES = {
    "int8": np.dtype("i1"),
    # RTL2832U delivers offset-binary uint8 natively, and rtl_tcp forwards it
    # unchanged. Decoding that stream as int8 misreads every sample by 128,
    # which lands as a phantom carrier at DC rather than as a decode error.
    "uint8": np.dtype("u1"),
    "int16": np.dtype("<i2"),
    "int32": np.dtype("<i4"),
    "float32": np.dtype("<f4"),
}

_FULL_SCALE = {
    "int8": 128.0,
    "uint8": 127.5,
    "int16": 32768.0,
    "int32": 2147483648.0,
    "float32": 1.0,
}

# Offset-binary formats sit centred on a positive value, not on zero.
_SAMPLE_OFFSET = {
    "uint8": 127.5,
}

_RIGCTL_MODES = {"FM", "WFM", "AM", "DSB", "USB", "CW", "LSB", "RAW"}

# rtl_tcp sends a 12-byte dongle_info block before the first sample: the magic
# "RTL0", then tuner type and tuner gain count as big-endian uint32.
#
# It was not being stripped. Those twelve bytes were decoded as six complex
# samples at the head of every connection, and because tuner type and gain count
# are small integers most of those bytes are 0x00 -- which in offset-binary uint8
# is negative full scale. Every reconnect therefore began with a full-scale
# transient that the FFT and the ring both saw as signal. Small, and wrong, and
# the header is also the only place the device says what tuner it has.
DONGLE_INFO_MAGIC = b"RTL0"
DONGLE_INFO_BYTES = 12

# librtlsdr tuner type codes.
_TUNER_TYPES = {0: "UNKNOWN", 1: "E4000", 2: "FC0012", 3: "FC0013", 4: "FC2580",
                5: "R820T", 6: "R828D"}

# Tenths of a dB, as librtlsdr reports and rtl_tcp expects. Declared per tuner
# and never interpolated: a gain "between" two of these is not a gain this device
# can be set to, and asking for one would silently land somewhere else.
#
# Authority is DRIVER_DECLARED. The table is only used when the device's own
# reported gain count matches its length, so a driver whose table has moved
# refuses manual gain instead of quietly setting the wrong value.
_TUNER_GAINS_TENTHS_DB = {
    "R820T": (0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254,
              280, 297, 328, 338, 364, 372, 386, 402, 421, 434, 439, 445, 480, 496),
    "R828D": (0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229, 254,
              280, 297, 328, 338, 364, 372, 386, 402, 421, 434, 439, 445, 480, 496),
}

# rtl_tcp control opcodes, one command byte then a big-endian uint32.
_RTL_TCP_SET_GAIN_MODE = 0x03
_RTL_TCP_SET_GAIN = 0x04
GAIN_MODE_MANUAL = 1
GAIN_MODE_AUTOMATIC = 0

# There is no rtl_tcp SET_SAMPLE_RATE opcode above, and that absence is the
# whole point. The client sends SET_GAIN_MODE and SET_GAIN and nothing else,
# so the rate is whatever rtl_tcp was launched with via -s. rtl_tcp never
# reports back what the tuner actually applied, and the RTL0 dongle_info
# header carries a tuner type and gain count -- not a rate. The figure this
# process publishes is therefore the rate BOTH processes were configured with
# from one shared launch file, not a rate anybody observed the hardware adopt.
#
# The distinction is not pedantry. bin_width = sample_rate_hz / fft_size, so
# the declared rate labels every frequency in the trace. A rate that is merely
# configured and a rate that is confirmed produce identical-looking spectra.
SAMPLE_RATE_AUTHORITY = "SHARED_LAUNCH_CONFIGURATION"
SAMPLE_RATE_RUNTIME_ATTESTATION = "UNAVAILABLE"

# What a refused IQ connection does and does not tell this process. Under WSL
# the USB device is absent until it is attached from Windows, but a refused
# socket looks exactly the same as a stopped rtl_tcp or a wrong port. Naming
# the cause would be a guess wearing an operational-status uniform.
SOURCE_UNREACHABLE_CAUSE = "NOT_DETERMINABLE_FROM_THIS_PROCESS"
SOURCE_UNREACHABLE_NOTE = (
    "A refused IQ connection cannot distinguish an absent USB device from a "
    "stopped rtl_tcp, a wrong endpoint or a busy receiver. This process "
    "reports reachability, not the reason for its absence."
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RFBridgeConfig:
    iq_host: str = "127.0.0.1"
    iq_port: int = 1234
    rigctl_host: str = "127.0.0.1"
    rigctl_port: int = 4532
    sample_rate_hz: float = 1_000_000.0
    center_frequency_hz: float = 100_000_000.0
    sample_type: str = "int16"
    fft_size: int = 2048
    max_bins: int = 512
    frames_per_second: float = 10.0
    socket_timeout_s: float = 2.0
    reconnect_max_s: float = 10.0
    auto_start: bool = False
    sensor_id: str = "SDRPP-EDGE-01"
    capture_owner: str = "orchestrator"

    @classmethod
    def from_env(cls) -> "RFBridgeConfig":
        return cls(
            iq_host=os.getenv("SDRPP_IQ_HOST", "127.0.0.1"),
            iq_port=int(os.getenv("SDRPP_IQ_PORT", "1234")),
            rigctl_host=os.getenv("SDRPP_RIGCTL_HOST", "127.0.0.1"),
            rigctl_port=int(os.getenv("SDRPP_RIGCTL_PORT", "4532")),
            sample_rate_hz=float(os.getenv("SDRPP_SAMPLE_RATE_HZ", "1000000")),
            center_frequency_hz=float(os.getenv("SDRPP_CENTER_FREQUENCY_HZ", "100000000")),
            sample_type=os.getenv("SDRPP_SAMPLE_TYPE", "int16").lower(),
            fft_size=int(os.getenv("SDRPP_FFT_SIZE", "2048")),
            max_bins=int(os.getenv("SDRPP_MAX_BINS", "512")),
            frames_per_second=float(os.getenv("SDRPP_FPS", "10")),
            socket_timeout_s=float(os.getenv("SDRPP_SOCKET_TIMEOUT_S", "2")),
            reconnect_max_s=float(os.getenv("SDRPP_RECONNECT_MAX_S", "10")),
            auto_start=_env_bool("SDRPP_AUTO_START", False),
            sensor_id=os.getenv("SDRPP_SENSOR_ID", "SDRPP-EDGE-01"),
            capture_owner=os.getenv("SCYTHE_RF_CAPTURE_OWNER", "orchestrator").strip().lower(),
        ).validated()

    def validated(self) -> "RFBridgeConfig":
        if self.sample_type not in _SAMPLE_DTYPES:
            raise ValueError(f"Unsupported SDRPP_SAMPLE_TYPE: {self.sample_type}")
        if not 64 <= self.fft_size <= 65536 or self.fft_size & (self.fft_size - 1):
            raise ValueError("SDRPP_FFT_SIZE must be a power of two from 64 to 65536")
        if not 16 <= self.max_bins <= self.fft_size:
            raise ValueError("SDRPP_MAX_BINS must be between 16 and fft_size")
        if self.sample_rate_hz <= 0 or not math.isfinite(self.sample_rate_hz):
            raise ValueError("SDRPP_SAMPLE_RATE_HZ must be positive")
        if not 0.5 <= self.frames_per_second <= 60:
            raise ValueError("SDRPP_FPS must be between 0.5 and 60")
        for name, port in (("SDRPP_IQ_PORT", self.iq_port), ("SDRPP_RIGCTL_PORT", self.rigctl_port)):
            if not 1 <= port <= 65535:
                raise ValueError(f"{name} must be between 1 and 65535")
        if self.capture_owner not in {"orchestrator", "child", "standalone"}:
            raise ValueError("SCYTHE_RF_CAPTURE_OWNER must be orchestrator, child, or standalone")
        return self

    def owns_capture(self) -> bool:
        role = os.getenv("SCYTHE_PROCESS_ROLE", "").strip().lower()
        if self.capture_owner == "standalone":
            return True
        if self.capture_owner == "orchestrator":
            return role != "child"
        return role == "child"


@dataclass(frozen=True)
class RFObservation:
    """Bounded RF evidence derived at the edge; never contains raw IQ."""

    evidence_id: str
    observed_at: float
    sensor_id: str
    sequence: int
    center_frequency_hz: float
    peak_frequency_hz: float
    sample_rate_hz: float
    peak_dbfs: float
    noise_floor_dbfs: float
    snr_db: float
    # The three independent axes. Each carries its own declared absence, so a
    # detection can be symbol-structured with an unresolved modulation without
    # either fact contaminating the other.
    modulation: str = "UNRESOLVED"
    information_structure: str = "NOT_ATTEMPTED"
    protocol: str = "UNRESOLVED"
    # A compatibility summary derived from the axes above, never an input.
    signal_family: str = "UNCLASSIFIED"
    # An unclassified detection says which nothing was found. NOT_ATTEMPTED and
    # NO_SYMBOL_CLOCK_DETECTED are different claims about the same zero.
    classification_reason_code: str = "NOT_ATTEMPTED"
    classification_authority: str = "UNCLASSIFIED"
    classification_method: Optional[str] = None
    classification_confidence: Optional[float] = None
    classification_symbol_rate_hz: Optional[float] = None
    classification_window_start: Optional[float] = None
    classification_window_end: Optional[float] = None
    evidence_class: str = "OBSERVED"
    source: str = "sdrpp_iq_exporter"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RFObservationStore:
    """Thread-safe, bounded store for significant spectral observations."""

    def __init__(self, maxlen: int = 2048, min_snr_db: float = 12.0,
                 cooldown_s: float = 1.0, bucket_hz: float = 5_000.0,
                 method_registry=None):
        self._items: Deque[RFObservation] = deque(maxlen=max(16, int(maxlen)))
        self._min_snr_db = float(min_snr_db)
        self._cooldown_s = max(0.0, float(cooldown_s))
        self._bucket_hz = max(1.0, float(bucket_hz))
        self._last_seen: Dict[tuple[str, int], float] = {}
        # Which detectors this store will hear a family claim from. None means
        # the shipped registry, in which no method has passed Phase 3.
        self._method_registry = method_registry
        self._subscribers: list[Callable[[Dict[str, Any]], None]] = []
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls) -> "RFObservationStore":
        return cls(
            maxlen=int(os.getenv("SDRPP_OBSERVATION_MAX", "2048")),
            min_snr_db=float(os.getenv("SDRPP_DETECTION_SNR_DB", "12")),
            cooldown_s=float(os.getenv("SDRPP_DETECTION_COOLDOWN_S", "1")),
            bucket_hz=float(os.getenv("SDRPP_DETECTION_BUCKET_HZ", "5000")),
        )

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def ingest_frame(self, frame: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        peak = float(frame.get("peak_dbfs", -240.0))
        floor = float(frame.get("noise_floor_dbfs", -240.0))
        snr = round(peak - floor, 2)
        if not math.isfinite(snr) or snr < self._min_snr_db:
            return None
        observed_at = float(frame.get("timestamp", time.time()))
        frequency = float(frame.get("peak_frequency_hz", 0.0))
        sensor_id = str(frame.get("sensor_id", "unknown"))
        bucket = int(round(frequency / self._bucket_hz))
        key = (sensor_id, bucket)
        # The admission gate lives in rf_signal_family so there is one contract
        # rather than one per caller. A refused claim keeps its reason.
        verdict = normalize_classification(
            frame.get("signal_classification"), observed_at=observed_at,
            registry=self._method_registry)
        with self._lock:
            if observed_at - self._last_seen.get(key, float("-inf")) < self._cooldown_s:
                return None
            self._last_seen[key] = observed_at
            sequence = int(frame.get("sequence", 0))
            digest = hashlib.blake2s(
                f"{sensor_id}:{sequence}:{observed_at:.6f}:{frequency:.3f}".encode(),
                digest_size=8,
            ).hexdigest()
            observation = RFObservation(
                evidence_id=f"rf-{digest}",
                observed_at=observed_at,
                sensor_id=sensor_id,
                sequence=sequence,
                center_frequency_hz=float(frame.get("center_frequency_hz", 0.0)),
                peak_frequency_hz=frequency,
                sample_rate_hz=float(frame.get("sample_rate_hz", 0.0)),
                peak_dbfs=peak,
                noise_floor_dbfs=floor,
                snr_db=snr,
                modulation=verdict.modulation,
                information_structure=verdict.information_structure,
                protocol=verdict.protocol,
                signal_family=verdict.family,
                classification_reason_code=verdict.reason_code,
                classification_authority=verdict.authority,
                classification_method=verdict.method,
                classification_confidence=verdict.confidence,
                classification_symbol_rate_hz=verdict.symbol_rate_hz,
                classification_window_start=verdict.window_start,
                classification_window_end=verdict.window_end,
                # A frame submitted over the ingest API was not produced by this
                # bridge and must not inherit the IQ exporter's source label.
                source=str(frame.get("observation_origin") or "sdrpp_iq_exporter").lower(),
            )
            self._items.append(observation)
            subscribers = list(self._subscribers)
        item = observation.to_dict()
        for callback in subscribers:
            try:
                callback(dict(item))
            except Exception:
                LOG.exception("RF observation subscriber failed")
        return item

    def query(self, *, since: Optional[float] = None, until: Optional[float] = None,
              frequency_hz: Optional[float] = None, tolerance_hz: float = 25_000.0,
              min_snr_db: Optional[float] = None, sensor_id: Optional[str] = None,
              limit: int = 100) -> list[Dict[str, Any]]:
        limit = min(max(int(limit), 1), 500)
        tolerance_hz = max(float(tolerance_hz), 0.0)
        with self._lock:
            items = list(self._items)
        result = []
        for item in reversed(items):
            if since is not None and item.observed_at < float(since):
                continue
            if until is not None and item.observed_at > float(until):
                continue
            if frequency_hz is not None and abs(item.peak_frequency_hz - float(frequency_hz)) > tolerance_hz:
                continue
            if min_snr_db is not None and item.snr_db < float(min_snr_db):
                continue
            if sensor_id is not None and item.sensor_id != sensor_id:
                continue
            result.append(item.to_dict())
            if len(result) >= limit:
                break
        return result

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            items = list(self._items)
        classifications = {"digital": 0, "analogue": 0, "unclassified": 0}
        reasons = empty_reason_counts()
        axes = empty_axis_counts()
        for item in items:
            key = item.signal_family.lower()
            classifications[key if key in classifications else "unclassified"] += 1
            code = item.classification_reason_code
            reasons[code if code in reasons else "UNQUALIFIED_CLAIM"] += 1
            for axis in AXES:
                bucket = axes[axis]
                value = getattr(item, axis)
                if value in bucket:
                    bucket[value] += 1
        return {
            "count": len(items),
            "capacity": self._items.maxlen,
            "min_snr_db": self._min_snr_db,
            "latest_observed_at": items[-1].observed_at if items else None,
            # The axes are the observation; the family below is a summary of two
            # of them. Both are published so a reader can check the derivation.
            "signal_axes": axes,
            "signal_classifications": {**classifications, "total": len(items)},
            # Why each unclassified detection is unclassified. A bare zero cannot
            # distinguish "nothing was there" from "nothing looked".
            "classification_reasons": {code: count for code, count in reasons.items() if count},
            "classifier": classifier_status(self._method_registry),
            "classification_scope": "bounded_retained_detection_events",
            "evidence_class": "OBSERVED",
            "raw_iq_exposed": False,
        }


class IQFFTProcessor:
    """Incrementally converts unframed interleaved IQ bytes into FFT frames."""

    def __init__(self, config: RFBridgeConfig, sample_sink=None):
        self.config = config.validated()
        # Optional consumer of the decoded IQ, called with every sample rather
        # than with the throttled FFT frames. None keeps the pre-Phase-1c
        # behaviour exactly: decode, transform, discard.
        self._sample_sink = sample_sink
        self._dtype = _SAMPLE_DTYPES[config.sample_type]
        self._scalar_bytes = self._dtype.itemsize
        self._window = np.hanning(config.fft_size).astype(np.float32)
        self._byte_remainder = bytearray()
        self._samples = np.empty(0, dtype=np.complex64)
        self._last_frame_at = 0.0
        # One processor is built per connection, so this is per-connection state
        # and the header is consumed exactly once where it actually appears.
        self._dongle_info: Optional[Dict[str, Any]] = None
        self._header_consumed = False

    @property
    def dongle_info(self) -> Optional[Dict[str, Any]]:
        """What the device said about itself, or None if it said nothing."""
        return None if self._dongle_info is None else dict(self._dongle_info)

    def feed(self, chunk: bytes, now: Optional[float] = None) -> Iterable[Dict]:
        if not chunk:
            return []
        now = time.time() if now is None else now
        self._byte_remainder.extend(chunk)
        if not self._header_consumed:
            if len(self._byte_remainder) < DONGLE_INFO_BYTES:
                return []                      # wait; do not decode a partial header
            if bytes(self._byte_remainder[:4]) == DONGLE_INFO_MAGIC:
                header = bytes(self._byte_remainder[:DONGLE_INFO_BYTES])
                del self._byte_remainder[:DONGLE_INFO_BYTES]
                tuner_code = int.from_bytes(header[4:8], "big")
                self._dongle_info = {
                    "tuner_type": _TUNER_TYPES.get(tuner_code, f"UNRECOGNISED_{tuner_code}"),
                    "tuner_type_code": tuner_code,
                    "tuner_gain_count": int.from_bytes(header[8:12], "big"),
                    # Named for what it describes. The gain *table* has a
                    # different authority -- driver, not device -- and one key
                    # meaning two things in one merged payload is how a claim
                    # gets promoted by accident.
                    "device_authority": "DEVICE_DECLARED",
                }
            # A stream without the magic is not an error: it may be any other IQ
            # source. It simply declares no tuner, and manual gain stays refused.
            self._header_consumed = True
        pair_bytes = self._scalar_bytes * 2
        usable = len(self._byte_remainder) - (len(self._byte_remainder) % pair_bytes)
        if usable:
            raw = bytes(self._byte_remainder[:usable])
            del self._byte_remainder[:usable]
            scalars = np.frombuffer(raw, dtype=self._dtype).astype(np.float32)
            offset = _SAMPLE_OFFSET.get(self.config.sample_type)
            if offset:
                scalars -= offset
            scalars /= _FULL_SCALE[self.config.sample_type]
            iq = (scalars[0::2] + 1j * scalars[1::2]).astype(np.complex64)
            if self._sample_sink is not None:
                # Every decoded sample, not one per frame: the FPS throttle below
                # governs how often a spectrum is published, not what was captured.
                # A sink failure must not interrupt frame production.
                try:
                    self._sample_sink(iq, now)
                except Exception:
                    LOG.exception("IQ sample sink failed")
            self._samples = np.concatenate((self._samples, iq))

        frames = []
        interval = 1.0 / self.config.frames_per_second
        while self._samples.size >= self.config.fft_size:
            block = self._samples[: self.config.fft_size]
            self._samples = self._samples[self.config.fft_size :]
            if now - self._last_frame_at < interval:
                continue
            self._last_frame_at = now
            frames.append(self._transform(block, now))
        return frames

    def _transform(self, block: np.ndarray, timestamp: float) -> Dict:
        centered = block - np.mean(block)
        spectrum = np.fft.fftshift(np.fft.fft(centered * self._window))
        window_gain = max(float(np.sum(self._window)), 1.0)
        magnitude = np.abs(spectrum) / window_gain
        power_dbfs = 20.0 * np.log10(np.maximum(magnitude, 1e-12))
        bins = self._downsample_peak(power_dbfs, self.config.max_bins)
        peak_index = int(np.argmax(power_dbfs))
        bin_width = self.config.sample_rate_hz / self.config.fft_size
        peak_offset_hz = (peak_index - self.config.fft_size / 2.0) * bin_width
        return {
            "timestamp": timestamp,
            "center_frequency_hz": self.config.center_frequency_hz,
            "sample_rate_hz": self.config.sample_rate_hz,
            "fft_size": self.config.fft_size,
            "bin_count": int(bins.size),
            "min_frequency_hz": self.config.center_frequency_hz - self.config.sample_rate_hz / 2.0,
            "max_frequency_hz": self.config.center_frequency_hz + self.config.sample_rate_hz / 2.0,
            "peak_frequency_hz": self.config.center_frequency_hz + peak_offset_hz,
            "peak_dbfs": round(float(power_dbfs[peak_index]), 2),
            "noise_floor_dbfs": round(float(np.median(power_dbfs)), 2),
            "bins_dbfs": np.round(bins, 2).tolist(),
        }

    @staticmethod
    def _downsample_peak(values: np.ndarray, target: int) -> np.ndarray:
        if values.size <= target:
            return values
        edges = np.linspace(0, values.size, target + 1, dtype=np.int64)
        return np.array([np.max(values[edges[i] : edges[i + 1]]) for i in range(target)])


class RigctlClient:
    """Small serialized client for the SDR++ Rigctl server."""

    def __init__(self, host: str, port: int, timeout_s: float = 2.0):
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._lock = threading.Lock()

    def command(self, command: str, response_lines: int = 1) -> list[str]:
        if "\n" in command or "\r" in command:
            raise ValueError("Rigctl command must be a single line")
        with self._lock, socket.create_connection((self.host, self.port), self.timeout_s) as sock:
            sock.settimeout(self.timeout_s)
            sock.sendall((command + "\n").encode("ascii"))
            data = bytearray()
            while data.count(b"\n") < response_lines and len(data) < 8192:
                part = sock.recv(1024)
                if not part:
                    break
                data.extend(part)
        lines = data.decode("ascii", errors="replace").splitlines()
        if len(lines) < response_lines:
            raise ConnectionError(f"Incomplete Rigctl response to {command!r}")
        return lines[:response_lines]

    def set_frequency(self, frequency_hz: float) -> None:
        if frequency_hz <= 0 or not math.isfinite(frequency_hz):
            raise ValueError("frequency_hz must be positive")
        response = self.command(f"F {int(round(frequency_hz))}")[0]
        if response != "RPRT 0":
            raise RuntimeError(f"Rigctl rejected frequency: {response}")

    def get_frequency(self) -> float:
        return float(self.command("f")[0])

    def set_mode(self, mode: str, bandwidth_hz: int = 0) -> None:
        mode = mode.upper()
        if mode not in _RIGCTL_MODES:
            raise ValueError(f"Unsupported mode {mode}; expected one of {sorted(_RIGCTL_MODES)}")
        response = self.command(f"M {mode} {int(bandwidth_hz)}")[0]
        if response != "RPRT 0":
            raise RuntimeError(f"Rigctl rejected mode: {response}")

    def get_mode(self) -> tuple[str, int]:
        mode, bandwidth = self.command("m", response_lines=2)
        return mode, int(bandwidth)


class SDRPlusPlusBridge:
    """Owns the native IQ connection and publishes bounded spectrum state."""

    def __init__(self, config: Optional[RFBridgeConfig] = None):
        self.config = (config or RFBridgeConfig.from_env()).validated()
        self.rigctl = RigctlClient(
            self.config.rigctl_host,
            self.config.rigctl_port,
            self.config.socket_timeout_s,
        )
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None
        self._dongle_info: Optional[Dict[str, Any]] = None
        self._frames: Deque[Dict] = deque(maxlen=4)
        self._sequence = 0
        self._state = "stopped"
        self._last_error: Optional[str] = None
        self._connected_at: Optional[float] = None
        self._bytes_received = 0
        self._callbacks: list[Callable[[Dict], None]] = []
        self.observations = RFObservationStore.from_env()
        # The bounded IQ ring is owned here and nowhere else. The owner refuses
        # by itself when this process may not hold raw IQ, so construction is
        # safe in a child; nothing is allocated until samples actually arrive.
        self.retention = IQRetentionOwner(
            sensor_id=self.config.sensor_id,
            sample_type=self.config.sample_type,
            sample_rate_hz=self.config.sample_rate_hz,
            owns_capture=self.config.owns_capture())
        try:
            from rf_sparse_analyzer import RFSparseAnalyzer
            self.sparse = RFSparseAnalyzer()
        except Exception:
            LOG.exception("RF sparse analyzer unavailable")
            self.sparse = None

    def add_frame_callback(self, callback: Callable[[Dict], None]) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def start(self) -> bool:
        if not self.config.owns_capture():
            LOG.info("SDR++ capture owned by %s; this process will not open the IQ socket",
                     self.config.capture_owner)
            with self._lock:
                self._state = "delegated"
                self._last_error = f"capture_owner={self.config.capture_owner}"
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._state = "connecting"
            self._last_error = None
            self._thread = threading.Thread(target=self._run, daemon=True, name="sdrpp-iq-bridge")
            self._thread.start()
            return True

    def stop(self, join_timeout_s: float = 3.0) -> bool:
        with self._lock:
            was_running = bool(self._thread and self._thread.is_alive())
            self._stop.set()
            sock = self._socket
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(join_timeout_s)
        self.retention.invalidate("ORCHESTRATOR_STOP")
        with self._lock:
            self._state = "stopped"
            self._socket = None
            self._condition.notify_all()
        return was_running

    def tune(self, frequency_hz: float, mode: Optional[str] = None, bandwidth_hz: int = 0) -> Dict:
        self.rigctl.set_frequency(frequency_hz)
        if mode:
            self.rigctl.set_mode(mode, bandwidth_hz)
        # Samples either side of a retune came from different spectrum. Cleared
        # before the reconfigure so the reason recorded is RETUNE and not the
        # DISCONNECT that the restart would otherwise leave behind.
        self.retention.invalidate("RETUNE")
        # Recreate the FFT processor so subsequent frame frequency axes reflect
        # the newly tuned center frequency immediately.
        self.configure_stream(center_frequency_hz=float(frequency_hz))
        return self.control_status()

    def supported_gains_db(self) -> Dict[str, Any]:
        """The gains this tuner actually has, or a declared refusal.

        Never interpolated and never a range. The R820T's steps are irregular --
        0.0, 0.9, 1.4, 2.7, 3.7, 7.7 ... -- so a "gain between" two of them is not
        a setting this device has, and offering one would land somewhere else
        without saying so.
        """
        with self._lock:
            info = dict(self._dongle_info) if self._dongle_info else None
        if info is None:
            return {"available": False, "reason": "NO_DEVICE_HEADER_SEEN",
                    "note": "THE STREAM DECLARED NO TUNER, SO NO GAIN TABLE APPLIES",
                    "gains_db": [], "authority": "UNDECLARED"}
        table = _TUNER_GAINS_TENTHS_DB.get(info["tuner_type"])
        if table is None:
            return {"available": False, "reason": "TUNER_NOT_IN_GAIN_CATALOGUE",
                    "note": f"NO DECLARED GAIN TABLE FOR {info['tuner_type']}",
                    "gains_db": [], "authority": "UNDECLARED", **info}
        if info["tuner_gain_count"] != len(table):
            # The device says how many gains it has. If that disagrees with the
            # table, the table is from a different driver and setting from it
            # would put the tuner somewhere other than where the caller asked.
            return {"available": False, "reason": "GAIN_COUNT_DISAGREES_WITH_DEVICE",
                    "note": (f"DEVICE REPORTS {info['tuner_gain_count']} GAINS, "
                             f"CATALOGUE HAS {len(table)}"),
                    "gains_db": [], "authority": "UNDECLARED", **info}
        return {"available": True, "reason": None,
                "gains_db": [value / 10.0 for value in table],
                "authority": "DRIVER_DECLARED_CONFIRMED_BY_DEVICE_COUNT", **info}

    def _send_rtl_command(self, opcode: int, value: int) -> None:
        with self._lock:
            sock = self._socket
        if sock is None:
            raise ConnectionError("no IQ socket is open; cannot send a control command")
        sock.sendall(bytes([opcode]) + int(value).to_bytes(4, "big", signed=False))

    def set_gain(self, gain_db: Optional[float]) -> Dict:
        """Set manual gain to a supported value, or return to automatic on None.

        The consequence -- clearing the ring under GAIN_CHANGE and moving the
        signal chain hash -- belongs to the retention owner and is applied there.
        This method owns only the control.
        """
        catalogue = self.supported_gains_db()
        if gain_db is None:
            self._send_rtl_command(_RTL_TCP_SET_GAIN_MODE, GAIN_MODE_AUTOMATIC)
            result = self.retention.set_gain_db(None)
            return {"mode": "AUTOMATIC", **result, "supported": catalogue}
        if not catalogue["available"]:
            raise ValueError(
                f"manual gain unavailable: {catalogue['reason']} -- {catalogue['note']}")
        requested = round(float(gain_db), 1)
        if requested not in catalogue["gains_db"]:
            raise ValueError(
                f"{requested} dB is not a gain this tuner has; supported values are "
                f"{catalogue['gains_db']}")
        self._send_rtl_command(_RTL_TCP_SET_GAIN_MODE, GAIN_MODE_MANUAL)
        self._send_rtl_command(_RTL_TCP_SET_GAIN, int(round(requested * 10.0)))
        result = self.retention.set_gain_db(requested)
        return {"mode": "MANUAL", **result, "supported": catalogue}

    def configure_stream(self, **changes) -> Dict:
        """Update FFT interpretation settings and restart ingestion if needed.

        Socket destinations remain deployment-owned environment configuration;
        browser callers may only change the format of the expected IQ stream.
        """
        allowed = {
            "sample_rate_hz",
            "center_frequency_hz",
            "sample_type",
            "fft_size",
            "max_bins",
            "frames_per_second",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unsupported stream settings: {sorted(unknown)}")
        with self._lock:
            values = asdict(self.config)
            values.update(changes)
            new_config = RFBridgeConfig(**values).validated()
            was_running = bool(self._thread and self._thread.is_alive() and not self._stop.is_set())
        # A rate or decode change alters both the required capacity and the
        # meaning of every retained sample, so the allocation is discarded rather
        # than resized; a centre-frequency change only clears it.
        reallocating = (new_config.sample_rate_hz != self.config.sample_rate_hz
                        or new_config.sample_type != self.config.sample_type)
        if reallocating:
            self.retention.reconfigure(
                sample_type=new_config.sample_type,
                sample_rate_hz=new_config.sample_rate_hz,
                sensor_id=new_config.sensor_id,
                owns_capture=new_config.owns_capture(),
                reason=("SAMPLE_RATE_CHANGE"
                        if new_config.sample_rate_hz != self.config.sample_rate_hz
                        else "SIGNAL_CHAIN_CHANGE"))
        elif new_config.center_frequency_hz != self.config.center_frequency_hz:
            self.retention.invalidate("RETUNE")
        if was_running:
            self.stop()
        with self._lock:
            self.config = new_config
            self._frames.clear()
        if was_running:
            self.start()
        return self.status()

    def control_status(self) -> Dict:
        result: Dict = {"reachable": False}
        try:
            result["frequency_hz"] = self.rigctl.get_frequency()
            mode, bandwidth = self.rigctl.get_mode()
            result.update({"mode": mode, "bandwidth_hz": bandwidth, "reachable": True})
        except Exception as exc:
            result["error"] = str(exc)
        return result

    def _maybe_channelize(self, frame: Dict) -> None:
        """Offer the newest complete window to the channelizer. Never raises.

        Frame-driven because the frame carries the coarse peak that selects a
        target; the window itself comes from the ring, complete and immutable,
        so the channelizer never reads whatever samples happen to be newest.
        The frame's peak is only a pointer -- the channelizer runs its own
        occupancy estimate and records that candidate in its own fields.
        """
        try:
            target = frame.get("peak_frequency_hz")
            centre = frame.get("center_frequency_hz")
            if target is None or centre is None:
                return
            self.retention.maybe_channelize(
                capture_center_hz=float(centre), target_frequency_hz=float(target))
        except Exception:
            LOG.exception("channelization dispatch failed")

    def capture_rate_declaration(self) -> Dict:
        """What the rate figure is, and what authority stands behind it.

        Published beside the rate itself so a reader cannot pick up the number
        without the qualifier attached to it.
        """
        return {
            "sample_rate_hz": self.config.sample_rate_hz,
            "sample_rate_authority": SAMPLE_RATE_AUTHORITY,
            "runtime_attestation": SAMPLE_RATE_RUNTIME_ATTESTATION,
            "native_bin_width_hz": self.config.sample_rate_hz / self.config.fft_size,
        }

    def capture_source_declaration(self) -> Dict:
        """Reachability of the IQ source, without inventing a cause."""
        connected = self._state == "streaming"
        declaration = {
            "iq_endpoint": f"{self.config.iq_host}:{self.config.iq_port}",
            "connection_state": self._state,
            "availability": "SOURCE_CONNECTED" if connected else "SOURCE_UNREACHABLE",
        }
        if not connected:
            declaration["unreachable_cause"] = SOURCE_UNREACHABLE_CAUSE
            declaration["cause_note"] = SOURCE_UNREACHABLE_NOTE
        return declaration

    def status(self, include_control: bool = False) -> Dict:
        with self._lock:
            thread_alive = bool(self._thread and self._thread.is_alive())
            latest = self._frames[-1] if self._frames else None
            status = {
                "status": "ok",
                "bridge_state": self._state,
                "running": thread_alive and not self._stop.is_set(),
                "iq_connected": self._state == "streaming",
                "connected_at": self._connected_at,
                "last_error": self._last_error,
                "bytes_received": self._bytes_received,
                "latest_sequence": self._sequence,
                "latest_frame_at": latest.get("timestamp") if latest else None,
                "config": asdict(self.config),
                # The rate in "config" above is a launch parameter, not a
                # measurement. This block travels with it saying so.
                "capture_rate_declaration": self.capture_rate_declaration(),
                "capture_source": self.capture_source_declaration(),
                "capture_owner": self.config.capture_owner,
                "owns_capture": self.config.owns_capture(),
                "process_role": os.getenv("SCYTHE_PROCESS_ROLE") or "unspecified",
                "sparse": None if self.sparse is None else self.sparse.stats(),
                # Bounded, process-local, never serialized. The block reports
                # metadata about the allocation and nothing derived from a sample.
                "iq_retention": self.retention.status(),
                # Device-declared, read from rtl_tcp's own header rather than
                # assumed from what is believed to be plugged in. Not a control,
                # so it is published whether or not controls are included.
                "device": self.supported_gains_db(),
            }
        if include_control:
            status["rigctl"] = self.control_status()
        return status

    def latest_frame(self) -> Optional[Dict]:
        with self._lock:
            return dict(self._frames[-1]) if self._frames else None

    def wait_for_frame(self, after_sequence: int, timeout_s: float = 15.0) -> Optional[Dict]:
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while not self._stop.is_set():
                if self._frames and self._frames[-1]["sequence"] > after_sequence:
                    return dict(self._frames[-1])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
        return None

    def _run(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            processor = IQFFTProcessor(self.config, sample_sink=self.retention.append)
            try:
                with self._lock:
                    self._state = "connecting"
                sock = socket.create_connection(
                    (self.config.iq_host, self.config.iq_port),
                    self.config.socket_timeout_s,
                )
                sock.settimeout(1.0)
                with self._lock:
                    self._socket = sock
                    self._state = "streaming"
                    self._connected_at = time.time()
                    self._last_error = None
                # A new socket is a new continuity claim. Whatever the ring held
                # from before the gap is not contiguous with what follows.
                self.retention.invalidate("RECONNECT")
                delay = 0.5
                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise ConnectionError("SDR++ IQ exporter closed the connection")
                    with self._lock:
                        self._bytes_received += len(chunk)
                    if self._dongle_info is None:
                        info = processor.dongle_info
                        if info is not None:
                            with self._lock:
                                self._dongle_info = info
                            LOG.info("rtl_tcp device: %s tuner, %d gains",
                                     info["tuner_type"], info["tuner_gain_count"])
                    for frame in processor.feed(chunk):
                        # Publish first, unconditionally. Channelization is
                        # analysis layered on top of the spectrum product; it
                        # must never be able to delay or suppress one.
                        self._publish(frame)
                        self._maybe_channelize(frame)
            except Exception as exc:
                if not self._stop.is_set():
                    LOG.warning("SDR++ IQ connection failed: %s", exc)
                    with self._lock:
                        self._state = "reconnecting"
                        self._last_error = str(exc)
                self.retention.invalidate("DISCONNECT")
            finally:
                with self._lock:
                    sock = self._socket
                    self._socket = None
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass
            if self._stop.wait(delay):
                break
            delay = min(delay * 2.0, self.config.reconnect_max_s)
        with self._lock:
            self._state = "stopped"
            self._condition.notify_all()

    def _publish(self, frame: Dict) -> None:
        with self._condition:
            self._sequence += 1
            frame = {**frame, "sequence": self._sequence, "sensor_id": self.config.sensor_id}
            self._frames.append(frame)
            callbacks = list(self._callbacks)
            self._condition.notify_all()
        for callback in callbacks:
            try:
                callback(dict(frame))
            except Exception:
                LOG.exception("SDR++ frame callback failed")
        self.observations.ingest_frame(frame)
        sparse = getattr(self, "sparse", None)
        if sparse is not None:
            try:
                sparse.ingest_frame(frame)
            except Exception:
                LOG.exception("RF sparse analyzer failed")


_bridge_lock = threading.Lock()
_bridge: Optional[SDRPlusPlusBridge] = None


def get_rf_bridge() -> SDRPlusPlusBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = SDRPlusPlusBridge()
            if _bridge.config.auto_start and _bridge.config.owns_capture():
                _bridge.start()
        return _bridge


def get_rf_observation_store() -> RFObservationStore:
    return get_rf_bridge().observations


def get_rf_sparse_analyzer():
    return getattr(get_rf_bridge(), "sparse", None)


def reset_rf_bridge_for_tests() -> None:
    global _bridge
    with _bridge_lock:
        if _bridge is not None:
            _bridge.stop()
        _bridge = None
