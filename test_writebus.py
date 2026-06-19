import json
import os
import sqlite3
import tempfile
import unittest

from writebus import (
    CommitStatus,
    GraphOp,
    SQLiteRecordSink,
    WriteBus,
    WriteBusKernelViolation,
    WriteContext,
)


class FakeHypergraph:
    def __init__(self, ok=True):
        self.ok = ok
        self.events = []

    def apply_graph_event(self, event):
        self.events.append(event)
        return self.ok


class NestedFakeHypergraph:
    def __init__(self):
        self.nodes = []
        self.events = []

    def add_node(self, node):
        self.nodes.append(node)
        return node.get("id")

    def apply_graph_event(self, event):
        self.events.append(event)
        self.add_node(event["entity_data"])
        return True


class FakeGraphEventBus:
    def __init__(self):
        self.subscribers = []

    def publish(self, event):
        for cb in list(self.subscribers):
            cb(event)


class FakeOperatorManager:
    def __init__(self, *, fail_publish=False):
        self.fail_publish = fail_publish
        self.rooms = {"Global": {"room_id": "room-global"}}
        self.published = []

    def get_room_by_name(self, name):
        return self.rooms.get(name)

    def create_room(self, name, description="", operator=None):
        room = {"room_id": f"room-{name}"}
        self.rooms[name] = room
        return room

    def publish_to_room(self, room_id, *, entity_id, entity_type, entity_data, operator=None):
        if self.fail_publish:
            raise RuntimeError("room store down")
        self.published.append((room_id, entity_id, entity_type, entity_data, operator))
        return True


class RejectingSchemaValidator:
    def validate(self, entity_type, entity_data):
        return {"ok": False, "reason": "schema says no"}


