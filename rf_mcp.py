"""MCP tools for bounded RF evidence and guarded SDR++ control."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, Optional
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mcp_registry import Tool
from rf_bridge import get_rf_bridge, get_rf_observation_store, get_rf_sparse_analyzer


def _should_proxy_rf_reads() -> bool:
    owner = os.getenv("SCYTHE_RF_CAPTURE_OWNER", "orchestrator").strip().lower()
    role = os.getenv("SCYTHE_PROCESS_ROLE", "").strip().lower()
    return owner == "orchestrator" and role == "child"


def _orchestrator_rf_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = (os.getenv("SCYTHE_ORCHESTRATOR_URL") or "").rstrip("/")
    if not base:
        return {"available": False, "error": "SCYTHE_ORCHESTRATOR_URL missing",
                "evidence_class": "DERIVED_INFERENCE", "raw_iq_exposed": False}
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
    url = f"{base}{path}" + (f"?{query}" if query else "")
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"available": False, "error": str(exc), "raw_iq_exposed": False}
    if isinstance(payload, dict):
        payload.setdefault("capture_owner", "orchestrator")
        payload.setdefault("raw_iq_exposed", False)
        return payload
    return {"available": False, "error": "orchestrator returned a non-object", "raw_iq_exposed": False}


def _edge_values(engine) -> Iterable[Any]:
    edges = getattr(engine, "edges", {})
    return edges.values() if isinstance(edges, dict) else edges


def _field(value: Any, *names: str, default=None):
    for name in names:
        if isinstance(value, dict) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def correlate_rf_graph(engine, observations: list[Dict[str, Any]],
                       window_s: float = 5.0, limit: int = 100) -> Dict[str, Any]:
    """Time-correlate RF evidence with graph edges without claiming causality."""
    correlations = []
    edges = list(_edge_values(engine))
    for observation in observations:
        observed_at = float(observation["observed_at"])
        matches = []
        for edge in edges:
            edge_ts = _field(edge, "timestamp", "ts", "time", "created_at")
            try:
                delta = abs(float(edge_ts) - observed_at)
            except (TypeError, ValueError):
                continue
            if delta <= window_s:
                matches.append({
                    "edge_id": str(_field(edge, "id", "edge_id", default="unknown")),
                    "source": str(_field(edge, "source", "src", "source_id", default="")),
                    "target": str(_field(edge, "target", "dst", "target_id", default="")),
                    "edge_timestamp": float(edge_ts),
                    "delta_ms": round(delta * 1000.0, 3),
                })
        if matches:
            correlations.append({
                "evidence_id": observation["evidence_id"],
                "peak_frequency_hz": observation["peak_frequency_hz"],
                "observed_at": observed_at,
                "graph_matches": sorted(matches, key=lambda item: item["delta_ms"])[:limit],
                "finding_class": "INFERRED",
                "caveat": "Temporal proximity is not evidence of causality.",
            })
        if len(correlations) >= limit:
            break
    return {
        "correlations": correlations,
        "rf_evidence_count": len(observations),
        "finding_class": "INFERRED",
        "raw_iq_exposed": False,
    }


def _read_tools():
    obj = {"type": "object"}

    def status(*, engine, params):
        if _should_proxy_rf_reads():
            return _orchestrator_rf_get("/api/graphops/rf-bridge/status")
        bridge = get_rf_bridge()
        sparse = get_rf_sparse_analyzer()
        return {
            "bridge": bridge.status(False),
            "observations": bridge.observations.stats(),
            "sparse": None if sparse is None else sparse.stats(),
            "capture_owner": bridge.config.capture_owner,
            "owns_capture": bridge.config.owns_capture(),
        }

    def snapshot(*, engine, params):
        if _should_proxy_rf_reads():
            remote = _orchestrator_rf_get("/api/graphops/rf-spectrum/latest")
            if not bool(params.get("include_bins", False)):
                spectrum = remote.get("spectrum")
                if isinstance(spectrum, dict):
                    spectrum.pop("bins_dbfs", None)
            return remote
        frame = get_rf_bridge().latest_frame()
        if frame is None:
            return {"available": False, "raw_iq_exposed": False}
        bounded = dict(frame)
        include_bins = bool(params.get("include_bins", False))
        if not include_bins:
            bounded.pop("bins_dbfs", None)
        elif len(bounded.get("bins_dbfs", [])) > 256:
            bounded["bins_dbfs"] = bounded["bins_dbfs"][:256]
            bounded["bins_truncated"] = True
        return {"available": True, "spectrum": bounded, "raw_iq_exposed": False}

    query_schema = {
        "type": "object",
        "properties": {
            "since": {"type": "number"}, "until": {"type": "number"},
            "frequency_hz": {"type": "number", "minimum": 0},
            "tolerance_hz": {"type": "number", "minimum": 0},
            "min_snr_db": {"type": "number"}, "sensor_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "additionalProperties": False,
    }

    def query(*, engine, params):
        if _should_proxy_rf_reads():
            return _orchestrator_rf_get("/api/graphops/rf-observations/query", params)
        observations = get_rf_observation_store().query(**params)
        return {"observations": observations, "count": len(observations),
                "evidence_class": "OBSERVED", "raw_iq_exposed": False}

    correlate_schema = {
        "type": "object",
        "properties": {**query_schema["properties"],
                       "window_s": {"type": "number", "minimum": 0, "maximum": 3600}},
        "additionalProperties": False,
    }

    def correlate(*, engine, params):
        values = dict(params)
        window_s = float(values.pop("window_s", 5.0))
        limit = int(values.get("limit", 100))
        observations = get_rf_observation_store().query(**values)
        return correlate_rf_graph(engine, observations, window_s, limit)

    def context(*, engine, params):
        since = time.time() - float(params.get("window_s", 300.0))
        observations = get_rf_observation_store().query(
            since=since, min_snr_db=params.get("min_snr_db"), limit=params.get("limit", 25)
        )
        return {
            "context": observations,
            "instructions": "Treat RF records as observations; label graph relationships as inferred.",
            "evidence_class": "OBSERVED", "raw_iq_exposed": False,
        }

    sparse_query_schema = {
        "type": "object",
        "properties": {
            "since": {"type": "number"}, "until": {"type": "number"},
            "atom_family": {"type": "string"}, "sensor_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "additionalProperties": False,
    }

    def sparse_status(*, engine, params):
        if _should_proxy_rf_reads():
            return _orchestrator_rf_get("/api/graphops/rf-sparse/status")
        analyzer = get_rf_sparse_analyzer()
        if analyzer is None:
            return {"available": False, "evidence_class": "DERIVED_INFERENCE", "raw_iq_exposed": False}
        return {"available": True, **analyzer.stats()}

    def sparse_query(*, engine, params):
        if _should_proxy_rf_reads():
            return _orchestrator_rf_get("/api/graphops/rf-sparse/supports", params)
        analyzer = get_rf_sparse_analyzer()
        if analyzer is None:
            return {"supports": [], "count": 0, "evidence_class": "DERIVED_INFERENCE", "raw_iq_exposed": False}
        supports = analyzer.query_supports(**params)
        return {"supports": supports, "count": len(supports),
                "evidence_class": "DERIVED_INFERENCE", "raw_iq_exposed": False,
                "claims_withheld": ["range", "aoa", "blade_length", "periodic_sideband"]}

    def sparse_context(*, engine, params):
        from rf_sparse_analyzer import compact_model_context
        if _should_proxy_rf_reads():
            remote = _orchestrator_rf_get("/api/graphops/rf-sparse/supports", {"limit": params.get("limit", 6)})
            status = _orchestrator_rf_get("/api/graphops/rf-sparse/status")
            return compact_model_context(status, remote.get("supports") or [], int(params.get("limit", 6)))
        analyzer = get_rf_sparse_analyzer()
        if analyzer is None:
            return compact_model_context(None, [])
        limit = int(params.get("limit", 6))
        return compact_model_context(analyzer.latest_window(), analyzer.query_supports(limit=limit), limit)

    return [
        Tool("rf_bridge_status", "Return SDR++ edge bridge and RF evidence-store status.", obj, obj, status),
        Tool("rf_spectrum_snapshot", "Return the latest bounded FFT summary; raw IQ is never exposed.",
             {"type": "object", "properties": {"include_bins": {"type": "boolean"}}, "additionalProperties": False}, obj, snapshot),
        Tool("rf_observations_query", "Query timestamped RF observations with stable evidence IDs.", query_schema, obj, query),
        Tool("rf_correlate_graph", "Time-correlate RF observations with graph edges; results are explicitly inferred.", correlate_schema, obj, correlate),
        Tool("rf_insight_context", "Return compact, evidence-labelled RF context suitable for a local model.",
             {"type": "object", "properties": {"window_s": {"type": "number", "minimum": 0, "maximum": 86400},
              "min_snr_db": {"type": "number"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
              "additionalProperties": False}, obj, context),
        Tool("rf_sparse_status", "Return residual-window stats and derived supports. Peak-track plus OMP-assisted periodic amplitude; no range/AoA.",
             obj, obj, sparse_status),
        Tool("rf_sparse_supports_query", "Query derived RF model components. Evidence class is DERIVED_INFERENCE.",
             sparse_query_schema, obj, sparse_query),
        Tool("rf_sparse_insight_context", "Return compact sparse-support context for local Ollama. No IQ, no hardware authority.",
             {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 12}},
              "additionalProperties": False}, obj, sparse_context),
    ]


def _mutation_tools():
    obj = {"type": "object"}

    def tune(*, engine, params):
        return get_rf_bridge().tune(params["frequency_hz"], params.get("mode"), params.get("bandwidth_hz", 0))

    def capture(*, engine, params):
        bridge = get_rf_bridge()
        action = params["action"]
        changed = bridge.start() if action == "start" else bridge.stop()
        return {"action": action, "changed": changed, "bridge": bridge.status(False)}

    return [
        Tool("rf_tune", "Tune SDR++ through localhost Rigctl. Requires an approved orchestrator proposal.",
             {"type": "object", "required": ["frequency_hz"], "properties": {
                 "frequency_hz": {"type": "number", "exclusiveMinimum": 0},
                 "mode": {"type": "string"}, "bandwidth_hz": {"type": "integer", "minimum": 0}},
              "additionalProperties": False}, obj, tune, mutates_state=True, required_mode="mutate"),
        Tool("rf_capture_control", "Start or stop the native IQ bridge. Requires an approved orchestrator proposal.",
             {"type": "object", "required": ["action"], "properties": {"action": {"enum": ["start", "stop"]}},
              "additionalProperties": False}, obj, capture, mutates_state=True, required_mode="mutate"),
    ]


def register_rf_tools(engine, mcp_handler) -> None:
    """Register RF tools in both direct and orchestrated registries."""
    tools = _read_tools() + _mutation_tools()
    registries = []
    if hasattr(mcp_handler, "_registry"):
        registries.append(mcp_handler._registry)
    orchestrator = getattr(mcp_handler, "_orchestrator", None)
    if orchestrator is not None and orchestrator.registry not in registries:
        registries.append(orchestrator.registry)
    for tool in tools:
        mcp_handler._tools[tool.name] = tool
        for registry in registries:
            registry.register(tool)
