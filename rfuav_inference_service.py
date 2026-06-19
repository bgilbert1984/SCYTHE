"""
rfuav_inference_service.py

Normalize RFUAV outputs into observed SCYTHE RF evidence without granting
classification, identity, or geolocation authority.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
import hashlib
import json
import logging
import time


logger = logging.getLogger(__name__)

DEFAULT_MIN_CONFIDENCE = 0.60
DEFAULT_KAFKA_TOPIC = "rf.uav.detections"


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _stable_id(*parts: Any) -> str:
    payload = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _slug(value: Any) -> str:
    raw = str(value or "").strip().lower()
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw)
    return "_".join(part for part in cleaned.split("_") if part)


def _normalize_frequency_hz(value: Any) -> Optional[float]:
    freq = _safe_float(value, 0.0)
    if freq <= 0.0:
        return None
    if freq < 1_000_000.0:
        return freq * 1_000_000.0
    return freq


def _normalize_embedding(value: Any) -> Optional[List[float]]:
    if not isinstance(value, (list, tuple)):
        return None
    normalized: List[float] = []
    for item in value:
        try:
            normalized.append(float(item))
        except (TypeError, ValueError):
            return None
    return normalized or None


def _classify_label(label: str) -> str:
    haystack = str(label or "").lower()
    if any(token in haystack for token in ("controller", "remote", "transmitter", "fpv", "dji", "radiomaster")):
        return "uav_controller"
    if any(token in haystack for token in ("video", "downlink", "camera")):
        return "drone_video_link"
    if any(token in haystack for token in ("telemetry", "uplink", "command")):
        return "drone_telemetry"
    if any(token in haystack for token in ("uav", "drone", "quadcopter", "hexacopter")):
        return "uav_emitter"
    return "unknown"


class RFUAVKafkaEmitter:
    def __init__(
        self,
        *,
        bootstrap_servers: Any = "localhost:9092",
        topic: str = DEFAULT_KAFKA_TOPIC,
        producer: Optional[Any] = None,
        flush: bool = True,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.flush = flush
        self._producer = producer

        if self._producer is None:
            try:
                from kafka import KafkaProducer
            except ImportError as exc:
                raise RuntimeError("Kafka support requires kafka-python to be installed") from exc

            self._producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            )

    def emit(self, detection: Dict[str, Any], *, timeout: float = 5.0) -> Dict[str, Any]:
        sensor_id = str(detection.get("sensor_id") or "").strip()
        if not sensor_id:
            raise ValueError("sensor_id is required for Kafka emission")

        future = self._producer.send(self.topic, key=sensor_id.encode("utf-8"), value=detection)
        metadata = None
        if hasattr(future, "get"):
            try:
                metadata = future.get(timeout=timeout)
            except Exception:
                metadata = None
        if self.flush and hasattr(self._producer, "flush"):
            self._producer.flush()

        return {
            "status": "ok",
            "topic": self.topic,
            "key": sensor_id,
            "partition": getattr(metadata, "partition", None),
            "offset": getattr(metadata, "offset", None),
        }


class RFUAVEvidenceEmitter:
    def __init__(
        self,
        model: Optional[Any] = None,
        *,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        writebus_provider: Optional[Callable[[], Any]] = None,
        stream_emitter: Optional[RFUAVKafkaEmitter] = None,
    ) -> None:
        self.model = model
        self.min_confidence = _clamp(float(min_confidence))
        self.writebus_provider = writebus_provider
        self.stream_emitter = stream_emitter

    def process_iq(
        self,
        iq_chunk: Any,
        *,
        sensor_id: str,
        timestamp: Optional[float] = None,
        ctx: Any = None,
        emit: bool = True,
        stream: Optional[bool] = None,
        **payload: Any,
    ) -> Dict[str, Any]:
        inference = self._run_model(iq_chunk, mode="iq")
        merged = dict(payload)
        merged.update({"sensor_id": sensor_id, "timestamp": timestamp or time.time(), "inference": inference})
        return self.ingest(merged, ctx=ctx, emit=emit, stream=stream if stream is not None else self.stream_emitter is not None)

    def process_spectrogram(
        self,
        spectrogram: Any,
        *,
        sensor_id: str,
        timestamp: Optional[float] = None,
        ctx: Any = None,
        emit: bool = True,
        stream: Optional[bool] = None,
        **payload: Any,
    ) -> Dict[str, Any]:
        inference = self._run_model(spectrogram, mode="spectrogram")
        merged = dict(payload)
        merged.update({"sensor_id": sensor_id, "timestamp": timestamp or time.time(), "inference": inference})
        return self.ingest(merged, ctx=ctx, emit=emit, stream=stream if stream is not None else self.stream_emitter is not None)

    def ingest(self, payload: Dict[str, Any], *, ctx: Any = None, emit: bool = True, stream: bool = False) -> Dict[str, Any]:
        payload = dict(payload or {})
        sensor_context = dict(payload.get("sensor_context") or {})
        inference = payload.get("inference") or payload.get("result") or payload
        inference = dict(inference or {})

        sensor_id = str(
            _coalesce(
                payload.get("sensor_id"),
                payload.get("sensorId"),
                payload.get("observer_id"),
                sensor_context.get("sensor_id"),
            )
            or ""
        ).strip()
        if not sensor_id:
            raise ValueError("sensor_id is required")

        timestamp = _safe_float(_coalesce(payload.get("timestamp"), inference.get("timestamp")), time.time())
        label = str(
            _coalesce(
                inference.get("label"),
                inference.get("prediction"),
                inference.get("class_name"),
                inference.get("drone_name"),
                inference.get("name"),
                "unknown",
            )
        )
        confidence = _clamp(
            _safe_float(
                _coalesce(
                    inference.get("confidence"),
                    inference.get("score"),
                    inference.get("probability"),
                    inference.get("prob"),
                ),
                0.0,
            )
        )
        if confidence < self.min_confidence:
            return {
                "status": "ignored",
                "accepted": False,
                "reason": "confidence_below_threshold",
                "confidence": round(confidence, 4),
                "min_confidence": self.min_confidence,
                "sensor_id": sensor_id,
                "label": label,
            }

        observation = self._build_observation(payload, inference, sensor_id=sensor_id, timestamp=timestamp, confidence=confidence)
        detection_event = self.build_detection_event(payload, observation=observation, inference=inference)
        stream_result = self.emit_detection_event(detection_event) if stream else None
        graph_result = self.emit_observation(observation, ctx=ctx) if emit else None
        return {
            "status": "ok",
            "accepted": True,
            "sensor_id": sensor_id,
            "label": label,
            "confidence": round(confidence, 4),
            "event": detection_event,
            "stream": stream_result,
            "observation": observation,
            "graph": graph_result,
        }

    def build_detection_event(
        self,
        payload: Dict[str, Any],
        *,
        observation: Dict[str, Any],
        inference: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        inference = dict(inference or {})
        payload = dict(payload or {})
        rf = dict(observation.get("rf") or {})
        signal = dict(rf.get("signal") or {})
        temporal = dict(rf.get("temporal") or {})
        embedding = _normalize_embedding(_coalesce(payload.get("embedding"), inference.get("embedding")))
        provenance = str(
            _coalesce(
                payload.get("provenance"),
                inference.get("provenance"),
                f"{rf.get('model', {}).get('name', 'rfuav_main')}_{rf.get('model', {}).get('version', 'v1')}",
            )
        )

        event = {
            "event_type": "rf_uav_detection",
            "detection_id": observation.get("observation_id"),
            "sensor_id": observation.get("sensor_id"),
            "timestamp": int(round(_safe_float(observation.get("timestamp"), time.time()))),
            "rf_node_id": observation.get("rf_node_id"),
            "mission_id": observation.get("mission_id"),
            "rf": {
                "class": rf.get("class"),
                "subtype": rf.get("subtype"),
                "confidence": rf.get("confidence"),
            },
            "signal": {
                "center_freq": signal.get("center_freq_hz"),
                "bandwidth": signal.get("bandwidth_hz"),
                "spectral_entropy": signal.get("spectral_entropy"),
                "burst_period_ms": signal.get("burst_period_ms"),
                "hopping_pattern": signal.get("hopping_pattern"),
            },
            "temporal": {
                "persistence_s": temporal.get("persistence_s"),
                "repeat_count": temporal.get("repeat_count"),
            },
            "provenance": provenance,
        }
        if embedding is not None:
            event["embedding"] = embedding
        if observation.get("lat") is not None and observation.get("lon") is not None:
            event["location"] = {
                "lat": observation.get("lat"),
                "lon": observation.get("lon"),
                "alt_m": observation.get("alt_m", 0.0),
            }
        return event

    def emit_detection_event(self, detection: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.stream_emitter is None:
            return None
        return self.stream_emitter.emit(detection)

    def emit_observation(self, observation: Dict[str, Any], *, ctx: Any = None) -> Optional[Dict[str, Any]]:
        if ctx is None or self.writebus_provider is None:
            return None

        bus = self.writebus_provider()
        from writebus import GraphOp

        rf = dict(observation.get("rf") or {})
        signal = rf.get("signal") or {}
        mission_id = observation.get("mission_id")
        sensor_id = str(observation.get("sensor_id") or "rf-sensor")
        sensor_node_id = sensor_id if sensor_id.startswith("sensor:") else f"sensor:{sensor_id}"
        rf_node_id = str(observation.get("rf_node_id") or "")
        observation_id = str(observation.get("observation_id") or "")
        source = str(observation.get("source") or "rfuav_inference_service")

        sensor_node = {
            "id": sensor_node_id,
            "kind": "sensor",
            "labels": {
                "missionId": mission_id,
                "platform": observation.get("platform") or observation.get("source_platform") or "rf_sensor",
            },
            "metadata": {
                "source": source,
                "role": "rf_sensor",
                "observer_id": sensor_id,
                "obs_class": "observed",
            },
        }
        rf_node = {
            "id": rf_node_id,
            "kind": "rf_emitter",
            "labels": {
                "missionId": mission_id,
                "rf_class": rf.get("class"),
                "rf_subtype": rf.get("subtype"),
            },
            "metadata": {
                "source": source,
                "observed": True,
                "obs_class": "observed",
                "sensor_id": sensor_id,
                "observation_id": observation_id,
                "rf": rf,
                "frequency_mhz": observation.get("frequency_mhz"),
                "bandwidth_mhz": observation.get("bandwidth_mhz"),
                "power_dbm": observation.get("power_dbm"),
                "burst_period_ms": signal.get("burst_period_ms"),
                "spectral_entropy": signal.get("spectral_entropy"),
            },
        }
        if observation.get("lat") is not None and observation.get("lon") is not None:
            sensor_node["position"] = [observation.get("lat"), observation.get("lon"), observation.get("alt_m") or 0.0]
            rf_node["position"] = [observation.get("lat"), observation.get("lon"), observation.get("alt_m") or 0.0]

        observed_edge_id = f"rfobs:{_stable_id(sensor_node_id, rf_node_id, observation_id)}"
        observed_edge = {
            "id": observed_edge_id,
            "kind": "RF_EMITTER_OBSERVED",
            "nodes": [sensor_node_id, rf_node_id],
            "weight": observation.get("rfuav_confidence", observation.get("confidence", 0.0)),
            "labels": {
                "missionId": mission_id,
                "evidence": "OBSERVED",
            },
            "metadata": {
                "source": source,
                "obs_class": "observed",
                "observation_id": observation_id,
                "rf": rf,
            },
            "timestamp": observation.get("timestamp"),
        }

        result = bus.commit(
            entity_id=observation_id,
            entity_type="RF_EMITTER_OBSERVATION",
            entity_data={
                "observation": observation,
                "rf": rf,
                "sensor_id": sensor_id,
                "rf_node_id": rf_node_id,
            },
            graph_ops=[
                GraphOp(event_type="NODE_UPDATE", entity_id=sensor_node_id, entity_data=sensor_node),
                GraphOp(event_type="NODE_UPDATE", entity_id=rf_node_id, entity_data=rf_node),
                GraphOp(event_type="EDGE_UPDATE", entity_id=observed_edge_id, entity_data=observed_edge),
            ],
            ctx=ctx,
            persist=True,
            audit=True,
        )
        return {
            "ok": bool(getattr(result, "ok", False)),
            "entity_id": getattr(result, "entity_id", observation_id),
            "entity_type": getattr(result, "entity_type", "RF_EMITTER_OBSERVATION"),
            "graph_applied": bool(getattr(result, "graph_applied", False)),
            "persisted": bool(getattr(result, "persisted", False)),
            "errors": list(getattr(result, "errors", []) or []),
        }

    def _run_model(self, sample: Any, *, mode: str) -> Dict[str, Any]:
        if self.model is None:
            raise RuntimeError("RFUAV model is not configured")
        method_name = f"infer_{mode}"
        method = getattr(self.model, method_name, None)
        if callable(method):
            return dict(method(sample) or {})
        infer = getattr(self.model, "infer", None)
        if callable(infer):
            return dict(infer(sample) or {})
        if callable(self.model):
            return dict(self.model(sample) or {})
        raise RuntimeError(f"RFUAV model does not support {mode} inference")

    def _build_observation(
        self,
        payload: Dict[str, Any],
        inference: Dict[str, Any],
        *,
        sensor_id: str,
        timestamp: float,
        confidence: float,
    ) -> Dict[str, Any]:
        sensor_context = dict(payload.get("sensor_context") or {})
        location = dict(payload.get("location") or {})
        rf_payload = dict(payload.get("rf") or inference.get("rf") or {})
        label = str(
            _coalesce(
                rf_payload.get("raw_label"),
                rf_payload.get("subtype"),
                rf_payload.get("class"),
                inference.get("label"),
                inference.get("prediction"),
                inference.get("class_name"),
                inference.get("drone_name"),
                inference.get("name"),
                "unknown",
            )
        )
        subtype = _slug(_coalesce(rf_payload.get("subtype"), inference.get("subtype"), inference.get("variant"), label)) or "unknown"
        rf_class = str(_coalesce(inference.get("rf_class"), rf_payload.get("class"), _classify_label(label)))
        features = dict(inference.get("features") or {})
        signal = dict(payload.get("signal") or inference.get("signal") or {})
        temporal = dict(payload.get("temporal") or inference.get("temporal") or {})

        center_freq_hz = _normalize_frequency_hz(
            _coalesce(
                signal.get("center_freq"),
                signal.get("center_freq_hz"),
                payload.get("center_freq"),
                features.get("center_freq_hz"),
                features.get("center_freq"),
                inference.get("center_freq_hz"),
                inference.get("center_freq"),
                inference.get("frequency_hz"),
                inference.get("frequency"),
                inference.get("middle_frequency_hz"),
                inference.get("middle_frequency"),
                features.get("Middle_Frequency"),
            )
        )
        bandwidth_hz = _normalize_frequency_hz(
            _coalesce(
                signal.get("bandwidth"),
                signal.get("bandwidth_hz"),
                payload.get("bandwidth"),
                features.get("bandwidth_hz"),
                features.get("bandwidth"),
                inference.get("bandwidth_hz"),
                inference.get("bandwidth"),
            )
        )
        burst_period_ms = _safe_float(
            _coalesce(
                signal.get("burst_period_ms"),
                signal.get("hop_period_ms"),
                features.get("burst_period_ms"),
                features.get("FHSPP"),
                inference.get("burst_period_ms"),
                inference.get("hop_period_ms"),
            ),
            0.0,
        )
        spectral_entropy = _clamp(
            _safe_float(
                _coalesce(
                    signal.get("spectral_entropy"),
                    features.get("spectral_entropy"),
                    inference.get("spectral_entropy"),
                    inference.get("entropy_score"),
                ),
                0.5,
            )
        )
        persistence_s = max(
            0.0,
            _safe_float(
                _coalesce(
                    temporal.get("persistence_s"),
                    temporal.get("duration_s"),
                    inference.get("persistence_s"),
                    inference.get("duration_s"),
                ),
                0.0,
            ),
        )
        repeat_count = max(
            0,
            int(
                _safe_float(
                    _coalesce(
                        temporal.get("repeat_count"),
                        features.get("repeat_count"),
                        inference.get("repeat_count"),
                        inference.get("burst_count"),
                    ),
                    0.0,
                )
            ),
        )

        rf = {
            "class": rf_class,
            "subtype": subtype,
            "confidence": round(_clamp(_safe_float(_coalesce(rf_payload.get("confidence"), confidence), 0.0)), 4),
            "raw_label": label,
            "signal": {
                "bandwidth_hz": round(bandwidth_hz, 3) if bandwidth_hz is not None else None,
                "center_freq_hz": round(center_freq_hz, 3) if center_freq_hz is not None else None,
                "hopping_pattern": _coalesce(
                    signal.get("hopping_pattern"),
                    features.get("hopping_pattern"),
                    inference.get("hopping_pattern"),
                    "adaptive" if burst_period_ms else "unknown",
                ),
                "burst_period_ms": round(burst_period_ms, 3) if burst_period_ms else None,
                "spectral_entropy": round(spectral_entropy, 4),
            },
            "temporal": {
                "persistence_s": round(persistence_s, 3),
                "repeat_count": repeat_count,
            },
            "model": {
                "name": str(_coalesce(payload.get("model_name"), inference.get("model_name"), "RFUAV")),
                "version": str(_coalesce(payload.get("model_version"), inference.get("model_version"), payload.get("provenance"), inference.get("provenance"), "unknown")),
            },
        }

        lat = _coalesce(payload.get("lat"), location.get("lat"), sensor_context.get("lat"))
        lon = _coalesce(payload.get("lon"), payload.get("lng"), location.get("lon"), location.get("lng"), sensor_context.get("lon"))
        alt_m = _coalesce(payload.get("alt_m"), payload.get("alt"), location.get("alt_m"), location.get("alt"), sensor_context.get("alt_m"))
        mission_id = _coalesce(payload.get("mission_id"), payload.get("missionId"))
        rf_node_id = str(
            _coalesce(
                payload.get("rf_node_id"),
                payload.get("rfNodeId"),
                f"rf:{sensor_id}:{_stable_id(sensor_id, rf_class, subtype, int(timestamp * 10), center_freq_hz)}",
            )
        )
        observation_id = str(
            _coalesce(
                payload.get("detection_id"),
                payload.get("observation_id"),
                payload.get("id"),
                f"rfuavobs:{_stable_id(rf_node_id, timestamp, sensor_id)}",
            )
        )

        return {
            "observation_id": observation_id,
            "rf_node_id": rf_node_id,
            "sensor_id": sensor_id,
            "timestamp": timestamp,
            "mission_id": mission_id,
            "source": str(_coalesce(payload.get("source"), "rfuav_inference_service")),
            "source_platform": _coalesce(payload.get("platform"), sensor_context.get("platform")),
            "observer_id": sensor_id,
            "observed": True,
            "obs_class": "observed",
            "forecast": False,
            "event_type": payload.get("event_type"),
            "frequency_mhz": round(center_freq_hz / 1_000_000.0, 6) if center_freq_hz is not None else None,
            "bandwidth_mhz": round(bandwidth_hz / 1_000_000.0, 6) if bandwidth_hz is not None else None,
            "power_dbm": _safe_float(_coalesce(inference.get("power_dbm"), inference.get("power"), payload.get("power_dbm")), 0.0) or None,
            "modulation": _coalesce(inference.get("modulation"), inference.get("waveform"), rf_class),
            "burst_period_ms": round(burst_period_ms, 3) if burst_period_ms else None,
            "entropy_score": round(spectral_entropy, 4),
            "lat": _safe_float(lat, None) if lat is not None else None,
            "lon": _safe_float(lon, None) if lon is not None else None,
            "alt_m": _safe_float(alt_m, 0.0) if alt_m is not None else 0.0,
            "rfuav_label": label,
            "rfuav_confidence": round(confidence, 4),
            "rf": rf,
            "supporting_evidence": {
                "source_model": "RFUAV",
                "features": {
                    key: value
                    for key, value in {
                        "label": label,
                        "subtype": subtype,
                        "center_freq_hz": rf["signal"]["center_freq_hz"],
                        "bandwidth_hz": rf["signal"]["bandwidth_hz"],
                        "hopping_pattern": rf["signal"]["hopping_pattern"],
                        "burst_period_ms": rf["signal"]["burst_period_ms"],
                        "spectral_entropy": rf["signal"]["spectral_entropy"],
                        "persistence_s": rf["temporal"]["persistence_s"],
                        "repeat_count": rf["temporal"]["repeat_count"],
                    }.items()
                    if value is not None
                },
            },
            "labels": {
                "missionId": mission_id,
                "rf_class": rf_class,
                "rf_subtype": subtype,
                "obs_class": "observed",
            },
        }
