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


class RunningCentreTests(unittest.TestCase):
    def test_wandering_station_beyond_first_anchor_radius_merges(self):
        r = Rig()
        # two anchors 110 kHz apart (the live 100.233 / 100.343 case), then hits between them
        r.store.add(1, 100_233_000.0); r.store.add(2, 100_343_000.0)
        r.pub.step()
        self.assertEqual(len({b["entity_id"] for _, b in r.posts}), 2)
        for t_ in range(3, 40):
            r.store.add(t_, 100_288_000.0)
        r.now[0] += 10; r.pub.step()
        last = {b["entity_id"]: b for _, b in r.posts}
        merged = [b for b in last.values() if "merged_into" in b["meta"]]
        live = [b for b in last.values() if "merged_into" not in b["meta"]]
        self.assertEqual(len(merged), 1); self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["meta"]["hit_count"], 2 + 37)
        self.assertIn("merged into", merged[0]["name"])

    def test_absorbed_entity_is_not_updated_again(self):
        r = Rig()
        r.store.add(1, 100_233_000.0); r.store.add(2, 100_343_000.0)
        for t_ in range(3, 10):
            r.store.add(t_, 100_288_000.0)
        r.pub.step(); r.posts.clear()
        r.store.add(20, 100_288_000.0); r.now[0] += 10; r.pub.step()
        self.assertTrue(all("merged_into" not in b["meta"] for _, b in r.posts))

    def test_distinct_stations_not_merged(self):
        r = Rig(); r.store.add(1, 99.9e6); r.store.add(2, 100.3e6); r.store.add(3, 99.9e6)
        r.pub.step()
        self.assertTrue(all("merged_into" not in b["meta"] for _, b in r.posts))


class HDSidebandTests(unittest.TestCase):
    # 512 bins x 4 kHz from 98.978 MHz; floor -58 dBFS
    F0, BW, N = 98_978_000.0, 4000.0, 512

    def _frame(self, seq, carrier_hz, lower_db=None, upper_db=None):
        bins = [-58.0] * self.N
        for i in range(self.N):
            f = self.F0 + (i + 0.5) * self.BW
            d = f - carrier_hz
            if abs(d) <= 60_000: bins[i] = -26.0
            elif lower_db is not None and -198_000 <= d <= -129_000: bins[i] = lower_db
            elif upper_db is not None and 129_000 <= d <= 198_000: bins[i] = upper_db
        return {"sequence": seq, "bins_dbfs": bins, "min_frequency_hz": self.F0, "sample_rate_hz": self.BW * self.N}

    def _rig(self, **kw):
        r = Rig()
        self.seq = [0]
        def src():
            self.seq[0] += 1
            return self._frame(self.seq[0], 99_900_000.0, **kw)
        r.pub = RFReconPublisher(r.store, r._post, location=(47.8, -122.3), clock=lambda: r.now[0], frame_source=src)
        r.store.add(1, 99_900_000.0)
        for _ in range(160): r.pub.step()
        r.now[0] += 10; r.pub.step()  # past the update throttle
        return r

    def _note(self, r):
        return next(b for _, b in reversed(r.posts) if "hd_radio_sidebands" in b["meta"])["meta"]["hd_radio_sidebands"]

    def test_both_sidebands_consistent_with_hd(self):
        n = self._note(self._rig(lower_db=-45.0, upper_db=-44.0))
        self.assertEqual(n["status"], "CONSISTENT_WITH_HYBRID_HD")
        self.assertEqual(n["evidence_class"], "DERIVED_INFERENCE")
        self.assertAlmostEqual(n["lower_dbc"], -19.0, delta=0.6)
        self.assertGreaterEqual(n["lower_over_floor_db"], 6)

    def test_no_sidebands_is_not_seen(self):
        self.assertEqual(self._note(self._rig())["status"], "NOT_SEEN")

    def test_one_sided(self):
        self.assertEqual(self._note(self._rig(lower_db=-45.0))["status"], "ONE_SIDEBAND_ONLY")

    def test_no_note_before_enough_frames(self):
        r = Rig(); r.store.add(1, 99.9e6)
        r.pub = RFReconPublisher(r.store, r._post, frame_source=lambda: self._frame(1, 99.9e6, -45.0, -45.0))
        r.pub.step()
        self.assertTrue(all("hd_radio_sidebands" not in b["meta"] for _, b in r.posts))

    def test_station_near_band_edge_is_out_of_span_not_guessed(self):
        r = Rig(); r.store.add(1, 99.05e6)
        s = [0]
        def src():
            s[0] += 1; return self._frame(s[0], 99_050_000.0, -45.0, -45.0)
        r.pub = RFReconPublisher(r.store, r._post, clock=lambda: r.now[0], frame_source=src)
        for _ in range(160): r.pub.step()
        r.now[0] += 10; r.pub.step()
        self.assertEqual(self._note(r)["status"], "OUT_OF_SPAN")


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
