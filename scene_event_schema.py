"""
scene_event_schema.py
=====================
Canonical 15-event schema for the Immutable Battlefield Ledger.

Design goals
------------
* Engine-agnostic — describes WHAT happened in space-time, never HOW to render it.
* Deterministic — given the same ordered log the scene is always identical.
* Compact — serialises to msgpack; compresses well with zstd.
* Extensible — extra keys in the payload are preserved and round-trip safely.

Event types
-----------
  Session lifecycle:  session.start  session.end
  Operator view:      camera.pose
  Map objects:        entity.spawn   entity.move   entity.update   entity.remove
  RF intelligence:    rf.detect      rf.triangulate
  Swarm clusters:     swarm.create   swarm.update   swarm.dissolve
  Sensor feeds:       sensor.frame
  Human markup:       overlay.annotation
  Asset pinning:      asset.reference

Usage
-----
    from scene_event_schema import make_event, validate_event, to_msgpack, from_msgpack

    evt = make_event("entity.spawn", id="uav_12", class_="uav",
                     lat=32.81, lon=-96.86, alt=400)
    raw = to_msgpack(evt)
    evt2 = from_msgpack(raw)
    assert evt2["type"] == "entity.spawn"
"""

import time
import json
import msgpack

# ---------------------------------------------------------------------------
# Valid type set
# ---------------------------------------------------------------------------

VALID_TYPES = frozenset({
    "session.start",
    "session.end",
    "camera.pose",
    "entity.spawn",
    "entity.move",
    "entity.update",
    "entity.remove",
    "rf.detect",
    "rf.triangulate",
    "swarm.create",
    "swarm.update",
    "swarm.dissolve",
    "sensor.frame",
    "overlay.annotation",
    "asset.reference",
})

# Required fields per type (beyond "type" and "timestamp")
_REQUIRED: dict[str, list[str]] = {
    "session.start":       ["session_id"],
    "session.end":         [],
    "camera.pose":         ["lat", "lon", "alt"],
    "entity.spawn":        ["id", "class_"],
    "entity.move":         ["id", "lat", "lon"],
    "entity.update":       ["id"],
    "entity.remove":       ["id"],
    "rf.detect":           ["sensor", "freq", "power"],
    "rf.triangulate":      ["emitter_id", "lat", "lon"],
    "swarm.create":        ["swarm_id", "centroid"],
    "swarm.update":        ["swarm_id"],
    "swarm.dissolve":      ["swarm_id"],
    "sensor.frame":        ["sensor"],
    "overlay.annotation":  ["shape", "points"],
    "asset.reference":     ["asset", "hash"],
}

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_event(type_: str, timestamp: float | None = None, **payload) -> dict:
    """
    Create a canonical event dict.

    ``class_`` (Python-safe alias) is stored as ``class`` in the event.
    All other kwargs become top-level payload fields.
    """
    if type_ not in VALID_TYPES:
        raise ValueError(f"Unknown event type: {type_!r}")

    evt: dict = {
        "type": type_,
        "timestamp": timestamp if timestamp is not None else time.time(),
    }

    for k, v in payload.items():
        canon_key = "class" if k == "class_" else k
        evt[canon_key] = v

    return evt


def validate_event(evt: dict) -> list[str]:
    """
    Return list of validation errors (empty = valid).
    """
    errors: list[str] = []
    t = evt.get("type")
    if t not in VALID_TYPES:
        errors.append(f"unknown type: {t!r}")
        return errors

    if "timestamp" not in evt:
        errors.append("missing timestamp")

    for req in _REQUIRED.get(t, []):
        canon = "class" if req == "class_" else req
        if canon not in evt:
            errors.append(f"missing required field: {canon!r}")

    return errors

# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def to_msgpack(evt: dict) -> bytes:
    return msgpack.packb(evt, use_bin_type=True)


def from_msgpack(raw: bytes) -> dict:
    return msgpack.unpackb(raw, raw=False, strict_map_key=False)


def to_json(evt: dict) -> str:
    return json.dumps(evt, separators=(",", ":"))


def from_json(s: str) -> dict:
    return json.loads(s)

# ---------------------------------------------------------------------------
# Convenience constructors for common events
# ---------------------------------------------------------------------------

def session_start(session_id: str, projection: str = "EPSG:4326",
                  seed: int = 0) -> dict:
    return make_event("session.start", session_id=session_id,
                      projection=projection, seed=seed)


def session_end() -> dict:
    return make_event("session.end")


