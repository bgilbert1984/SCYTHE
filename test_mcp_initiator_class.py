import logging
import os
import tempfile
import unittest
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import APPROVAL_MODES_ENV, LOCAL_STATE
from mcp_principal import (
    AUTONOMOUS, INITIATOR_CLASSES, OPERATOR_COMMAND, OPERATOR_SESSION, Principal, current_initiator, principal_scope,
)
from mcp_server import MCPHandler, ToolDef

ALICE = Principal(kind=OPERATOR_SESSION, operator_id="alice", session_id="s1")
BOB = Principal(kind=OPERATOR_SESSION, operator_id="bob", session_id="s2")
SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}}


class _Fixture(unittest.TestCase):
    """ADR 0005 Decision 5: the initiator class is assigned by server code, recorded once on the
    proposal, carried onto the audit rows, and decides only whether the autonomy phase applies."""
    SETTING = ""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)

    def setUp(self):
        self.ran = []
        self.seen_initiator = []
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = control_audit.configure(os.path.join(self.dir.name, "a.sqlite3"), 90)
        self.addCleanup(control_audit.reset)
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: self.SETTING}):
            self.h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        self.h._tools["probe_write"] = ToolDef(
            "probe_write", "d", SCHEMA,
            lambda p: self.ran.append(p) or self.seen_initiator.append(current_initiator()) or {"ok": True},
            mutates_state=True, effect=LOCAL_STATE)
        self.o = self.h._orchestrator

    def propose(self, initiator=None, who=ALICE, params=None):
        kw = {} if initiator is None else {"initiator": initiator}
        with principal_scope(who):
            return self.o.propose_action("probe_write", params if params is not None else {"n": 1},
                                         confidence=0.9, **kw)

    def execute(self, p, who=ALICE):
        with principal_scope(who):
            return self.o.execute_proposal(p["proposal_id"])

    def rows(self, p):
        return self.store.events(proposal_id=p["proposal_id"])


class ClassesTest(_Fixture):
    def test_the_two_classes_are_the_only_ones(self):
        self.assertEqual(set(INITIATOR_CLASSES), {AUTONOMOUS, OPERATOR_COMMAND})

    def test_a_proposal_defaults_to_autonomous(self):
        self.assertEqual(self.propose()["initiator"], AUTONOMOUS)

    def test_an_unknown_class_is_refused(self):
        with self.assertRaises(ValueError):
            self.propose(initiator="admin")


class AutonomousTest(_Fixture):
    def test_phase_0_blocks_and_nothing_runs_or_is_audited_as_executed(self):
        p = self.propose()
        out = self.execute(p)
        self.assertFalse(out["ok"])
        self.assertIn("observe-only", out["error"])
        self.assertEqual(self.ran, [])
        self.assertEqual([r for r in self.rows(p) if r["action"] == "probe_write"], [])

    def test_phase_1_is_a_shadow_run(self):
        self.o.set_phase(1, dry_run=False)
        out = self.execute(self.propose())
        self.assertTrue(out["result"]["_shadow_mode"])
        self.assertEqual(self.ran, [])

    def test_phase_2_runs_it(self):
        self.o.set_phase(2, dry_run=False)
        self.assertTrue(self.execute(self.propose())["ok"])
        self.assertEqual(len(self.ran), 1)


class OperatorCommandTest(_Fixture):
    def test_it_runs_in_phase_0_where_an_autonomous_proposal_is_blocked(self):
        self.assertEqual(self.o.phase.value, 0)
        self.assertFalse(self.execute(self.propose())["ok"])
        out = self.execute(self.propose(OPERATOR_COMMAND))
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(self.ran), 1)

    def test_it_is_not_a_shadow_run_in_phase_1(self):
        self.o.set_phase(1, dry_run=False)
        out = self.execute(self.propose(OPERATOR_COMMAND))
        self.assertNotIn("_shadow_mode", out["result"])
        self.assertEqual(len(self.ran), 1)

    def test_it_is_not_held_to_the_mutation_budget(self):
        self.o.set_phase(2, mutation_budget=1, dry_run=False)   # effective budget int(1 * trust) is 0
        self.assertFalse(self.execute(self.propose())["ok"])
        self.assertTrue(self.execute(self.propose(OPERATOR_COMMAND))["ok"])

    def test_it_still_goes_through_the_proposal_checks(self):
        p = self.propose(OPERATOR_COMMAND)
        p["status"] = "proposed"
        self.assertFalse(self.execute(p)["ok"])
        self.assertEqual(self.ran, [])
        low = self.o.propose_action("probe_write", {"n": 1}, confidence=0.1, initiator=OPERATOR_COMMAND)
        self.assertEqual(low["status"], "rejected")
        self.assertFalse(self.execute(low)["ok"])

    def test_agent_stability_gates_do_not_apply_to_it_and_do_to_an_agent(self):
        self.o.set_phase(2, dry_run=False)
        for _ in range(5):
            self.o.dual_agent.stability.add_mutation()      # an agent burst: 5 mutations in a minute
        self.assertEqual(self.propose()["status"], "rejected")
        op = self.propose(OPERATOR_COMMAND)
        self.assertEqual(op["status"], "approved")
        before = len(self.o.dual_agent.stability.mutation_timestamps)
        self.assertTrue(self.execute(op)["ok"])
        self.assertEqual(len(self.o.dual_agent.stability.mutation_timestamps), before)   # and it is not counted

    def test_it_runs_an_unknown_tool_nowhere(self):
        p = self.o.propose_action("no_such_tool", {}, confidence=0.9, initiator=OPERATOR_COMMAND)
        self.assertFalse(self.execute(p)["ok"])


