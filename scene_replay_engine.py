"""
scene_replay_engine.py
======================
Deterministic replay engine for the Immutable Battlefield Ledger.

Features
--------
* Variable-speed playback  (``speed=10.0`` → 10× real-time)
* Pause / resume / scrub to any timestamp
* Fork at any point in time to run counterfactual branches
* Event filtering by type set
* Frame callback interface (called once per "rendered frame" interval)
* Works offline from a .atakrec archive OR live from a SceneEventLog

Architecture
------------
The engine maintains a *logical clock* (independent of wall clock):

    logical_time = start_timestamp + (wall_elapsed * speed)

Events whose timestamp ≤ logical_time are applied to the SceneState.
After applying all ready events the on_frame callback fires (if bound).

The state is always reconstructable:

    state = replay(events[:cursor])

Forking
-------
    original = ReplayEngine.from_log(log, session_id)
    branch   = original.fork(at_timestamp=T)
    branch.inject(make_event("entity.spawn", id="counterfactual_uav", ...))
    branch.run()

Usage
-----
    eng = ReplayEngine.from_log(log, "op_2026_03_14")
    eng.speed = 5.0
    eng.on_frame = lambda state: print(len(state.entities), "entities")
    eng.run(until_timestamp=1710460000)
"""

import time
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable, Iterator

from scene_event_log import SceneEventLog, SceneState
from scene_event_schema import from_msgpack, make_event, to_msgpack, validate_event

# ---------------------------------------------------------------------------
# Replay cursor — points to a position in an event sequence
# ---------------------------------------------------------------------------

@dataclass
class ReplayCursor:
    events:      list[dict]       # full ordered event list
    position:    int = 0          # index of next event to apply
    logical_t:   float = 0.0      # current logical timestamp

    def peek(self) -> "dict | None":
        if self.position < len(self.events):
            return self.events[self.position]
        return None

    def advance(self) -> "dict | None":
        if self.position < len(self.events):
            evt = self.events[self.position]
            self.position += 1
            self.logical_t = evt["timestamp"]
            return evt
        return None

    def remaining(self) -> int:
        return len(self.events) - self.position

    def at_end(self) -> bool:
        return self.position >= len(self.events)

    def rewind(self, to_index: int = 0) -> None:
        self.position = max(0, min(to_index, len(self.events)))
        if self.position > 0:
            self.logical_t = self.events[self.position - 1]["timestamp"]

# ---------------------------------------------------------------------------
# Main replay engine
# ---------------------------------------------------------------------------

