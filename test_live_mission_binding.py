"""Live graph events bound to a mission they declare (docs/globe_command_instrument_backend_audit.md s.4).

The drain in rf_scythe_api_server.py stores each live event under ``live_session(data)``; these
tests exercise that function and the store it feeds, since the drain itself is a closure inside
server start-up.
"""
import unittest

from live_mission_binding import declared_missions, live_mission_id, live_session
from scene_duckdb_store import ScytheDuckStore, TacticalEvent


def _store(tag):
    return ScytheDuckStore(db_path=":memory:", parquet_dir=f"/tmp/scythe_live_binding_{tag}")


def _append(store, data, entity_id="e1"):
    session_id, mission_id = live_session(data)
    store.append(TacticalEvent(timestamp=1_700_000_000_000, event_type="NODE_CREATE",
                               entity_id=entity_id, session_id=session_id,
                               payload=data, mission_id=mission_id))


class DeclaredMissionTests(unittest.TestCase):
    def test_reads_labels_metadata_and_write_provenance(self):
        self.assertEqual(live_mission_id({"labels": {"mission_id": "m1"}}), "m1")
        self.assertEqual(live_mission_id({"labels": {"missionId": "m1"}}), "m1")
        self.assertEqual(live_mission_id({"metadata": {"mission_id": "m1"}}), "m1")
        self.assertEqual(live_mission_id({"meta": {"missionId": "m1"}}), "m1")
        self.assertEqual(
            live_mission_id({"metadata": {"provenance_write": {"mission_id": "m1"}}}), "m1")

    def test_the_same_mission_declared_twice_is_one_mission(self):
        data = {"labels": {"mission_id": "m1"},
                "metadata": {"missionId": "m1", "provenance_write": {"mission_id": "m1"}}}
        self.assertEqual(live_mission_id(data), "m1")

    def test_a_single_entry_list_is_one_mission(self):
        self.assertEqual(live_mission_id({"labels": {"mission_id": ["m1"]}}), "m1")

    def test_nothing_declared_is_unbound(self):
        for data in ({}, None, "text", {"labels": {}}, {"labels": {"mission_id": ""}},
                     {"labels": {"mission_id": None}}, {"metadata": "x"}):
            self.assertIsNone(live_mission_id(data), data)
            self.assertEqual(live_session(data), ("global", None))

    def test_two_different_missions_are_unbound(self):
        self.assertIsNone(live_mission_id({"labels": {"mission_id": ["m1", "m2"]}}))
        self.assertIsNone(live_mission_id(
            {"labels": {"mission_id": "m1"}, "metadata": {"provenance_write": {"mission_id": "m2"}}}))

    def test_an_invalid_id_makes_the_event_unbound_even_beside_a_valid_one(self):
        self.assertIsNone(live_mission_id({"labels": {"mission_id": "has space"}}))
        self.assertIsNone(live_mission_id({"labels": {"mission_id": 7}}))
        self.assertIsNone(live_mission_id(
            {"labels": {"mission_id": "m1"}, "metadata": {"missionId": "bad id"}}))

    def test_the_mission_is_never_inferred_from_anything_else(self):
        data = {"operator_id": "alice", "session": "s1", "mission": "m1", "mission_name": "m1"}
        self.assertEqual(declared_missions(data), [])

    def test_a_declared_mission_gets_its_own_session(self):
        self.assertEqual(live_session({"labels": {"mission_id": "m1"}}), ("live:m1", "m1"))


class StoreBindingTests(unittest.TestCase):
    def test_a_declared_live_event_appears_in_its_missions_events(self):
        store = _store("a")
        _append(store, {"labels": {"mission_id": "m1"}}, "n1")
        events = store.events_for_mission("m1")
        self.assertEqual([e.entity_id for e in events], ["n1"])
        self.assertEqual(events[0].mission_id, "m1")
        store.close()

    def test_an_undeclared_live_event_stays_global_and_joins_no_mission(self):
        store = _store("b")
        _append(store, {"labels": {"mission_id": "m1"}}, "n1")
        _append(store, {"kind": "host"}, "n2")
        self.assertEqual([e.entity_id for e in store.events_for_mission("m1")], ["n1"])
        rows = store.query_sql("SELECT entity_id, session_id, mission_id FROM events ORDER BY seq")
        self.assertEqual(rows[1], {"entity_id": "n2", "session_id": "global", "mission_id": None})
        store.close()

    def test_two_missions_do_not_see_each_others_events(self):
        store = _store("c")
        _append(store, {"labels": {"mission_id": "m1"}}, "n1")
        _append(store, {"labels": {"mission_id": "m2"}}, "n2")
        _append(store, {"labels": {"mission_id": "m1"}}, "n3")
        self.assertEqual([e.entity_id for e in store.events_for_mission("m1")], ["n1", "n3"])
        self.assertEqual([e.entity_id for e in store.events_for_mission("m2")], ["n2"])
        store.close()

    def test_the_global_session_is_never_bound_so_earlier_events_stay_unbound(self):
        store = _store("d")
        _append(store, {"kind": "host"}, "before")
        _append(store, {"labels": {"mission_id": "m1"}}, "n1")
        _append(store, {"kind": "host"}, "after")
        self.assertIsNone(store.resolve_or_create_session("global")["mission_id"])
        self.assertEqual([e.entity_id for e in store.events_for_mission("m1")], ["n1"])
        store.close()

    def test_a_mission_event_keeps_its_sequence_order_with_the_rest(self):
        store = _store("e")
        _append(store, {"kind": "host"}, "a")
        _append(store, {"labels": {"mission_id": "m1"}}, "b")
        _append(store, {"labels": {"mission_id": "m1"}}, "c")
        seqs = [e.seq for e in store.events_for_mission("m1")]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), 2)
        store.close()


if __name__ == "__main__":
    unittest.main()
