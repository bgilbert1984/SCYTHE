import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

from flask import Flask

import control_audit
from control_audit import ControlAuditStore, redact
from hypergraph_engine import HypergraphEngine
from mcp_effects import HARDWARE_CONTROL, LOCAL_STATE, READ_ONLY
from mcp_principal import OPERATOR_SESSION, Principal, principal_scope, proposal_scope
from mcp_registry import Registry, Tool
from mcp_server import register_mcp_routes


def _tool(name, run, mutates_state=True, effect=HARDWARE_CONTROL):
    return Tool(name, "d", {"type": "object"}, {"type": "object"}, run, mutates_state=mutates_state, effect=effect)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "audit.sqlite3")
        self.store = ControlAuditStore(self.path, retention_days=90)

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def rec(self, **kw):
        base = dict(execution_id="x1", action="rf_tune", effect=HARDWARE_CONTROL, result="success")
        return self.store.record(**{**base, **kw})

    def test_schema_matches_the_agreed_columns(self):
        self.rec()
        cols = {r[1] for r in sqlite3.connect(self.path).execute("PRAGMA table_info(control_audit_events)")}
        self.assertTrue({"event_id", "timestamp", "principal_kind", "principal_id", "session_id", "action", "effect",
                         "proposal_id", "parameters_json", "result", "result_summary", "software_version",
                         "host_id"} <= cols)

    def test_record_and_read_back(self):
        self.rec(principal={"kind": OPERATOR_SESSION, "operator_id": "op-1", "session_id": "s-1"},
                 proposal_id="p-1", parameters={"frequency_hz": 433e6}, result_summary="ok")
        (e,) = self.store.events(proposal_id="p-1")
        self.assertEqual((e["principal_kind"], e["principal_id"], e["session_id"]), (OPERATOR_SESSION, "op-1", "s-1"))
        self.assertEqual(json.loads(e["parameters_json"]), {"frequency_hz": 433e6})
        self.assertTrue(e["timestamp"].endswith("+00:00"))
        self.assertTrue(e["host_id"])

    def test_build_and_deployment_identity_are_recorded(self):
        self.store.close()
        store = ControlAuditStore(self.path, 90, deployment_id=lambda: "soak-arr-1")
        store.record(execution_id="x", action="a", effect="X", result="success")
        (e,) = store.events()
        self.assertEqual(e["deployment_id"], "soak-arr-1")
        self.assertTrue(e["host_id"])
        self.assertIn("software_version", e)                 # git commit; None outside a git checkout
        store.close()

    def test_deployment_id_failure_does_not_break_recording(self):
        self.store.close()
        def boom():
            raise RuntimeError("no app context")
        store = ControlAuditStore(self.path, 90, deployment_id=boom)
        store.record(execution_id="x", action="a", effect="X", result="success")
        self.assertIsNone(store.events()[0]["deployment_id"])
        store.close()

    def test_missing_principal_is_recorded_as_none_not_dropped(self):
        self.rec()
        self.assertEqual(self.store.events()[0]["principal_kind"], "none")

    def test_secrets_in_parameters_are_redacted(self):
        self.assertEqual(redact({"a": 1, "api_key": "k", "nested": {"Auth_Token": "t", "ok": 2}}),
                         {"a": 1, "api_key": "<redacted>", "nested": {"Auth_Token": "<redacted>", "ok": 2}})
        self.rec(parameters={"password": "hunter2", "frequency_hz": 1})
        self.assertNotIn("hunter2", self.store.events()[0]["parameters_json"])

    def test_update_is_refused(self):
        self.rec()
        with self.assertRaises(sqlite3.DatabaseError) as ctx:
            self.store._conn().execute("UPDATE control_audit_events SET result = 'tampered'")
        self.assertIn("append-only", str(ctx.exception))
        self.assertEqual(self.store.events()[0]["result"], "success")

    def test_delete_inside_retention_window_is_refused(self):
        self.rec()
        with self.assertRaises(sqlite3.DatabaseError):
            self.store._conn().execute("DELETE FROM control_audit_events")
        self.assertEqual(len(self.store.events()), 1)

    def test_purge_removes_only_expired_rows(self):
        self.rec(execution_id="old", timestamp=time.time() - 91 * 86400)
        self.rec(execution_id="new", timestamp=time.time() - 89 * 86400)
        self.assertEqual(self.store.purge_expired(), 1)
        self.assertEqual([e["execution_id"] for e in self.store.events()], ["new"])

    def test_retention_is_configuration_and_persisted(self):
        self.store.close()
        short = ControlAuditStore(self.path, retention_days=7)
        short.record(execution_id="a", action="a", effect="X", result="success", timestamp=time.time() - 8 * 86400)
        self.assertEqual(short.purge_expired(), 1)
        short.close()
        with self.assertRaises(ValueError):
            ControlAuditStore(self.path, retention_days=0)
        with mock.patch.dict(os.environ, {"SCYTHE_CONTROL_AUDIT_RETENTION_DAYS": "365"}):
            self.assertEqual(control_audit.configure(self.path).retention_days, 365)
        control_audit.reset()

    def test_inspection_open_does_not_overwrite_stored_retention(self):
        self.store.close()
        ControlAuditStore(self.path, retention_days=365).events()
        viewer = ControlAuditStore(self.path, retention_days=None)
        viewer.events()
        self.assertEqual(viewer.retention_days, 365)
        viewer.close()

    def test_path_provider_is_resolved_lazily(self):
        late = os.path.join(self.dir.name, "late", "a.sqlite3")
        holder = {"p": os.path.join(self.dir.name, "early.sqlite3")}
        store = ControlAuditStore(lambda: holder["p"], 90)
        holder["p"] = late
        store.record(execution_id="e", action="a", effect="X", result="success")
        self.assertTrue(os.path.exists(late))
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "early.sqlite3")))
        store.close()

    def test_safe_record_never_raises_and_counts_failures(self):
        bad = ControlAuditStore(os.path.join(self.dir.name, "x", "y"), 90)
        with mock.patch.object(bad, "record", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertLogs("control_audit", level="ERROR"):
                self.assertIsNone(bad.safe_record(execution_id="e", action="a", effect="X", result="success"))
        self.assertEqual(bad.write_failures, 1)


class RegistryHookTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = control_audit.configure(os.path.join(self.dir.name, "audit.sqlite3"), 90)
        self.registry = Registry()
        self.engine = HypergraphEngine()

    def tearDown(self):
        control_audit.reset()
        self.dir.cleanup()

    def test_mutating_tool_writes_started_then_success_with_identity(self):
        self.registry.register(_tool("rf_tune_like", lambda **k: {"tuned": True}))
        alice = Principal(OPERATOR_SESSION, operator_id="op-alice", session_id="sess-1")
        with principal_scope(alice), proposal_scope("prop-1"):
            self.registry.execute(self.engine, "rf_tune_like", {"frequency_hz": 145.5e6}, agent_mode="mutate")
        started, done = self.store.events(proposal_id="prop-1")
        self.assertEqual((started["result"], done["result"]), ("started", "success"))
        self.assertEqual(started["execution_id"], done["execution_id"])
        for e in (started, done):
            self.assertEqual((e["principal_id"], e["effect"], e["action"]),
                             ("op-alice", HARDWARE_CONTROL, "rf_tune_like"))
        self.assertEqual(json.loads(done["parameters_json"]), {"frequency_hz": 145.5e6})

    def test_failure_is_recorded_and_still_raises(self):
        def boom(**k):
            raise RuntimeError("rig refused")
        self.registry.register(_tool("rf_tune_like", boom))
        with self.assertRaises(RuntimeError):
            self.registry.execute(self.engine, "rf_tune_like", {}, agent_mode="mutate")
        started, failed = self.store.events()
        self.assertEqual((started["result"], failed["result"]), ("started", "error"))
        self.assertIn("rig refused", failed["result_summary"])

    def _done_summary(self, tool_name):
        *_, done = self.store.events()
        self.assertEqual(done["action"], tool_name)
        return json.loads(done["result_summary"])

    def test_hardware_result_keeps_the_resulting_state_not_just_dict_markers(self):
        """#219: the audit says what state the hardware ended in."""
        result = {"action": "start", "changed": True,
                  "bridge": {"bridge_state": "streaming", "running": True, "latest_sequence": 7,
                             "config": {"sample_rate_hz": 2.4e6, "api_key": "k-123", "deep": {"x": {"y": 1}}},
                             "gains": [1, 2, 3]}}
        self.registry.register(_tool("rf_capture_start", lambda **k: result))
        self.registry.execute(self.engine, "rf_capture_start", {}, agent_mode="mutate")
        summary = self._done_summary("rf_capture_start")
        self.assertEqual(summary["bridge.bridge_state"], "streaming")
        self.assertIs(summary["bridge.running"], True)
        self.assertEqual(summary["bridge.config.sample_rate_hz"], 2.4e6)
        self.assertEqual(summary["bridge.config.api_key"], "<redacted>")
        self.assertEqual(summary["bridge.config.deep"], "<dict>")
        self.assertEqual(summary["bridge.gains"], "<list: 3 items>")
        self.assertNotIn("k-123", json.dumps(summary))

    def test_hardware_summary_is_bounded(self):
        big = {f"k{i}": "x" * 1000 for i in range(200)}
        self.registry.register(_tool("rf_stream_configure", lambda **k: {"bridge": big}))
        self.registry.execute(self.engine, "rf_stream_configure", {}, agent_mode="mutate")
        summary = self._done_summary("rf_stream_configure")
        self.assertLessEqual(len(summary), 61)
        self.assertIs(summary["_truncated"], True)
        self.assertTrue(all(len(v) <= 256 for v in summary.values() if isinstance(v, str)))

    def test_non_hardware_results_keep_the_scalar_only_summary(self):
        self.registry.register(_tool("decay_like", lambda **k: {"n": 1, "inner": {"a": 1}}, effect=LOCAL_STATE))
        self.registry.execute(self.engine, "decay_like", {}, agent_mode="mutate")
        self.assertEqual(self._done_summary("decay_like"), {"n": 1, "inner": "<dict>"})

    def test_read_only_tool_is_not_audited(self):
        self.registry.register(_tool("lookup", lambda **k: {}, mutates_state=False, effect=READ_ONLY))
        self.registry.execute(self.engine, "lookup", {}, agent_mode="observe")
        self.assertEqual(self.store.events(), [])

    def test_unconfigured_store_is_a_no_op(self):
        control_audit.reset()
        self.registry.register(_tool("rf_tune_like", lambda **k: {"ok": 1}))
        self.assertEqual(self.registry.execute(self.engine, "rf_tune_like", {}, agent_mode="mutate"), {"ok": 1})

    def test_audit_write_failure_does_not_block_the_action(self):
        self.registry.register(_tool("rf_tune_like", lambda **k: {"ok": 1}))
        with mock.patch.object(self.store, "record", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertLogs("control_audit", level="ERROR"):
                result = self.registry.execute(self.engine, "rf_tune_like", {}, agent_mode="mutate")
        self.assertEqual(result, {"ok": 1})
        self.assertEqual(self.store.write_failures, 2)         # started + success


class McpFlowTest(unittest.TestCase):
    """propose by one operator, execute by another, over the real /mcp route."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = control_audit.configure(os.path.join(self.dir.name, "audit.sqlite3"), 90)
        self.app = Flask(__name__)
        self.current = None
        self.handler = register_mcp_routes(self.app, HypergraphEngine(), use_orchestrator=True,
                                           auth_validator=lambda: self.current)
        tool = _tool("probe_control", lambda **k: {"done": True}, effect=LOCAL_STATE)
        self.handler._tools["probe_control"] = tool
        self.handler._orchestrator.registry.register(tool)
        self.handler._orchestrator.set_phase(2, dry_run=False)
        self.client = self.app.test_client()

    def tearDown(self):
        control_audit.reset()
        self.dir.cleanup()

    def call(self, method, params):
        return self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).get_json()["result"]

    def test_execution_is_attributed_and_linked_to_its_proposal(self):
        self.current = Principal(OPERATOR_SESSION, operator_id="op-alice", session_id="s-a")
        proposal = self.call("orchestrate/propose", {"tool_name": "probe_control", "params": {"x": 1}, "confidence": 0.95})
        self.current = Principal(OPERATOR_SESSION, operator_id="op-bob", session_id="s-b")
        self.assertTrue(self.call("orchestrate/execute", {"proposal_id": proposal["proposal_id"]})["ok"])
        rows = self.store.events(proposal_id=proposal["proposal_id"])
        self.assertEqual([r["action"] for r in rows][0], "proposal.created")     # the lifecycle row, from the proposer
        self.assertEqual(rows[0]["principal_id"], "op-alice")
        started, done = [r for r in rows if not r["action"].startswith("proposal.")]
        self.assertEqual([started["result"], done["result"]], ["started", "success"])
        self.assertEqual({started["principal_id"], done["principal_id"]}, {"op-bob"})
        self.assertEqual(done["effect"], LOCAL_STATE)


if __name__ == "__main__":
    unittest.main()
