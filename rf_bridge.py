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


LOG = logging.getLogger(__name__)

_SAMPLE_DTYPES = {
    "int8": np.dtype("i1"),
    "int16": np.dtype("<i2"),
    "int32": np.dtype("<i4"),
    "float32": np.dtype("<f4"),
}

_FULL_SCALE = {
    "int8": 128.0,
    "int16": 32768.0,
    "int32": 2147483648.0,
    "float32": 1.0,
}

_RIGCTL_MODES = {"FM", "WFM", "AM", "DSB", "USB", "CW", "LSB", "RAW"}


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
        return self


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
    evidence_class: str = "OBSERVED"
    source: str = "sdrpp_iq_exporter"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RFObservationStore:
    """Thread-safe, bounded store for significant spectral observations."""

    def __init__(self, maxlen: int = 2048, min_snr_db: float = 12.0,
                 cooldown_s: float = 1.0, bucket_hz: float = 5_000.0):
        self._items: Deque[RFObservation] = deque(maxlen=max(16, int(maxlen)))
        self._min_snr_db = float(min_snr_db)
        self._cooldown_s = max(0.0, float(cooldown_s))
        self._bucket_hz = max(1.0, float(bucket_hz))
        self._last_seen: Dict[tuple[str, int], float] = {}
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
        return {
            "count": len(items),
            "capacity": self._items.maxlen,
            "min_snr_db": self._min_snr_db,
            "latest_observed_at": items[-1].observed_at if items else None,
            "evidence_class": "OBSERVED",
            "raw_iq_exposed": False,
        }


class IQFFTProcessor:
    """Incrementally converts unframed interleaved IQ bytes into FFT frames."""

    def __init__(self, config: RFBridgeConfig):
        self.config = config.validated()
        self._dtype = _SAMPLE_DTYPES[config.sample_type]
        self._scalar_bytes = self._dtype.itemsize
        self._window = np.hanning(config.fft_size).astype(np.float32)
        self._byte_remainder = bytearray()
        self._samples = np.empty(0, dtype=np.complex64)
        self._last_frame_at = 0.0

    def feed(self, chunk: bytes, now: Optional[float] = None) -> Iterable[Dict]:
        if not chunk:
            return []
        now = time.time() if now is None else now
        self._byte_remainder.extend(chunk)
        pair_bytes = self._scalar_bytes * 2
        usable = len(self._byte_remainder) - (len(self._byte_remainder) % pair_bytes)
        if usable:
            raw = bytes(self._byte_remainder[:usable])
            del self._byte_remainder[:usable]
            scalars = np.frombuffer(raw, dtype=self._dtype).astype(np.float32)
            scalars /= _FULL_SCALE[self.config.sample_type]
            iq = (scalars[0::2] + 1j * scalars[1::2]).astype(np.complex64)
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
        self._frames: Deque[Dict] = deque(maxlen=4)
        self._sequence = 0
        self._state = "stopped"
        self._last_error: Optional[str] = None
        self._connected_at: Optional[float] = None
        self._bytes_received = 0
        self._callbacks: list[Callable[[Dict], None]] = []
        self.observations = RFObservationStore.from_env()
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
        with self._lock:
            self._state = "stopped"
            self._socket = None
            self._condition.notify_all()
        return was_running

    def tune(self, frequency_hz: float, mode: Optional[str] = None, bandwidth_hz: int = 0) -> Dict:
        self.rigctl.set_frequency(frequency_hz)
        if mode:
            self.rigctl.set_mode(mode, bandwidth_hz)
        # Recreate the FFT processor so subsequent frame frequency axes reflect
        # the newly tuned center frequency immediately.
        self.configure_stream(center_frequency_hz=float(frequency_hz))
        return self.control_status()

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
                "sparse": None if self.sparse is None else self.sparse.stats(),
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
            processor = IQFFTProcessor(self.config)
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
                    for frame in processor.feed(chunk):
                        self._publish(frame)
            except Exception as exc:
                if not self._stop.is_set():
                    LOG.warning("SDR++ IQ connection failed: %s", exc)
                    with self._lock:
                        self._state = "reconnecting"
                        self._last_error = str(exc)
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
            if _bridge.config.auto_start:
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
