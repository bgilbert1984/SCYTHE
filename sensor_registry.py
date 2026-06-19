# sensor_registry_refactored.py
"""
Sensor Registry (WriteBus refactor)

This refactor migrates the original sensor_registry.py chokepoint into the new
WriteBus pipeline so the "nothing bypasses provenance + graph emission" rule
becomes mechanically true.

Key changes vs the original sensor_registry.py fileciteturn142file0:
- NO direct calls to OperatorSessionManager.publish_to_room(...)
- NO direct calls to HypergraphEngine.add_node/add_edge/update_node/remove_edge...
- All cross-layer writes are expressed as GraphOps and committed via bus().commit(...)
- LPI IQ-window integration emits graph entities via GraphOps; optional room persistence remains opt-in

Public API (backwards-compatible):
- upsert_sensor(sensor, ...)
- assign_sensor(sensor_id, recon_entity_id, ...)
- emit_activity(sensor_id, kind, payload, ...)

Preferred API (new):
- upsert_sensor(sensor, ctx=WriteContext(...))
- assign_sensor(..., ctx=WriteContext(...))
- emit_activity(..., ctx=WriteContext(...))
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, List, Tuple, Iterable
import json
import time
import hashlib

import numpy as np

from writebus import bus, init_writebus, WriteContext, GraphOp

logger = logging.getLogger("SensorRegistry")

# LPI Frontend import (unchanged)
try:
    from RF_QUANTUM_SCYTHE.SignalIntelligence.lpi_frontend import (
        LPIFrontend,
        SignalObservation,
        create_lpi_graph_entities,
        process_iq_with_lpi
    )
    LPI_AVAILABLE = True
except ImportError:
    try:
        from lpi_frontend import LPIFrontend, SignalObservation, create_lpi_graph_entities, process_iq_with_lpi
        LPI_AVAILABLE = True
    except ImportError:
        logger.warning("LPI Frontend not available for sensor_registry")
        LPI_AVAILABLE = False


Json = Dict[str, Any]


def _coalesce(*vals):
    for v in vals:
        if v is not None and v != "":
            return v
    return None


def _safe_hash(obj: Any) -> str:
    try:
        raw = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    except Exception:
        raw = repr(obj).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _as_dict(x: Any) -> Dict[str, Any]:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    try:
        return dict(x)
    except Exception:
        return getattr(x, "__dict__", {}) or {}


def _norm_position(sensor: Dict[str, Any]) -> Optional[List[float]]:
    """
    UI expects node position as [lat, lon, alt] and renders fromDegrees(lon, lat, alt).
    """
    loc = sensor.get("location") or sensor.get("geo") or {}
    lat = _coalesce(sensor.get("lat"), loc.get("lat"), loc.get("latitude"))
    lon = _coalesce(sensor.get("lon"), sensor.get("lng"), loc.get("lon"), loc.get("lng"), loc.get("longitude"))
    alt = _coalesce(sensor.get("alt"), loc.get("alt"), loc.get("alt_m"), loc.get("altitude_m"), 0)
    if lat is None or lon is None:
        return sensor.get("position")  # already normalized upstream
    try:
        return [float(lat), float(lon), float(alt or 0)]
    except Exception:
        return sensor.get("position")


def _norm_labels(obj: Dict[str, Any], mission_id: Optional[str] = None) -> Dict[str, Any]:
    labels = dict(obj.get("labels") or {})
    mid = _coalesce(mission_id, obj.get("mission_id"), obj.get("missionId"), labels.get("missionId"))
    if mid:
        labels["missionId"] = str(mid)
    tags = obj.get("tags") or obj.get("tag") or labels.get("tags")
    if tags:
        labels["tags"] = tags
    role = obj.get("role") or labels.get("role")
    if role:
        labels["role"] = role
    return labels


def _norm_sensor_id(sensor: Dict[str, Any]) -> str:
    sid = _coalesce(sensor.get("sensor_id"), sensor.get("sensorId"), sensor.get("id"), sensor.get("name"))
    if not sid:
        sid = f"sensor_{int(time.time() * 1000)}"
    return str(sid)


def _sensor_node_id(sensor_id: str) -> str:
    sid = str(sensor_id)
    return sid if sid.startswith("sensor:") else f"sensor:{sid}"


def _recon_node_id(recon_entity_id: str) -> str:
    # Align with recon_registry: "recon:<ENTITY-0001>"
    rid = str(recon_entity_id)
    return rid if rid.startswith("recon:") else f"recon:{rid}"


def _ctx_from_legacy(
    *,
    operator: Any = None,
    operator_id: Optional[str] = None,
    session_token: Optional[str] = None,
    room_name: Optional[str] = None,
    mission_id: Optional[str] = None,
    source: str = "manual_ui",
) -> WriteContext:
    # NOTE: WriteContext hashes session_token inside ctx.provenance(); safe to pass token
    return WriteContext(
        room_name=room_name or "Global",
        mission_id=mission_id,
        operator=operator,
        operator_id=operator_id,
        session_token=session_token,
        source=source,
    )


def _graphop_from_entity(entity: Dict[str, Any]) -> Optional[GraphOp]:
    """
    Best-effort converter from various dict shapes into GraphOp.
    LPI helper may return entities with:
      - {'id': ..., 'kind': ..., ...} nodes
      - {'id': ..., 'nodes': [...], 'kind': ...} edges
      - {'entity_id': ...} / {'edge_id': ...} legacy fields
    """
    if not isinstance(entity, dict):
        return None
    eid = entity.get("id") or entity.get("entity_id") or entity.get("edge_id")
    if not eid:
        return None
    # edge heuristic: presence of 'nodes' list
    if "nodes" in entity and isinstance(entity.get("nodes"), list):
        return GraphOp(event_type="EDGE_UPDATE", entity_id=str(eid), entity_data=entity)
    return GraphOp(event_type="NODE_UPDATE", entity_id=str(eid), entity_data=entity)


@dataclass
class SensorRegistry:
    """
    WriteBus-native registry.
    """
    global_room_name: str = "Global"

    # ----------------------------
    # Sensors
    # ----------------------------

    def upsert_sensor(
        self,
        sensor: Dict[str, Any],
        *,
        ctx: Optional[WriteContext] = None,
        operator: Any = None,
        operator_id: Optional[str] = None,
        session_token: Optional[str] = None,
        room_name: Optional[str] = None,
        mission_id: Optional[str] = None,
        persist_to_room: bool = True,
        audit: bool = True,
    ) -> Dict[str, Any]:
        sensor = dict(sensor or {})
        sid = _norm_sensor_id(sensor)
        node_id = _sensor_node_id(sid)

        mid = _coalesce(mission_id, sensor.get("mission_id"), sensor.get("missionId"), (sensor.get("labels") or {}).get("missionId"))
        labels = _norm_labels(sensor, mission_id=mid)
        position = _norm_position(sensor)

        canonical = {
            **sensor,
            "sensor_id": sid,
            "id": sensor.get("id", sid),
            "node_id": node_id,
            "type": "sensor",
            "updated_at": time.time(),
        }

        node = {
            "id": node_id,
            "kind": "sensor",
            "position": position,
            "labels": labels,
            "metadata": {
                "sensor": canonical,
                "tx": sensor.get("tx") or sensor.get("transmit") or {},
                "rx": sensor.get("rx") or sensor.get("receive") or {},
                "status": sensor.get("status") or "active",
            },
        }

        wctx = ctx or _ctx_from_legacy(
            operator=operator,
            operator_id=operator_id,
            session_token=session_token,
            room_name=room_name or self.global_room_name,
            mission_id=mid,
            source="sensor_upsert",
        )

        res = bus().commit(
            entity_id=node_id,
            entity_type="SENSOR",
            entity_data={"id": node_id, "type": "SENSOR", "sensor": canonical, "node": node, "timestamp": time.time()},
            graph_ops=[GraphOp(event_type="NODE_UPDATE", entity_id=node_id, entity_data=node)],
            ctx=wctx,
            persist=bool(persist_to_room),
            audit=bool(audit),
        )

        return {
            "ok": res.ok,
            "sensor_id": sid,
            "node_id": node_id,
            "persisted": res.persisted,
            "graph_applied": res.graph_applied,
            "errors": res.errors,
            "write_debug": res.debug,
            "node": node,
        }

    def assign_sensor(
        self,
        sensor_id: str,
        recon_entity_id: str,
        *,
        ctx: Optional[WriteContext] = None,
        operator: Any = None,
        operator_id: Optional[str] = None,
        session_token: Optional[str] = None,
        room_name: Optional[str] = None,
        mission_id: Optional[str] = None,
        mode: str = "txrx",
        metadata: Optional[Dict[str, Any]] = None,
        persist_to_room: bool = True,
        audit: bool = True,
    ) -> Dict[str, Any]:
        s_node = _sensor_node_id(str(sensor_id))
        r_node = _recon_node_id(str(recon_entity_id))

        mid = mission_id
        labels = {"missionId": str(mid)} if mid else {}

        edge_id = f"edge:sensor_assigned:{s_node}->{r_node}".replace(" ", "_")
        now = time.time()

        edge = {
            "id": edge_id,
            "kind": "sensor_assigned",
            "nodes": [s_node, r_node],
            "weight": 1.0,
            "labels": labels,
            "metadata": {
                "mode": mode,
                "assigned_at": now,
                **(metadata or {}),
            },
            "timestamp": now,
        }

        # Ensure stubs exist (safe upsert; avoids edge rendering issues)
        stub_ops: List[GraphOp] = [
            GraphOp(event_type="NODE_UPDATE", entity_id=s_node, entity_data={"id": s_node, "kind": "sensor_stub", "labels": {"sensor_id": str(sensor_id)}}),
            GraphOp(event_type="NODE_UPDATE", entity_id=r_node, entity_data={"id": r_node, "kind": "recon_stub", "labels": {"entity_id": str(recon_entity_id)}}),
        ]

        wctx = ctx or _ctx_from_legacy(
            operator=operator,
            operator_id=operator_id,
            session_token=session_token,
            room_name=room_name or self.global_room_name,
            mission_id=mid,
            source="sensor_assign",
        )

        res = bus().commit(
            entity_id=edge_id,
            entity_type="SENSOR_ASSIGNMENT",
            entity_data=edge,  # durable is fine as the edge dict (small, low volume)
            graph_ops=stub_ops + [GraphOp(event_type="EDGE_UPDATE", entity_id=edge_id, entity_data=edge)],
            ctx=wctx,
            persist=bool(persist_to_room),
            audit=bool(audit),
        )

        return {
            "ok": res.ok,
            "edge_id": edge_id,
            "persisted": res.persisted,
            "graph_applied": res.graph_applied,
            "errors": res.errors,
            "write_debug": res.debug,
            "edge": edge,
        }

    # ----------------------------
    # Activity + LPI IQ window
    # ----------------------------

    def emit_activity(
        self,
        sensor_id: str,
        kind: str,
        payload: Dict[str, Any],
        *,
        ctx: Optional[WriteContext] = None,
        operator: Any = None,
        operator_id: Optional[str] = None,
        session_token: Optional[str] = None,
        room_name: Optional[str] = None,
        mission_id: Optional[str] = None,
        persist_to_room: bool = False,
        audit: bool = False,
        lpi_config: Optional[Dict[str, Any]] = None,
        persist_lpi_entities: bool = False,
    ) -> Dict[str, Any]:
        """
        Default behavior:
          - graph firehose (persist_to_room=False) to avoid room DB spam.

        If kind == "iq_window" and LPI frontend is available:
          - process IQ window and emit the resulting nodes/edges as graph ops
          - optionally persist the LPI entities to the room (persist_lpi_entities=True)
        """
        payload = dict(payload or {})
        s_node = _sensor_node_id(str(sensor_id))

        mid = _coalesce(mission_id, payload.get("missionId"), payload.get("mission_id"))
        labels = {"missionId": str(mid)} if mid else {}

        wctx = ctx or _ctx_from_legacy(
            operator=operator,
            operator_id=operator_id,
            session_token=session_token,
            room_name=room_name or self.global_room_name,
            mission_id=mid,
            source="sensor_activity",
        )

        # Ensure sensor stub exists (safe)
        stub_op = GraphOp(event_type="NODE_UPDATE", entity_id=s_node, entity_data={"id": s_node, "kind": "sensor_stub", "labels": {"sensor_id": str(sensor_id)}})

        # ---- LPI IQ window path ----
        if kind == "iq_window" and LPI_AVAILABLE:
            return self._emit_iq_window_lpi(
                sensor_id=str(sensor_id),
                sensor_node=s_node,
                payload=payload,
                labels=labels,
                ctx=wctx,
                persist_to_room=bool(persist_to_room),
                persist_lpi_entities=bool(persist_lpi_entities),
                audit=bool(audit),
                lpi_config=lpi_config,
                stub_op=stub_op,
            )

        # ---- Standard activity edge ----
        related: List[str] = []
        if isinstance(payload.get("nodes"), list):
            related.extend([str(x) for x in payload["nodes"] if x])

        rid = _coalesce(payload.get("recon_entity_id"), payload.get("reconEntityId"), payload.get("entity_id"), payload.get("target_entity_id"))
        if rid:
            related.append(_recon_node_id(str(rid)))

        # unique, keep order
        seen = set([s_node])
        uniq_related: List[str] = []
        for n in related:
            if n and n not in seen:
                seen.add(n)
                uniq_related.append(n)

        activity_id = payload.get("activity_id") or payload.get("activityId")
        if activity_id:
            edge_id = f"edge:activity:{activity_id}"
        else:
            ts_ms = int(time.time() * 1000)
            edge_id = f"edge:activity:{kind}:{sensor_id}:{ts_ms}:{_safe_hash(payload)}"

        now = time.time()
        edge = {
            "id": edge_id,
            "kind": str(kind),
            "nodes": [s_node] + uniq_related,
            "weight": float(payload.get("weight", 1.0) or 1.0),
            "labels": labels,
            "metadata": {"payload": payload, "emitted_at": now},
            "timestamp": now,
        }

        res = bus().commit(
            entity_id=edge_id,
            entity_type="SENSOR_ACTIVITY",
            entity_data=edge,
            graph_ops=[stub_op, GraphOp(event_type="EDGE_UPDATE", entity_id=edge_id, entity_data=edge)],
            ctx=wctx,
            persist=bool(persist_to_room),
            audit=bool(audit),
        )

        return {
            "ok": res.ok,
            "edge_id": edge_id,
            "persisted": res.persisted,
            "graph_applied": res.graph_applied,
            "errors": res.errors,
            "write_debug": res.debug,
            "edge": edge,
        }

    def _emit_iq_window_lpi(
        self,
        *,
        sensor_id: str,
        sensor_node: str,
        payload: Dict[str, Any],
        labels: Dict[str, Any],
        ctx: WriteContext,
        persist_to_room: bool,
        persist_lpi_entities: bool,
        audit: bool,
        lpi_config: Optional[Dict[str, Any]],
        stub_op: GraphOp,
    ) -> Dict[str, Any]:
        # Extract IQ data from payload (mirrors original behavior)
        iq_data = None
        if "iq_data" in payload:
            iq_data = payload["iq_data"]
            if isinstance(iq_data, list):
                iq_data = np.array(iq_data, dtype=np.complex128)
        elif "iq_real" in payload and "iq_imag" in payload:
            iq_real = np.array(payload["iq_real"], dtype=np.float64)
            iq_imag = np.array(payload["iq_imag"], dtype=np.float64)
            iq_data = iq_real + 1j * iq_imag
        elif "samples" in payload:
            samples = payload["samples"]
            if isinstance(samples, list) and len(samples) > 0:
                if isinstance(samples[0], (list, tuple)) and len(samples[0]) == 2:
                    iq_data = np.array([s[0] + 1j * s[1] for s in samples], dtype=np.complex128)
                else:
                    iq_data = np.array(samples, dtype=np.complex128)

        if iq_data is None or len(iq_data) < 32:
            return {"ok": False, "error": "No valid IQ data", "sensor_id": sensor_id}

        config = lpi_config or {}
        sample_rate = payload.get("sample_rate", config.get("sample_rate", 1e6))

        lpi_frontend = LPIFrontend({
            "n_bands": config.get("n_bands", 16),
            "window_size": config.get("window_size", 64),
            "hop_size": config.get("hop_size", 32),
            "max_lag": config.get("max_lag", 8),
            "sample_rate": sample_rate,
            "detection_threshold": config.get("detection_threshold", 0.05),
        })

        observation = lpi_frontend.process_iq(
            iq_data,
            sensor_id=sensor_id,
            timestamp=payload.get("timestamp", time.time()),
            sample_rate=sample_rate,
        )

        # Even if no detection, emit a low-weight activity edge so the timeline isn't empty.
        if observation is None:
            return self.emit_activity(
                sensor_id=sensor_id,
                kind="iq_window",
                payload={**payload, "lpi_detected": False},
                ctx=ctx,
                persist_to_room=persist_to_room,
                audit=audit,
            )

        # Convert LPI observation into graph entities (nodes/edges)
        entities = create_lpi_graph_entities(observation, None)  # many implementations ignore engine param
        ops: List[GraphOp] = [stub_op]

        # Attach mission label if provided
        if labels.get("missionId"):
            mid = labels["missionId"]
            for ent in entities:
                if isinstance(ent, dict):
                    ent_labels = dict(ent.get("labels") or {})
                    ent_labels["missionId"] = mid
                    ent["labels"] = ent_labels

        # Convert to GraphOps
        for ent in entities:
            op = _graphop_from_entity(ent)
            if op:
                ops.append(op)

        # Also emit a high-level "activity" edge from sensor -> observation id (if present)
        obs_id = getattr(observation, "observation_id", None) or getattr(observation, "id", None)
        if obs_id:
            obs_node = str(obs_id)
            edge_id = f"edge:observed:{sensor_node}->{obs_node}"
            obs_edge = {
                "id": edge_id,
                "kind": "OBSERVED_BY",
                "nodes": [obs_node, sensor_node],
                "timestamp": time.time(),
                "labels": labels,
                "metadata": {"kind": "iq_window", "sample_rate": sample_rate},
            }
            ops.append(GraphOp(event_type="EDGE_UPDATE", entity_id=edge_id, entity_data=obs_edge))

        # Graph firehose commit (no room persistence by default)
        graph_batch_id = f"lpi:iq_window:{sensor_id}:{int(time.time()*1000)}"
        graph_commit = bus().commit(
            entity_id=graph_batch_id,
            entity_type="LPI_GRAPH_BATCH",
            entity_data={"id": graph_batch_id, "type": "LPI_GRAPH_BATCH", "sensor_id": sensor_id, "timestamp": time.time()},
            graph_ops=ops,
            ctx=ctx,
            persist=False,
            audit=False,
        )

        # Optional: persist a compact summary to the room (bounded)
        summary_entity = {
            "id": graph_batch_id,
            "type": "IQ_WINDOW_OBSERVATION",
            "sensor_id": sensor_id,
            "sample_rate": sample_rate,
            "timestamp": time.time(),
            "observation_id": obs_id,
            "top_hypothesis": (observation.hypotheses[0].to_dict() if getattr(observation, "hypotheses", None) else None),
        }

        persisted = None
        if persist_to_room:
            persisted = bus().commit(
                entity_id=graph_batch_id,
                entity_type="IQ_WINDOW_OBSERVATION",
                entity_data=summary_entity,
                graph_ops=[],  # already applied above
                ctx=ctx,
                persist=True,
                audit=audit,
            )

        # Optional: persist each generated LPI entity to the room (can be noisy)
        lpi_persist_results: List[Dict[str, Any]] = []
        if persist_to_room and persist_lpi_entities:
            for ent in entities:
                if not isinstance(ent, dict):
                    continue
                eid = ent.get("id") or ent.get("entity_id") or ent.get("edge_id")
                etype = ent.get("type") or ent.get("kind") or "LPI_ENTITY"
                if not eid:
                    continue
                r = bus().commit(
                    entity_id=str(eid),
                    entity_type=str(etype),
                    entity_data=ent,
                    graph_ops=[],  # already applied in graph batch
                    ctx=ctx,
                    persist=True,
                    audit=False,
                )
                lpi_persist_results.append({"id": eid, "type": etype, "ok": r.ok, "persisted": r.persisted, "errors": r.errors})

        return {
            "ok": graph_commit.ok,
            "sensor_id": sensor_id,
            "observation_id": obs_id,
            "graph_applied": graph_commit.graph_applied,
            "errors": graph_commit.errors,
            "persisted_summary": (persisted.persisted if persisted else False),
            "lpi_entities_persisted": lpi_persist_results,
            "write_debug": graph_commit.debug,
        }


# ----------------------------
# Module-level singleton API
# ----------------------------

_DEFAULT_REGISTRY: Optional[SensorRegistry] = None


def init_sensor_registry(
    operator_manager: Any = None,
    hypergraph: Any = None,
    *,
    global_room_name: str = "Global",
    ensure_writebus: bool = False,
) -> SensorRegistry:
    """
    Initialize registry. In a fully migrated server, WriteBus is already initialized and this
    should be called with operator_manager/hypergraph omitted.

    If ensure_writebus=True and bus() is not initialized, this will call init_writebus(opman, hg).
    """
    global _DEFAULT_REGISTRY
    if ensure_writebus:
        try:
            bus()
        except Exception:
            if operator_manager is None or hypergraph is None:
                raise RuntimeError("ensure_writebus=True requires operator_manager and hypergraph")
            init_writebus(operator_manager, hypergraph, default_room=global_room_name)
    _DEFAULT_REGISTRY = SensorRegistry(global_room_name=global_room_name)
    return _DEFAULT_REGISTRY


def _require_registry() -> SensorRegistry:
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = SensorRegistry()
    return _DEFAULT_REGISTRY


def upsert_sensor(sensor: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    return _require_registry().upsert_sensor(sensor, **kwargs)


def assign_sensor(sensor_id: str, recon_entity_id: str, **kwargs) -> Dict[str, Any]:
    return _require_registry().assign_sensor(sensor_id, recon_entity_id, **kwargs)


def emit_activity(sensor_id: str, kind: str, payload: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    return _require_registry().emit_activity(sensor_id, kind, payload, **kwargs)
