"""PR C: pcap_ingest.py's IngestConfig gains an optional mission_id, with no change to any
sessionization/emission logic. Omitting it must reproduce the exact pre-PR-C provenance shape.
PR D adds mission_id to the same nodes' LABELS (not just metadata.provenance), and
HypergraphEngine.nodes_by_mission() as the backend query primitive for "show Mission X"."""
import unittest
from unittest.mock import patch

import scythe_mission
from hypergraph_engine import HypergraphEngine
from pcap_ingest import HypergraphEmitter, IngestConfig, PacketMeta, SessionData, _pcap_artifact_id

VALID_MISSION_ID = "RFMAP-20261017-001"


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


def _emitter(config):
    engine = CollectingEngine()
    with patch("pcap_ingest._get_writebus_instance", return_value=None):
        return engine, HypergraphEmitter(engine, config)


def _session(pcap_file="capture.pcapng"):
    packet = PacketMeta(timestamp=1000.0, src_ip="10.0.0.1", dst_ip="10.0.0.2",
                        src_port=1234, dst_port=443, proto="TCP", length=100)
    return SessionData(session_id="SESSION-test", session_key="test-key", packets=[packet],
                       time_bucket=0, src_ip="10.0.0.1", dst_ip="10.0.0.2", src_port=1234,
                       dst_port=443, proto="TCP", pcap_file=pcap_file)


def _minimal_config(**over):
    base = dict(enable_geoip=False, enable_dpi=False, emit_flow_nodes=False,
                enable_bsg=False, register_ledger=False)
    base.update(over)
    return IngestConfig(**base)


class MissionIdTests(unittest.TestCase):
    def test_omitted_mission_id_produces_the_unchanged_pre_pr_c_provenance_shape(self):
        engine, emitter = _emitter(_minimal_config())
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        provenance = engine.nodes[artifact_id]["metadata"]["provenance"]
        self.assertNotIn("mission_id", provenance)
        self.assertEqual(set(provenance), {"source", "evidence_type"})

    def test_mission_id_is_attached_to_pcap_artifact_provenance_when_set(self):
        engine, emitter = _emitter(_minimal_config(mission_id=VALID_MISSION_ID))
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        self.assertEqual(engine.nodes[artifact_id]["metadata"]["provenance"]["mission_id"], VALID_MISSION_ID)

    def test_mission_id_is_attached_to_session_provenance_when_set(self):
        engine, emitter = _emitter(_minimal_config(mission_id=VALID_MISSION_ID))
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        emitter.emit_session(_session(), artifact_id)
        session_node = engine.nodes["SESSION-test"]
        self.assertEqual(session_node["metadata"]["provenance"]["mission_id"], VALID_MISSION_ID)
        # source_tag, evidence_type, pcap_file, session_key -- the existing fields -- are untouched
        self.assertEqual(session_node["metadata"]["provenance"]["source"], "pcap_ingest")
        self.assertEqual(session_node["metadata"]["provenance"]["pcap_file"], "capture.pcapng")

    def test_omitted_mission_id_leaves_session_provenance_unchanged_too(self):
        engine, emitter = _emitter(_minimal_config())
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        emitter.emit_session(_session(), artifact_id)
        provenance = engine.nodes["SESSION-test"]["metadata"]["provenance"]
        self.assertNotIn("mission_id", provenance)
        self.assertEqual(set(provenance), {"source", "evidence_type", "pcap_file", "session_key"})

    def test_malformed_mission_id_is_rejected_at_config_construction(self):
        with self.assertRaisesRegex(ValueError, "mission_id must be a valid Mission identifier"):
            _minimal_config(mission_id="has a space")
        with self.assertRaisesRegex(ValueError, "mission_id must be a valid Mission identifier"):
            _minimal_config(mission_id="")

    def test_mission_id_agrees_with_scythe_mission_s_own_rule(self):
        m = scythe_mission.Mission(mission_id=VALID_MISSION_ID, mission_type="PCAP_COLLECTION",
                                   purpose="test", operator="test")
        config = _minimal_config(mission_id=m.mission_id)
        self.assertEqual(config.mission_id, m.mission_id)

    def test_bsg_detector_source_is_preserved_and_mission_id_still_attaches(self):
        from pcap_ingest import _provenance
        config = _minimal_config(mission_id=VALID_MISSION_ID)
        prov = _provenance(config, source="bsg_detector", evidence_type="behavioral_inference")
        self.assertEqual(prov["source"], "bsg_detector")
        self.assertEqual(prov["mission_id"], VALID_MISSION_ID)
        self.assertEqual(prov["evidence_type"], "behavioral_inference")