class OperatorCommandSecondPrincipalTest(_Fixture):
    SETTING = "LOCAL_STATE=SECOND_PRINCIPAL"

    def test_the_approval_mode_still_applies_and_the_phase_does_not(self):
        p = self.propose(OPERATOR_COMMAND)
        self.assertEqual(p["status"], "pending_approval")
        self.assertFalse(self.execute(p)["ok"])
        with principal_scope(BOB):
            self.assertEqual(self.o.approve_proposal(p["proposal_id"], "approve")["status"], "approved")
        self.assertTrue(self.execute(p, BOB)["ok"])          # phase 0, and it ran
        self.assertEqual(len(self.ran), 1)

    def test_a_changed_parameter_digest_is_refused(self):
        p = self.propose(OPERATOR_COMMAND)
        with principal_scope(BOB):
            self.o.approve_proposal(p["proposal_id"], "approve")
        p["params"] = {"n": 2}
        out = self.execute(p, BOB)
        self.assertFalse(out["ok"])
        self.assertEqual(self.ran, [])


class AttributionTest(_Fixture):
    SETTING = "LOCAL_STATE=SECOND_PRINCIPAL"

    def test_every_row_for_a_proposal_carries_the_initiator_it_was_created_with(self):
        for cls in INITIATOR_CLASSES:
            with self.subTest(cls=cls):
                p = self.propose(cls)
                with principal_scope(BOB):
                    self.o.approve_proposal(p["proposal_id"], "approve")
                self.o.set_phase(2, dry_run=False)
                self.assertTrue(self.execute(p, BOB)["ok"])
                rows = self.rows(p)
                self.assertEqual([r["action"] for r in rows],
                                 ["proposal.created", "proposal.approved", "probe_write", "probe_write"])
                self.assertEqual({r["initiator"] for r in rows}, {cls})
                self.o.set_phase(0)

    def test_the_initiator_is_visible_to_the_running_tool_and_gone_afterwards(self):
        self.o.set_phase(2, dry_run=False)
        p = self.propose(OPERATOR_COMMAND)
        with principal_scope(BOB):
            self.o.approve_proposal(p["proposal_id"], "approve")
        self.execute(p, BOB)
        self.assertEqual(self.seen_initiator, [OPERATOR_COMMAND])
        self.assertIsNone(current_initiator())

    def test_a_pre_existing_store_gains_the_column(self):
        import sqlite3
        path = os.path.join(self.dir.name, "old.sqlite3")
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE control_audit_events (event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,"
                         " ts_epoch REAL NOT NULL, execution_id TEXT NOT NULL, principal_kind TEXT NOT NULL,"
                         " principal_id TEXT, session_id TEXT, action TEXT NOT NULL, effect TEXT NOT NULL,"
                         " proposal_id TEXT, parameters_json TEXT NOT NULL, result TEXT NOT NULL,"
                         " result_summary TEXT, software_version TEXT, deployment_id TEXT, host_id TEXT);")
        db.close()
        store = control_audit.ControlAuditStore(path, retention_days=90)
        store.record(execution_id="x", action="a", effect="LOCAL_STATE", result="success", initiator=OPERATOR_COMMAND)
        self.assertEqual(store.events()[0]["initiator"], OPERATOR_COMMAND)
        store.close()

    def test_a_non_lifecycle_row_has_no_initiator(self):
        with control_audit.audited(action="x", effect="HARDWARE_CONTROL", params={}, principal=None, source="api.sdr.x"):
            pass
        self.assertEqual({r["initiator"] for r in self.store.events()}, {None})


class NotSettableFromRequestsTest(_Fixture):
    BODIES = ({"initiator": OPERATOR_COMMAND}, {"initiator_class": OPERATOR_COMMAND},
              {"initiator": {"class": OPERATOR_COMMAND}})

    def rpc(self, method, params):
        return self.h.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

    def test_orchestrate_propose_ignores_an_initiator_in_the_request(self):
        for extra in self.BODIES:
            with self.subTest(extra=extra):
                params = {"tool_name": "probe_write", "params": {"n": 1}, "confidence": 0.9, **extra}
                r = self.rpc("orchestrate/propose", params)
                self.assertEqual(r["result"]["initiator"], AUTONOMOUS)

    def test_an_initiator_inside_the_tool_params_is_just_a_tool_param(self):
        r = self.rpc("orchestrate/propose", {"tool_name": "probe_write", "confidence": 0.9,
                                             "params": {"n": 1, "initiator": OPERATOR_COMMAND}})
        self.assertEqual(r["result"]["initiator"], AUTONOMOUS)

    def test_over_rpc_phase_0_still_blocks_whatever_the_request_claims(self):
        r = self.rpc("orchestrate/propose", {"tool_name": "probe_write", "params": {"n": 1}, "confidence": 0.9,
                                             "initiator": OPERATOR_COMMAND})
        out = self.rpc("orchestrate/execute", {"proposal_id": r["result"]["proposal_id"]})
        self.assertIn("observe-only", str(out))
        self.assertEqual(self.ran, [])

    def test_only_server_code_passes_an_initiator(self):
        import ast
        import inspect
        import mcp_server
        for node in ast.walk(ast.parse(inspect.getsource(mcp_server))):
            if isinstance(node, ast.keyword):
                self.assertNotEqual(node.arg, "initiator", "an /mcp handler must not pass an initiator")


if __name__ == "__main__":
    unittest.main()
