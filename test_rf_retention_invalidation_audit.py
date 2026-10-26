"""#219: an IQ-ring invalidation caused by a control-plane execution is traceable.

principal -> proposal -> execution -> retention invalidation, in the one control audit.
System-triggered invalidations (reconnect, starvation) stay in the in-memory history, marked
as such, and are not appended to the durable audit.
"""
import json
import os
import tempfile
import unittest

import numpy as np

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import HARDWARE_CONTROL
from mcp_principal import OPERATOR_SESSION, Principal, principal_scope, proposal_scope
from mcp_registry import Registry, Tool
from rf_iq_retention import IQRetentionOwner


def _owner():
    owner = IQRetentionOwner(sensor_id="T", sample_type="int16", sample_rate_hz=2.4e6, owns_capture=True)
    owner.append(np.full(4096, 0.25, dtype=np.complex64), 1.0)
    return owner


def _tune_tool(owner, then_fail=False):
    def run(**kw):
        owner.invalidate("RETUNE")
        if then_fail:
            raise RuntimeError("rig refused after the clear")
        return {"reachable": True}
    return Tool("rf_tune", "d", {"type": "object"}, {"type": "object"}, run,
                mutates_state=True, effect=HARDWARE_CONTROL)


class RetentionInvalidationAuditTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = control_audit.configure(os.path.join(self.dir.name, "audit.sqlite3"), 90)
        self.registry = Registry()
        self.engine = HypergraphEngine()
        self.alice = Principal(OPERATOR_SESSION, operator_id="op-alice", session_id="sess-1")

    def tearDown(self):
        control_audit.reset()
        self.dir.cleanup()

    def _execute(self, tool):
        self.registry.register(tool)
        with principal_scope(self.alice), proposal_scope("prop-1"):
            return self.registry.execute(self.engine, tool.name, {}, agent_mode="mutate")

    def test_an_operator_retune_leaves_a_row_linked_to_its_execution_and_proposal(self):
        owner = _owner()
        self._execute(_tune_tool(owner))
        started, inval, done = self.store.events(proposal_id="prop-1")
        self.assertEqual((started["result"], done["result"]), ("started", "success"))
        self.assertEqual((inval["action"], inval["result"]), (control_audit.RETENTION_INVALIDATED, "recorded"))
        params = json.loads(inval["parameters_json"])
        self.assertEqual((params["reason"], params["triggering_execution_id"]), ("RETUNE", started["execution_id"]))
        self.assertEqual((inval["principal_id"], inval["proposal_id"]), ("op-alice", "prop-1"))
        self.assertNotEqual(inval["execution_id"], started["execution_id"])

    def test_the_history_entry_names_the_cause(self):
        owner = _owner()
        self._execute(_tune_tool(owner))
        cause = owner.status()["invalidation_history"][-1]["cause"]
        self.assertEqual(cause["kind"], "lifecycle")
        self.assertEqual((cause["proposal_id"], cause["principal_id"]), ("prop-1", "op-alice"))
        self.assertTrue(cause["execution_id"])

    def test_a_system_invalidation_is_marked_system_and_not_made_durable(self):
        owner = _owner()
        owner.invalidate("RECONNECT")
        self.assertEqual(owner.status()["invalidation_history"][-1]["cause"], {"kind": "system"})
        self.assertEqual(self.store.events(), [])

    def test_the_context_does_not_leak_past_the_execution(self):
        owner = _owner()
        self._execute(_tune_tool(owner))
        owner.append(np.full(4096, 0.25, dtype=np.complex64), 2.0)
        owner.invalidate("DISCONNECT")
        self.assertEqual(owner.status()["invalidation_history"][-1]["cause"], {"kind": "system"})
        self.assertIsNone(control_audit.current_execution_id())

    def test_a_failed_execution_is_not_reconstructed_as_executed_because_of_the_row(self):
        owner = _owner()
        with self.assertRaises(RuntimeError):
            self._execute(_tune_tool(owner, then_fail=True))
        out = control_audit.reconstruct_proposal(self.store, "prop-1")
        self.assertFalse(out["executed"])
        self.assertEqual([i["reason"] for i in out["retention_invalidations"]], ["RETUNE"])

    def test_a_successful_execution_reconstructs_with_its_invalidations(self):
        owner = _owner()
        self._execute(_tune_tool(owner))
        out = control_audit.reconstruct_proposal(self.store, "prop-1")
        self.assertTrue(out["executed"])
        self.assertEqual(out["executed_by"], "op-alice")
        self.assertEqual(len(out["retention_invalidations"]), 1)

    def test_without_a_configured_store_nothing_breaks(self):
        control_audit.reset()
        owner = _owner()
        self._execute(_tune_tool(owner))
        self.assertEqual(owner.status()["invalidation_history"][-1]["cause"]["kind"], "lifecycle")


if __name__ == "__main__":
    unittest.main()
