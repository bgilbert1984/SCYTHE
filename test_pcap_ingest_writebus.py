import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pcap_ingest import HypergraphEmitter, IngestConfig, _pcap_artifact_id


class DirectMutationForbiddenEngine:
    trace_id = "test-graph"
    nodes = {}
    edges = {}

    def add_node(self, node):
        raise AssertionError("direct add_node should not be called")

    def add_edge(self, edge):
        raise AssertionError("direct add_edge should not be called")


class CollectingEngine:
    def __init__(self):
        self.trace_id = "direct-graph"
        self.nodes = {}
        self.edges = {}

    def add_node(self, node):
        self.nodes[node["id"]] = node
        return node["id"]

    def add_edge(self, edge):
        self.edges[edge["id"]] = edge
        return edge["id"]


class FakeWriteBus:
    def __init__(self):
        self.calls = []

    def commit(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(ok=True, errors=[], commit_status="COMMITTED")


class PcapIngestWriteBusTests(unittest.TestCase):
    def test_emitter_queues_nodes_and_flushes_through_writebus(self):
        bus = FakeWriteBus()
        config = IngestConfig(source_tag="pcap_ingest")
        artifact_id = _pcap_artifact_id("capture.pcapng")

        with patch("pcap_ingest._get_writebus_instance", return_value=bus):
            emitter = HypergraphEmitter(DirectMutationForbiddenEngine(), config)
            self.assertEqual(artifact_id, emitter.emit_pcap_artifact("capture.pcapng", 123))
            self.assertEqual(0, len(bus.calls))

            emitter.flush(
                entity_id=artifact_id,
                entity_type="PCAP_INGEST_MATERIALIZATION",
                entity_data={"id": artifact_id, "kind": "pcap_artifact"},
                request_id=f"pcap_ingest:{artifact_id}:materialize",
                evidence_refs=[artifact_id, "capture.pcapng"],
                idempotency_key=emitter.idempotency_key(artifact_id, "materialize"),
            )

        self.assertEqual(1, len(bus.calls))
        call = bus.calls[0]
        self.assertFalse(call["persist"])
        self.assertTrue(call["audit"])
        self.assertEqual(
            f"pcap-ingest:{artifact_id}:materialize:test-graph:v2",
            call["idempotency_key"],
        )
        self.assertEqual("pcap_ingest", call["ctx"].source)
        self.assertEqual("SYSTEM:PCAP_INGEST", call["ctx"].operator_id)
        self.assertEqual([artifact_id, "capture.pcapng"], call["ctx"].evidence_refs)
        self.assertEqual(1, len(call["graph_ops"]))
        self.assertEqual("NODE_UPDATE", call["graph_ops"][0].event_type)
        self.assertEqual(artifact_id, call["graph_ops"][0].entity_id)

    def test_discard_pending_prevents_stale_ops_from_flushing(self):
        bus = FakeWriteBus()
        config = IngestConfig()
        artifact_id = _pcap_artifact_id("partial.pcapng")

        with patch("pcap_ingest._get_writebus_instance", return_value=bus):
            emitter = HypergraphEmitter(DirectMutationForbiddenEngine(), config)
            emitter.emit_pcap_artifact("partial.pcapng", 123)
            emitter.discard_pending()
            emitter.flush(
                entity_id=artifact_id,
                entity_type="PCAP_INGEST_MATERIALIZATION",
                entity_data={"id": artifact_id, "kind": "pcap_artifact"},
                request_id=f"pcap_ingest:{artifact_id}:materialize",
                evidence_refs=[artifact_id, "partial.pcapng"],
                idempotency_key=emitter.idempotency_key(artifact_id, "materialize"),
            )

        self.assertEqual([], bus.calls)

    def test_emitter_falls_back_to_direct_graph_writer_without_writebus(self):
        engine = CollectingEngine()
        artifact_id = _pcap_artifact_id("standalone.pcapng")

        with patch("pcap_ingest._get_writebus_instance", return_value=None):
            emitter = HypergraphEmitter(engine, IngestConfig())
            emitter.emit_pcap_artifact("standalone.pcapng", 456)
            emitter.flush(
                entity_id=artifact_id,
                entity_type="PCAP_INGEST_MATERIALIZATION",
                entity_data={"id": artifact_id, "kind": "pcap_artifact"},
                request_id=f"pcap_ingest:{artifact_id}:materialize",
                evidence_refs=[artifact_id, "standalone.pcapng"],
                idempotency_key=emitter.idempotency_key(artifact_id, "materialize"),
            )

        self.assertIn(artifact_id, engine.nodes)
        self.assertEqual("pcap_artifact", engine.nodes[artifact_id]["kind"])

    def test_idempotency_key_is_scoped_to_graph_epoch(self):
        bus = FakeWriteBus()
        config = IngestConfig()
        artifact_id = _pcap_artifact_id("same-capture.pcapng")
        first_engine = DirectMutationForbiddenEngine()
        second_engine = DirectMutationForbiddenEngine()
        first_engine.trace_id = "graph-one"
        second_engine.trace_id = "graph-two"

        with patch("pcap_ingest._get_writebus_instance", return_value=bus):
            first = HypergraphEmitter(first_engine, config)
            second = HypergraphEmitter(second_engine, config)

        self.assertNotEqual(
            first.idempotency_key(artifact_id, "materialize"),
            second.idempotency_key(artifact_id, "materialize"),
        )


if __name__ == "__main__":
    unittest.main()