def camera_pose(lat: float, lon: float, alt: float,
                heading: float = 0, pitch: float = -30, roll: float = 0) -> dict:
    return make_event("camera.pose", lat=lat, lon=lon, alt=alt,
                      heading=heading, pitch=pitch, roll=roll)


def entity_spawn(id_: str, class_: str, lat: float = 0, lon: float = 0,
                 alt: float = 0, **extra) -> dict:
    return make_event("entity.spawn", id=id_, class_=class_,
                      lat=lat, lon=lon, alt=alt, **extra)


def entity_move(id_: str, lat: float, lon: float, alt: float = 0) -> dict:
    return make_event("entity.move", id=id_, lat=lat, lon=lon, alt=alt)


def entity_update(id_: str, **fields) -> dict:
    return make_event("entity.update", id=id_, **fields)


def entity_remove(id_: str) -> dict:
    return make_event("entity.remove", id=id_)


def rf_detect(sensor: str, freq: float, power: float,
              bearing: float | None = None, **extra) -> dict:
    evt = make_event("rf.detect", sensor=sensor, freq=freq, power=power, **extra)
    if bearing is not None:
        evt["bearing"] = bearing
    return evt


def rf_triangulate(emitter_id: str, lat: float, lon: float,
                   confidence: float = 1.0) -> dict:
    return make_event("rf.triangulate", emitter_id=emitter_id,
                      lat=lat, lon=lon, confidence=confidence)


def swarm_create(swarm_id: str, centroid: list, members: int = 0,
                 threat_score: float = 0.0, behavior: str = "MIXED") -> dict:
    return make_event("swarm.create", swarm_id=swarm_id, centroid=centroid,
                      members=members, threat_score=threat_score, behavior=behavior)


def swarm_update(swarm_id: str, centroid: list | None = None,
                 members: int | None = None, **extra) -> dict:
    kw: dict = {"swarm_id": swarm_id}
    if centroid is not None:
        kw["centroid"] = centroid
    if members is not None:
        kw["members"] = members
    kw.update(extra)
    return make_event("swarm.update", **kw)


def swarm_dissolve(swarm_id: str) -> dict:
    return make_event("swarm.dissolve", swarm_id=swarm_id)


def sensor_frame(sensor: str, lat: float = 0, lon: float = 0,
                 heading: float = 0, frame_hash: str = "") -> dict:
    return make_event("sensor.frame", sensor=sensor, lat=lat, lon=lon,
                      heading=heading, frame_hash=frame_hash)


def overlay_annotation(shape: str, points: list, label: str = "",
                       author: str = "") -> dict:
    return make_event("overlay.annotation", shape=shape, points=points,
                      label=label, author=author)


def asset_reference(asset: str, hash_: str) -> dict:
    return make_event("asset.reference", asset=asset, hash=hash_)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        session_start("test_session_001"),
        camera_pose(32.81, -96.87, 2000, heading=214, pitch=-35),
        entity_spawn("uav_12", "uav", lat=32.81, lon=-96.86, alt=400),
        entity_move("uav_12", 32.813, -96.861, 420),
        entity_update("uav_12", status="loiter", speed=18),
        rf_detect("rf_node_3", freq=2450, power=-47, bearing=212),
        rf_triangulate("rf_emitter_7", 32.814, -96.863, confidence=0.91),
        swarm_create("swarm_A", [32.81, -96.86], members=8, threat_score=0.92),
        swarm_update("swarm_A", centroid=[32.812, -96.862], members=10),
        sensor_frame("drone_cam_1", lat=32.81, lon=-96.86, heading=212,
                     frame_hash="sha256:ad3f1234"),
        overlay_annotation("polygon",
                           [[32.81,-96.86],[32.82,-96.86],[32.82,-96.85]],
                           label="suspected launch area", author="analyst_3"),
        asset_reference("terrain_tileset", "sha256:9b2f8c12abcd"),
        swarm_dissolve("swarm_A"),
        entity_remove("uav_12"),
        session_end(),
    ]

    print(f"Generated {len(tests)} events (15 types)")
    errors_found = 0
    for evt in tests:
        errs = validate_event(evt)
        if errs:
            print(f"  FAIL {evt['type']}: {errs}")
            errors_found += 1
        else:
            raw = to_msgpack(evt)
            back = from_msgpack(raw)
            assert back["type"] == evt["type"], "round-trip mismatch"
            print(f"  OK   {evt['type']:28s}  {len(raw):4d} bytes msgpack")

    print(f"\n{'ALL PASS' if not errors_found else f'{errors_found} FAILURES'}")
