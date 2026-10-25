"""Harness server for the SDR approvals walkthrough.

The real Flask app and the real MCP lifecycle (orchestrator, approval modes, control audit), with a fake
radio bridge and three signed-in operator sessions.  Not a mock of the lifecycle: only the radio and the
operator directory are replaced.  Run by ``run.py``; see README.md.

    python e2e/sdr_approvals/server.py [--port 5099]

Test-only endpoints (this process only):  GET /__bridge_calls, /__audit, /__lifecycle/<on|off>.
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
os.chdir(REPO)                       # the app serves its page and assets relative to the working directory
sys.path.insert(0, str(REPO))

try:
    import jsonschema  # noqa: F401
except ImportError:
    # Without it the registry skips parameter validation, which would turn the malformed-input cases into false results.
    sys.exit("harness: jsonschema is not importable by this interpreter (see README.md, Dependencies)")

import control_audit
import rf_scythe_api_server as rf
from hypergraph_engine import HypergraphEngine
from mcp_effects import APPROVAL_MODES_ENV, APPROVERS_ENV
from mcp_server import MCPHandler
from rf_mcp import register_rf_tools

TOKENS = {"tok-alpha": ("op-1", "s1"), "tok-alpha2": ("op-1", "s1b"), "tok-bravo": ("op-2", "s2"),
          "tok-charlie": ("op-3", "s3")}
OPERATORS = {"op-1": "ALPHA", "op-2": "BRAVO", "op-3": "CHARLIE"}
APPROVERS = "op-1,op-2"      # CHARLIE is signed in but has not been granted approval authority


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.running = False

    def start(self): self.calls.append("start"); self.running = True; return True
    def stop(self): self.calls.append("stop"); self.running = False; return True
    def tune(self, frequency_hz, mode=None, bandwidth_hz=0): self.calls.append(f"tune {frequency_hz}"); return {"frequency_hz": frequency_hz}
    def configure_stream(self, **changes): self.calls.append("config"); return {"state": "configured"}
    def status(self, include_control=False): return {"running": self.running, "state": "fake"}

    def wait_for_frame(self, sequence, timeout_s=15.0):
        import time
        time.sleep(min(timeout_s, 2))
        return None


def build(port):
    bridge = FakeBridge()

    def make_handler():
        """What a process start builds: a handler and orchestrator with an empty in-memory proposal log."""
        with mock.patch.dict(os.environ, {APPROVAL_MODES_ENV: "HARDWARE_CONTROL=SECOND_PRINCIPAL", APPROVERS_ENV: APPROVERS}):
            h = MCPHandler(HypergraphEngine(), use_orchestrator=True)
        register_rf_tools(h.engine, h)
        return h

    handler = make_handler()
    live = {"handler": handler}
    operators = {oid: SimpleNamespace(operator_id=oid, callsign=name, role=SimpleNamespace(value="operator"))
                 for oid, name in OPERATORS.items()}
    manager = SimpleNamespace(
        operators=operators,
        validate_session=lambda t: SimpleNamespace(operator_id=TOKENS[t][0], session_id=TOKENS[t][1]) if t in TOKENS else None)
    mock.patch.object(rf, "_rf_bridge_or_error", lambda: (bridge, None)).start()
    mock.patch("rf_mcp.get_rf_bridge", lambda: bridge).start()
    mock.patch.object(rf, "operator_manager", manager).start()
    mock.patch.dict(rf.__dict__, {"mcp_handler": handler}).start()
    control_audit.configure(os.path.join(tempfile.mkdtemp(prefix="sdr-e2e-"), "audit.sqlite3"), 90)

    app = rf.app

    @app.route("/__bridge_calls")
    def _bridge_calls():
        return json.dumps(bridge.calls)

    @app.route("/__audit")
    def _audit():
        store = control_audit.get_store()
        ids = []
        for row in store.events(limit=10000):
            if row["proposal_id"] and row["proposal_id"] not in ids:
                ids.append(row["proposal_id"])
        return json.dumps([control_audit.reconstruct_proposal(store, pid) for pid in ids])

    @app.route("/__lifecycle/<state>")
    def _lifecycle(state):
        rf.mcp_handler = live["handler"] if state == "on" else None
        return "ok"

    @app.route("/__restart")
    def _restart():
        """Simulate a process restart: a fresh handler (so the pending proposals are gone) and the startup step
        that closes in the audit what the previous process left open."""
        live["handler"] = make_handler()
        rf.mcp_handler = live["handler"]
        return json.dumps(control_audit.close_orphaned_proposals())

    if "/" not in [r.rule for r in app.url_map.iter_rules()]:
        from flask import send_from_directory
        app.add_url_rule("/", "e2e_index", lambda: send_from_directory(".", "command-ops-visualization.html"))
        app.add_url_rule("/<path:f>", "e2e_static", lambda f: send_from_directory(".", f))
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5099)
    args = ap.parse_args()
    build(args.port).run(host="127.0.0.1", port=args.port, threaded=True)
