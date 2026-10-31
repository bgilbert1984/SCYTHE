"""The companion services (ws_ingest, rf_voxel_processor) have no authentication of their own,
so they must listen on loopback only. Remote operators reach them through the orchestrator's /proxy routes."""
import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent


def _tree(name):
    return ast.parse((ROOT / name).read_text(encoding="utf8"))


def _string_constants(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


class CompanionLoopbackTests(unittest.TestCase):
    def test_orchestrator_launches_ws_ingest_on_loopback(self):
        for node in ast.walk(_tree("scythe_orchestrator.py")):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                if "script" in keys:
                    values = dict(zip(keys, node.values))
                    script = values["script"]
                    if isinstance(script, ast.Constant) and script.value == "ws_ingest.py":
                        strings = _string_constants(values["args"])
                        self.assertIn("127.0.0.1", strings)
                        self.assertNotIn("0.0.0.0", strings)
                        return
        self.fail("ws_ingest launch spec not found")

    def test_ws_ingest_default_host_is_loopback(self):
        for node in ast.walk(_tree("ws_ingest.py")):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
                if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--host":
                    default = [k.value for k in node.keywords if k.arg == "default"][0]
                    self.assertEqual(default.value, "127.0.0.1")
                    return
        self.fail("--host argument not found")

    def test_voxel_processor_binds_loopback(self):
        for node in ast.walk(_tree("rf_voxel_processor.py")):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "run":
                hosts = [k.value.value for k in node.keywords if k.arg == "host" and isinstance(k.value, ast.Constant)]
                if hosts:
                    self.assertEqual(hosts, ["127.0.0.1"])
                    return
        self.fail("uvicorn.run call with a host not found")


if __name__ == "__main__":
    unittest.main()
