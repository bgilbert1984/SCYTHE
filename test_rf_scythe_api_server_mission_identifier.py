"""Reconciling the mission_id/missionId identifier mismatch
(docs/research/scythe_mission_browser_rendering.md section 4, Option A) between the
operational Mission system in rf_scythe_api_server.py and the domain Mission system
(scythe_mission.py + PRs #188-#193). Also covers Option B (the two systems are kept separate,
linked only by sharing one mission_id namespace): create_mission() now rejects an explicitly
supplied mission_id that would not be constructible as a domain Mission.

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


def _run_probe(script, *extra_argv):
    with tempfile.TemporaryDirectory(prefix="rf-api-mission-") as cwd:
        return subprocess.run(
            [sys.executable, "-I", "-c", script, REPO, *extra_argv],
            cwd=cwd, capture_output=True, text=True, timeout=180,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))


# Host-dedup mission-attribution semantics (docs/research/scythe_mission_browser_rendering.md
# section 6): a host observed under several missions carries a LIST-valued mission_id label,
# not a scalar. mission_subgraph's filter must match either member of the list, same as
# graphops_graph_resolver.py's _label_matches_mission.
SHARED_HOST_PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import rf_scythe_api_server as server

    MISSION_A = "RFMAP-20261018-010"
    MISSION_B = "RFMAP-20261018-011"

    server.hypergraph_store.nodes = {
        "host:shared": {"id": "host:shared", "labels": {"mission_id": [MISSION_A, MISSION_B]},
                         "metadata": {}},
    }
    server.hypergraph_store.hyperedges = []

    client = server.app.test_client()
    resp_a = client.get(f"/api/missions/{MISSION_A}/subgraph")
    resp_b = client.get(f"/api/missions/{MISSION_B}/subgraph")
    resp_other = client.get("/api/missions/RFMAP-UNRELATED/subgraph")
    print(json.dumps({
        "a": [n["id"] for n in resp_a.get_json()["subgraph"]["nodes"]],
        "b": [n["id"] for n in resp_b.get_json()["subgraph"]["nodes"]],
        "other": [n["id"] for n in resp_other.get_json()["subgraph"]["nodes"]],
    }))
""")


class SharedHostListValuedMissionTests(unittest.TestCase):
    def test_a_list_valued_mission_id_matches_every_mission_in_it_and_no_others(self):
        proc = _run_probe(SHARED_HOST_PROBE)
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["a"], ["host:shared"])
        self.assertEqual(result["b"], ["host:shared"])
        self.assertEqual(result["other"], [])


# Option B link: an explicitly supplied mission_id must be valid for a domain
# scythe_mission.Mission too, so a later Mission(mission_id=...) referencing the same
# operational mission is always constructible.
CREATE_MISSION_PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import rf_scythe_api_server as server

    client = server.app.test_client()
    resp = client.post("/api/missions", json={"mission_id": sys.argv[2], "name": "probe"})
    print(json.dumps({"status_code": resp.status_code, "body": resp.get_json()}))
""")


class CreateMissionIdentifierLinkTests(unittest.TestCase):
    def test_a_valid_explicit_mission_id_is_accepted_and_constructs_a_domain_mission(self):
        mission_id = "RFMAP-20261018-003"
        proc = _run_probe(CREATE_MISSION_PROBE, mission_id)
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status_code"], 201, result)
        self.assertEqual(result["body"]["mission"]["mission_id"], mission_id)
        import scythe_mission
        self.assertTrue(scythe_mission.is_valid_mission_id(mission_id))
        m = scythe_mission.Mission(mission_id=mission_id, mission_type="CUSTOM",
                                    purpose="linked to the operational mission above", operator="test")
        self.assertEqual(m.mission_id, mission_id)

    def test_a_malformed_explicit_mission_id_is_rejected(self):
        proc = _run_probe(CREATE_MISSION_PROBE, "has a space")
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status_code"], 400, result)
        self.assertIn("mission_id must be a valid Mission identifier", result["body"]["message"])

    def test_the_auto_generated_mission_id_is_already_a_valid_domain_identifier(self):
        proc = _run_probe(CREATE_MISSION_PROBE, "")
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status_code"], 201, result)
        import scythe_mission
        self.assertTrue(scythe_mission.is_valid_mission_id(result["body"]["mission"]["mission_id"]))


# ARR-API-1: mission enumeration. Creates a few missions through the real create_mission()
# route (so the test exercises the same persistence path production traffic uses), then
# lists them back.
LIST_MISSIONS_PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import rf_scythe_api_server as server

    client = server.app.test_client()
    ids = []
    for i, status in enumerate(["open", "open", "closed"]):
        resp = client.post("/api/missions", json={"name": f"probe-{i}"})
        mission_id = resp.get_json()["mission"]["mission_id"]
        ids.append(mission_id)
        if status == "closed":
            client.post(f"/api/missions/{mission_id}/end")

    resp_all = client.get("/api/missions")
    resp_open = client.get("/api/missions?status=open")
    print(json.dumps({
        "ids": ids,
        "all": [m["mission_id"] for m in resp_all.get_json()["missions"]],
        "open": [m["mission_id"] for m in resp_open.get_json()["missions"]],
        "all_count": resp_all.get_json()["count"],
    }))
""")


class ListMissionsTests(unittest.TestCase):
    def test_all_created_missions_are_enumerated(self):
        proc = _run_probe(LIST_MISSIONS_PROBE)
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(set(result["all"]), set(result["ids"]))
        self.assertEqual(result["all_count"], len(result["ids"]))

    def test_status_filter_excludes_ended_missions(self):
        proc = _run_probe(LIST_MISSIONS_PROBE)
        self.assertEqual(proc.returncode, 0, proc.stderr[-4000:])
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        open_ids, all_ids = set(result["open"]), set(result["ids"])
        closed_id = result["ids"][2]
        self.assertNotIn(closed_id, open_ids)
        self.assertEqual(open_ids, all_ids - {closed_id})


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
