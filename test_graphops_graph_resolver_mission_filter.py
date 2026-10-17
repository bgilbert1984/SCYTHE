"""GraphSelectionResolver.snapshot()'s optional mission_id filter -- wiring Mission identity
(docs/research/scythe_mission_definition.md, PR D) into the Cesium-facing
/api/graphops/selection/graph resolver. Omitting mission_id must reproduce the exact
pre-existing behavior; the canonical/cached snapshot and its graphRevision must never be
filtered by mission (a revision identifies total graph state, independent of any one
request's filter)."""
import unittest

from flask import Flask

from graphops_graph_resolver import GraphSelectionResolver
from mcp_server import register_mcp_routes

MISSION_A = "RFMAP-20261017-001"
MISSION_B = "RFMAP-20261017-002"


class _Graph:
    """Two missions' worth of PCAP-derived nodes (mirroring pcap_ingest.py's real label
    shape), one unlabeled node (as RF-ingested observations currently are -- PR B's known
    gap), and edges spanning within and across the mission boundary."""

    def __init__(self):
        self.nodes = {
            "pcap:a": {"id": "pcap:a", "kind": "pcap_artifact",
                       "labels": {"filename": "a.pcapng", "mission_id": MISSION_A},
                       "timestamp": 100},
            "session:a1": {"id": "session:a1", "kind": "session",
                           "labels": {"window_sec": 30, "mission_id": MISSION_A},
                           "timestamp": 101},
            "pcap:b": {"id": "pcap:b", "kind": "pcap_artifact",
                       "labels": {"filename": "b.pcapng", "mission_id": MISSION_B},
                       "timestamp": 102},
            "session:b1": {"id": "session:b1", "kind": "session",
                           "labels": {"window_sec": 30, "mission_id": MISSION_B},
                           "timestamp": 103},
            "host:unlabeled": {"id": "host:unlabeled", "kind": "host",
                               "labels": {"ip": "10.0.0.9"}, "timestamp": 104},
        }
        self.edges = {
            "edge:a-within": {"id": "edge:a-within", "kind": "SESSION_DERIVED_FROM_PCAP",
                              "nodes": ["pcap:a", "session:a1"], "timestamp": 105},
            "edge:b-within": {"id": "edge:b-within", "kind": "SESSION_DERIVED_FROM_PCAP",
                              "nodes": ["pcap:b", "session:b1"], "timestamp": 106},
            "edge:a-to-unlabeled": {"id": "edge:a-to-unlabeled", "kind": "SESSION_OBSERVED_HOST",
                                    "nodes": ["session:a1", "host:unlabeled"], "timestamp": 107},
        }


class MissionFilterTests(unittest.TestCase):
    def test_omitted_mission_id_is_byte_for_byte_the_pre_existing_behavior(self):
        resolver = GraphSelectionResolver(_Graph())
        snap = resolver.snapshot(node_limit=50, edge_limit=50)
        self.assertNotIn("missionFilter", snap)
        self.assertEqual(snap["detectedNodeCount"], 5)
        self.assertEqual({n["id"] for n in snap["nodes"]},
                         {"pcap:a", "session:a1", "pcap:b", "session:b1", "host:unlabeled"})

    def test_mission_filter_returns_only_that_mission_s_nodes(self):
        resolver = GraphSelectionResolver(_Graph())
        snap = resolver.snapshot(node_limit=50, edge_limit=50, mission_id=MISSION_A)
        self.assertEqual(snap["missionFilter"], MISSION_A)
        self.assertEqual({n["id"] for n in snap["nodes"]}, {"pcap:a", "session:a1"})
        self.assertEqual(snap["detectedNodeCount"], 2)  # not 5 -- scoped to the mission, not the whole graph

    def test_mission_filter_excludes_edges_crossing_the_mission_boundary(self):
        resolver = GraphSelectionResolver(_Graph())
        snap = resolver.snapshot(node_limit=50, edge_limit=50, mission_id=MISSION_A)
        edge_ids = {e["id"] for e in snap["edges"]}
        self.assertIn("edge:a-within", edge_ids)
        self.assertNotIn("edge:a-to-unlabeled", edge_ids)  # touches an unlabeled node outside mission A
        self.assertNotIn("edge:b-within", edge_ids)        # entirely mission B
        self.assertEqual(snap["detectedEdgeCount"], 1)

    def test_unrelated_mission_id_returns_empty_not_an_error(self):
        resolver = GraphSelectionResolver(_Graph())
        snap = resolver.snapshot(node_limit=50, edge_limit=50, mission_id="RFMAP-NO-SUCH-MISSION")
        self.assertEqual(snap["nodes"], [])
        self.assertEqual(snap["edges"], [])
        self.assertEqual(snap["detectedNodeCount"], 0)
        self.assertEqual(snap["missionFilter"], "RFMAP-NO-SUCH-MISSION")

    def test_graph_revision_is_identical_across_different_mission_filters(self):
        # The canonical/cached snapshot represents total graph state and must never be
        # filtered by mission -- two requests against the same graph state share one
        # cache entry regardless of mission_id.
        resolver = GraphSelectionResolver(_Graph())
        rev_unfiltered = resolver.snapshot()["graphRevision"]
        rev_a = GraphSelectionResolver(_Graph()).snapshot(mission_id=MISSION_A)["graphRevision"]
        rev_b = GraphSelectionResolver(_Graph()).snapshot(mission_id=MISSION_B)["graphRevision"]
        self.assertEqual(rev_unfiltered, rev_a)
        self.assertEqual(rev_a, rev_b)

    def test_mission_filter_respects_node_limit(self):
        resolver = GraphSelectionResolver(_Graph())
        snap = resolver.snapshot(node_limit=1, edge_limit=50, mission_id=MISSION_A)
        self.assertEqual(len(snap["nodes"]), 1)
        self.assertEqual(snap["ranking"]["suppressedNodes"], 1)  # 2 detected, 1 displayed


class RouteWiringTests(unittest.TestCase):
    """The actual Flask route (mcp_server.py), not just the resolver function directly --
    confirms mission_id really reaches GraphSelectionResolver.snapshot() through the real
    /api/graphops/selection/graph endpoint, same pattern as test_graphops_conversation.py."""

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        register_mcp_routes(self.app, _Graph())
        self.client = self.app.test_client()

    def test_mission_id_query_param_reaches_the_resolver(self):
        snap = self.client.get(
            "/api/graphops/selection/graph?mission_id=" + MISSION_A).get_json()
        self.assertEqual(snap["missionFilter"], MISSION_A)
        self.assertEqual({n["id"] for n in snap["nodes"]}, {"pcap:a", "session:a1"})

    def test_omitted_mission_id_query_param_is_unchanged(self):
        snap = self.client.get("/api/graphops/selection/graph").get_json()
        self.assertNotIn("missionFilter", snap)
        self.assertEqual(snap["detectedNodeCount"], 5)


if __name__ == "__main__":
    unittest.main()
