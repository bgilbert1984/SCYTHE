#!/usr/bin/env python3
"""rf_scythe_api_server must degrade, not crash, when an optional import fails.

`network_ingress_aggregator` is optional. When it cannot be imported the
module is meant to log a warning and carry on with the feature off. It used
to call `logger.warning(...)` from that fallback before `logger` was defined,
so the fallback raised NameError and the whole API server failed to import,
hiding the dependency that was actually missing.

The failure is injected, not environmental. Putting None in sys.modules makes
`import network_ingress_aggregator` raise ImportError whether or not psutil
(which the real aggregator imports) happens to be installed, so this test
means the same thing on every host.

The import runs in a subprocess with a throwaway working directory: importing
the server creates assets/, metrics_logs/ and poi_database.db in the current
directory and registers module-level state that must not leak into this
process.
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(REPO, "rf_scythe_api_server.py")
FALLBACK_MESSAGE = "[API] network_ingress_aggregator not available"

# argv[1] is the repository, argv[2] says how to make the aggregator behave.
PROBE = textwrap.dedent("""
    import json, sys, types
    sys.path.insert(0, sys.argv[1])
    if sys.argv[2] == "unavailable":
        sys.modules["network_ingress_aggregator"] = None   # import -> ImportError
    else:
        sys.modules["network_ingress_aggregator"] = types.ModuleType(
            "network_ingress_aggregator")
    import rf_scythe_api_server as server
    print(json.dumps({"available": server._ingress_aggregator_available}))
""")


def _import_server(aggregator):
    with tempfile.TemporaryDirectory(prefix="api-import-") as cwd:
        return subprocess.run(
            [sys.executable, "-I", "-c", PROBE, REPO, aggregator],
            cwd=cwd, capture_output=True, text=True, timeout=180,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


class _ModuleLevelLoggerUses(ast.NodeVisitor):
    """Names read as `logger` while the module is executing: not inside a
    function or lambda body, which only runs after the module has loaded."""

    def __init__(self):
        self.lines = []

    def visit_FunctionDef(self, node):
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults + node.args.kw_defaults:
            if default is not None:
                self.visit(default)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        pass

    def visit_Name(self, node):
        if node.id == "logger" and isinstance(node.ctx, ast.Load):
            self.lines.append(node.lineno)


class IngressAggregatorFallbackTests(unittest.TestCase):

    def test_missing_aggregator_degrades_instead_of_crashing(self):
        proc = _import_server("unavailable")
        self.assertNotIn("NameError", proc.stderr, proc.stderr[-2000:])
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertEqual(json.loads(proc.stdout.strip().splitlines()[-1]),
                         {"available": False})

    def test_the_fallback_says_which_dependency_is_missing(self):
        proc = _import_server("unavailable")
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertIn(FALLBACK_MESSAGE, proc.stderr)

    def test_a_present_aggregator_enables_the_feature_without_the_warning(self):
        """The control: the same probe with the module present, so the two
        tests above are shown to depend on the injected failure."""
        proc = _import_server("present")
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        self.assertEqual(json.loads(proc.stdout.strip().splitlines()[-1]),
                         {"available": True})
        self.assertNotIn(FALLBACK_MESSAGE, proc.stderr)


class LoggerDefinedBeforeUseTests(unittest.TestCase):
    """Static companion: cheap, and still meaningful if the import above ever
    starts failing for an unrelated reason."""

    def test_no_module_level_use_of_logger_precedes_its_definition(self):
        with open(SERVER, encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), SERVER)
        assigned = [node.lineno for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "logger"
                            for t in node.targets)]
        self.assertTrue(assigned, "logger is never assigned at module level")
        uses = _ModuleLevelLoggerUses()
        for statement in tree.body:
            uses.visit(statement)
        early = [line for line in uses.lines if line < min(assigned)]
        self.assertEqual(early, [],
                         f"logger read at module level on line(s) {early}, "
                         f"before its first assignment on line {min(assigned)}")


if __name__ == "__main__":
    unittest.main()
