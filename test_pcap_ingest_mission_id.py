"""PR C: pcap_ingest.py's IngestConfig gains an optional mission_id, with no change to any
sessionization/emission logic. Omitting it must reproduce the exact pre-PR-C provenance shape."""
import unittest
from unittest.mock import patch

import scythe_mission
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


if __name__ == "__main__":
    unittest.main()
