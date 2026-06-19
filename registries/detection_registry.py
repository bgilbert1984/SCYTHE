# registries/detection_registry.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import hashlib
import time
import threading

from writebus import bus, WriteContext, GraphOp

Json = Dict[str, Any]


def _coalesce(*vals):
    for v in vals:
        if v is not None and v != "":
            return v
    return None


def _stable_id(prefix: str, *parts: str, max_len: int = 96) -> str:
    raw = "|".join([prefix, *[str(p) for p in parts]])
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    base = f"{prefix}:{h}"
    return base if len(base) <= max_len else base[:max_len]


def _sensor_node_id(sensor_id: str) -> str:
    sid = str(sensor_id)
    # allow already-namespaced ids
    if sid.startswith("sensor:"):
        return sid
    return f"sensor:{sid}"


def _recon_node_id(recon_entity_id: str) -> str:
    rid = str(recon_entity_id)
    if rid.startswith("recon:"):
        return rid
    return f"recon:{rid}"


@dataclass
class DetectionRegistryConfig:
    # "firehose" always updates the rolling live edge (bounded graph growth)
    live_edge_kind: str = "DETECTION_LIVE"
    # durable summaries (bounded rate)
    persist_summaries: bool = True
    summary_bucket_s: int = 60          # aggregate window size
    flush_interval_s: int = 15          # flush summaries at most every N seconds
    flush_count: int = 50               # or every N detections per key
    max_recent: int = 20                # store last N recent detections in live metadata
    audit_live: bool = False            # avoid audit spam on firehose
    audit_summary: bool = True          # summaries are audit-worthy


@dataclass
class _AggState:
    bucket_start: int
    last_flush_ts: float
    count: int = 0
    conf_sum: float = 0.0
    conf_min: float = 1.0
    conf_max: float = 0.0
    last_conf: float = 0.0
    labels: Dict[str, int] = field(default_factory=dict)
    bytes_sum: int = 0
    recent: List[Json] = field(default_factory=list)


