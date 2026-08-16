import json
import time
import unittest
from unittest.mock import patch

from graphops_flow_evidence import (TEMPORAL_RING_LIMIT, clear_temporal_dissections,
                                    prepare_flow_evidence, record_temporal_dissection,
                                    temporal_dissections)
from graphops_full_fidelity import build_full_fidelity_capsule, validate_cloud_report
from scythe_orchestrator import _FLOW_EVIDENCE, app


def resolved_flow():
    return {
        "graphRevision": "graph-flow", "selectionKind": "graph-edge",
        "edge": {"id": "flow:tcp:10.0.0.1:50000->8.8.8.8:443", "kind": "network_flow",
                 "nodes": ["host:10.0.0.1", "host:8.8.8.8"], "evidenceClass": "OBSERVED",
                 "observedAt": 1770000000.5,
                 "labels": {"src_ip": "10.0.0.1", "src_port": "50000",
                            "dest_ip": "8.8.8.8", "dest_port": "443", "proto": "tcp",
                            "app_proto": "tls", "tls_sni": "example.org",
                            "tls_version": "TLS 1.3", "flow_bytes_toserver": "2048"},
                 "metadata": {"source": "eve-streamer", "evidence_class": "OBSERVED",
                              "reinforcement_count": 4, "packet_payload": "must-not-leave"}},
        "memberNodes": [
            {"id": "host:10.0.0.1", "labels": {"ip": "10.0.0.1"}},
            {"id": "host:8.8.8.8", "labels": {"ip": "8.8.8.8"},
             "enrichment": {"network": {"asn": 15169, "organization": "Google"}}},
        ], "incidentEdges": [], "bounded": True,
    }


