import inspect
import logging
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import control_audit
from hypergraph_engine import HypergraphEngine
from mcp_effects import APPROVAL_MODES_ENV
from mcp_server import MCPHandler
from rf_mcp import register_rf_tools


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.fail_with = None

    def _do(self, name, result):
        self.calls.append(name)
        if self.fail_with:
            raise self.fail_with
        return result

    def start(self): return self._do("start", True)
    def stop(self): return self._do("stop", True)
    def tune(self, frequency_hz, mode=None, bandwidth_hz=0): return self._do("tune", {"frequency_hz": frequency_hz})
    def configure_stream(self, **changes): return self._do("config", {"state": "configured"})
    def status(self, include_control=False): return {"state": "fake"}


class _Fixture(unittest.TestCase):
    """ADR 0005 Decision 4 and 5: the four SDR control routes are adapters into the lifecycle, as operator commands."""
    SETTING = ""

    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.addClassCleanup(logging.disable, logging.NOTSET)
        import rf_scythe_api_server as rf
        cls.rf = rf

    def setUp(self):
        rf = self.rf
        self.bridge = FakeBridge()
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: self.SETTING}):
            self.handler = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        register_rf_tools(self.handler.engine, self.handler)
        self.orch = self.handler._orchestrator
        op = SimpleNamespace(operator_id="op-1", callsign="ALPHA", role=SimpleNamespace(value="operator"))
        sess = SimpleNamespace(operator_id="op-1", session_id="sess-9")
        manager = SimpleNamespace(operators={"op-1": op}, validate_session=lambda t: sess if t == "good" else None)
        for p in (mock.patch.object(rf, "_rf_bridge_or_error", lambda: (self.bridge, None)),
                  mock.patch("rf_mcp.get_rf_bridge", lambda: self.bridge),
                  mock.patch.object(rf, "operator_manager", manager),
                  mock.patch.dict(rf.__dict__, {"mcp_handler": self.handler})):
            p.start()
            self.addCleanup(p.stop)
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = control_audit.configure(os.path.join(self.dir.name, "a.sqlite3"), 90)
        self.addCleanup(control_audit.reset)
        self.client = rf.app.test_client()

    def post(self, path, body=None):
        return self.client.post(path, json=body, headers={"X-Session-Token": "good"},
                                environ_base={"REMOTE_ADDR": "203.0.113.5"})


class DefaultDeploymentTest(_Fixture):
    def test_every_route_works_in_phase_0_and_keeps_its_response_shape(self):
        self.assertEqual(self.orch.phase.value, 0)
        cases = (("/api/sdr/start", {}, "started", True, "start"),
                 ("/api/sdr/stop", {}, "stopped", True, "stop"),
                 ("/api/sdr/tune", {"frequency_hz": 433.92e6, "mode": "NFM", "bandwidth_hz": 12500}, "control",
                  {"frequency_hz": 433.92e6}, "tune"),
                 ("/api/sdr/config", {"fft_size": 1024}, None, None, "config"))
        for path, body, key, value, call in cases:
            with self.subTest(path=path):
                r = self.post(path, body)
                j = r.get_json()
                self.assertEqual((r.status_code, j["status"], j["outcome"]), (200, "ok", "executed"), j)
                self.assertEqual(j["bridge"], {"state": "fake"} if call != "config" else j["bridge"])
                if key:
                    self.assertEqual(j[key], value)
                self.assertTrue(j["proposal_id"])
                self.assertEqual(self.bridge.calls[-1], call)

    def test_rapid_operator_commands_are_not_mistaken_for_graph_oscillation(self):
        for i in range(8):
            r = self.post("/api/sdr/tune", {"frequency_hz": 100e6 + i})
            self.assertEqual(r.status_code, 200, (i, r.get_json()))
        self.assertEqual(self.bridge.calls, ["tune"] * 8)
        self.assertEqual(self.orch.dual_agent.stability.mutation_timestamps, [])

    def test_an_agent_on_mcp_is_still_held_to_phase_0_on_the_same_handler(self):
        self.assertEqual(self.post("/api/sdr/tune", {"frequency_hz": 1e6}).status_code, 200)
        p = self.orch.propose_action("rf_tune", {"frequency_hz": 1e6}, confidence=0.99)
        out = self.orch.execute_proposal(p["proposal_id"])
        self.assertFalse(out["ok"])
        self.assertIn("observe-only", out["error"])
        self.assertEqual(self.bridge.calls, ["tune"])

    def test_the_routes_no_longer_touch_the_bridge_themselves(self):
        self.assertFalse(hasattr(self.rf, "_sdr_audit"))
        for endpoint in ("sdr_bridge_start", "sdr_bridge_stop", "sdr_bridge_tune", "sdr_bridge_configure"):
            src = inspect.getsource(self.rf.app.view_functions[endpoint])
            for forbidden in ("bridge.start(", "bridge.stop(", "bridge.tune(", "bridge.configure_stream(", "_sdr_audit"):
                self.assertNotIn(forbidden, src, f"{endpoint} must not call {forbidden}")