class DetectionRegistry:
    """
    Two-tier policy:
      (A) Graph firehose: always upserts a bounded "live detection" edge (no unbounded edge growth)
      (B) Durable summaries: periodically persists a compact DETECTION_SUMMARY entity to the room
    """

    def __init__(self, cfg: Optional[DetectionRegistryConfig] = None):
        self.cfg = cfg or DetectionRegistryConfig()
        self._lock = threading.Lock()
        # key: (room, mission_id, sensor_id, recon_entity_id, kind)
        self._agg: Dict[Tuple[str, str, str, str, str], _AggState] = {}

    def emit_detection(self, detection: Json, ctx: WriteContext) -> Json:
        """
        detection: {
          sensor_id, recon_entity_id, kind,
          confidence/score,
          freq_hz or band,
          bytes, hypothesis/label,
          payload: {...}
        }
        """
        now = time.time()
        det = dict(detection or {})

        sensor_id = _coalesce(det.get("sensor_id"), det.get("sensorId"))
        recon_entity_id = _coalesce(det.get("recon_entity_id"), det.get("reconEntityId"), det.get("entity_id"), det.get("entityId"))
        kind = str(_coalesce(det.get("kind"), det.get("type"), "detection"))

        if not sensor_id:
            raise ValueError("detection must include sensor_id")
        if not recon_entity_id:
            raise ValueError("detection must include recon_entity_id")

        confidence = float(_coalesce(det.get("confidence"), det.get("score"), 0.0) or 0.0)
        label = _coalesce(det.get("label"), det.get("hypothesis"), det.get("class")) or "unknown"
        bytes_n = int(_coalesce(det.get("bytes"), det.get("size_bytes"), 0) or 0)

        s_node = _sensor_node_id(str(sensor_id))
        r_node = _recon_node_id(str(recon_entity_id))

        # --- Tier A: bounded live edge upsert ---
        live_edge_id = _stable_id("edge:detection_live", s_node, r_node, kind)
        live_edge = {
            "id": live_edge_id,
            "kind": self.cfg.live_edge_kind,
            "nodes": [s_node, r_node],
            "timestamp": now,
            "labels": {
                "kind": kind,
                "confidence": confidence,
                "label": str(label),
            },
            "metadata": {
                "last_seen": now,
                "sensor_id": str(sensor_id),
                "recon_entity_id": str(recon_entity_id),
                "kind": kind,
                "confidence": confidence,
                "label": str(label),
                "bytes": bytes_n,
                "payload": det.get("payload") or {k: v for k, v in det.items() if k not in ("payload",)},
            }
        }

        # Optional: ensure minimal stubs exist (safe upsert)
        stub_ops: List[GraphOp] = [
            GraphOp(
                event_type="NODE_UPDATE",
                entity_id=s_node,
                entity_data={"id": s_node, "kind": "sensor_stub", "labels": {"sensor_id": str(sensor_id)}},
            ),
            GraphOp(
                event_type="NODE_UPDATE",
                entity_id=r_node,
                entity_data={"id": r_node, "kind": "recon_stub", "labels": {"entity_id": str(recon_entity_id)}},
            ),
        ]

        firehose_ops = stub_ops + [
            GraphOp(event_type="EDGE_UPDATE", entity_id=live_edge_id, entity_data=live_edge),
        ]

        # Attach recent ring buffer and rolling counts from aggregator (bounded)
        agg_info = self._update_aggregator(ctx, str(sensor_id), str(recon_entity_id), kind, confidence, str(label), bytes_n, now)
        live_edge["metadata"]["recent"] = agg_info.get("recent", [])
        live_edge["metadata"]["rolling"] = {k: v for k, v in agg_info.items() if k != "recent"}

        firehose_res = bus().commit(
            entity_id=live_edge_id,
            entity_type="DETECTION_LIVE",
            entity_data={"id": live_edge_id, "type": "DETECTION_LIVE", "edge": live_edge, "timestamp": now},
            graph_ops=firehose_ops,
            ctx=ctx,
            persist=False,
            audit=self.cfg.audit_live,
        )

        out = {
            "ok": firehose_res.ok,
            "live_edge_id": live_edge_id,
            "graph_applied": firehose_res.graph_applied,
            "errors": firehose_res.errors,
        }

        # --- Tier B: durable summary flush (bounded rate) ---
        summary = self._maybe_flush_summary(ctx, str(sensor_id), str(recon_entity_id), kind, now)
        if summary:
            out["summary"] = summary

        return out

    def _key(self, ctx: WriteContext, sensor_id: str, recon_entity_id: str, kind: str) -> Tuple[str, str, str, str, str]:
        room = ctx.room_name or "Global"
        mission = str(ctx.mission_id or "none")
        return (room, mission, sensor_id, recon_entity_id, kind)

    def _bucket_start(self, ts: float) -> int:
        b = int(ts) // int(self.cfg.summary_bucket_s)
        return b * int(self.cfg.summary_bucket_s)

    def _update_aggregator(
        self,
        ctx: WriteContext,
        sensor_id: str,
        recon_entity_id: str,
        kind: str,
        confidence: float,
        label: str,
        bytes_n: int,
        now: float,
    ) -> Json:
        k = self._key(ctx, sensor_id, recon_entity_id, kind)
        with self._lock:
            state = self._agg.get(k)
            bstart = self._bucket_start(now)
            if state is None or state.bucket_start != bstart:
                state = _AggState(bucket_start=bstart, last_flush_ts=now)
                self._agg[k] = state

            state.count += 1
            state.conf_sum += confidence
            state.conf_min = min(state.conf_min, confidence)
            state.conf_max = max(state.conf_max, confidence)
            state.last_conf = confidence
            state.bytes_sum += int(bytes_n or 0)
            state.labels[label] = state.labels.get(label, 0) + 1

            # recent ring buffer
            state.recent.append({"ts": now, "confidence": confidence, "label": label, "bytes": bytes_n})
            if len(state.recent) > self.cfg.max_recent:
                state.recent = state.recent[-self.cfg.max_recent :]

            avg = (state.conf_sum / state.count) if state.count else 0.0
            top_label = max(state.labels.items(), key=lambda kv: kv[1])[0] if state.labels else "unknown"

            return {
                "bucket_start": state.bucket_start,
                "count": state.count,
                "avg_conf": round(avg, 6),
                "min_conf": round(state.conf_min, 6),
                "max_conf": round(state.conf_max, 6),
                "last_conf": round(state.last_conf, 6),
                "bytes_sum": state.bytes_sum,
                "top_label": top_label,
                "recent": list(state.recent),
            }

    def _maybe_flush_summary(self, ctx: WriteContext, sensor_id: str, recon_entity_id: str, kind: str, now: float) -> Optional[Json]:
        if not self.cfg.persist_summaries:
            return None

        k = self._key(ctx, sensor_id, recon_entity_id, kind)
        with self._lock:
            state = self._agg.get(k)
            if state is None:
                return None

            # Flush rules: time-based OR count-based
            should = (now - state.last_flush_ts) >= float(self.cfg.flush_interval_s) or state.count >= int(self.cfg.flush_count)
            if not should:
                return None

            state.last_flush_ts = now

            avg = (state.conf_sum / state.count) if state.count else 0.0
            top = sorted(state.labels.items(), key=lambda kv: kv[1], reverse=True)[:5]

            summary_entity_id = _stable_id(
                "DETSUM",
                (ctx.room_name or "Global"),
                str(ctx.mission_id or "none"),
                sensor_id,
                recon_entity_id,
                kind,
                str(state.bucket_start),
            )
            summary_node_id = f"summary:{summary_entity_id}"

            s_node = _sensor_node_id(sensor_id)
            r_node = _recon_node_id(recon_entity_id)

            summary_payload = {
                "id": summary_entity_id,
                "type": "DETECTION_SUMMARY",
                "sensor_id": sensor_id,
                "recon_entity_id": recon_entity_id,
                "kind": kind,
                "bucket_start": state.bucket_start,
                "bucket_end": state.bucket_start + int(self.cfg.summary_bucket_s),
                "count": state.count,
                "avg_conf": round(avg, 6),
                "min_conf": round(state.conf_min, 6) if state.count else 0.0,
                "max_conf": round(state.conf_max, 6) if state.count else 0.0,
                "bytes_sum": state.bytes_sum,
                "top_labels": [{"label": a, "count": b} for a, b in top],
                "last_update": now,
            }

            summary_node = {
                "id": summary_node_id,
                "kind": "detection_summary",
                "labels": {
                    "kind": kind,
                    "count": state.count,
                    "avg_conf": round(avg, 6),
                },
                "metadata": summary_payload,
                "created_at": now,
            }

            # Edges: summary -> sensor, summary -> recon (for navigation)
            e1 = {
                "id": _stable_id("edge:summary_sensor", summary_node_id, s_node, kind),
                "kind": "SUMMARY_OF_SENSOR",
                "nodes": [summary_node_id, s_node],
                "timestamp": now,
            }
            e2 = {
                "id": _stable_id("edge:summary_recon", summary_node_id, r_node, kind),
                "kind": "SUMMARY_OF_RECON",
                "nodes": [summary_node_id, r_node],
                "timestamp": now,
            }

            ops = [
                GraphOp(event_type="NODE_UPDATE", entity_id=summary_node_id, entity_data=summary_node),
                GraphOp(event_type="EDGE_UPDATE", entity_id=e1["id"], entity_data=e1),
                GraphOp(event_type="EDGE_UPDATE", entity_id=e2["id"], entity_data=e2),
            ]

        # commit outside lock
        res = bus().commit(
            entity_id=summary_entity_id,
            entity_type="DETECTION_SUMMARY",
            entity_data=summary_payload,
            graph_ops=ops,
            ctx=ctx,
            persist=True,
            audit=self.cfg.audit_summary,
        )

        return {
            "ok": res.ok,
            "summary_id": summary_entity_id,
            "persisted": res.persisted,
            "graph_applied": res.graph_applied,
            "errors": res.errors,
            "entity": summary_payload,
            "node_id": summary_node_id,
        }


# ---------------------------
# Singleton convenience
# ---------------------------

_REGISTRY: Optional[DetectionRegistry] = None


def init_detection_registry(cfg: Optional[DetectionRegistryConfig] = None) -> DetectionRegistry:
    global _REGISTRY
    _REGISTRY = DetectionRegistry(cfg=cfg)
    return _REGISTRY


def registry() -> DetectionRegistry:
    if _REGISTRY is None:
        _REGISTRY = DetectionRegistry()
    return _REGISTRY


def emit_detection(detection: Json, ctx: WriteContext) -> Json:
    return registry().emit_detection(detection, ctx)
