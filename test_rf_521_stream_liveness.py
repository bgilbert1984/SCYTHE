"""The stream-liveness guard refuses a replayed/constant IQ stream and passes terminated noise."""
import unittest
import numpy as np
import rf_spur_catalogue_sequencer as seq

W = 1 << 20


def noise(seed, n=W):
    """~1 LSB terminated-input noise, like the real spools (8-9 distinct values)."""
    r = np.random.default_rng(seed)
    return r.choice(np.arange(124, 132), size=n, p=[.01, .03, .12, .35, .33, .1, .05, .01]).astype(np.uint8).tobytes()


def replayed(seed, period=3760, n=W):
    block = np.frombuffer(noise(seed, period), dtype=np.uint8)
    return np.resize(block, n).tobytes()          # exactly periodic, like the wedged XTR


class StubTuner:
    def __init__(self, windows): self._w = list(windows); self.lo = None
    def set_lo_hz(self, lo): self.lo = lo
    def discard(self, _s): pass
    def read_window_raw(self): return self._w.pop(0)


class Liveness(unittest.TestCase):
    def test_noise_is_live(self):
        r = seq.stream_liveness(noise(1))
        self.assertTrue(r["live"]); self.assertLess(r["match_fraction"], 0.6)

    def test_exactly_periodic_stream_is_not_live_and_the_lag_is_found(self):
        r = seq.stream_liveness(replayed(2))
        self.assertFalse(r["live"]); self.assertEqual(r["best_lag_bytes"] % 3760, 0)
        self.assertGreaterEqual(r["match_fraction"], 0.99); self.assertIn("repeats exactly", r["reason"])

    def test_other_periods_are_caught_too(self):
        for period in (64, 1000, 4097, 16384):
            self.assertFalse(seq.stream_liveness(replayed(3, period))["live"], period)

    def test_constant_stream_is_not_live(self):
        r = seq.stream_liveness(bytes([127]) * W)
        self.assertFalse(r["live"]); self.assertEqual(r["reason"], "constant stream")

    def test_a_noisy_tone_is_still_live(self):
        t = np.arange(W // 2); tone = 3.0 * np.cos(2 * np.pi * 0.0123 * t)
        iq = np.empty(W, dtype=np.float64); iq[0::2] = tone; iq[1::2] = tone * 0
        raw = np.clip(np.round(127.5 + iq + np.random.default_rng(4).normal(0, .8, W)), 0, 255).astype(np.uint8).tobytes()
        self.assertTrue(seq.stream_liveness(raw)["live"])


class VisitGuard(unittest.TestCase):
    def test_live_visit_is_acquired(self):
        wins = [noise(10 + i) for i in range(seq.WINDOWS_PER_VISIT)]
        v = seq.acquire_visit(StubTuner(wins), 0, "t", 0, 4.0e8, 0.0)
        self.assertEqual(len(v.windows), seq.WINDOWS_PER_VISIT)

    def test_stuck_stream_aborts_the_run_with_stream_not_live(self):
        wins = [replayed(20 + i) for i in range(seq.WINDOWS_PER_VISIT)]
        with self.assertRaises(seq.SequencerRefused) as cm:
            seq.acquire_visit(StubTuner(wins), 5, "t", 0, 4.0e8, 0.0)
        self.assertEqual(cm.exception.code, "STREAM_NOT_LIVE"); self.assertIn("visit 5", str(cm.exception))

    def test_identical_windows_abort_the_run(self):
        w = noise(30)
        with self.assertRaises(seq.SequencerRefused) as cm:
            seq.acquire_visit(StubTuner([w] * seq.WINDOWS_PER_VISIT), 1, "t", 0, 4.0e8, 0.0)
        self.assertEqual(cm.exception.code, "STREAM_NOT_LIVE"); self.assertIn("byte-identical", str(cm.exception))

    def test_a_refused_stream_is_not_retried_as_a_short_read(self):
        wins = [replayed(40 + i) for i in range(seq.WINDOWS_PER_VISIT * 3)]
        t = StubTuner(wins)
        with self.assertRaises(seq.SequencerRefused): seq.acquire_visit(t, 0, "t", 0, 4.0e8, 0.0)
        self.assertEqual(len(t._w), seq.WINDOWS_PER_VISIT * 2)   # only the first attempt's 8 windows were read


if __name__ == "__main__":
    unittest.main()
