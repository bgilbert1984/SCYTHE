import unittest
import json
from unittest.mock import patch

from eve_graph_ingest import EveIngestError, commit_eve_events, validate_eve_batch
from graphops_graph_resolver import GraphSelectionResolver
from hypergraph_engine import HypergraphEngine


def event(event_type="flow"):
    return {"event_id": "eve-1", "type": event_type, "timestamp": "2026-08-07T01:00:00Z",
            "entities": [{"key": "src_ip", "value": "10.0.0.1"},
                         {"key": "dest_ip", "value": "8.8.8.8"},
                         {"key": "src_port", "value": "50000"},
                         {"key": "dest_port", "value": "443"},
                         {"key": "proto", "value": "tcp"}],
            "edges": ["10.0.0.1 -> 8.8.8.8"]}


class _Result:
    ok = True
    errors = []
    debug = {}


class _Bus:
    def __init__(self): self.calls = []
    def commit(self, **kwargs): self.calls.append(kwargs); return _Result()


class _DuplicateBus(_Bus):
    def commit(self, **kwargs):
        self.calls.append(kwargs)
        return type("DuplicateResult", (), {"ok": True, "errors": [],
                    "debug": {"idempotent_replay": True}})()


class _Store:
    def __init__(self, engine): self.nodes = {}; self.hyperedges = []; self.hypergraph_engine = engine


