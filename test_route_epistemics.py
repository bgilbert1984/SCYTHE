import unittest

from fusion_engine import RTTAnalyzer


class RouteEpistemicsTests(unittest.TestCase):
    def test_single_trace_timing_flags_are_derived_not_root_causes(self):
        hops = RTTAnalyzer().filter_hops([
            {'hop': 1, 'ip': '203.0.113.1', 'rtt_ms': 3.0},
            {'hop': 2, 'ip': '203.0.113.2', 'rtt_ms': 12.0},
            {'hop': 3, 'ip': '203.0.113.3', 'rtt_ms': 2.0},
        ])
        self.assertEqual(hops[1]['anomaly'], 'rtt_spike')
        self.assertEqual(hops[2]['anomaly'], 'non_monotonic')
        for hop in hops[1:]:
            self.assertEqual(hop['anomaly_evidence_class'], 'DERIVED_INFERENCE')
            self.assertIn('NOT A ROOT-CAUSE CLAIM', hop['anomaly_interpretation'])


if __name__ == '__main__':
    unittest.main()
