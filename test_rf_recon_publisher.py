import unittest

import os, tempfile
from rf_recon_publisher import RFReconPublisher, _dotenv_keys, sensor_location_from_env


class FakeStore:
    def __init__(self):
        self.rows = []

    def add(self, t, freq, snr=20.0, eid=None):
        self.rows.append({"evidence_id": eid or f"e{len(self.rows)}", "observed_at": t,
                          "peak_frequency_hz": freq, "snr_db": snr, "peak_dbfs": -40.0,
                          "noise_floor_dbfs": -67.0, "sensor_id": "S1", "mission_id": None})

    def query(self, since=None, limit=100, **_):
        out = [r for r in self.rows if since is None or r["observed_at"] >= since]
        return list(reversed(out))[:limit]


class Rig:
    def __init__(self, location=(47.9, -122.3), ok=True):
        self.store, self.posts, self.ok, self.now = FakeStore(), [], ok, [1000.0]
        self.pub = RFReconPublisher(self.store, self._post, location=location, clock=lambda: self.now[0])

    def _post(self, path, body):
        self.posts.append((path, body))
        return {"status": "ok"} if self.ok else None


class PublisherTests(unittest.TestCase):
    def test_one_entity_per_signal_with_wandering_peak(self):
        r = Rig()
        r.store.add(1, 99_900_000.0); r.store.add(2, 99_902_000.0); r.store.add(3, 100_700_000.0)
        self.assertEqual(r.pub.step(), 2)
        ids = sorted(b["entity_id"] for _, b in r.posts)
        self.assertEqual(ids, ["RF-099900kHz", "RF-100700kHz"])
        first = next(b for _, b in r.posts if b["entity_id"] == "RF-099900kHz")
        self.assertEqual(first["meta"]["hit_count"], 2)

    def test_wideband_fm_peak_wander_is_one_entity_and_names_strongest(self):
        r = Rig()
        r.store.add(1, 99_910_000.0); r.store.add(2, 99_846_000.0); r.store.add(3, 99_945_000.0)
        r.store.rows[1]["peak_dbfs"] = -30.0  # strongest hit at 99.846
        r.pub.step()
        self.assertEqual(len(r.posts), 1)
        self.assertEqual(r.posts[0][1]["name"], "RF 99.846 MHz")
        self.assertEqual(r.posts[0][1]["entity_id"], "RF-099910kHz")
        self.assertEqual(r.posts[0][1]["meta"]["hit_count"], 3)

    def test_adjacent_stations_stay_apart(self):
        r = Rig(); r.store.add(1, 99.9e6); r.store.add(2, 100.7e6); r.pub.step()
        self.assertEqual(len(r.posts), 2)

    def test_position_is_receiver_and_labelled(self):
        r = Rig(); r.store.add(1, 99.9e6); r.pub.step()
        body = r.posts[0][1]
        self.assertEqual(body["location"]["lat"], 47.9)
        self.assertEqual(body["meta"]["position_kind"], "RECEIVER_LOCATION")
        self.assertEqual(body["meta"]["evidence_class"], "OBSERVED")

    def test_no_location_means_no_position(self):
        r = Rig(location=None); r.store.add(1, 99.9e6); r.pub.step()
        self.assertNotIn("location", r.posts[0][1])
        self.assertEqual(r.posts[0][1]["meta"]["position_kind"], "NONE")

    def test_updates_throttled_and_not_double_counted(self):
        r = Rig(); r.store.add(1, 99.9e6); r.pub.step()
        r.pub.step()  # same row re-queried: not counted again, nothing dirty
        self.assertEqual(len(r.posts), 1)
        r.store.add(2, 99.9e6); r.now[0] += 1
        self.assertEqual(r.pub.step(), 0)  # throttled
        r.now[0] += 10
        self.assertEqual(r.pub.step(), 1)
        self.assertEqual(r.posts[-1][1]["meta"]["hit_count"], 2)

    def test_same_timestamp_rows_all_counted_once(self):
        r = Rig(); r.store.add(1, 99.9e6); r.pub.step()
        r.store.add(1, 99.9e6); r.now[0] += 10; r.pub.step(); r.pub.step()
        self.assertEqual(r.posts[-1][1]["meta"]["hit_count"], 2)

    def test_failed_post_retried(self):
        r = Rig(ok=False); r.store.add(1, 99.9e6)
        self.assertEqual(r.pub.step(), 0); self.assertEqual(r.pub.failed, 1)
        r.ok = True
        self.assertEqual(r.pub.step(), 1)


class LocationEnvTests(unittest.TestCase):
    def test_blank_is_none(self):
        self.assertIsNone(sensor_location_from_env({}))

    def test_valid(self):
        self.assertEqual(sensor_location_from_env({"SDRPP_SENSOR_LAT": "47.9", "SDRPP_SENSOR_LON": "-122.3"}), (47.9, -122.3))

    def test_bad_values_name_the_variable(self):
        for env, name in (({"SDRPP_SENSOR_LAT": "47.9"}, "SDRPP_SENSOR_LON"),
                          ({"SDRPP_SENSOR_LAT": "x", "SDRPP_SENSOR_LON": "1"}, "SDRPP_SENSOR_LAT"),
                          ({"SDRPP_SENSOR_LAT": "91", "SDRPP_SENSOR_LON": "1"}, "SDRPP_SENSOR_LAT"),
                          ({"SDRPP_SENSOR_LAT": "1", "SDRPP_SENSOR_LON": "nan"}, "SDRPP_SENSOR_LON")):
            with self.assertRaisesRegex(ValueError, name):
                sensor_location_from_env(env)


class DotenvTests(unittest.TestCase):
    def test_reads_only_own_keys(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("SECRET_TOKEN=abc\nSDRPP_SENSOR_LAT=47.887\n# c\nSDRPP_SENSOR_LON='-122.284'\nOTHER=1\n")
        try:
            self.assertEqual(_dotenv_keys(f.name), {"SDRPP_SENSOR_LAT": "47.887", "SDRPP_SENSOR_LON": "-122.284"})
        finally:
            os.unlink(f.name)

    def test_missing_file_is_empty(self):
        self.assertEqual(_dotenv_keys("/nonexistent/.env"), {})


if __name__ == "__main__":
    unittest.main()
