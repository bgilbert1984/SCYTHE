"""The line detector finds a narrow tone at its offset, ignores the DC guard and the canary lines, and does not call broadband noise a line."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import persistence_pass as p  # noqa: E402


def window(tones, seconds=0.5, noise=2.0, seed=1):
    rng = np.random.default_rng(seed)
    n = int(p.FS * seconds)
    t = np.arange(n) / p.FS
    z = noise * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    for off, amp in tones:
        z = z + amp * np.exp(2j * np.pi * off * t)
    iq = np.empty(2 * n, dtype=np.uint8)
    iq[0::2] = np.clip(np.round(127.5 + z.real), 0, 255)
    iq[1::2] = np.clip(np.round(127.5 + z.imag), 0, 255)
    return iq.tobytes()


class DetectLines(unittest.TestCase):
    def test_a_tone_is_found_at_its_offset(self):
        lines = p.detect_lines(window([(300_000.0, 12.0)]))
        self.assertTrue(any(abs(off - 300_000.0) < 1000.0 and snr > 15.0 for off, snr in lines), lines)

    def test_noise_alone_gives_no_lines(self):
        self.assertEqual(p.detect_lines(window([])), [])

    def test_the_dc_guard_and_the_canary_lines_are_not_reported(self):
        lines = p.detect_lines(window([(2_000.0, 15.0), (p.CANARY_HZ, 15.0), (-p.CANARY_HZ, 15.0)]))
        self.assertEqual(lines, [])

    def test_a_wide_signal_is_not_a_narrow_line(self):
        rng = np.random.default_rng(3)
        n = int(p.FS * 0.5)
        t = np.arange(n) / p.FS
        wide = np.exp(2j * np.pi * (200_000.0 * t + 30_000.0 * np.cumsum(rng.standard_normal(n)) / p.FS * 40))
        z = 2.0 * (rng.standard_normal(n) + 1j * rng.standard_normal(n)) + 10.0 * wide
        iq = np.empty(2 * n, dtype=np.uint8)
        iq[0::2] = np.clip(np.round(127.5 + z.real), 0, 255)
        iq[1::2] = np.clip(np.round(127.5 + z.imag), 0, 255)
        self.assertEqual([x for x in p.detect_lines(iq.tobytes()) if abs(x[0] - 200_000.0) < 100_000.0], [])


class Aggregation(unittest.TestCase):
    def doc(self, centres, offset, clipped=()):
        visits = [{"error": None, "centre_hz": c, "retune_delta_hz": 0.0, "position": i,
                   "clipping_pct": 0.5 if i in clipped else 0.0, "lines": [[offset + (450_000_000.0 - c) * 0, 20.0]]}
                  for i, c in enumerate(centres)]
        return {"visits": visits}

    def test_a_line_at_a_fixed_offset_is_not_rf_fixed(self):
        d = self.doc([445_000_000.0, 445_000_000.0, 445_000_000.0], 300_000.0)
        lines = p.lines_of(p.aggregate_run(d))
        self.assertTrue(lines and not lines[0]["rf_fixed"])

    def test_a_line_seen_from_centres_150_khz_apart_is_rf_fixed(self):
        centres = [445_000_000.0, 445_150_000.0, 445_300_000.0]
        visits = [{"error": None, "centre_hz": c, "retune_delta_hz": 0.0, "position": i, "clipping_pct": 0.0,
                   "lines": [[445_400_000.0 - c, 18.0]]} for i, c in enumerate(centres)]
        lines = p.lines_of(p.aggregate_run({"visits": visits}))
        self.assertTrue(lines and lines[0]["rf_fixed"] and lines[0]["persistence"] == 1.0, lines)

    def test_clipped_visits_are_excluded(self):
        centres = [445_000_000.0, 445_150_000.0, 445_300_000.0]
        visits = [{"error": None, "centre_hz": c, "retune_delta_hz": 0.0, "position": i, "clipping_pct": 0.5,
                   "lines": [[445_400_000.0 - c, 18.0]]} for i, c in enumerate(centres)]
        self.assertEqual(p.lines_of(p.aggregate_run({"visits": visits})), [])


if __name__ == "__main__":
    unittest.main()
