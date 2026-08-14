import json
import tempfile
import unittest
from pathlib import Path

from graphops_infrastructure import attach_external_infrastructure_evidence, build_infrastructure_snapshot
from graphops_peeringdb import PeeringDbClient, load_peeringdb_api_key
from graphops_infrastructure_contradictions import evaluate_infrastructure_contradictions
from graphops_ris_live import RisLiveCollector, RisObservationStore, normalize_ris_message


class _Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self): return json.dumps({"data": self.payload}).encode()


class ExternalInfrastructureTests(unittest.TestCase):
    def test_credential_loader_reads_named_key_without_confusing_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secrets"
            path.write_text("API=ollama-secret\nPeeringDB=pdb-secret\n", encoding="utf-8")
            self.assertEqual(load_peeringdb_api_key(str(path)), "pdb-secret")

    def test_peeringdb_snapshot_is_asn_bounded_versioned_and_self_reported(self):
        records = {
            "net": [{"id": 7, "asn": 8075, "name": "Example", "policy_general": "Open", "updated": "2026-08-01T00:00:00Z"}],
            "netfac": [{"id": 8, "net_id": 7, "fac_id": 9, "updated": "2026-08-02T00:00:00Z"}],
            "netixlan": [{"id": 10, "net_id": 7, "ix_id": 11, "updated": "2026-08-03T00:00:00Z"}],
            "fac": [{"id": 9, "name": "Facility", "latitude": 47.6, "longitude": -122.3, "updated": "2026-08-04T00:00:00Z"}],
            "ix": [{"id": 11, "name": "Exchange", "updated": "2026-08-05T00:00:00Z"}],
        }
        calls = []
        def opener(req, timeout):
            calls.append((req.full_url, req.headers)); kind = req.full_url.split("/api/")[1].split("?")[0]
            return _Response(records[kind])
        with tempfile.TemporaryDirectory() as directory:
            client = PeeringDbClient(api_key="secret", cache_path=Path(directory) / "cache.json",
                                     opener=opener, clock=lambda: 1000)
            result = client.snapshot([8075, "bad", -1])
        self.assertEqual(result["scope"]["asns"], [8075])
        self.assertEqual(result["networks"][0]["authority"], "PEERINGDB_SELF_REPORTED")
        self.assertEqual(len(result["datasetRevision"]), 64)
        self.assertTrue(all(headers.get("Authorization") == "Api-Key secret" for _, headers in calls))
        self.assertNotIn("secret", json.dumps(result))

    def test_ris_update_normalizes_collector_vantage_without_raw_payload(self):
        result = normalize_ris_message({"type": "ris_message", "data": {
            "type": "UPDATE", "timestamp": 1000.5, "host": "rrc21", "peer": "192.0.2.1",
            "peer_asn": "64496", "id": "message-1", "path": [64496, 3356, [8075, 8076]],
            "announcements": [{"next_hop": "192.0.2.1", "prefixes": ["20.0.0.0/8"]}],
            "raw": "must-not-survive",
        }})
        self.assertEqual(result[0]["evidenceClass"], "CONTROL_PLANE_OBSERVATION")
        self.assertEqual(result[0]["collectorId"], "rrc21")
        self.assertEqual(result[0]["dataPlaneAuthority"], "NON_AUTHORITATIVE")
        self.assertEqual(result[0]["originAsn"], [8075, 8076])
        self.assertNotIn("must-not-survive", json.dumps(result))

    def test_ris_observations_survive_collector_restart_and_are_time_windowed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ris.sqlite"
            store = RisObservationStore(path, clock=lambda: 2_000)
            rows = normalize_ris_message({"type": "ris_message", "data": {
                "type": "UPDATE", "timestamp": 1_000.5, "host": "rrc21",
                "peer_asn": 64496, "path": [64496, 8075],
                "announcements": [{"prefixes": ["20.0.0.0/8"]}],
            }})
            self.assertEqual(store.insert_many(rows), 1)
            self.assertEqual(store.insert_many(rows), 0)
            restarted = RisLiveCollector(store=RisObservationStore(path, clock=lambda: 2_000))
            restarted.prefixes = ["20.0.0.0/8"]; restarted.asns = [8075]
            self.assertEqual(len(restarted.snapshot(since=1_000, until=1_001)["controlPlanePaths"]), 1)
            self.assertEqual(restarted.snapshot(since=1_001, until=1_002)["controlPlanePaths"], [])
            self.assertEqual(restarted.snapshot()["summary"]["store"], "SQLITE_WAL")

    def test_contradictions_preserve_disagreement_and_withhold_absence(self):
        infrastructure = {
            "schemaVersion": "graphops.infrastructure.v1", "graphRevision": "graph-1",
            "domains": [{"id": "asn:8075", "asn": 8075, "prefixes": ["20.0.0.0/8"],
                         "authority": "LOCAL_PREFIX_TO_AS", "evidenceClass": "INFERRED"}],
            "observedFlows": [{"id": "flow-1", "sourceDomain": "asn:8075",
                               "targetDomain": "asn:54113", "firstSeen": 999, "lastSeen": 1_002}],
            "peeringdbEvidence": {"datasetRevision": "pdb-1"},
            "controlPlaneEvidence": {"snapshotRevision": "ris-1", "controlPlanePaths": [
                {"id": "a", "collectorId": "rrc21", "collectorReceivedAt": 1_000,
                 "messageType": "ANNOUNCE", "prefix": "20.0.0.0/8", "originAsn": 64500,
                 "asPath": [64496, 64500]},
                {"id": "b", "collectorId": "rrc21", "collectorReceivedAt": 1_001,
                 "messageType": "ANNOUNCE", "prefix": "20.0.0.0/8", "originAsn": 64501,
                 "asPath": [64496, 64501]},
                {"id": "c", "collectorId": "rrc21", "collectorReceivedAt": 1_002,
                 "messageType": "WITHDRAW", "prefix": "20.0.0.0/8", "originAsn": None,
                 "asPath": []},
                {"id": "broad", "collectorId": "rrc21", "collectorReceivedAt": 1_002,
                 "messageType": "ANNOUNCE", "prefix": "0.0.0.0/0", "originAsn": 64510,
                 "asPath": [64496, 64510]},
            ]},
            "referenceCatalog": {"caidaRelationships": {"datasetRevision": None}},
        }
        result = evaluate_infrastructure_contradictions(infrastructure, since=999, until=1_003)
        kinds = {item["kind"] for item in result["findings"]}
        change_kinds = {item["kind"] for item in result["changes"]}
        self.assertIn("ORIGIN_DISAGREEMENT", kinds)
        self.assertIn("WITHDRAWAL_WITH_DATA_PLANE_ACTIVITY", kinds)
        self.assertIn("ORIGIN_CHANGE_OBSERVED", change_kinds)
        self.assertIn("AS_PATH_CHANGE_OBSERVED", change_kinds)
        self.assertFalse(any(item.get("prefix") == "0.0.0.0/0" for item in result["findings"]))
        self.assertTrue(any(row["kind"] == "ABSENCE_INFERENCE_WITHHELD" for row in result["withheld"]))
        self.assertIn("NOT A HIJACK DETERMINATION", json.dumps(result))

    def test_external_layers_disable_legacy_model_and_remain_parallel(self):
        graph = {"graphRevision": "graph-1", "nodes": [
            {"id": "host:20.1.1.1", "kind": "network_host", "labels": {"ip": "20.1.1.1"},
             "enrichment": {"network": {"asn": 8075}}},
            {"id": "host:151.101.1.91", "kind": "network_host", "labels": {"ip": "151.101.1.91"},
             "enrichment": {"network": {"asn": 54113}}}], "edges": []}
        base = build_infrastructure_snapshot(graph, modeled_path_resolver=lambda a, b: [a, b])
        pdb = {"schemaVersion": "graphops.peeringdb.v1", "networks": [{"asn": 8075}],
               "ixMemberships": [{"asn": 8075, "ix_id": 1}, {"asn": 54113, "ix_id": 1}],
               "facilityPresences": [], "facilities": [], "exchanges": []}
        ris = {"schemaVersion": "graphops.ris-live.v1", "controlPlanePaths": [
            {"prefix": "20.0.0.0/8", "evidenceClass": "CONTROL_PLANE_OBSERVATION"}]}
        result = attach_external_infrastructure_evidence(base, pdb, ris)
        self.assertEqual(result["referenceCatalog"]["legacyEmbeddedAdjacency"], "DISABLED")
        self.assertEqual(result["modeledPathCandidates"], [])
        self.assertFalse(result["declaredSharedIxCandidates"][0]["trafficObserved"])
        self.assertEqual(result["summary"]["controlPlaneObservations"], 1)


if __name__ == "__main__": unittest.main()