class MissionLabelTests(unittest.TestCase):
    """PR D: mission_id in labels (not just provenance), so HypergraphEngine's label_index --
    and therefore nodes_by_mission() -- can actually find these nodes."""

    def test_omitted_mission_id_leaves_pcap_artifact_labels_unchanged(self):
        engine, emitter = _emitter(_minimal_config())
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        labels = engine.nodes[artifact_id]["labels"]
        self.assertNotIn("mission_id", labels)
        self.assertEqual(set(labels), {"filename", "file_size", "ingested_at"})

    def test_mission_id_is_attached_to_pcap_artifact_labels_when_set(self):
        engine, emitter = _emitter(_minimal_config(mission_id=VALID_MISSION_ID))
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        self.assertEqual(engine.nodes[artifact_id]["labels"]["mission_id"], VALID_MISSION_ID)

    def test_mission_id_is_attached_to_session_and_host_labels_when_set(self):
        engine, emitter = _emitter(_minimal_config(mission_id=VALID_MISSION_ID))
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        emitter.emit_session(_session(), artifact_id)
        self.assertEqual(engine.nodes["SESSION-test"]["labels"]["mission_id"], VALID_MISSION_ID)
        host_nodes = [n for nid, n in engine.nodes.items() if n["kind"] == "host"]
        self.assertTrue(host_nodes)
        for host in host_nodes:
            self.assertEqual(host["labels"]["mission_id"], VALID_MISSION_ID)

    def test_omitted_mission_id_leaves_session_and_host_labels_unchanged(self):
        engine, emitter = _emitter(_minimal_config())
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        emitter.emit_session(_session(), artifact_id)
        self.assertNotIn("mission_id", engine.nodes["SESSION-test"]["labels"])
        for nid, n in engine.nodes.items():
            if n["kind"] == "host":
                self.assertNotIn("mission_id", n["labels"])


class NodesByMissionTests(unittest.TestCase):
    """End-to-end against the REAL HypergraphEngine (not the CollectingEngine test double),
    since nodes_by_mission() depends on the engine's own label_index actually being built."""

    def test_nodes_by_mission_finds_the_pcap_artifact_and_session_after_emission(self):
        engine = HypergraphEngine()
        with patch("pcap_ingest._get_writebus_instance", return_value=None):
            emitter = HypergraphEmitter(engine, _minimal_config(mission_id=VALID_MISSION_ID))
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        emitter.emit_session(_session(), artifact_id)

        found_ids = {n.id for n in engine.nodes_by_mission(VALID_MISSION_ID)}
        self.assertIn(artifact_id, found_ids)
        self.assertIn("SESSION-test", found_ids)
        host_ids = {nid for nid, n in engine.nodes.items() if n.kind == "host"}
        self.assertTrue(host_ids)
        self.assertTrue(host_ids <= found_ids)

    def test_nodes_by_mission_is_empty_for_an_unrelated_mission_id(self):
        engine = HypergraphEngine()
        with patch("pcap_ingest._get_writebus_instance", return_value=None):
            emitter = HypergraphEmitter(engine, _minimal_config(mission_id=VALID_MISSION_ID))
        emitter.emit_pcap_artifact("capture.pcapng", 123)

        self.assertEqual(list(engine.nodes_by_mission("RFMAP-UNRELATED-002")), [])

    def test_nodes_by_mission_is_empty_when_no_node_ever_set_mission_id(self):
        engine = HypergraphEngine()
        with patch("pcap_ingest._get_writebus_instance", return_value=None):
            emitter = HypergraphEmitter(engine, _minimal_config())
        emitter.emit_pcap_artifact("capture.pcapng", 123)

        self.assertEqual(list(engine.nodes_by_mission(VALID_MISSION_ID)), [])

    def test_nodes_by_kind_is_unaffected_by_the_new_label(self):
        # The existing index/query mechanism must not change behavior for callers that don't
        # care about mission_id at all.
        engine = HypergraphEngine()
        with patch("pcap_ingest._get_writebus_instance", return_value=None):
            emitter = HypergraphEmitter(engine, _minimal_config(mission_id=VALID_MISSION_ID))
        artifact_id = emitter.emit_pcap_artifact("capture.pcapng", 123)
        kinds = {n.id for n in engine.nodes_by_kind("pcap_artifact")}
        self.assertEqual(kinds, {artifact_id})


if __name__ == "__main__":
    unittest.main()
