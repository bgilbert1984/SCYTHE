import copy
import unittest

from manifest_tools import aggregate, compare

BASE = {
    "pcap_sha256": "a" * 64, "config_hash": "c", "session_hash": "s",
    "session_count": 4, "entity_count": 10, "edge_count": 12,
    "pipeline_version": "p/1", "git_commit": "g1", "runtime": {"python": "3.12"},
    "ok": True, "pcap_size": 2_000_000, "packets_decoded": 100, "duration_sec": 2.0,
    "stage_timings_sec": {"decode": 1.0, "sessionize": 0.5, "emit": 0.5},
}


class ManifestToolsTest(unittest.TestCase):
    def test_equivalent_ignores_environment(self):
        cand = copy.deepcopy(BASE)
        cand["git_commit"] = "g2"
        cand["duration_sec"] = 9.0
        r = compare(BASE, cand)
        self.assertEqual(r["status"], "EQUIVALENT")
        self.assertIn("git_commit", r["context_differences"])

    def test_different_on_output(self):
        cand = copy.deepcopy(BASE)
        cand["session_hash"] = "x"
        r = compare(BASE, cand)
        self.assertEqual(r["status"], "DIFFERENT")
        self.assertEqual(list(r["mismatches"]), ["session_hash"])

    def test_aggregate(self):
        failed = dict(BASE, ok=False)
        agg = aggregate([BASE, copy.deepcopy(BASE), failed])
        self.assertEqual((agg["manifests"], agg["successful"]), (3, 2))
        m = agg["metrics"]
        self.assertAlmostEqual(m["decode_ms"]["median"], 1000)
        self.assertAlmostEqual(m["packets_per_sec"]["median"], 50)
        self.assertAlmostEqual(m["ms_per_session"]["median"], 500)
        self.assertAlmostEqual(m["ms_per_mb"]["median"], 1000)


if __name__ == "__main__":
    unittest.main()
