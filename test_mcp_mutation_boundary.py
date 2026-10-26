"""#218 / ADR 0007 section 5: the mutating MCP tools against a guarded engine.

A real HypergraphEngine under a real WriteBus(strict_no_bypass=True).  Each tool must end in
an outcome its classification allows: ingest_live_event through the graph boundary,
decay_now as the documented maintenance exception.
"""
import os
import tempfile
import time
import unittest

import live_ingest
import mcp_registry
import writebus
from hypergraph_engine import HypergraphEngine


class _OperatorStub:
    def __getattr__(self, name):
        return lambda *a, **k: None


def _guarded_engine(strict=True):
    engine = HypergraphEngine()
    engine.decay_lambda = 1.0
    engine.add_node({"id": "a", "kind": "host", "position": [1, 2, 0]})
    engine.add_node({"id": "b", "kind": "host", "position": [1, 3, 0]})
    engine.add_edge({"id": "stale", "kind": "link", "nodes": ["a", "b"], "src": "a", "dst": "b",
                     "timestamp": time.time() - 10000})
    writebus.init_writebus(_OperatorStub(), engine, strict_no_bypass=strict,
                           writebus_db_path=os.path.join(tempfile.mkdtemp(), "wb.db"))
    return engine, mcp_registry.build_registry(engine)


def _event(eid, **data):
    return {"event_type": "NODE_CREATE", "entity_id": eid,
            "entity_data": {"kind": "host", "position": [5.0, 6.0, 0.0], **data}}


class IngestLiveEventBoundaryTest(unittest.TestCase):
    def setUp(self):
        live_ingest.live_event_queue.queue.clear()
        self.engine, self.reg = _guarded_engine()
        self.addCleanup(setattr, writebus, "_DEFAULT_BUS", None)

    def _run(self, **params):
        return self.reg["ingest_live_event"].tool.run(self.engine, params)

    def test_commits_through_writebus_under_strict_no_bypass(self):
        live_ingest.enqueue(_event("n1"))
        out = self._run(limit=5)
        self.assertEqual((out["committed"], out["dequeued"], out["rejected"]), (1, 1, 0))
        self.assertIn("n1", self.engine.nodes)

    def test_a_rejected_event_is_reported_not_silently_lost(self):
        live_ingest.enqueue(_event("good"))
        live_ingest.live_event_queue.queue.append({"event_type": "NODE_CREATE", "entity_data": {}})  # no entity_id
        out = self._run(limit=5)
        self.assertEqual((out["committed"], out["dequeued"], out["rejected"]), (1, 2, 1))
        self.assertEqual(len(out["rejected_events"]), 1)
        self.assertTrue(out["rejected_events"][0]["errors"])

    def test_missing_writebus_fails_closed_without_consuming_events(self):
        live_ingest.enqueue(_event("n1"))
        writebus._DEFAULT_BUS = None
        with self.assertRaises(RuntimeError):
            self._run(limit=5)
        self.assertEqual(len(live_ingest.live_event_queue.queue), 1)

    def test_a_bus_bound_to_another_engine_fails_closed_without_consuming_events(self):
        live_ingest.enqueue(_event("n1"))
        with self.assertRaises(RuntimeError):
            self.reg["ingest_live_event"].tool.run(HypergraphEngine(), {"limit": 5})
        self.assertEqual(len(live_ingest.live_event_queue.queue), 1)


class DecayNowMaintenanceExceptionTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, writebus, "_DEFAULT_BUS", None)

    def test_prunes_under_strict_no_bypass_and_records_the_ids(self):
        engine, reg = _guarded_engine(strict=True)
        with self.assertLogs("mcp_registry", level="INFO") as logs:
            out = reg["decay_now"].tool.run(engine, {})
        self.assertEqual(out["edges_pruned"], 1)
        self.assertEqual(out["edges_remaining"], 0)
        self.assertEqual(len(out["pruned_ids_sha256"]), 64)
        self.assertTrue(any("[DECAY_PRUNED]" in line and "stale" in line for line in logs.output))

    def test_the_engines_other_mutations_are_still_guarded(self):
        engine, _ = _guarded_engine(strict=True)
        with self.assertRaises(writebus.WriteBusKernelViolation):
            engine.remove_edge("stale")
        with self.assertRaises(writebus.WriteBusKernelViolation):
            engine.add_node({"id": "x", "kind": "host", "position": [1, 1, 0]})

    def test_background_style_decay_also_works_under_the_guard(self):
        engine, _ = _guarded_engine(strict=True)
        self.assertEqual(engine.decay_edges(), 1)

    def test_nothing_to_prune_records_no_ids(self):
        engine, reg = _guarded_engine(strict=True)
        engine.decay_edges()
        out = reg["decay_now"].tool.run(engine, {})
        self.assertEqual(out["edges_pruned"], 0)


if __name__ == "__main__":
    unittest.main()
