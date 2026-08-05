import unittest

from graphops_copilot import GraphOpsAgent


class GraphOpsAuthorityTests(unittest.TestCase):
    def test_empty_result_overrides_model_confidence(self):
        agent = GraphOpsAgent.__new__(GraphOpsAgent)
        final = agent._finalize_interpretation(
            "Explain the suspicious host", {"node_count": 0, "edge_count": 0},
            {"situation": "Definitely malicious", "confidence": 0.99},
            allow_grounding=False,
        )
        self.assertEqual(final["confidence"], 0.0)
        self.assertTrue(final["situation"].startswith("UNKNOWN"))

    def test_empty_temporal_result_stays_zero_confidence(self):
        agent = GraphOpsAgent.__new__(GraphOpsAgent)
        final = agent._finalize_interpretation(
            "Explain this network burst", {"node_count": 0, "edge_count": 0},
            {"confidence": 0.99}, allow_grounding=False,
        )
        self.assertEqual(final["confidence"], 0.0)
        self.assertEqual(final["temporal_evidence"], "TEMPORAL_EVIDENCE: ABSENT")

    def test_sparse_result_confidence_is_capped(self):
        final = GraphOpsAgent._enforce_evidence_authority(
            {"node_count": 2, "edge_count": 1}, {"confidence": 0.97},
        )
        self.assertEqual(final["confidence"], 0.25)


if __name__ == "__main__":
    unittest.main()
