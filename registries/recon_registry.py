# registries/recon_registry.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import time

from writebus import bus, WriteContext, GraphOp


def _coalesce(*vals):
    for v in vals:
        if v is not None and v != "":
            return v
    return None


def _norm_entity_id(entity: Dict[str, Any]) -> str:
    eid = _coalesce(entity.get("entity_id"), entity.get("entityId"), entity.get("id"))
    if not eid:
        eid = f"ENTITY-{int(time.time()*1000)}"
    return str(eid)


def _norm_location(entity: Dict[str, Any]) -> Dict[str, Any]:
    loc = entity.get("location") or entity.get("geo") or {}
    lat = _coalesce(entity.get("lat"), loc.get("lat"), loc.get("latitude"))
    lon = _coalesce(entity.get("lon"), entity.get("lng"), loc.get("lon"), loc.get("lng"), loc.get("longitude"))
    alt = _coalesce(entity.get("alt"), loc.get("alt_m"), loc.get("altitude_m"), 0)
    out = {}
    if lat is not None and lon is not None:
        out["lat"] = float(lat)
        out["lon"] = float(lon)
        out["altitude_m"] = float(alt or 0)
    return out


# Fields an analyst or a sensor decision owns. A write that does not carry them must leave them
# alone, and a null/empty value means "not provided", never "clear".
_PROTECTED_FIELDS = ("disposition", "threat_level")
_POSITION_KEYS = ("lat", "lon", "lng", "alt", "location", "geo")


def _is_blank(value) -> bool:
    return value is None or value == ""


def _existing_entity(entity_id: str, ctx: WriteContext, writer) -> Optional[Dict[str, Any]]:
    """The durable payload the room already holds for this entity, if any."""
    try:
        operator_manager = getattr(writer, "operator_manager", None)
        if not operator_manager:
            return None
        room = operator_manager.get_room_by_name(ctx.room_name)
        if not room:
            return None
        room_id = room.get("room_id") or room.get("id") if isinstance(room, dict) else getattr(room, "room_id", None)
        held = (operator_manager.room_entities.get(room_id) or {}).get(entity_id)
        data = held.get("data") if isinstance(held, dict) else None
        return dict(data) if isinstance(data, dict) else None
    except Exception:
        return None


def merge_recon_update(existing: Optional[Dict[str, Any]], patch: Dict[str, Any]) -> Dict[str, Any]:
    """PATCH semantics for a recon write.

    - No existing entity: the patch is the entity (creation).
    - Keys the patch omits keep their held values; false, 0 and empty lists are values.
    - disposition / threat_level: null or "" counts as omitted.
    - Position: replaced as a unit when the patch carries a complete lat/lon pair, preserved
      when it carries none, and a lone coordinate is rejected (ValueError) so a position is
      never half-written.
    """
    patch = dict(patch or {})
    if not existing:
        return patch

    for field in _PROTECTED_FIELDS:
        if field in patch and _is_blank(patch[field]):
            del patch[field]

    carries_position = any(not _is_blank(patch.get(k)) for k in _POSITION_KEYS)
    if carries_position:
        new_location = _norm_location(patch)
        if not new_location:
            raise ValueError("position update needs both lat and lon")
    else:
        for key in _POSITION_KEYS:
            patch.pop(key, None)

    merged = dict(existing)
    if carries_position:
        for key in _POSITION_KEYS:
            merged.pop(key, None)
    merged.update(patch)
    return merged


def _recon_node_id(entity_id: str) -> str:
    # Keep a stable namespace so sensors/edges don't collide
    return f"recon:{entity_id}"


def _recon_room_entity_payload(entity: Dict[str, Any], entity_id: str) -> Dict[str, Any]:
    # This is the durable payload you want all operators to retain
    payload = dict(entity or {})
    payload["entity_id"] = entity_id
    payload.setdefault("type", "RECON_ENTITY")
    payload.setdefault("created", time.time())
    payload["last_update"] = time.time()
    # normalize location if provided
    loc = _norm_location(payload)
    if loc:
        payload["location"] = loc
    return payload


