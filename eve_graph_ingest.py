"""Authority-preserving Eve Streamer event ingestion for the live graph.

Only normalized protobuf summaries enter this boundary. Packet payloads and raw
bytes are not accepted. Each accepted flow is committed through WriteBus as two
host nodes and one stable flow edge with explicit observation provenance.
"""

from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import math
import threading
import time
from typing import Any, Dict, Iterable


MAX_BATCH_EVENTS = 500
MAX_ENTITIES_PER_EVENT = 32
MAX_STRING = 1024
ALLOWED_EVENT_FIELDS = {"event_id", "type", "entities", "edges", "timestamp"}
ALLOWED_ENTITY_FIELDS = {"key", "value"}


class EveIngestError(ValueError):
    pass


def _bounded_string(value: Any, name: str, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or len(value) > MAX_STRING or (required and not value):
        raise EveIngestError(f"{name} must be a{' non-empty' if required else ''} string of at most {MAX_STRING} characters")
    return value


def _timestamp(value: Any) -> tuple[float, str]:
    text = _bounded_string(value, "timestamp", required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        epoch = parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise EveIngestError("timestamp must be ISO-8601") from exc
    if not math.isfinite(epoch):
        raise EveIngestError("timestamp must be finite")
    return epoch, parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_eve_event(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise EveIngestError("event must be an object")
    unknown = set(value) - ALLOWED_EVENT_FIELDS
    if unknown:
        raise EveIngestError(f"event contains unknown fields: {sorted(unknown)}")
    event_id = _bounded_string(value.get("event_id"), "event_id", required=True)
    event_type = _bounded_string(value.get("type"), "type", required=True)
    observed_at, timestamp = _timestamp(value.get("timestamp"))
    entities = value.get("entities")
    if not isinstance(entities, list) or len(entities) > MAX_ENTITIES_PER_EVENT:
        raise EveIngestError(f"entities must be an array of at most {MAX_ENTITIES_PER_EVENT} items")
    normalized_entities = []
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict) or set(entity) - ALLOWED_ENTITY_FIELDS:
            raise EveIngestError(f"entities[{index}] contains unknown fields")
        normalized_entities.append({
            "key": _bounded_string(entity.get("key"), f"entities[{index}].key", required=True),
            "value": _bounded_string(entity.get("value"), f"entities[{index}].value"),
        })
    edges = value.get("edges") or []
    if not isinstance(edges, list) or len(edges) > 32:
        raise EveIngestError("edges must be an array of at most 32 strings")
    return {"event_id": event_id, "type": event_type, "entities": normalized_entities,
            "edges": [_bounded_string(item, "edge") for item in edges],
            "timestamp": timestamp, "observed_at": observed_at}


def validate_eve_batch(payload: Any) -> list[Dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {"events"}:
        raise EveIngestError("payload must contain only an events array")
    events = payload["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_BATCH_EVENTS:
        raise EveIngestError(f"events must contain 1-{MAX_BATCH_EVENTS} items")
    return [validate_eve_event(event) for event in events]


def _entity_map(event: Dict[str, Any]) -> Dict[str, str]:
    return {item["key"]: item["value"] for item in event["entities"]}


def _ip(value: str, name: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise EveIngestError(f"{name} is not a valid IP address") from exc


def _port(value: str | None) -> int:
    if value in (None, ""):
        return 0
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise EveIngestError("ports must be integers") from exc
    if not 0 <= port <= 65535:
        raise EveIngestError("ports must be between 0 and 65535")
    return port


def graph_ops_for_event(event: Dict[str, Any]):
    from writebus import GraphOp

    fields = _entity_map(event)
    src = _ip(fields.get("src_ip", ""), "src_ip")
    dst = _ip(fields.get("dest_ip") or fields.get("dst_ip") or "", "dest_ip")
    src_port = _port(fields.get("src_port")); dst_port = _port(fields.get("dest_port") or fields.get("dst_port"))
    proto = (fields.get("proto") or "unknown").lower()[:32]
    evidence_class = "SYNTHETIC" if event["type"].lower().startswith(("test", "synthetic")) else "OBSERVED"
    provenance = {"source": "eve-streamer", "tool": "eve_event_stream",
                  "evidence_refs": [event["event_id"]], "timestamp": event["timestamp"]}
    metadata = {"source": "eve-streamer", "evidence_class": evidence_class,
                "observed_at": event["observed_at"], "provenance_write": provenance,
                "geospatialAuthority": "ABSENT"}
    if fields.get("scythe_ingest_mode") == "bootstrap_replay":
        metadata["ingest_mode"] = "BOOTSTRAP_REPLAY"
    node_ops = []
    for role, address in (("source", src), ("destination", dst)):
        entity_id = f"host:{address}"
        node_ops.append(GraphOp(event_type="NODE_UPDATE", entity_id=entity_id, entity_data={
            "id": entity_id, "kind": "network_host", "labels": {"ip": address, "flowRole": role},
            "metadata": dict(metadata), "created_at": event["observed_at"],
        }))
    flow_id = f"flow:{proto}:{src}:{src_port}->{dst}:{dst_port}"
    edge = {"id": flow_id, "kind": "network_flow", "nodes": [f"host:{src}", f"host:{dst}"],
            "labels": {"src_ip": src, "dest_ip": dst, "src_port": str(src_port),
                       "dest_port": str(dst_port), "proto": proto,
                       "packets": fields.get("packets", ""), "bytes": fields.get("bytes", "")},
            "metadata": dict(metadata), "timestamp": event["observed_at"]}
    return [*node_ops, GraphOp(event_type="EDGE_UPDATE", entity_id=flow_id, entity_data=edge)], flow_id, evidence_class


class EveIngestStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.received = 0; self.committed = 0; self.replayed = 0
        self.deduplicated = 0; self.rejected = 0
        self.last_received_at = None; self.last_event_at = None; self.last_error = None

    def record(self, *, received=0, committed=0, replayed=0, deduplicated=0,
               rejected=0, event_at=None, error=None):
        with self._lock:
            self.received += received; self.committed += committed
            self.replayed += replayed; self.deduplicated += deduplicated
            self.rejected += rejected
            self.last_received_at = time.time()
            if event_at is not None: self.last_event_at = event_at
            self.last_error = error

    def snapshot(self):
        with self._lock:
            return {"received": self.received, "committed": self.committed, "replayed": self.replayed,
                    "deduplicated": self.deduplicated, "rejected": self.rejected,
                    "lastReceivedAt": self.last_received_at, "lastEventAt": self.last_event_at,
                    "lastError": self.last_error, "rawPacketsAccepted": False,
                    "authority": "EVE_NORMALIZED_EVENT_SUMMARIES"}


STATS = EveIngestStats()


def commit_eve_events(events: Iterable[Dict[str, Any]], bus: Any,
                      *, idempotency_scope: str = "default") -> Dict[str, Any]:
    from writebus import WriteContext

    accepted = 0; replayed = 0; deduplicated = 0; rejected = []
    evidence_classes = set()
    for event in events:
        try:
            is_replay = _entity_map(event).get("scythe_ingest_mode") == "bootstrap_replay"
            ops, flow_id, evidence_class = graph_ops_for_event(event)
            ctx = WriteContext(room_name="Global", operator_id="SYSTEM:EVE_STREAMER",
                               request_id=event["event_id"], source="eve-streamer",
                               evidence_refs=[event["event_id"]], temporal_state={"observedAt": event["timestamp"]})
            result = bus.commit(entity_id=f"eve:{event['event_id']}", entity_type="NETWORK_FLOW_OBSERVATION",
                                entity_data={"event_id": event["event_id"], "type": event["type"],
                                             "timestamp": event["timestamp"], "flow_id": flow_id,
                                             "evidence_class": evidence_class},
                                graph_ops=ops, ctx=ctx, persist=False, audit=True,
                                idempotency_key=f"eve:{idempotency_scope}:{event['event_id']}")
            if not result.ok:
                raise EveIngestError("WriteBus commit failed: " + "; ".join(result.errors))
            is_duplicate = bool((getattr(result, "debug", None) or {}).get("idempotent_replay"))
            accepted += int(not is_duplicate); replayed += int(is_replay)
            deduplicated += int(is_duplicate); evidence_classes.add(evidence_class)
            STATS.record(received=1, committed=int(not is_duplicate), replayed=int(is_replay),
                         deduplicated=int(is_duplicate),
                         event_at=event["observed_at"])
        except Exception as exc:
            rejected.append({"eventId": event.get("event_id"), "error": str(exc)})
            STATS.record(received=1, rejected=1, error=str(exc))
    return {"status": "ok" if not rejected else "partial",
            "received": accepted + deduplicated + len(rejected),
            "committed": accepted, "replayed": replayed, "deduplicated": deduplicated,
            "rejected": rejected[:20], "bounded": True,
            "evidenceClasses": sorted(evidence_classes), "rawPacketsAccepted": False,
            "authority": "EVE_NORMALIZED_EVENT_SUMMARIES"}