class ReplayEngine:
    """
    Logical-clock driven replay engine.

    Parameters
    ----------
    events      Ordered list of event dicts to replay.
    initial_state  Optional pre-built SceneState (from a snapshot).
                   If None, state is built from scratch by replaying from t=0.
    speed       Playback multiplier. 1.0 = real-time, 10.0 = 10× speed.
    filter_types    If set, only these event types are applied to the state.
                    All events still advance the cursor (skipped events are
                    counted but not applied).
    on_frame    Callable(SceneState) called after every frame_interval_sec
                (wall-clock) of playback.  Executes in the replay thread.
    """

    def __init__(self,
                 events: list[dict],
                 initial_state: SceneState | None = None,
                 speed: float = 1.0,
                 filter_types: set[str] | None = None,
                 on_frame: Callable[[SceneState], None] | None = None,
                 frame_interval_sec: float = 0.05):

        self.events              = sorted(events, key=lambda e: e.get("timestamp", 0))
        self._cursor             = ReplayCursor(self.events)
        self.speed               = max(0.001, speed)
        self.filter_types        = filter_types
        self.on_frame            = on_frame
        self.frame_interval_sec  = frame_interval_sec

        # State
        self._state       = deepcopy(initial_state) if initial_state else SceneState()
        self._lock        = threading.RLock()
        self._paused      = False
        self._stop_flag   = False
        self._thread: threading.Thread | None = None

        # Wall-clock reference (set when playback starts / resumes)
        self._wall_start: float | None = None
        self._logical_start: float | None = None

        # Stats
        self.events_applied = 0
        self.events_skipped = 0

    # ------------------------------------------------------------------
    # Factory constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_log(cls, log: SceneEventLog, session_id: str,
                 use_snapshot: bool = True, **kwargs) -> "ReplayEngine":
        """
        Build engine from a live SceneEventLog.

        If *use_snapshot* is True, loads the latest snapshot and only
        replays events after it (much faster for long sessions).
        """
        # Try to load snapshot
        initial_state: SceneState | None = None
        after_rowid = 0

        if use_snapshot:
            snap = log._conn.execute(
                "SELECT state_blob, after_rowid FROM snapshots"
                " WHERE session_id=? ORDER BY taken_at DESC LIMIT 1",
                (session_id,)
            ).fetchone()
            if snap:
                import msgpack, zstandard
                dctx = zstandard.ZstdDecompressor()
                state_dict = msgpack.unpackb(dctx.decompress(bytes(snap[0])),
                                             raw=False, strict_map_key=False)
                initial_state = SceneState.from_dict(state_dict)
                after_rowid   = snap[1]

        # Collect events (all, or only post-snapshot)
        events = list(log.iter_events(session_id, after_rowid=after_rowid))
        return cls(events, initial_state=initial_state, **kwargs)

    @classmethod
    def from_atakrec(cls, archive_path: str, **kwargs) -> "ReplayEngine":
        """
        Build engine from a .atakrec archive.

        The snapshot (if present) is ignored for replay purposes — the engine
        always replays all events from the beginning so that scrub/fork work
        correctly across the full timeline.
        """
        meta, events, _snap_state = SceneEventLog.import_atakrec(archive_path)
        return cls(events, initial_state=None, **kwargs)

    # ------------------------------------------------------------------
    # Playback control
    # ------------------------------------------------------------------

    def _now_logical(self) -> float:
        """Current logical timestamp based on wall clock + speed."""
        if self._wall_start is None or self._logical_start is None:
            return self._cursor.logical_t
        wall_elapsed = time.monotonic() - self._wall_start
        return self._logical_start + wall_elapsed * self.speed

    def _set_clock(self) -> None:
        """Sync wall-clock reference to current logical time."""
        self._wall_start    = time.monotonic()
        self._logical_start = self._cursor.logical_t

    def run(self, until_timestamp: float | None = None,
            blocking: bool = True) -> None:
        """
        Start or resume playback.

        Parameters
        ----------
        until_timestamp   Stop automatically when logical time reaches this.
        blocking          If False, runs in a background thread.
        """
        self._stop_flag = False
        self._paused    = False

        # Anchor logical clock to first pending event so the first event fires
        # immediately regardless of its Unix timestamp magnitude.
        if self._cursor.position == 0 and self._cursor.logical_t == 0.0:
            first = self._cursor.peek()
            if first:
                self._cursor.logical_t = first["timestamp"] - 1e-3

        self._set_clock()

        if blocking:
            self._replay_loop(until_timestamp)
        else:
            self._thread = threading.Thread(
                target=self._replay_loop,
                args=(until_timestamp,),
                daemon=True
            )
            self._thread.start()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._paused = False
            self._set_clock()

    def stop(self) -> None:
        self._stop_flag = True
        if self._thread:
            self._thread.join(timeout=2.0)

    def scrub(self, to_timestamp: float) -> SceneState:
        """
        Jump to a specific timestamp.  Rebuilds state from scratch.
        Returns the reconstructed SceneState at that point.
        """
        with self._lock:
            new_state = SceneState()
            new_cursor = ReplayCursor(self.events)
            for evt in self.events:
                if evt["timestamp"] > to_timestamp:
                    break
                if self.filter_types is None or evt["type"] in self.filter_types:
                    new_state.apply(evt)
                new_cursor.advance()
            self._state  = new_state
            self._cursor = new_cursor
            self._set_clock()
        return deepcopy(self._state)

    def get_state(self) -> SceneState:
        """Thread-safe snapshot of current scene state."""
        with self._lock:
            return deepcopy(self._state)

    # ------------------------------------------------------------------
    # Fork
    # ------------------------------------------------------------------

    def fork(self, at_timestamp: float | None = None) -> "ReplayEngine":
        """
        Create a new engine branching from the current (or specified) time.
        The fork shares no mutable state with the original.
        """
        with self._lock:
            base_state = (
                self.scrub(at_timestamp)
                if at_timestamp is not None
                else deepcopy(self._state)
            )
            # Events after fork point
            t_split = (at_timestamp
                       if at_timestamp is not None
                       else self._cursor.logical_t)
            remaining = [e for e in self.events if e["timestamp"] > t_split]

        branch = ReplayEngine(
            events=remaining,
            initial_state=base_state,
            speed=self.speed,
            filter_types=self.filter_types,
            on_frame=self.on_frame,
            frame_interval_sec=self.frame_interval_sec,
        )
        return branch

    def inject(self, evt: dict) -> None:
        """
        Insert a new event into the remaining event queue at its natural
        timestamp position.  Useful for counterfactual simulations.
        """
        errs = validate_event(evt)
        if errs:
            raise ValueError(f"inject: invalid event: {errs}")
        with self._lock:
            pos = self._cursor.position
            remaining = self.events[pos:]
            t = evt["timestamp"]
            insert_at = pos
            for i, e in enumerate(remaining):
                if e["timestamp"] > t:
                    insert_at = pos + i
                    break
            else:
                insert_at = len(self.events)
            self.events = self.events[:insert_at] + [evt] + self.events[insert_at:]
            self._cursor.events = self.events

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _replay_loop(self, until_timestamp: float | None) -> None:
        last_frame_wall = time.monotonic()

        while not self._stop_flag:
            if self._paused:
                time.sleep(0.05)
                continue

            now_logical = self._now_logical()

            # Apply all events that are due
            events_this_tick = 0
            with self._lock:
                while not self._cursor.at_end():
                    evt = self._cursor.peek()
                    if evt is None or evt["timestamp"] > now_logical:
                        break
                    self._cursor.advance()
                    if self.filter_types is None or evt["type"] in self.filter_types:
                        self._state.apply(evt)
                        self.events_applied += 1
                    else:
                        self.events_skipped += 1
                    events_this_tick += 1

            # Check end conditions
            if self._cursor.at_end():
                break
            if until_timestamp is not None and now_logical >= until_timestamp:
                break

            # Frame callback
            wall_now = time.monotonic()
            if self.on_frame and (wall_now - last_frame_wall) >= self.frame_interval_sec:
                try:
                    self.on_frame(self.get_state())
                except Exception:
                    pass
                last_frame_wall = wall_now

            # Sleep until next event or next frame.
            # If the gap to the next event exceeds 1 second of wall time at
            # current speed, snap the logical clock forward to just before that
            # event rather than sleeping through it.
            next_evt = self._cursor.peek()
            if next_evt:
                logical_gap  = next_evt["timestamp"] - now_logical
                wall_gap_sec = logical_gap / self.speed
                if wall_gap_sec > 1.0:
                    # Fast-forward: reset clock anchor to just before next event
                    self._logical_start = next_evt["timestamp"] - 1e-3
                    self._wall_start    = time.monotonic()
                    sleep_sec = 0.0
                else:
                    sleep_sec = min(wall_gap_sec, self.frame_interval_sec)
            else:
                sleep_sec = self.frame_interval_sec
            if sleep_sec > 0.001:
                time.sleep(sleep_sec)

        # Final frame
        if self.on_frame:
            try:
                self.on_frame(self.get_state())
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def progress(self) -> dict:
        n = len(self.events)
        pos = self._cursor.position
        return {
            "position":         pos,
            "total":            n,
            "pct":              round(100 * pos / n, 1) if n else 0,
            "logical_time":     self._cursor.logical_t,
            "events_applied":   self.events_applied,
            "events_skipped":   self.events_skipped,
            "remaining":        self._cursor.remaining(),
        }


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile, os
    print("=== SceneReplayEngine self-test ===\n")

    from scene_event_schema import (
        camera_pose, entity_move, entity_remove, entity_spawn,
        rf_triangulate, swarm_create, swarm_dissolve, swarm_update,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path  = os.path.join(tmpdir, "test.db")
        rec_path = os.path.join(tmpdir, "test.atakrec")

        log = SceneEventLog(db_path)
        sid = log.new_session("replay_test", seed=99)

        t0 = 1_710_000_000.0
        events = []
        events.append(camera_pose(32.81, -96.87, 2000))
        events[-1]["timestamp"] = t0 + 1

        for i in range(10):
            e = entity_spawn(f"uav_{i}", "uav", lat=32.81+i*0.01, lon=-96.86, alt=400)
            e["timestamp"] = t0 + 2 + i
            events.append(e)

        for i in range(20):
            e = entity_move(f"uav_{i%10}", 32.82, -96.85 + i*0.001)
            e["timestamp"] = t0 + 15 + i
            events.append(e)

        e = swarm_create("swarm_X", [32.81, -96.86], members=5)
        e["timestamp"] = t0 + 40
        events.append(e)

        e = swarm_dissolve("swarm_X")
        e["timestamp"] = t0 + 60
        events.append(e)

        log.append_many(events)
        log.snapshot(sid)
        log.export_atakrec(sid, rec_path)
        log.close()

        # ---- Test 1: from_log ----
        log2 = SceneEventLog(db_path)
        eng = ReplayEngine.from_log(log2, sid, speed=1000.0)
        eng.run(blocking=True)  # instant at 1000× speed
        state = eng.get_state()
        print(f"  from_log replay: {len(state.entities)} entities, "
              f"{len(state.swarms)} swarms  (events applied={eng.events_applied})")
        assert len(state.entities) == 10
        assert len(state.swarms) == 0    # swarm_X dissolved
        print("  Assertions: PASS")
        log2.close()

        # ---- Test 2: from_atakrec ----
        eng2 = ReplayEngine.from_atakrec(rec_path, speed=1000.0)
        eng2.run(blocking=True)
        state2 = eng2.get_state()
        assert len(state2.entities) == len(state.entities)
        print(f"  from_atakrec replay: {len(state2.entities)} entities — MATCH")

        # ---- Test 3: scrub to midpoint ----
        eng3 = ReplayEngine.from_atakrec(rec_path, speed=1.0)
        mid_state = eng3.scrub(t0 + 20)  # after spawns, before all moves
        print(f"  scrub to t+20: {len(mid_state.entities)} entities "
              f"(expect 10), swarms={len(mid_state.swarms)}")
        assert len(mid_state.entities) == 10

        # ---- Test 4: fork + inject counterfactual ----
        fork = eng3.fork(at_timestamp=t0 + 41)  # right after swarm_X created
        counterfactual_evt = entity_spawn("counterfactual_uav", "uav",
                                          lat=33.0, lon=-97.0)
        counterfactual_evt["timestamp"] = t0 + 42
        fork.inject(counterfactual_evt)
        fork.speed = 1000.0
        fork.run(blocking=True)
        fork_state = fork.get_state()
        assert "counterfactual_uav" in fork_state.entities, "injected entity missing"
        print(f"  fork+inject: counterfactual_uav present in branch — PASS")

        print("\nALL PASS ✓")
