import unittest
from unittest.mock import patch

import sensor_registry


class SensorRegistryLpiTests(unittest.TestCase):
    def test_missing_optional_frontend_rejects_iq_window_explicitly(self):
        registry = sensor_registry.SensorRegistry()

        with (
            patch.object(sensor_registry, "LPI_AVAILABLE", False),
            patch.object(sensor_registry, "LPI_IMPORT_ERROR", "frontend not installed"),
            self.assertLogs("SensorRegistry", level="WARNING") as captured,
        ):
            result = registry.emit_activity(
                "sensor-alpha",
                "iq_window",
                {"iq_real": [0.0] * 32, "iq_imag": [0.0] * 32},
            )

        self.assertFalse(result["ok"])
        self.assertEqual("lpi_frontend_unavailable", result["error_code"])
        self.assertEqual("sensor-alpha", result["sensor_id"])
        self.assertFalse(result["lpi_available"])
        self.assertEqual("frontend not installed", result["detail"])
        self.assertIn("Rejected IQ window", "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