class WriteBusTests(unittest.TestCase):
    def _db_path(self, tmpdir):
        return os.path.join(tmpdir, "writebus.sqlite3")

    def test_committed_idempotency_key_replays_without_second_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hg = FakeHypergraph()
            opman = FakeOperatorManager()
            wb = WriteBus(opman, hg, writebus_db_path=self._db_path(tmpdir))
            ctx = WriteContext(operator_id="operator-1", request_id="req-1")
            op = GraphOp("NODE_UPDATE", "node-1", {"id": "node-1", "kind": "test"})

            first = wb.commit(
                entity_id="entity-1",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-1", "type": "TEST_ENTITY"},
                graph_ops=[op],
                ctx=ctx,
                idempotency_key="idem-1",
            )
            second = wb.commit(
                entity_id="entity-1",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-1", "type": "TEST_ENTITY"},
                graph_ops=[op],
                ctx=ctx,
                idempotency_key="idem-1",
            )

            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertTrue(second.debug["idempotent_replay"])
            self.assertEqual(CommitStatus.COMMITTED, second.commit_status)
            self.assertEqual(1, len(hg.events))
            self.assertEqual(1, len(opman.published))

    def test_room_failure_records_dead_letter_and_repair_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self._db_path(tmpdir)
            hg = FakeHypergraph()
            opman = FakeOperatorManager(fail_publish=True)
            wb = WriteBus(opman, hg, writebus_db_path=db_path)
            ctx = WriteContext(operator_id="operator-1", request_id="req-2")

            res = wb.commit(
                entity_id="entity-2",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-2", "type": "TEST_ENTITY"},
                graph_ops=[GraphOp("NODE_UPDATE", "node-2", {"id": "node-2", "kind": "test"})],
                ctx=ctx,
                idempotency_key="idem-2",
            )

            self.assertFalse(res.ok)
            self.assertTrue(res.graph_applied)
            self.assertEqual(CommitStatus.FAILED_PARTIAL, res.commit_status)
            self.assertEqual(1, len(hg.events))

            with sqlite3.connect(db_path) as conn:
                dead_letters = conn.execute("SELECT COUNT(*) FROM writebus_dead_letter").fetchone()[0]
                repair_tasks = conn.execute("SELECT COUNT(*) FROM writebus_repair_tasks").fetchone()[0]
                status = conn.execute(
                    "SELECT status FROM writebus_commit_status WHERE idempotency_key = ?",
                    ("idem-2",),
                ).fetchone()[0]

            self.assertEqual(1, dead_letters)
            self.assertEqual(1, repair_tasks)
            self.assertEqual(CommitStatus.FAILED_PARTIAL, status)

    def test_schema_validation_stops_before_graph_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hg = FakeHypergraph()
            wb = WriteBus(
                FakeOperatorManager(),
                hg,
                schema_validator=RejectingSchemaValidator(),
                writebus_db_path=self._db_path(tmpdir),
            )
            ctx = WriteContext(operator_id="operator-1", request_id="req-3")

            res = wb.commit(
                entity_id="entity-3",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-3", "type": "TEST_ENTITY"},
                graph_ops=[GraphOp("NODE_UPDATE", "node-3", {"id": "node-3"})],
                ctx=ctx,
                idempotency_key="idem-3",
            )

            self.assertFalse(res.ok)
            self.assertEqual(CommitStatus.REJECTED, res.commit_status)
            self.assertEqual([], hg.events)
            self.assertIn("schema says no", " ".join(res.errors))

            with sqlite3.connect(self._db_path(tmpdir)) as conn:
                idempotency_rows = conn.execute(
                    "SELECT COUNT(*) FROM writebus_idempotency WHERE idempotency_key = ?",
                    ("idem-3",),
                ).fetchone()[0]

            self.assertEqual(0, idempotency_rows)

    def test_skipped_stages_are_recorded_honestly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hg = FakeHypergraph()
            opman = FakeOperatorManager()
            wb = WriteBus(opman, hg, writebus_db_path=self._db_path(tmpdir))
            ctx = WriteContext(operator_id="operator-1", request_id="req-4")

            res = wb.commit(
                entity_id="entity-4",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-4", "type": "TEST_ENTITY"},
                graph_ops=[GraphOp("NODE_UPDATE", "node-4", {"id": "node-4"})],
                ctx=ctx,
                persist=False,
                audit=False,
                idempotency_key="idem-4",
            )

            self.assertTrue(res.ok)
            self.assertFalse(res.persisted)
            self.assertEqual(CommitStatus.COMMITTED, res.commit_status)
            self.assertIn(CommitStatus.ROOM_SKIPPED, res.debug["status_history"])
            self.assertIn(CommitStatus.BUS_SKIPPED, res.debug["status_history"])
            self.assertIn(CommitStatus.AUDIT_SKIPPED, res.debug["status_history"])
            self.assertEqual([], opman.published)

    def test_stale_pending_idempotency_can_be_reclaimed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self._db_path(tmpdir)
            hg = FakeHypergraph()
            wb = WriteBus(
                FakeOperatorManager(),
                hg,
                writebus_db_path=db_path,
                idempotency_stale_seconds=60,
            )
            wb.idempotency_store.record_pending("idem-stale", {"original": True})
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "UPDATE writebus_idempotency SET updated_at = ? WHERE idempotency_key = ?",
                    ("2000-01-01T00:00:00Z", "idem-stale"),
                )

            res = wb.commit(
                entity_id="entity-stale",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-stale", "type": "TEST_ENTITY"},
                graph_ops=[GraphOp("NODE_UPDATE", "node-stale", {"id": "node-stale"})],
                ctx=WriteContext(operator_id="operator-1", request_id="req-stale"),
                idempotency_key="idem-stale",
            )

            self.assertTrue(res.ok)
            self.assertTrue(res.debug["reclaimed_stale_pending"])
            self.assertEqual(1, len(hg.events))

    def test_fresh_pending_idempotency_blocks_duplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hg = FakeHypergraph()
            wb = WriteBus(FakeOperatorManager(), hg, writebus_db_path=self._db_path(tmpdir))
            wb.idempotency_store.record_pending("idem-pending", {"original": True})

            res = wb.commit(
                entity_id="entity-pending",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-pending", "type": "TEST_ENTITY"},
                graph_ops=[GraphOp("NODE_UPDATE", "node-pending", {"id": "node-pending"})],
                ctx=WriteContext(operator_id="operator-1", request_id="req-pending"),
                idempotency_key="idem-pending",
            )

            self.assertFalse(res.ok)
            self.assertIn("idempotency_pending", " ".join(res.errors))
            self.assertEqual([], hg.events)

    def test_record_sink_rejects_unsafe_table_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                SQLiteRecordSink(self._db_path(tmpdir), "bad;drop_table")

    def test_temporal_context_records_entity_type(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self._db_path(tmpdir)
            wb = WriteBus(FakeOperatorManager(), FakeHypergraph(), writebus_db_path=db_path)

            res = wb.commit(
                entity_id="entity-temporal",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-temporal"},
                graph_ops=[GraphOp("NODE_UPDATE", "node-temporal", {"id": "node-temporal"})],
                ctx=WriteContext(operator_id="operator-1", request_id="req-temporal"),
                idempotency_key="idem-temporal",
            )

            self.assertTrue(res.ok)
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT summary_json FROM writebus_temporal_context WHERE entity_id = ?",
                    ("entity-temporal",),
                ).fetchone()

            self.assertIsNotNone(row)
            summary = json.loads(row[0])
            self.assertEqual("TEST_ENTITY", summary["entity_type"])

    def test_strict_kernel_blocks_direct_hypergraph_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hg = NestedFakeHypergraph()
            WriteBus(
                FakeOperatorManager(),
                hg,
                strict_no_bypass=True,
                writebus_db_path=self._db_path(tmpdir),
            )

            with self.assertRaises(WriteBusKernelViolation):
                hg.add_node({"id": "direct-node"})

    def test_strict_kernel_allows_nested_hypergraph_mutation_inside_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hg = NestedFakeHypergraph()
            wb = WriteBus(
                FakeOperatorManager(),
                hg,
                strict_no_bypass=True,
                writebus_db_path=self._db_path(tmpdir),
            )

            res = wb.commit(
                entity_id="entity-kernel",
                entity_type="TEST_ENTITY",
                entity_data={"id": "entity-kernel", "type": "TEST_ENTITY"},
                graph_ops=[GraphOp("NODE_UPDATE", "node-kernel", {"id": "node-kernel"})],
                ctx=WriteContext(operator_id="operator-1", request_id="req-kernel"),
                idempotency_key="idem-kernel",
            )

            self.assertTrue(res.ok)
            self.assertEqual(1, len(hg.events))
            self.assertEqual(1, len(hg.nodes))

    def test_strict_kernel_blocks_direct_room_publish(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            opman = FakeOperatorManager()
            WriteBus(
                opman,
                FakeHypergraph(),
                strict_no_bypass=True,
                writebus_db_path=self._db_path(tmpdir),
            )

            with self.assertRaises(WriteBusKernelViolation):
                opman.publish_to_room(
                    "room-global",
                    entity_id="direct-entity",
                    entity_type="TEST_ENTITY",
                    entity_data={},
                )

    def test_strict_kernel_removes_graph_event_bus_hypergraph_writer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hg = FakeHypergraph()
            bus = FakeGraphEventBus()
            bus.subscribers.append(hg.apply_graph_event)

            WriteBus(
                FakeOperatorManager(),
                hg,
                graph_event_bus=bus,
                strict_no_bypass=True,
                writebus_db_path=self._db_path(tmpdir),
            )

            self.assertEqual([], bus.subscribers)


if __name__ == "__main__":
    unittest.main()
