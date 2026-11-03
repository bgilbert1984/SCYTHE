"""PreparedVisit reuses a visit's per-repeat spectra; results are bit-identical to the unprepared path."""
import unittest
import numpy as np
import rf_spur_retention_estimator as r

FS = 2048000.0


def visit(seed=3, n=4096, repeats=8, tone_hz=250000.0):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / FS
    return [rng.normal(size=n) + 1j * rng.normal(size=n) + 3 * np.exp(2j * np.pi * tone_hz * t)
            for _ in range(repeats)]


class PreparedVisitTests(unittest.TestCase):
    def test_bit_identical_over_many_features(self):
        w = visit(); p = r.PreparedVisit(w)
        for f in np.linspace(-FS / 2, FS / 2, 61):
            a = r.measure_persistence(tuning_id="t", windows=w, sample_rate_hz=FS, feature_baseband_hz=float(f))
            b = r.measure_persistence(tuning_id="t", windows=w, sample_rate_hz=FS, feature_baseband_hz=float(f), prepared=p)
            self.assertEqual(a.repeat_excess_db, b.repeat_excess_db)

    def test_spectra_are_computed_once(self):
        w = visit(); p = r.PreparedVisit(w); calls = []
        orig = r._power_db
        r._power_db = lambda x: (calls.append(1), orig(x))[1]
        try:
            for f in (0.0, 1e5, -1e5, 2e5):
                r.measure_persistence(tuning_id="t", windows=w, sample_rate_hz=FS, feature_baseband_hz=f, prepared=p)
        finally:
            r._power_db = orig
        self.assertEqual(len(calls), len(w))

    def test_lazy_no_work_without_a_measurement(self):
        r.PreparedVisit("not a visit")  # constructing never validates

    def test_refusals_match_the_unprepared_path(self):
        cases = [np.zeros(8), [], [np.zeros(8), np.zeros(9)], [np.array([1.0, np.nan])], 5]
        for bad in cases:
            with self.assertRaises(r.RetentionRefused) as e1:
                r.measure_persistence(tuning_id="t", windows=bad, sample_rate_hz=FS, feature_baseband_hz=0.0)
            with self.assertRaises(r.RetentionRefused) as e2:
                r.measure_persistence(tuning_id="t", windows=bad, sample_rate_hz=FS, feature_baseband_hz=0.0,
                                      prepared=r.PreparedVisit(bad))
            self.assertEqual(e1.exception.code, e2.exception.code)

    def test_argument_checks_still_run_per_call(self):
        w = visit(); p = r.PreparedVisit(w)
        with self.assertRaises(r.RetentionRefused) as e:
            r.measure_persistence(tuning_id="", windows=w, sample_rate_hz=FS, feature_baseband_hz=0.0, prepared=p)
        self.assertEqual(e.exception.code, r.RETENTION_TUNING_NOT_DECLARED)
        with self.assertRaises(r.RetentionRefused) as e:
            r.measure_persistence(tuning_id="t", windows=w, sample_rate_hz=FS, feature_baseband_hz=FS, prepared=p)
        self.assertEqual(e.exception.code, r.RETENTION_FEATURE_OUT_OF_SPAN)


if __name__ == "__main__":
    unittest.main()
