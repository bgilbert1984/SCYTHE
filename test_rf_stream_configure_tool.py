import importlib.util
import logging
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import control_audit
from mcp_effects import HARDWARE_CONTROL
from mcp_principal import current_source, source_scope
from mcp_server import MCPHandler
from rf_bridge import get_rf_bridge, reset_rf_bridge_for_tests
from rf_mcp import register_rf_tools


class _Engine:
    nodes = {}
    edges = {}
    degree = {}


class StreamConfigureToolTest(unittest.TestCase):
    """Slice 0 of the /api/sdr migration (ADR 0005 Decision 5, ADR 0006 Decision 7):
    rf_stream_configure exists as a tool, and the lifecycle can carry a source."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        reset_rf_bridge_for_tests()
        self.addCleanup(reset_rf_bridge_for_tests)
        with mock.patch.dict(os.environ, {"MCP_APPROVAL_MODES": "HARDWARE_CONTROL=AUTOMATED"}):
            self.handler = MCPHandler(_Engine(), use_orchestrator=True)
        register_rf_tools(self.handler.engine, self.handler)
        self.orch = self.handler._orchestrator
        self.tool = self.orch.registry._tools["rf_stream_configure"]
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.audit_path = os.path.join(self.dir.name, "a.sqlite3")
        control_audit.configure(self.audit_path, 90)
        self.addCleanup(control_audit.reset)

    def rows(self):
        with sqlite3.connect(self.audit_path) as conn:
            return conn.execute("SELECT action, source, result FROM control_audit_events ORDER BY ts_epoch, rowid").fetchall()

    def test_it_is_a_hardware_control_mutation_in_both_registries(self):
        self.assertTrue(self.tool.mutates_state)
        self.assertEqual(self.tool.effect, HARDWARE_CONTROL)
        self.assertIn("rf_stream_configure", self.handler._registry._tools)

    def test_a_direct_call_is_refused_like_the_other_hardware_tools(self):
        r = self.handler.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "rf_stream_configure", "arguments": {"fft_size": 1024}}})
        self.assertIn("orchestrate/propose", r["error"]["message"])

    # Registry.execute skips parameter validation when jsonschema is missing (the python-rf job
    # does not install it); the python-platform job does and runs this file.
    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema not installed")
    def test_the_schema_is_strict_and_a_bad_request_changes_nothing(self):
        self.orch.set_phase(2, dry_run=False)
        before = get_rf_bridge().config
        for bad in ({}, {"unknown": 1}, {"fft_size": 0}, {"sample_rate_hz": -1}, {"fft_size": "big"}):
            with self.subTest(bad=bad):
                p = self.orch.propose_action("rf_stream_configure", bad, confidence=0.99, justification="t")
                out = self.orch.execute_proposal(p["proposal_id"])
                self.assertFalse(out["ok"], out)
                self.assertEqual(get_rf_bridge().config, before)

    def test_in_phase_2_it_changes_the_bridge_and_the_audit_says_mcp(self):
        self.orch.set_phase(2, dry_run=False)
        p = self.orch.propose_action("rf_stream_configure", {"fft_size": 1024}, confidence=0.99, justification="t")
        self.assertEqual(p["status"], "approved", p)
        out = self.orch.execute_proposal(p["proposal_id"])
        self.assertTrue(out["ok"], out)
        self.assertEqual(get_rf_bridge().config.fft_size, 1024)
        rows = [r for r in self.rows() if not r[0].startswith("proposal.")]
        self.assertEqual(rows, [("rf_stream_configure", "mcp", "started"), ("rf_stream_configure", "mcp", "success")])

    def test_a_source_scope_is_carried_onto_the_execution_rows_and_then_reset(self):
        self.assertEqual(current_source(), "mcp")
        self.orch.set_phase(2, dry_run=False)
        p = self.orch.propose_action("rf_stream_configure", {"fft_size": 512}, confidence=0.99, justification="t")
        with source_scope("api.sdr.config"):
            self.assertEqual(current_source(), "api.sdr.config")
            self.assertTrue(self.orch.execute_proposal(p["proposal_id"])["ok"])
        self.assertEqual(current_source(), "mcp")
        rows = [r for r in self.rows() if not r[0].startswith("proposal.")]
        self.assertEqual({r[1] for r in rows}, {"api.sdr.config"})


if __name__ == "__main__":
    unittest.main()
