"""The live drain reads WriteBus's dict events as well as the engine's object events."""
import os
import tempfile
import unittest
from types import SimpleNamespace

from graph_event_bus import GraphEventBus
from graph_event_fields import graph_event_fields
from live_mission_binding import live_session
from scene_duckdb_store import ScytheDuckStore, TacticalEvent
from writebus import GraphOp, WriteBus, WriteContext


class FakeHypergraph:
    def apply_graph_event(self, event):
        return True


class ObjectEventTests(unittest.TestCase):
    def test_an_engine_event_reads_as_it_always_did(self):
        ev = SimpleNamespace(event_type="NODE_CREATE", entity_id="n1", entity_kind="host",
                             sequence_id=7, entity_data={"lat": 1.0})
        self.assertEqual(graph_event_fields(ev), {
            "event_type": "NODE_CREATE", "entity_id": "n1", "entity_kind": "host",
            "sequence_id": 7, "data": {"lat": 1.0}})

    def test_object_fallback_names(self):
        ev = SimpleNamespace(type="EDGE_CREATE", id="e1", entity_type="edge", payload={"w": 1})
        f = graph_event_fields(ev)
        self.assertEqual((f["event_type"], f["entity_id"], f["entity_kind"], f["data"]),
                         ("EDGE_CREATE", "e1", "edge", {"w": 1}))
        self.assertEqual(f["sequence_id"], 0)

    def test_an_object_with_nothing_reads_empty(self):
        self.assertEqual(graph_event_fields(SimpleNamespace()), {
            "event_type": "", "entity_id": "", "entity_kind": "", "sequence_id": 0, "data": {}})


class DictEventTests(unittest.TestCase):
    def test_a_writebus_dict_event_is_read(self):
        ev = {"event_type": "ENTITY_UPSERT", "entity_id": "n1", "entity_type": "host",
              "entity_data": {"id": "n1"}, "room": "Global"}
        self.assertEqual(graph_event_fields(ev), {
            "event_type": "ENTITY_UPSERT", "entity_id": "n1", "entity_kind": "host",
            "sequence_id": 0, "data": {"id": "n1"}})

    def test_dict_fallback_names_and_sequence(self):
        f = graph_event_fields({"type": "X", "id": "i", "entity_kind": "k", "sequence_id": "9",
                                "payload": {"a": 1}})
        self.assertEqual((f["event_type"], f["entity_id"], f["entity_kind"], f["sequence_id"],
                          f["data"]), ("X", "i", "k", 9, {"a": 1}))

    def test_an_empty_dict_reads_empty(self):
        self.assertEqual(graph_event_fields({}), {
            "event_type": "", "entity_id": "", "entity_kind": "", "sequence_id": 0, "data": {}})

    def test_a_none_data_reads_as_an_empty_dict(self):
        self.assertEqual(graph_event_fields({"entity_data": None})["data"], {})


class WriteBusToStoreTests(unittest.TestCase):
    """The real WriteBus and GraphEventBus, then the same steps the drain takes."""

    def _commit(self, mission_id):
        bus = GraphEventBus(redis_client=None)
        received = []
        bus.subscribe(received.append)
        wb = WriteBus(operator_manager=None, hypergraph_engine=FakeHypergraph(), graph_event_bus=bus,
                      writebus_db_path=os.path.join(tempfile.mkdtemp(), "wb.sqlite3"))
        data = {"id": "n1", "kind": "host", "labels": {}}
        result = wb.commit(
            entity_id="n1", entity_type="host", entity_data=data,
            graph_ops=[GraphOp("NODE_CREATE", "n1", dict(data))],
            ctx=WriteContext(room_name="Global", operator_id="SYSTEM:TEST", source="test",
                             mission_id=mission_id),
            persist=False, audit=False)
        self.assertTrue(result.ok, result.errors)
        return received

    def test_a_writebus_event_is_a_dict_the_old_reader_could_not_read(self):
        (ev,) = self._commit("m1")
        self.assertIsInstance(ev, dict)
        self.assertEqual(getattr(ev, "event_type", ""), "")   # what the drain used to see

    def test_a_writebus_commit_with_a_mission_is_stored_under_it(self):
        (ev,) = self._commit("m1")
        fields = graph_event_fields(ev)
        self.assertEqual((fields["event_type"], fields["entity_id"]), ("ENTITY_UPSERT", "n1"))
        session_id, mission_id = live_session(fields["data"])
        self.assertEqual((session_id, mission_id), ("live:m1", "m1"))

        store = ScytheDuckStore(db_path=":memory:", parquet_dir="/tmp/scythe_event_fields_test")
        store.append(TacticalEvent(timestamp=1_700_000_000_000, event_type=fields["event_type"],
                                   entity_id=fields["entity_id"], session_id=session_id,
                                   payload=fields["data"], mission_id=mission_id))
        stored = store.events_for_mission("m1")
        self.assertEqual([(e.event_type, e.entity_id) for e in stored], [("ENTITY_UPSERT", "n1")])
        store.close()

    def test_a_writebus_commit_without_a_mission_stays_unbound(self):
        (ev,) = self._commit(None)
        fields = graph_event_fields(ev)
        self.assertEqual(fields["entity_id"], "n1")
        self.assertEqual(live_session(fields["data"]), ("global", None))


if __name__ == "__main__":
    unittest.main()
