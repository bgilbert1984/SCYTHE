import unittest

from graphops_infrastructure import build_infrastructure_snapshot


class InfrastructureProjectionTests(unittest.TestCase):
    def setUp(self):
        self.graph = {
            "status": "ok", "graphRevision": "graph-fixture", "capturedAt": "2026-08-13T20:00:00Z",
            "nodes": [
                {"id": "host:10.0.0.1", "kind": "network_host", "labels": {"ip": "10.0.0.1"}},
                {"id": "host:20.1.1.1", "kind": "network_host", "labels": {"ip": "20.1.1.1"},
                 "enrichment": {"network": {"asn": 8075, "organization": "Microsoft", "prefix": "20.0.0.0/8"},
                                "geo": {"latitude": 47.61, "longitude": -122.33, "uncertaintyRadiusKm": 20}}},
                {"id": "host:151.101.1.91", "kind": "network_host", "labels": {"ip": "151.101.1.91"},
                 "enrichment": {"network": {"asn": 54113, "organization": "Fastly", "prefix": "151.101.0.0/16"},
                                "geo": {"latitude": 37.75, "longitude": -97.82, "uncertaintyRadiusKm": 1000}}},
            ],
            "edges": [
                {"id": "flow:1", "nodes": ["host:10.0.0.1", "host:20.1.1.1"],
                 "labels": {"proto": "tcp", "bytes": 1200, "packets": 4}, "observedAt": "2026-08-13T20:00:00Z"},
                {"id": "flow:2", "nodes": ["host:20.1.1.1", "host:151.101.1.91"],
                 "labels": {"proto": "tcp", "bytes": 800}, "observedAt": "2026-08-13T20:00:01Z"},
            ],
        }

    def test_partitions_observation_inference_model_and_display(self):
        result = build_infrastructure_snapshot(self.graph, "host:20.1.1.1", lambda source, target: [source, 3356, target])
        self.assertEqual(result["authority"]["flows"], "OBSERVED_GRAPH_EDGES")
        self.assertEqual(result["authority"]["rendering"], "DISPLAY_ONLY_NOT_ROUTE")
        self.assertEqual(result["focus"]["domainId"], "asn:8075")
        self.assertEqual(len(result["observedFlows"]), 2)
        self.assertTrue(all(not row["pathClaim"] for row in result["observedFlows"]))
        self.assertEqual(result["modeledPathCandidates"][0]["evidenceClass"], "MODELED_CANDIDATE")
        self.assertFalse(result["modeledPathCandidates"][0]["observedRoute"])
        self.assertEqual(result["referenceCatalog"]["asPathModel"]["freshness"], "UNKNOWN")
        self.assertIn("sourceFreshness", result)

    def test_private_hosts_are_not_assigned_public_ownership_or_location(self):
        result = build_infrastructure_snapshot(self.graph)
        local = next(item for item in result["domains"] if item["id"] == "network:local")
        self.assertIsNone(local["asn"])
        self.assertIsNone(local["centroid"])
        self.assertEqual(local["authority"], "IP_SCOPE_CLASSIFICATION")

    def test_vocabulary_does_not_overclaim(self):
        rendered = str(build_infrastructure_snapshot(self.graph)).upper()
        for forbidden in ("PHYSICAL PATH", "SYNTHETIC ROUTE", "VPN TUNNEL", "KILL CHAIN"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
