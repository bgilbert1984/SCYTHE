"""#231: test_mcp_principal and test_rf_mcp must pass in either order.

rf_scythe_api_server calls eventlet.monkey_patch() at import. If that import
happens after an rf_bridge singleton exists, the bridge holds unpatched
threading primitives and stop() raises "cannot notify on un-acquired lock".
Each order runs in a fresh interpreter so the result does not depend on what
this process already imported.
"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class RFTestIsolationTests(unittest.TestCase):
    def _run(self, *modules):
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", *modules],
            cwd=HERE, capture_output=True, text=True, timeout=300,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertNotIn("un-acquired lock", proc.stderr)

    def test_principal_then_rf_mcp(self):
        self._run("test_mcp_principal", "test_rf_mcp")

    def test_rf_mcp_then_principal(self):
        self._run("test_rf_mcp", "test_mcp_principal")


if __name__ == "__main__":
    unittest.main()
