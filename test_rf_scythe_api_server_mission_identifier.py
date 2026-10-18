"""Reconciling the mission_id/missionId identifier mismatch
(docs/research/scythe_mission_browser_rendering.md section 4, Option A) between the
operational Mission system in rf_scythe_api_server.py and the domain Mission system
(scythe_mission.py + PRs #188-#193).

Importing rf_scythe_api_server.py has real side effects (creates assets/, metrics_logs/,
poi_database.db in the current directory) and installs module-level global state, so -- same
pattern as test_rf_scythe_api_server_import_fallback.py -- the actual import and HTTP exercise
run in a subprocess with a throwaway working directory, never in this test process.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))

# GET /api/missions/<id>/subgraph has no auth/session requirement (confirmed by reading the
# route directly), so it is the one mission-scoped route testable without standing up an
# operator session. Nodes/edges are injected straight into hypergraph_store, bypassing the
# RFHypergraphStore.add_node()/enrich_hypergraph_rf_node() pipeline entirely -- that pipeline is
# unrelated to the identifier fix and not exercised here.
SUBGRAPH_PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import rf_scythe_api_server as server

    MISSION = "RFMAP-20261018-001"
    OTHER = "RFMAP-20261018-002"

    server.hypergraph_store.nodes = {
        "legacy-tagged": {"id": "legacy-tagged", "labels": {"missionId": MISSION}, "metadata": {}},
        "new-tagged": {"id": "new-tagged", "labels": {"mission_id": MISSION}, "metadata": {}},
        "metadata-legacy-tagged": {"id": "metadata-legacy-tagged", "labels": {},
                                    "metadata": {"missionId": MISSION}},
        "metadata-new-tagged": {"id": "metadata-new-tagged", "labels": {},
                                 "metadata": {"mission_id": MISSION}},
        "other-mission": {"id": "other-mission", "labels": {"mission_id": OTHER}, "metadata": {}},
        "untagged": {"id": "untagged", "labels": {}, "metadata": {}},
    }
    server.hypergraph_store.hyperedges = [
        {"id": "edge-legacy", "metadata": {"missionId": MISSION}},
        {"id": "edge-new", "metadata": {"mission_id": MISSION}},
        {"id": "edge-other", "metadata": {"mission_id": OTHER}},
    ]

    client = server.app.test_client()
    resp = client.get(f"/api/missions/{MISSION}/subgraph")
    print(json.dumps({"status_code": resp.status_code, "body": resp.get_json()}))
""")


def _run_probe(script):
    with tempfile.TemporaryDirectory(prefix="rf-api-mission-") as cwd:
        return subprocess.run(
            [sys.executable, "-I", "-c", script, REPO],
            cwd=cwd, capture_output=True, text=True, timeout=180,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


class MissionSubgraphIdentifierTests(unittest.TestCase):
    def test_both_identifier_conventions_are_found_in_labels_and_metadata(self):
        proc = _run_probe(SUBGRAPH_PROBE)
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status_code"], 200, result)
        node_ids = {n["id"] for n in result["body"]["subgraph"]["nodes"]}
        self.assertEqual(
            node_ids,
            {"legacy-tagged", "new-tagged", "metadata-legacy-tagged", "metadata-new-tagged"},
        )
        self.assertNotIn("other-mission", node_ids)
        self.assertNotIn("untagged", node_ids)

    def test_both_identifier_conventions_are_found_on_edges(self):
        proc = _run_probe(SUBGRAPH_PROBE)
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        edge_ids = {e["id"] for e in result["body"]["subgraph"]["edges"]}
        self.assertEqual(edge_ids, {"edge-legacy", "edge-new"})
        self.assertNotIn("edge-other", edge_ids)


if __name__ == "__main__":
    unittest.main()
