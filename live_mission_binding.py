"""Which mission, if any, a live graph event declares.

The live graph-event drain used to write every event under the shared ``'global'`` session with no
``mission_id``, so ``GET /api/missions/<id>/events`` never returned live events
(docs/globe_command_instrument_backend_audit.md section 4).  A session's mission binding is
immutable and binding the shared session would backfill every event already in it, so the live
path cannot bind ``'global'``.  Instead an event that *declares* exactly one valid mission goes
into that mission's own live session; everything else stays in ``'global'`` unbound.

Evidence only: the mission is read from the event's own data, never inferred from which mission
is open or which operator is connected.  The places read are the ones the rest of the server
already honours -- WriteBus write provenance, and the ``mission_id`` / ``missionId`` convention in
``labels`` and ``metadata`` (docs/research/scythe_mission_browser_rendering.md section 4).

An event is left unbound when it declares nothing, declares two different missions, or declares
anything that is not a valid mission id.  A host observed under several missions therefore stays
unbound: one stored row can belong to one mission, and copying it would double-count it.
"""
from typing import Any, List, Optional, Tuple

from scythe_mission import is_valid_mission_id

GLOBAL_SESSION = "global"
LIVE_SESSION_PREFIX = "live:"


def _scalars(value: Any) -> List[Any]:
    """A declared value as a list: a scalar, or the list form a multi-mission host carries."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def declared_missions(data: Any) -> List[Any]:
    """Every mission value the event declares, in the places the server already reads."""
    if not isinstance(data, dict):
        return []
    found: List[Any] = []
    containers = [data.get("labels"), data.get("metadata"), data.get("meta")]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("mission_id", "missionId"):
            found.extend(_scalars(container.get(key)))
        write = container.get("provenance_write")
        if isinstance(write, dict):
            found.extend(_scalars(write.get("mission_id")))
    return found


def live_mission_id(data: Any) -> Optional[str]:
    """The one valid mission the event declares, or None when it is not unambiguously one."""
    declared = declared_missions(data)
    if not declared or not all(is_valid_mission_id(v) for v in declared):
        return None
    distinct = set(declared)
    return declared[0] if len(distinct) == 1 else None


def live_session(data: Any) -> Tuple[str, Optional[str]]:
    """``(session_id, mission_id)`` to store a live graph event under."""
    mission_id = live_mission_id(data)
    if mission_id is None:
        return GLOBAL_SESSION, None
    return LIVE_SESSION_PREFIX + mission_id, mission_id
