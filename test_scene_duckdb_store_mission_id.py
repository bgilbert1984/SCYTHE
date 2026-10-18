"""ARR-API-2: Mission identity propagation into ScytheDuckStore.

capture_sessions is the operational persistence projection of scythe_mission.Session for
live ingest -- these tests exercise the store directly (no Flask import needed), covering:
omission-preserves-prior-behavior, acceptance+persistence, conflict rejection, and the
events/capture_sessions consistency invariant that denormalizing mission_id onto events
depends on.
"""
import unittest

from scene_duckdb_store import ScytheDuckStore, TacticalEvent, SessionMissionConflictError


def _event(session_id, mission_id=None, entity_id="e1", ts=1_700_000_000_000):
    return TacticalEvent(
        timestamp=ts, event_type="probe", entity_id=entity_id,
        session_id=session_id, mission_id=mission_id,
    )


class OmittedMissionIdTests(unittest.TestCase):
    def test_omitting_mission_id_preserves_prior_behavior(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_1")
        count = store.append_batch([_event("sess-1"), _event("sess-1", entity_id="e2")])
        self.assertEqual(count, 2)
        rows = store.query_sql("SELECT session_id, entity_id, mission_id FROM events ORDER BY seq")
        self.assertEqual(rows, [
            {"session_id": "sess-1", "entity_id": "e1", "mission_id": None},
            {"session_id": "sess-1", "entity_id": "e2", "mission_id": None},
        ])
        session = store.resolve_or_create_session("sess-1")
        self.assertIsNone(session["mission_id"])
        store.close()


class MissionIdAcceptanceTests(unittest.TestCase):
    def test_a_mission_id_is_accepted_and_persists_on_both_tables(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_2")
        store.append_batch([_event("sess-2", mission_id="mission-A")])
        session_row = store.query_sql(
            "SELECT mission_id FROM capture_sessions WHERE session_id = 'sess-2'")
        event_row = store.query_sql(
            "SELECT mission_id FROM events WHERE session_id = 'sess-2'")
        self.assertEqual(session_row, [{"mission_id": "mission-A"}])
        self.assertEqual(event_row, [{"mission_id": "mission-A"}])
        store.close()

    def test_a_later_event_with_no_mission_id_inherits_the_session_s_bound_mission(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_3")
        store.append_batch([_event("sess-3", mission_id="mission-A")])
        store.append_batch([_event("sess-3", entity_id="e2")])  # mission_id omitted
        rows = store.query_sql(
            "SELECT entity_id, mission_id FROM events WHERE session_id = 'sess-3' ORDER BY seq")
        self.assertEqual(rows, [
            {"entity_id": "e1", "mission_id": "mission-A"},
            {"entity_id": "e2", "mission_id": "mission-A"},
        ])
        store.close()

    def test_a_null_session_can_be_backfilled_with_a_mission_id_later(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_4")
        store.append_batch([_event("sess-4")])  # no mission_id yet
        store.append_batch([_event("sess-4", entity_id="e2", mission_id="mission-B")])
        rows = store.query_sql(
            "SELECT entity_id, mission_id FROM events WHERE session_id = 'sess-4' ORDER BY seq")
        self.assertEqual(rows, [
            {"entity_id": "e1", "mission_id": "mission-B"},
            {"entity_id": "e2", "mission_id": "mission-B"},
        ])
        store.close()


class MissionIdConflictTests(unittest.TestCase):
    def test_a_conflicting_mission_id_on_an_already_bound_session_is_rejected(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_5")
        store.append_batch([_event("sess-5", mission_id="mission-A")])
        with self.assertRaises(SessionMissionConflictError):
            store.append_batch([_event("sess-5", entity_id="e2", mission_id="mission-B")])
        store.close()

    def test_a_rejected_batch_inserts_no_events_at_all_not_even_for_other_sessions(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_6")
        store.append_batch([_event("sess-6", mission_id="mission-A")])
        with self.assertRaises(SessionMissionConflictError):
            store.append_batch([
                _event("sess-other", entity_id="e-other", mission_id="mission-C"),
                _event("sess-6", entity_id="e2", mission_id="mission-B"),
            ])
        self.assertEqual(store.query_sql(
            "SELECT COUNT(*) AS n FROM events WHERE session_id = 'sess-other'"), [{"n": 0}])
        store.close()

    def test_a_same_batch_internally_conflicting_mission_id_is_rejected(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_7")
        with self.assertRaises(SessionMissionConflictError):
            store.append_batch([
                _event("sess-7", mission_id="mission-A"),
                _event("sess-7", entity_id="e2", mission_id="mission-Z"),
            ])
        self.assertEqual(store.query_sql(
            "SELECT COUNT(*) AS n FROM events WHERE session_id = 'sess-7'"), [{"n": 0}])
        store.close()

    def test_re_ingesting_the_same_matching_mission_id_is_idempotent(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_8")
        store.append_batch([_event("sess-8", mission_id="mission-A")])
        count = store.append_batch([_event("sess-8", entity_id="e2", mission_id="mission-A")])
        self.assertEqual(count, 1)
        store.close()


class ConsistencyInvariantTests(unittest.TestCase):
    def test_events_and_capture_sessions_mission_id_always_agree(self):
        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_arr_api2_test_9")
        store.append_batch([_event("sess-9a", mission_id="mission-A")])
        store.append_batch([_event("sess-9b", mission_id="mission-B", entity_id="e2")])
        store.append_batch([_event("sess-9a", entity_id="e3")])  # inherits mission-A
        store.append_batch([_event("sess-9c", entity_id="e4")])  # no mission at all
        mismatches = store.query_sql("""
            SELECT COUNT(*) AS n FROM events e
            JOIN capture_sessions s ON e.session_id = s.session_id
            WHERE e.mission_id IS DISTINCT FROM s.mission_id
        """)
        self.assertEqual(mismatches, [{"n": 0}])
        store.close()


if __name__ == "__main__":
    unittest.main()
