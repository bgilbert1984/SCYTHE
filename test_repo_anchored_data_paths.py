"""Default data locations are anchored to the checkout, not to one developer's home or to the cwd.

The failure this guards: modules named /home/<user>/NerfEngine/... absolutely, so on any other checkout the DuckDB event
store opened with EACCES and the caller logged "DuckDB store unavailable" and carried on with no store.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent
ANCHORED = ("embedding_engine.py", "new_host_pcapng_logger.py", "scene_duckdb_store.py",
            "tools/rf_structure_channel_report.py", "tools/rf_structure_channel_sweep.py")


def store_default(cwd, **env):
    """DEFAULT_DB_PATH as a fresh interpreter sees it from `cwd`, with only the given SCYTHE_DATA_DIR."""
    clean = {k: v for k, v in os.environ.items() if k != "SCYTHE_DATA_DIR"}
    clean.update(env)
    clean["PYTHONPATH"] = str(REPO)
    out = subprocess.run([sys.executable, "-c", "import scene_duckdb_store as s; print(s.DEFAULT_DB_PATH)"],
                         cwd=cwd, env=clean, capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise AssertionError(out.stderr[-800:])
    return Path(out.stdout.strip().splitlines()[-1])


class RepoAnchoredDataPaths(unittest.TestCase):
    def test_the_default_store_is_inside_the_checkout_whatever_the_cwd(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(store_default(elsewhere), REPO / "metrics_logs" / "scythe_events.duckdb")

    def test_a_relative_data_dir_resolves_against_the_checkout_not_the_cwd(self):
        # The orchestrator spawns child instances from their own working directories; against the cwd each would
        # open a different store and none the one already on disk.
        with tempfile.TemporaryDirectory() as elsewhere:
            self.assertEqual(store_default(elsewhere, SCYTHE_DATA_DIR="data_x"), REPO / "data_x" / "scythe_events.duckdb")

    def test_an_absolute_data_dir_is_used_as_given(self):
        with tempfile.TemporaryDirectory() as elsewhere, tempfile.TemporaryDirectory() as data:
            self.assertEqual(store_default(elsewhere, SCYTHE_DATA_DIR=data), Path(data) / "scythe_events.duckdb")

    def test_no_developer_home_path_is_named_in_the_anchored_modules(self):
        pattern = re.compile(r"/home/[A-Za-z_][\w.-]*/")
        for name in ANCHORED:
            hits = [n for n, line in enumerate((REPO / name).read_text().splitlines(), 1) if pattern.search(line)]
            self.assertEqual(hits, [], f"{name} names an absolute home path on line(s) {hits}")


if __name__ == "__main__":
    unittest.main()
