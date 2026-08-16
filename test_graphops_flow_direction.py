import json
import os
import tempfile
import unittest
from unittest.mock import patch

from graphops_flow_direction import (classify_flow_direction, clear_flow_motion,
                                     preview_flow_motion, record_flow_motion)


class GraphOpsFlowDirectionTests(unittest.TestCase):
    def setUp(self):
        clear_flow_motion()

    def test_operational_direction_requires_explicit_sensor_boundary(self):
        with patch.dict(os.environ, {}, clear=True):
            result = classify_flow_direction("10.0.40.2", "8.8.8.8")
        self.assertEqual(result["tuple_direction"], "SOURCE_TO_DESTINATION")
        self.assertEqual(result["operational_direction"], "UNRESOLVED")

    def test_boundary_classifies_out_in_and_east_west(self):
        self.assertEqual(classify_flow_direction("10.0.40.2", "8.8.8.8",
            cidrs=["10.0.40.162/24"])["operational_direction"], "OUTBOUND")
        self.assertEqual(classify_flow_direction("8.8.8.8", "10.0.40.2",
            cidrs=["10.0.40.0/24"])["operational_direction"], "INBOUND")
        self.assertEqual(classify_flow_direction("10.0.40.2", "10.0.40.3",
            cidrs=["10.0.40.0/24"])["operational_direction"], "EAST_WEST")

    def test_utf8_sensor_file_is_read_on_demand(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8",
                                         delete=False) as handle:
            json.dump({"localCidrs": ["192.168.52.17/24"], "sensorId": "field/Wi-Fi",
                       "capturedAt": "2026-08-15T12:00:00Z",
                       "authority": "DISCOVERED_SENSOR_INTERFACE"}, handle)
            name = handle.name
        try:
            result = classify_flow_direction("1.1.1.1", "192.168.52.8", zone_file=name)
            self.assertEqual(result["operational_direction"], "INBOUND")
            self.assertEqual(result["sensor_id"], "field/Wi-Fi")
        finally:
            os.unlink(name)

    def test_motion_requires_two_monotonic_counter_observations(self):
        fields = {"flow_pkts_toserver": "2", "flow_pkts_toclient": "1"}
        self.assertEqual(preview_flow_motion("flow:1", fields, 100)["motion_basis"],
                         "INSUFFICIENT_TEMPORAL_COUNTERS")
        record_flow_motion("flow:1", fields, 100)
        result = preview_flow_motion("flow:1", {"flow_pkts_toserver": "7",
            "flow_pkts_toclient": "3"}, 101.5)
        self.assertEqual(result["motion_basis"], "OBSERVED_SURICATA_COUNTER_DELTA")
        self.assertEqual(result["motion_forward_delta_packets"], 5)
        self.assertEqual(result["motion_reverse_delta_packets"], 2)
        self.assertEqual(result["motion_interval_ms"], 1500)


if __name__ == "__main__":
    unittest.main()
