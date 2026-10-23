"""One reader for a graph event, whether it arrives as an object or as a dict.

``GraphEventBus`` fans out whatever it is given.  The hypergraph engine publishes a
``SimpleNamespace``; ``WriteBus.publish_bus`` publishes a plain dict (``ENTITY_UPSERT``).  The live
drain in rf_scythe_api_server.py read every event with ``getattr``, which returns nothing for a dict,
so each WriteBus event was stored and pushed to clients as an empty event (``event_type=''``,
``entity_id=''``, no payload) -- see docs/globe_command_instrument_backend_audit.md section 4.

The field names and fallbacks here are exactly the ones the drain already used for objects, so an
object event reads as before.
"""
from typing import Any, Dict


def _get(ev: Any, name: str, default: Any = None) -> Any:
    if isinstance(ev, dict):
        return ev.get(name, default)
    return getattr(ev, name, default)


def graph_event_fields(ev: Any) -> Dict[str, Any]:
    """``event_type``, ``entity_id``, ``entity_kind``, ``sequence_id`` and ``data`` of ``ev``.

    ``sequence_id`` is 0 when the event has none; a dict published to the bus never gets the
    sequence the bus assigns (it can only set an attribute), so a WriteBus event reads 0.
    """
    return {
        "event_type": _get(ev, "event_type", "") or _get(ev, "type", ""),
        "entity_id": _get(ev, "entity_id", "") or _get(ev, "id", "") or "",
        "entity_kind": _get(ev, "entity_kind", "") or _get(ev, "entity_type", ""),
        "sequence_id": int(_get(ev, "sequence_id", 0) or 0),
        "data": _get(ev, "entity_data", None) or _get(ev, "payload", {}) or {},
    }