class GraphOpsFlowEvidenceTests(unittest.TestCase):
    def setUp(self):
        _FLOW_EVIDENCE.clear()
        clear_temporal_dissections()

    def test_prepared_flow_preserves_decoded_facts_without_packet_payload(self):
        result = prepare_flow_evidence({"kind": "graph-edge", "entityId":
            "flow:tcp:10.0.0.1:50000->8.8.8.8:443", "graphRevision": "graph-flow"}, resolved_flow())
        encoded = json.dumps(result)
        self.assertIn("example.org", encoded)
        self.assertIn("TLS 1.3", encoded)
        self.assertNotIn("must-not-leave", encoded)
        self.assertFalse(result["rawPacketsExposed"])
        self.assertFalse(result["coverage"]["packetSequenceRetained"])

    def test_latest_32_decoded_events_are_ordered_and_cadence_bounded(self):
        flow_id = "flow:tcp:10.0.0.1:50000->8.8.8.8:443"
        for index in range(40):
            record_temporal_dissection(flow_id, {
                "eventId": f"eve-{index}", "eventType": "tls",
                "observedAt": f"2026-08-15T00:00:{index:02d}Z",
                "observedAtEpoch": 1000 + index * .25, "evidenceClass": "OBSERVED",
                "fields": {"tls_sni": "example.org", "packet_payload": "excluded"},
            })
        resolved = resolved_flow(); resolved["edge"]["metadata"]["reinforcement_count"] = 40
        result = prepare_flow_evidence({"kind": "graph-edge", "entityId": flow_id,
            "graphRevision": "graph-flow"}, resolved)
        self.assertEqual(len(result["packetDissections"]), TEMPORAL_RING_LIMIT)
        self.assertEqual(result["packetDissections"][0]["eventId"], "eve-8")
        self.assertEqual(result["packetDissections"][-1]["eventId"], "eve-39")
        self.assertEqual(result["temporalDissection"]["medianInterArrivalMilliseconds"], 250.0)
        self.assertEqual(result["temporalDissection"]["eventsOmittedBeforeRing"], 8)
        self.assertNotIn("packet_payload", json.dumps(result))

    @patch("scythe_orchestrator._graphops_directive_authorized", return_value=True)
    @patch("scythe_orchestrator._get_primary_instance_port", return_value=36501)
    @patch("scythe_orchestrator._proxy_post")
    def test_accepted_eve_batches_feed_the_orchestrator_temporal_sidecar(
            self, proxy, _port, _authorized):
        proxy.return_value = {"status": "ok", "committed": 1, "rejected": []}
        flow_id = "flow:udp:10.0.40.162:49152->239.255.255.250:1900"
        response = app.test_client().post("/api/graphops/eve/events", json={"events": [{
            "event_id": "eve-ssdp-1", "type": "flow", "timestamp": "2026-08-15T01:02:03Z",
            "entities": [{"key": "src_ip", "value": "10.0.40.162"},
                         {"key": "dest_ip", "value": "239.255.255.250"},
                         {"key": "src_port", "value": "49152"},
                         {"key": "dest_port", "value": "1900"},
                         {"key": "proto", "value": "udp"},
                         {"key": "app_proto", "value": "failed"},
                         {"key": "flow_pkts_toserver", "value": "1"}],
            "edges": []}]})
        self.assertEqual(response.status_code, 200)
        ring = temporal_dissections(flow_id)
        self.assertEqual(len(ring), 1)
        self.assertEqual(ring[0]["eventId"], "eve-ssdp-1")
        self.assertEqual(ring[0]["fields"]["flow_pkts_toserver"], "1")

    def test_cloud_validator_removes_definitive_maliciousness_verdict(self):
        selection = {"kind": "graph-edge", "entityId":
                     "flow:tcp:10.0.0.1:50000->8.8.8.8:443", "graphRevision": "graph-flow"}
        evidence = prepare_flow_evidence(selection, resolved_flow())
        capsule = build_full_fidelity_capsule(
            "Classify this activity", selection, resolved_flow(), {}, flow_evidence=evidence)
        report = validate_cloud_report({
            "situation": "This flow is malicious.", "anomalies": "TLS SNI observed",
            "measuredVsInferred": "Metadata observed; intent inferred",
            "assessment": "The activity confirms compromise.",
            "falsifier": "Capture a bounded packet window",
            "direction": "Capture and compare decoded fields", "confidence": .9,
        }, capsule)
        self.assertIn("UNSUPPORTED FLOW VERDICT REMOVED", report["situation"])
        self.assertLessEqual(report["confidence"], .35)

    @patch("scythe_orchestrator._graphops_directive_authorized", return_value=True)
    @patch("scythe_orchestrator._get_primary_instance_port", return_value=36501)
    @patch("scythe_orchestrator._proxy_post_with_status")
    def test_endpoint_prepares_server_owned_flow_evidence(self, resolve, _port, _authorized):
        resolve.return_value = (resolved_flow(), 200)
        selection = {"kind": "graph-edge", "entityId":
                     "flow:tcp:10.0.0.1:50000->8.8.8.8:443", "graphRevision": "graph-flow"}
        response = app.test_client().post("/api/graphops/flow-evidence", json={"selection": selection})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "prepared")
        self.assertIn(payload["evidenceId"], _FLOW_EVIDENCE)
        self.assertEqual(payload["coverage"]["decodedFieldCount"], 4)

    @patch("graphops_full_fidelity.ask_ollama_cloud")
    @patch("scythe_orchestrator._graphops_directive_authorized", return_value=True)
    def test_cloud_capsule_accepts_prepared_flow_and_receipts_decoded_fields(
            self, _authorized, ask_cloud):
        selection = {"kind": "graph-edge", "entityId":
                     "flow:tcp:10.0.0.1:50000->8.8.8.8:443", "graphRevision": "graph-flow"}
        result = prepare_flow_evidence(selection, resolved_flow())
        _FLOW_EVIDENCE[result["evidenceId"]] = {
            "capturedAt": time.time(), "result": result, "resolved": resolved_flow()}
        ask_cloud.return_value = {"model": "gpt-oss:20b", "report": {
            "situation": "Observed TLS flow", "anomalies": "None established",
            "measuredVsInferred": "TLS fields observed; purpose inferred",
            "assessment": "Could be ordinary HTTPS or automated service traffic",
            "falsifier": "Capture the next bounded bidirectional flow window",
            "direction": "Capture and compare directional TLS metadata", "confidence": .6}}
        response = app.test_client().post("/api/graphops/conversation/cloud-full-fidelity", json={
            "mode": "cloud-full-fidelity", "question": "Classify this activity",
            "selection": selection, "evidenceId": result["evidenceId"],
            "acknowledgeExactDisclosure": True})
        self.assertEqual(response.status_code, 200)
        receipt = response.get_json()["disclosureReceipt"]["disclosed"]
        self.assertEqual(receipt["decodedPacketFields"], 4)
        self.assertEqual(receipt["rawPacketPayloads"], 0)
        outbound = ask_cloud.call_args.args[0]
        self.assertIsNone(outbound["hostTrace"])
        self.assertEqual(outbound["flowEvidence"]["flow"]["evidenceClass"], "OBSERVED")


if __name__ == "__main__":
    unittest.main()
