import unittest
from unittest.mock import patch

from graphops_address_context import classify_address, prepare_address_context
from scythe_orchestrator import app


def resolved(address="ff02::1:3"):
    return {"graphRevision": "graph-address", "selectionKind": "graph-node",
            "node": {"id": f"host:{address}", "kind": "network_multicast_group",
                     "labels": {"ip": address}, "evidenceClass": "OBSERVED"},
            "incidentEdges": [{"id": "flow:llmnr", "kind": "network_flow",
                               "evidenceClass": "OBSERVED", "observedAt": 100,
                               "labels": {"src_ip": "fe80::1", "src_port": "5355",
                                          "dest_ip": address, "dest_port": "5355",
                                          "proto": "udp", "app_proto": "llmnr"}}],
            "bounded": True}


class AddressContextTests(unittest.TestCase):
    def test_known_multicast_group_is_not_promoted_to_a_host(self):
        result = prepare_address_context({"kind": "graph-node", "entityId": "host:ff02::1:3",
                                          "graphRevision": "graph-address"}, resolved())
        self.assertEqual(result["address"]["knownService"], "LLMNR")
        self.assertEqual(result["address"]["addressClass"], "MULTICAST_GROUP")
        self.assertEqual(result["activeMeasurement"]["status"], "NOT_APPLICABLE")
        self.assertEqual(result["passiveEvidence"]["observedSenders"], ["fe80::1"])

    def test_unspecified_address_is_a_non_routable_sentinel(self):
        value = classify_address("::")
        self.assertEqual(value["addressClass"], "UNSPECIFIED_ADDRESS")
        self.assertEqual(value["scope"], "NON_ROUTABLE_SENTINEL")

    @patch("scythe_orchestrator._graphops_directive_authorized", return_value=True)
    @patch("scythe_orchestrator._get_primary_instance_port", return_value=36501)
    @patch("scythe_orchestrator._proxy_post_with_status")
    def test_endpoint_resolves_only_server_owned_graph_context(self, resolve_call, _port, _authorized):
        resolve_call.return_value = (resolved(), 200)
        selection = {"kind": "graph-node", "entityId": "host:ff02::1:3",
                     "graphRevision": "graph-address"}
        response = app.test_client().post("/api/graphops/address-context",
                                          json={"selection": selection})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["rawPacketsExposed"])


if __name__ == "__main__":
    unittest.main()