class EveGraphIngestTests(unittest.TestCase):
    def test_batch_rejects_raw_or_unknown_payload_fields(self):
        with self.assertRaisesRegex(EveIngestError, "unknown fields"):
            validate_eve_batch({"events": [{**event(), "payload": "raw"}]})
        invalid = validate_eve_batch({"events": [{**event(), "entities": [
            {"key": "src_ip", "value": "not-an-ip"},
            {"key": "dest_ip", "value": "8.8.8.8"}]}]})
        result = commit_eve_events(invalid, _Bus())
        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("valid IP", result["rejected"][0]["error"])

    def test_commit_uses_writebus_and_observed_provenance(self):
        bus = _Bus(); normalized = validate_eve_batch({"events": [event()]})
        result = commit_eve_events(normalized, bus)
        self.assertEqual(result["committed"], 1)
        self.assertEqual(len(bus.calls), 1)
        self.assertFalse(bus.calls[0]["persist"])
        self.assertTrue(bus.calls[0]["audit"])
        self.assertEqual(bus.calls[0]["graph_ops"][-1].entity_data["metadata"]["evidence_class"], "OBSERVED")

    def test_allow_listed_packet_dissections_enter_flow_labels_without_payloads(self):
        bus = _Bus(); decoded = event("tls")
        decoded["entities"].extend([
            {"key": "app_proto", "value": "tls"},
            {"key": "tls_sni", "value": "example.org"},
            {"key": "tls_version", "value": "TLS 1.3"},
        ])
        commit_eve_events(validate_eve_batch({"events": [decoded]}), bus)
        labels = bus.calls[0]["graph_ops"][-1].entity_data["labels"]
        self.assertEqual(labels["app_proto"], "tls")
        self.assertEqual(labels["tls_sni"], "example.org")
        self.assertEqual(labels["flow_type"], "TLS")
        self.assertEqual(labels["flow_type_basis"], "OBSERVED_DECODED")
        self.assertNotIn("payload", labels)

    def test_multicast_ssdp_tuple_is_display_classified_without_claiming_decoder_authority(self):
        bus = _Bus(); discovery = event()
        discovery["entities"][1] = {"key": "dest_ip", "value": "239.255.255.250"}
        discovery["entities"][3] = {"key": "dest_port", "value": "1900"}
        commit_eve_events(validate_eve_batch({"events": [discovery]}), bus)
        labels = bus.calls[0]["graph_ops"][-1].entity_data["labels"]
        self.assertEqual(labels["flow_type"], "SERVICE_DISCOVERY")
        self.assertEqual(labels["flow_type_basis"], "INFERRED_TUPLE")

    @patch.dict("os.environ", {"SCYTHE_SENSOR_LOCAL_CIDRS": "10.0.0.0/24"}, clear=False)
    def test_flow_direction_is_boundary_relative_and_tuple_provenance_is_retained(self):
        bus = _Bus()
        commit_eve_events(validate_eve_batch({"events": [event()]}), bus)
        labels = bus.calls[0]["graph_ops"][-1].entity_data["labels"]
        self.assertEqual(labels["tuple_direction"], "SOURCE_TO_DESTINATION")
        self.assertEqual(labels["tuple_direction_basis"], "OBSERVED_EVE_TUPLE")
        self.assertEqual(labels["operational_direction"], "OUTBOUND")
        self.assertEqual(labels["direction_basis"], "CONFIGURED_SENSOR_BOUNDARY")

    def test_reinforced_flow_retains_latest_decoded_summary(self):
        engine = HypergraphEngine()
        base = {"id": "flow:a", "kind": "network_flow",
                "nodes": ["host:a", "host:b"], "metadata": {"source": "eve-streamer"}}
        engine.add_edge({**base, "labels": {"app_proto": "unknown"}})
        engine.add_edge({**base, "labels": {"app_proto": "tls", "tls_sni": "example.org"}})
        edge = engine.edges["flow:a"]
        self.assertEqual(edge.labels["app_proto"], "tls")
        self.assertEqual(edge.labels["tls_sni"], "example.org")
        self.assertEqual(edge.metadata["reinforcement_count"], 2)

    def test_idempotency_is_scoped_to_the_graph_session(self):
        bus = _Bus(); normalized = validate_eve_batch({"events": [event()]})
        commit_eve_events(normalized, bus, idempotency_scope="session-42")
        self.assertEqual(bus.calls[0]["idempotency_key"], "eve:session-42:eve-1")

    def test_idempotent_replay_is_counted_without_claiming_a_second_commit(self):
        result = commit_eve_events(validate_eve_batch({"events": [event()]}), _DuplicateBus())
        self.assertEqual(result["committed"], 0)
        self.assertEqual(result["deduplicated"], 1)
        self.assertEqual(result["received"], 1)

    def test_controlled_feed_remains_synthetic(self):
        bus = _Bus(); normalized = validate_eve_batch({"events": [event("test_flow")]})
        result = commit_eve_events(normalized, bus)
        self.assertEqual(result["evidenceClasses"], ["SYNTHETIC"])

    def test_multicast_and_unspecified_addresses_are_not_typed_as_hosts(self):
        bus = _Bus(); multicast = event()
        multicast["entities"][1] = {"key": "dest_ip", "value": "ff02::1:3"}
        commit_eve_events(validate_eve_batch({"events": [multicast]}), bus)
        nodes = bus.calls[0]["graph_ops"][:2]
        self.assertEqual(nodes[1].entity_data["kind"], "network_multicast_group")
        self.assertEqual(nodes[1].entity_data["labels"]["addressClass"], "MULTICAST_GROUP")

        bus = _Bus(); unspecified = event()
        unspecified["entities"][1] = {"key": "dest_ip", "value": "0.0.0.0"}
        commit_eve_events(validate_eve_batch({"events": [unspecified]}), bus)
        self.assertEqual(bus.calls[0]["graph_ops"][1].entity_data["kind"],
                         "network_unspecified_address")

    def test_bootstrap_replay_is_observed_history_with_explicit_ingest_mode(self):
        bus = _Bus(); replay = event()
        replay["entities"].append({"key": "scythe_ingest_mode", "value": "bootstrap_replay"})
        result = commit_eve_events(validate_eve_batch({"events": [replay]}), bus)
        self.assertEqual(result["replayed"], 1)
        edge = bus.calls[0]["graph_ops"][-1].entity_data
        self.assertEqual(edge["metadata"]["ingest_mode"], "BOOTSTRAP_REPLAY")
        self.assertEqual(edge["metadata"]["evidence_class"], "OBSERVED")

    def test_selection_projection_includes_attached_eve_graph(self):
        engine = HypergraphEngine()
        metadata = {"source": "eve-streamer", "evidence_class": "OBSERVED", "observed_at": 100.0}
        engine.add_node({"id": "host:10.0.0.1", "kind": "network_host", "metadata": metadata})
        engine.add_node({"id": "host:8.8.8.8", "kind": "network_host", "metadata": metadata})
        engine.add_edge({"id": "flow:a", "kind": "network_flow",
                         "nodes": ["host:10.0.0.1", "host:8.8.8.8"], "metadata": metadata, "timestamp": 100})
        snapshot = GraphSelectionResolver(_Store(engine)).snapshot()
        self.assertEqual(snapshot["nodeCount"], 2)
        self.assertEqual(snapshot["edgeCount"], 1)
        self.assertEqual({node["evidenceClass"] for node in snapshot["nodes"]}, {"OBSERVED"})

    def test_writebus_metadata_alias_does_not_create_circular_graph_state(self):
        engine = HypergraphEngine()
        metadata = {"source": "eve-streamer", "evidence_class": "OBSERVED"}
        engine.apply_graph_event({
            "event_type": "NODE_UPDATE", "entity_id": "host:10.0.0.1",
            "entity_data": {"id": "host:10.0.0.1", "kind": "network_host",
                            "metadata": metadata, "meta": metadata},
        })
        node = engine.nodes["host:10.0.0.1"].to_dict()
        self.assertNotIn("meta", node["metadata"])
        json.dumps(node)


if __name__ == "__main__":
    unittest.main()