def _recon_graph_node(entity: Dict[str, Any], entity_id: str, ctx: WriteContext) -> Dict[str, Any]:
    loc = entity.get("location") or {}
    # command-ops expects position = [lat, lon, alt]
    position = None
    if loc and ("lat" in loc and "lon" in loc):
        position = [loc["lat"], loc["lon"], loc.get("altitude_m", 0)]

    labels = dict(entity.get("labels") or {})
    if ctx.mission_id:
        labels["mission_id"] = str(ctx.mission_id)

    # minimal node shape (hypergraph styles by kind)
    return {
        "id": _recon_node_id(entity_id),
        "kind": "recon_entity",
        "position": position,
        "labels": labels,
        "metadata": {
            "entity_id": entity_id,
            "name": entity.get("name") or entity.get("label") or entity_id,
            "ontology": entity.get("ontology"),
            "disposition": entity.get("disposition"),
            "threat_level": entity.get("threat_level"),
            "raw": entity,  # optional: keep raw for UI drill-down
        },
    }


# ---------------------------
# Public API
# ---------------------------

def upsert_recon_entity(entity: Dict[str, Any], ctx: WriteContext, *, room_persist: bool = True, writer=None) -> Dict[str, Any]:
    """
    Upsert a recon entity through the WriteBus.
    An update carries only what changed: omitted fields keep what the room holds (see
    merge_recon_update). Returns WriteResult dict for easy JSON serialization.
    """
    entity = dict(entity or {})
    entity_id = _norm_entity_id(entity)
    entity = merge_recon_update(_existing_entity(entity_id, ctx, writer or bus()), entity)

    durable = _recon_room_entity_payload(entity, entity_id)
    node = _recon_graph_node(durable, entity_id, ctx)

    ops: List[GraphOp] = [
        GraphOp(event_type="NODE_UPDATE", entity_id=node["id"], entity_data=node),
    ]

    # Persist a durable RECON_ENTITY into the room + broadcast
    res = (writer or bus()).commit(
        entity_id=entity_id,                   # durable entity id
        entity_type="RECON_ENTITY",
        entity_data=durable,
        graph_ops=ops,
        ctx=ctx,
        persist=room_persist,
        audit=True,
    )

    return {
        "write_result": {
            "ok": res.ok,
            "entity_id": res.entity_id,
            "entity_type": res.entity_type,
            "room": res.room_name,
            "persisted": res.persisted,
            "graph_applied": res.graph_applied,
            "errors": res.errors,
            "debug": res.debug,
        },
        "entity": durable,
        "graph_node": node,
    }


def delete_recon_entity(entity_id: str, ctx: WriteContext, *, room_persist: bool = True) -> Dict[str, Any]:
    """
    Minimal skeleton for deletion. You’ll probably also want:
      - tombstone semantics
      - edge cleanup
      - audit record
    """
    entity_id = str(entity_id)
    node_id = _recon_node_id(entity_id)

    ops: List[GraphOp] = [
        GraphOp(event_type="NODE_DELETE", entity_id=node_id, entity_data={"id": node_id}),
    ]

    # For room persistence, you likely want operator_manager.delete_from_room(...) instead of publish.
    # Keep this skeleton simple: publish a tombstone to room (clients interpret as delete).
    tombstone = {"entity_id": entity_id, "type": "RECON_ENTITY_TOMBSTONE", "deleted_at": time.time()}

    res = bus().commit(
        entity_id=entity_id,
        entity_type="RECON_ENTITY_TOMBSTONE",
        entity_data=tombstone,
        graph_ops=ops,
        ctx=ctx,
        persist=room_persist,
        audit=True,
    )

    return {
        "write_result": {
            "ok": res.ok,
            "entity_id": res.entity_id,
            "entity_type": res.entity_type,
            "room": res.room_name,
            "persisted": res.persisted,
            "graph_applied": res.graph_applied,
            "errors": res.errors,
            "debug": res.debug,
        },
        "tombstone": tombstone,
    }


def update_disposition(entity_id: str, disposition: str, ctx: WriteContext, *, room_persist: bool = True) -> Dict[str, Any]:
    """
    Convenience: update only the disposition/threat fields while still going through chokepoint.
    """
    patch = {
        "entity_id": str(entity_id),
        "type": "RECON_ENTITY",
        "disposition": disposition,
        "last_update": time.time(),
    }
    return upsert_recon_entity(patch, ctx, room_persist=room_persist)
