import unittest
import json

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


class _Bus:
    def __init__(self): self.calls = []
    def commit(self, **kwargs): self.calls.append(kwargs); return _Result()


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

    def test_controlled_feed_remains_synthetic(self):
        bus = _Bus(); normalized = validate_eve_batch({"events": [event("test_flow")]})
        result = commit_eve_events(normalized, bus)
        self.assertEqual(result["evidenceClasses"], ["SYNTHETIC"])

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
