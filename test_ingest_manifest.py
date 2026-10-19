import json
import struct
import tempfile
import unittest
from pathlib import Path

from ingest_manifest import MANIFEST_SCHEMA, config_hash, session_hash
from pcap_ingest import IngestConfig, PcapIngestPipeline


def _ip_udp(src, dst, sport, dport):
    payload = b"hello"
    udp = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 1, 0, 64, 17, 0,
                     bytes(map(int, src.split("."))), bytes(map(int, dst.split("."))))
    return b"\x00" * 12 + b"\x08\x00" + ip + udp


def _write_pcap(path):
    out = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    for i in range(4):
        pkt = _ip_udp("10.0.0.1", "10.0.0.2", 5000, 53)
        out += struct.pack("<IIII", 1_700_000_000 + i, 0, len(pkt), len(pkt)) + pkt
    path.write_bytes(out)


class Engine:
    trace_id = "t"

    def __init__(self):
        self.nodes, self.edges = {}, {}

    def add_node(self, n):
        self.nodes[n["id"]] = n
        return n["id"]

    def add_edge(self, e):
        self.edges[e["id"]] = e
        return e["id"]


class IngestManifestTest(unittest.TestCase):
    def _ingest(self, tmp, pcap, out):
        cfg = IngestConfig(manifest_dir=str(out), enable_geoip=False, enable_bsg=False,
                           register_ledger=False, staging_dir=str(tmp))
        return PcapIngestPipeline(Engine(), config=cfg).ingest_file(pcap)

    def test_manifest_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            pcap = d / "a.pcap"
            _write_pcap(pcap)
            r1 = self._ingest(d, pcap, d / "m1")
            r2 = self._ingest(d, pcap, d / "m2")
            self.assertTrue(r1.ok, r1.errors)
            m1 = json.loads(Path(r1.manifest_path).read_text())
            m2 = json.loads(Path(r2.manifest_path).read_text())
            self.assertEqual(m1["schema"], MANIFEST_SCHEMA)
            self.assertEqual(len(m1["pcap_sha256"]), 64)
            for k in ("pcap_sha256", "config_hash", "session_hash", "session_count",
                      "entity_count", "edge_count"):
                self.assertEqual(m1[k], m2[k], k)
            self.assertGreaterEqual(m1["session_count"], 1)
            self.assertEqual(set(m1["stage_timings_sec"]), {"decode", "sessionize", "emit"})

    def test_disabled_by_default(self):
        self.assertIsNone(IngestConfig().manifest_dir)

    def test_hash_helpers(self):
        self.assertEqual(session_hash(["b", "a"]), session_hash(["a", "b"]))
        self.assertEqual(config_hash(IngestConfig(staging_dir="/x")),
                         config_hash(IngestConfig(staging_dir="/y")))
        self.assertNotEqual(config_hash(IngestConfig(session_window_sec=30)),
                            config_hash(IngestConfig(session_window_sec=60)))


if __name__ == "__main__":
    unittest.main()
