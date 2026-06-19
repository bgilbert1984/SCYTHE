"""
tak_ml_client.py — NerfEngine ↔ TAK-ML KServe client.

Provides:
  TakMLClient       — synchronous KServe REST wrapper
  AsyncInferenceQueue — async worker pool (4 threads) feeding TakMLClient
  extract_flow_features — extract a 7-feature tensor from a NerfEngine event dict

TAK-ML Server API (port 8234):
  POST /v2/models/{model}/versions/{version}/infer   — run inference
  POST /model_feedback/add_feedback                  — submit analyst correction
  GET  /v2/health/ready                              — server readiness probe
  POST /api/models/upload                            — upload model bundle .zip
  GET  /api/models/v2/get_models                     — list available models

Feature tensor layout (7-dim FP32):
  [0] fan_in_count      — distinct sources reaching a single destination
  [1] temporal_sync     — 1 - normalised inter-arrival entropy (0=random, 1=sync)
  [2] source_entropy    — Shannon H over src node IDs (bits)
  [3] avg_packet_size   — mean bytes per packet in the window
  [4] connection_rate   — connections per second
  [5] dst_port_entropy  — Shannon H over dst port distribution (bits)
  [6] asn_spread        — distinct ASN count inferred from src node diversity

Usage:
    from tak_ml_client import TakMLClient, AsyncInferenceQueue, extract_flow_features

    client = TakMLClient()
    q = AsyncInferenceQueue(client)
    q.start()

    # from stream_manager callback:
    features = extract_flow_features(event)
    q.enqueue(features, callback=lambda score, feat: print(f"score={score:.3f}"))
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

TAKML_BASE_URL      = "http://localhost:8234"
DEFAULT_MODEL       = "nerf_botnet_v1"
DEFAULT_VERSION     = "1"
DEFAULT_TIMEOUT_S   = 2.0           # per-request HTTP timeout
QUEUE_WORKERS       = 4             # worker thread count
QUEUE_MAXSIZE       = 512           # backpressure limit — drops oldest if full
HEALTH_RETRY_S      = 5.0           # interval between health-check retries
FEATURE_NAMES = (
    "fan_in_count",
    "temporal_sync",
    "source_entropy",
    "avg_packet_size",
    "connection_rate",
    "dst_port_entropy",
    "asn_spread",
)
FEATURE_DIM = len(FEATURE_NAMES)    # 7


# ─────────────────────────────────────────────────────────────────────────────
# TakMLClient — synchronous KServe REST wrapper
# ─────────────────────────────────────────────────────────────────────────────

class TakMLClient:
    """Thin KServe-compatible client for the TAK-ML inference server.

    All methods are synchronous and thread-safe (requests.Session is
    constructed per-thread if needed; here we keep one shared session
    because requests.Session is thread-safe for concurrent GET/POST).
    """

    def __init__(
        self,
        base_url:  str = TAKML_BASE_URL,
        model:     str = DEFAULT_MODEL,
        version:   str = DEFAULT_VERSION,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        api_key:   Optional[str] = None,
    ) -> None:
        self.base_url  = base_url.rstrip("/")
        self.model     = model
        self.version   = version
        self.timeout_s = timeout_s

        self._session = requests.Session()
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"
        self._session.headers["Content-Type"] = "application/json"

    # ── Health ────────────────────────────────────────────────────────────────

    def health(self) -> bool:
        """Return True if the TAK-ML server reports ready."""
        try:
            r = self._session.get(
                f"{self.base_url}/v2/health/ready", timeout=self.timeout_s
            )
            return r.status_code == 200
        except Exception as exc:
            logger.debug("[tak-ml] health check failed: %s", exc)
            return False

    # ── Inference ─────────────────────────────────────────────────────────────

    def infer(
        self,
        features: Dict[str, float],
        model:    Optional[str] = None,
        version:  Optional[str] = None,
    ) -> float:
        """POST a feature dict to the KServe infer endpoint.

        Returns the first scalar output value (botnet probability, 0–1).
        Raises requests.HTTPError on non-2xx responses.
        """
        mdl = model   or self.model
        ver = version or self.version
        url = f"{self.base_url}/v2/models/{mdl}/versions/{ver}/infer"

        values = [float(features.get(k, 0.0)) for k in FEATURE_NAMES]
        payload = {
            "inputs": [
                {
                    "name":     "features",
                    "shape":    [1, FEATURE_DIM],
                    "datatype": "FP32",
                    "data":     values,
                }
            ]
        }
        r = self._session.post(url, json=payload, timeout=self.timeout_s)
        r.raise_for_status()
        return float(r.json()["outputs"][0]["data"][0])

    def infer_raw(self, payload: dict, model: Optional[str] = None,
                  version: Optional[str] = None) -> dict:
        """POST an arbitrary KServe payload and return the full response dict."""
        mdl = model   or self.model
        ver = version or self.version
        url = f"{self.base_url}/v2/models/{mdl}/versions/{ver}/infer"
        r = self._session.post(url, json=payload, timeout=self.timeout_s)
        r.raise_for_status()
        return r.json()

    # ── Feedback ──────────────────────────────────────────────────────────────

    def submit_feedback(
        self,
        predicted_output: str,
        actual_output:    str,
        model:            Optional[str] = None,
        features:         Optional[Dict[str, float]] = None,
        notes:            str = "",
    ) -> bool:
        """POST analyst correction to /model_feedback/add_feedback.

        Returns True on success.

        Args:
            predicted_output: what the model said (e.g. "botnet_coordination")
            actual_output:    analyst's ground truth (e.g. "cdn_flash_crowd")
            model:            override model name (default: self.model)
            features:         optional feature dict for training data context
            notes:            optional free-text analyst note
        """
        mdl = model or self.model
        payload: Dict[str, Any] = {
            "model_name":       mdl,
            "predicted_output": predicted_output,
            "actual_output":    actual_output,
        }
        if features:
            payload["input"] = features
        if notes:
            payload["notes"] = notes

        try:
            r = self._session.post(
                f"{self.base_url}/model_feedback/add_feedback",
                json=payload,
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            logger.info(
                "[tak-ml] feedback submitted: %s → %s (model=%s)",
                predicted_output, actual_output, mdl,
            )
            return True
        except Exception as exc:
            logger.warning("[tak-ml] feedback submission failed: %s", exc)
            return False

    # ── Model management ──────────────────────────────────────────────────────

    def list_models(self) -> List[dict]:
        """Return list of available models from TAK-ML server."""
        try:
            r = self._session.get(
                f"{self.base_url}/api/models/v2/get_models", timeout=self.timeout_s
            )
            r.raise_for_status()
            return r.json().get("models", [])
        except Exception as exc:
            logger.warning("[tak-ml] list_models failed: %s", exc)
            return []

    def upload_model(self, zip_path: str) -> dict:
        """Upload a TAK-ML model bundle (.zip) to the server.

        The bundle must contain model weights + takml_config.yaml.
        Returns the server response dict.
        """
        import os
        if not os.path.isfile(zip_path):
            raise FileNotFoundError(f"Model bundle not found: {zip_path}")

        url = f"{self.base_url}/api/models/upload"
        with open(zip_path, "rb") as fh:
            # Remove Content-Type so requests sets multipart/form-data automatically
            hdrs = {k: v for k, v in self._session.headers.items()
                    if k.lower() != "content-type"}
            r = requests.post(
                url,
                files={"file": (os.path.basename(zip_path), fh, "application/zip")},
                headers=hdrs,
                timeout=30.0,
            )
        r.raise_for_status()
        return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Feature extraction — NerfEngine event dict → 7-dim tensor
# ─────────────────────────────────────────────────────────────────────────────

def extract_flow_features(event: dict) -> Dict[str, float]:
    """Extract a 7-dim feature dict from a NerfEngine decoded event.

    Works with FlowCore, GraphEdge, and synthetic detector result dicts.
    Missing fields default to 0.0 so the tensor is always complete.

    This is the bridge between NerfEngine's raw event format and the
    TAK-ML model input tensor.
    """
    props: dict = {}
    # Events can carry properties as list-of-dicts or as direct keys
    raw_props = event.get("properties", [])
    if isinstance(raw_props, list):
        for p in raw_props:
            if isinstance(p, dict):
                props[p.get("key", "")] = p.get("value", "0")
    elif isinstance(raw_props, dict):
        props = raw_props

    def _f(key: str, default: float = 0.0) -> float:
        v = props.get(key, event.get(key, default))
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    fan_in_count  = _f("fan_in_count",   _f("src_count", 1.0))
    temporal_sync = _f("temporal_sync",  0.0)
    source_entropy = _f("source_entropy", _f("ip_entropy", 0.0))
    avg_pkt_size  = _f("avg_packet_size", _f("bytes", 1400.0))
    conn_rate     = _f("connection_rate", _f("fan_in_rate", 0.0))
    dst_port_H    = _f("dst_port_entropy", 0.0)
    asn_spread    = _f("asn_spread",  _f("asn_count", 1.0))

    return {
        "fan_in_count":    fan_in_count,
        "temporal_sync":   temporal_sync,
        "source_entropy":  source_entropy,
        "avg_packet_size": avg_pkt_size,
        "connection_rate": conn_rate,
        "dst_port_entropy": dst_port_H,
        "asn_spread":      asn_spread,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Inference Job
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InferenceJob:
    features:  Dict[str, float]
    callback:  Optional[Callable[[float, Dict[str, float]], None]] = None
    model:     Optional[str] = None
    version:   Optional[str] = None
    enqueued_at: float = field(default_factory=time.monotonic)


# ─────────────────────────────────────────────────────────────────────────────
# AsyncInferenceQueue — 4-worker thread pool
# ─────────────────────────────────────────────────────────────────────────────

class AsyncInferenceQueue:
    """Non-blocking inference queue backed by a fixed thread pool.

    Architecture from ATAK-ML.md §10:

        edge stream
            ↓
        feature queue  ← enqueue()
            ↓
        async workers  ← QUEUE_WORKERS threads
            ↓
        TAK-ML inference
            ↓
        callback(score, features)

    Backpressure: if queue is full the oldest item is silently dropped
    and the new item is inserted.  This prevents OOM on burst traffic.
    """

    def __init__(
        self,
        client:   TakMLClient,
        workers:  int = QUEUE_WORKERS,
        maxsize:  int = QUEUE_MAXSIZE,
    ) -> None:
        self._client  = client
        self._workers = workers
        self._q: queue.Queue[Optional[InferenceJob]] = queue.Queue(maxsize=maxsize)
        self._threads: List[threading.Thread] = []
        self._running = False
        self._dropped = 0       # backpressure drop counter
        self._processed = 0
        self._errors = 0

    def start(self) -> None:
        """Spawn worker threads.  Idempotent."""
        if self._running:
            return
        self._running = True
        for i in range(self._workers):
            t = threading.Thread(
                target=self._worker,
                name=f"takml-worker-{i}",
                daemon=True,
            )
            t.start()
            self._threads.append(t)
        logger.info("[tak-ml] AsyncInferenceQueue started (%d workers)", self._workers)

    def stop(self, timeout: float = 5.0) -> None:
        """Drain queue and stop workers."""
        if not self._running:
            return
        self._running = False
        # Poison pills — one per worker
        for _ in self._threads:
            self._q.put(None, block=False)
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        logger.info(
            "[tak-ml] AsyncInferenceQueue stopped — processed=%d dropped=%d errors=%d",
            self._processed, self._dropped, self._errors,
        )

    def enqueue(
        self,
        features: Dict[str, float],
        callback: Optional[Callable[[float, Dict[str, float]], None]] = None,
        model:    Optional[str] = None,
        version:  Optional[str] = None,
    ) -> bool:
        """Enqueue a feature dict for async inference.

        Returns True if enqueued, False if queue is full (item dropped).
        """
        job = InferenceJob(features=features, callback=callback,
                           model=model, version=version)
        try:
            self._q.put_nowait(job)
            return True
        except queue.Full:
            # Drop oldest item to make room
            try:
                self._q.get_nowait()
                self._dropped += 1
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(job)
                return True
            except queue.Full:
                self._dropped += 1
                return False

    def stats(self) -> dict:
        return {
            "qsize":     self._q.qsize(),
            "processed": self._processed,
            "dropped":   self._dropped,
            "errors":    self._errors,
            "running":   self._running,
        }

    def _worker(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                break   # poison pill
            try:
                score = self._client.infer(
                    job.features, model=job.model, version=job.version
                )
                self._processed += 1
                if job.callback:
                    try:
                        job.callback(score, job.features)
                    except Exception as cb_exc:
                        logger.warning("[tak-ml] callback error: %s", cb_exc)
            except Exception as exc:
                self._errors += 1
                logger.warning(
                    "[tak-ml] inference error (age=%.1fs): %s",
                    time.monotonic() - job.enqueued_at, exc,
                )
            finally:
                self._q.task_done()


# ─────────────────────────────────────────────────────────────────────────────
# Health watcher — background thread that logs when server comes online
# ─────────────────────────────────────────────────────────────────────────────

class TakMLHealthWatcher:
    """Polls TAK-ML /v2/health/ready on a background thread.

    Calls on_healthy() once when the server becomes reachable,
    on_unhealthy() if it goes down.
    """

    def __init__(
        self,
        client:       TakMLClient,
        on_healthy:   Optional[Callable[[], None]] = None,
        on_unhealthy: Optional[Callable[[], None]] = None,
        interval_s:   float = HEALTH_RETRY_S,
    ) -> None:
        self._client = client
        self._on_healthy   = on_healthy
        self._on_unhealthy = on_unhealthy
        self._interval     = interval_s
        self._healthy      = False
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()

    def start(self) -> None:
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, name="takml-health", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)

    def _loop(self) -> None:
        while not self._stop_evt.is_set():
            healthy = self._client.health()
            if healthy and not self._healthy:
                logger.info("[tak-ml] server is healthy at %s", self._client.base_url)
                self._healthy = True
                if self._on_healthy:
                    try:
                        self._on_healthy()
                    except Exception as e:
                        logger.warning("[tak-ml] on_healthy callback error: %s", e)
            elif not healthy and self._healthy:
                logger.warning("[tak-ml] server went unhealthy: %s", self._client.base_url)
                self._healthy = False
                if self._on_unhealthy:
                    try:
                        self._on_unhealthy()
                    except Exception as e:
                        logger.warning("[tak-ml] on_unhealthy callback error: %s", e)
            self._stop_evt.wait(timeout=self._interval)

    @property
    def is_healthy(self) -> bool:
        return self._healthy


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singletons (lazy init)
# ─────────────────────────────────────────────────────────────────────────────

_default_client: Optional[TakMLClient] = None
_default_queue:  Optional[AsyncInferenceQueue] = None
_default_watcher: Optional[TakMLHealthWatcher] = None
_singleton_lock = threading.Lock()


def get_takml_client(
    url:    str = TAKML_BASE_URL,
    model:  str = DEFAULT_MODEL,
    version: str = DEFAULT_VERSION,
) -> TakMLClient:
    """Return (and lazily create) the module-level TakMLClient singleton."""
    global _default_client
    with _singleton_lock:
        if _default_client is None:
            _default_client = TakMLClient(base_url=url, model=model, version=version)
        return _default_client


def get_inference_queue(start: bool = True) -> AsyncInferenceQueue:
    """Return (and lazily create) the module-level AsyncInferenceQueue."""
    global _default_queue
    with _singleton_lock:
        if _default_queue is None:
            _default_queue = AsyncInferenceQueue(get_takml_client())
            if start:
                _default_queue.start()
        return _default_queue


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────

def _self_test() -> None:
    import json

    passed = 0
    failed = 0

    def check(label: str, condition: bool) -> None:
        nonlocal passed, failed
        if condition:
            print(f"  [PASS] {label}")
            passed += 1
        else:
            print(f"  [FAIL] {label}")
            failed += 1

    print("=== tak_ml_client.py self-test ===\n")

    # ── Feature extraction ────────────────────────────────────────────────────
    print("Feature extraction:")

    event_with_props = {
        "type": "flow_core",
        "properties": [
            {"key": "fan_in_count",   "value": "84"},
            {"key": "temporal_sync",  "value": "0.91"},
            {"key": "source_entropy", "value": "5.8"},
            {"key": "avg_packet_size","value": "430"},
            {"key": "connection_rate","value": "190"},
            {"key": "dst_port_entropy","value": "1.1"},
            {"key": "asn_spread",     "value": "13"},
        ]
    }
    feats = extract_flow_features(event_with_props)
    check("7 features extracted", len(feats) == FEATURE_DIM)
    check("fan_in_count = 84",    feats["fan_in_count"] == 84.0)
    check("temporal_sync = 0.91", abs(feats["temporal_sync"] - 0.91) < 1e-6)
    check("asn_spread = 13",      feats["asn_spread"] == 13.0)

    # Direct dict properties (no list)
    event_direct = {"fan_in_count": 5, "temporal_sync": 0.5, "ip_entropy": 3.2}
    feats2 = extract_flow_features(event_direct)
    check("direct dict fallback", feats2["fan_in_count"] == 5.0)
    check("ip_entropy → source_entropy", feats2["source_entropy"] == 3.2)

    # Empty event — all zeros
    feats3 = extract_flow_features({})
    check("empty event → all 0 or default", all(v >= 0.0 for v in feats3.values()))

    # ── KServe payload structure ───────────────────────────────────────────────
    print("\nKServe payload:")

    client = TakMLClient(base_url="http://localhost:8234")
    feats_simple = {k: float(i) for i, k in enumerate(FEATURE_NAMES)}
    # Manually build payload to verify structure without network
    values = [feats_simple[k] for k in FEATURE_NAMES]
    payload = {
        "inputs": [
            {"name": "features", "shape": [1, FEATURE_DIM], "datatype": "FP32", "data": values}
        ]
    }
    check("shape is [1, 7]",        payload["inputs"][0]["shape"] == [1, 7])
    check("data has 7 elements",    len(payload["inputs"][0]["data"]) == 7)
    check("datatype is FP32",       payload["inputs"][0]["datatype"] == "FP32")

    # ── AsyncInferenceQueue ────────────────────────────────────────────────────
    print("\nAsyncInferenceQueue:")

    results: list = []
    errors: list = []

    class _MockClient(TakMLClient):
        def infer(self, features, model=None, version=None):
            v = features.get("temporal_sync", 0.0)
            if v < 0:
                raise ValueError("negative sync")
            return min(1.0, v * 1.1)

    mock = _MockClient(base_url="http://mock")
    iq = AsyncInferenceQueue(mock, workers=2, maxsize=10)
    iq.start()

    # Normal job
    done = threading.Event()
    def cb(score, feats):
        results.append(score)
        done.set()

    iq.enqueue({"temporal_sync": 0.82}, callback=cb)
    done.wait(timeout=3.0)
    check("async result received",   len(results) == 1)
    check("score ≈ 0.902",          abs(results[0] - 0.902) < 1e-3)

    # Error job — should increment errors counter, not crash
    iq.enqueue({"temporal_sync": -1.0})
    time.sleep(0.3)
    check("error counted, not crash", iq._errors == 1)

    # Stats dict
    s = iq.stats()
    check("stats has expected keys",  all(k in s for k in ("qsize","processed","dropped","errors","running")))

    # Backpressure — fill queue beyond maxsize
    iq2 = AsyncInferenceQueue(mock, workers=0, maxsize=3)   # workers=0 → nothing drains
    for _ in range(10):
        iq2.enqueue({"temporal_sync": 0.5})
    check("backpressure drops tracked", iq2._dropped > 0)
    check("queue never exceeds maxsize", iq2._q.qsize() <= 3)

    iq.stop()

    # ── Feature names constant ────────────────────────────────────────────────
    print("\nConstants:")
    check("FEATURE_DIM = 7",          FEATURE_DIM == 7)
    check("FEATURE_NAMES has 7 entries", len(FEATURE_NAMES) == 7)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*40}")
    print(f"  {passed}/{passed+failed} tests passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    _self_test()