class InvalidInputTest(_Fixture):
    def test_bad_input_is_a_400_and_never_reaches_the_bridge(self):
        cases = (("/api/sdr/tune", {"mode": "NFM"}), ("/api/sdr/tune", {"frequency_hz": "abc"}),
                 ("/api/sdr/tune", {"frequency_hz": -5}), ("/api/sdr/tune", {"frequency_hz": 1e6, "bandwidth_hz": -1}),
                 ("/api/sdr/config", {}), ("/api/sdr/config", {"nonsense": 1}), ("/api/sdr/config", {"fft_size": 0}))
        for path, body in cases:
            with self.subTest(path=path, body=body):
                r = self.post(path, body)
                self.assertEqual(r.status_code, 400, r.get_json())
                self.assertEqual(r.get_json()["status"], "error")
        self.assertEqual(self.bridge.calls, [])

    def test_a_bridge_validation_error_is_a_400_and_a_bridge_failure_a_503(self):
        self.bridge.fail_with = ValueError("sample rate out of range")
        r = self.post("/api/sdr/config", {"sample_rate_hz": 1})
        self.assertEqual((r.status_code, r.get_json()["message"]), (400, "sample rate out of range"))
        self.bridge.fail_with = RuntimeError("rig refused")
        r = self.post("/api/sdr/tune", {"frequency_hz": 1e6})
        self.assertEqual((r.status_code, r.get_json()["status"]), (503, "error"))

    def test_the_initiator_cannot_be_set_from_the_request(self):
        r = self.post("/api/sdr/config", {"initiator": "autonomous", "fft_size": 512})
        self.assertEqual(r.status_code, 400)
        j = self.post("/api/sdr/tune", {"frequency_hz": 1e6, "initiator": "autonomous"}).get_json()
        pid = j["proposal_id"]
        initiators = {e["initiator"] for e in self.store.events(proposal_id=pid)}
        self.assertEqual(initiators, {"operator_command"})

    def test_it_is_a_503_when_the_lifecycle_is_not_initialized(self):
        with mock.patch.dict(self.rf.__dict__, {"mcp_handler": None}):
            for path, body in (("/api/sdr/start", {}), ("/api/sdr/stop", {}), ("/api/sdr/tune", {"frequency_hz": 1e6}),
                               ("/api/sdr/config", {"fft_size": 512})):
                with self.subTest(path=path):
                    self.assertEqual(self.post(path, body).status_code, 503)
        self.assertEqual(self.bridge.calls, [])


class RaisedHardwareModeTest(_Fixture):
    SETTING = "HARDWARE_CONTROL=SECOND_PRINCIPAL"

    def test_start_tune_and_config_wait_for_a_second_operator_and_are_never_2xx(self):
        for path, body in (("/api/sdr/start", {}), ("/api/sdr/tune", {"frequency_hz": 1e6}),
                           ("/api/sdr/config", {"fft_size": 512})):
            with self.subTest(path=path):
                r = self.post(path, body)
                j = r.get_json()
                self.assertEqual((r.status_code, j["status"], j["outcome"]), (409, "not_executed", "pending_approval"), j)
                self.assertIn("awaiting independent approval", j["message"])
        self.assertEqual(self.bridge.calls, [])

    def test_stop_is_not_held_for_a_second_operator(self):
        r = self.post("/api/sdr/stop", {})
        self.assertEqual((r.status_code, r.get_json()["outcome"]), (200, "executed"))
        self.assertEqual(self.bridge.calls, ["stop"])

    def test_a_pending_command_can_be_approved_over_mcp_by_another_operator_and_then_runs(self):
        from mcp_principal import OPERATOR_SESSION, Principal, principal_scope
        pid = self.post("/api/sdr/tune", {"frequency_hz": 1e6}).get_json()["proposal_id"]
        with principal_scope(Principal(OPERATOR_SESSION, operator_id="op-2", session_id="s2")):
            self.assertEqual(self.orch.approve_proposal(pid, "approve")["status"], "approved")
            out = self.orch.execute_proposal(pid)
        self.assertTrue(out["ok"], out)    # phase 0, and it ran: it is an operator command
        self.assertEqual(self.bridge.calls, ["tune"])


if __name__ == "__main__":
    unittest.main()
