import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace as C

import rf_canary_gate as G

TABLE = Path(__file__).parent / "scripts" / "s521_canary_threshold" / "visits.json"


def _tier(offsets=(-255996.0, 255996.0), sessions=3, hashed=True):
    doc = {"revision": "tiers-1", "rule": {"min_sessions": sessions},
           "intrinsic": [{"offset_hz": o} for o in offsets]}
    if hashed:
        doc["content_sha256"] = hashlib.sha256(json.dumps(doc, indent=1, sort_keys=True).encode()).hexdigest()
    return doc


CLEAN = [bytes([128, 127]) * 100]


def _cands(neg=14.0, pos=14.0):
    out = []
    if neg is not None:
        out.append(C(baseband_hz=-256000.0 + 3, excess_db=neg))
    if pos is not None:
        out.append(C(baseband_hz=256000.0 - 5, excess_db=pos))
    out.append(C(baseband_hz=1234.0, excess_db=40.0))
    return out


class VisitGate(unittest.TestCase):
    OFFS = [-255996.0, 255996.0]

    def test_both_sides_above_floor_passes(self):
        r = G.visit_gate(_cands(), CLEAN, self.OFFS)
        self.assertTrue(r["passed"])
        self.assertEqual(r["reasons"], [])

    def test_one_side_missing_fails(self):
        for kw in ({"neg": None}, {"pos": None}):
            r = G.visit_gate(_cands(**kw), CLEAN, self.OFFS)
            self.assertEqual(r["reasons"], ["CANARY_NOT_DETECTED"])

    def test_min_excess_boundary(self):
        self.assertTrue(G.visit_gate(_cands(11.0, 20.0), CLEAN, self.OFFS)["passed"])
        r = G.visit_gate(_cands(10.99, 20.0), CLEAN, self.OFFS)
        self.assertEqual(r["reasons"], ["CANARY_BELOW_MIN_EXCESS"])

    def test_clipping_limit(self):
        ok = [bytes([128]) * 99999 + bytes([0])]          # 0.001% exactly
        bad = [bytes([128]) * 9999 + bytes([255])]        # 0.01%
        self.assertTrue(G.visit_gate(_cands(), ok, self.OFFS)["passed"])
        self.assertIn("CLIPPING_ABOVE_LIMIT", G.visit_gate(_cands(), bad, self.OFFS)["reasons"])

    def test_match_tolerance(self):
        far = [C(baseband_hz=-256000.0 + 200, excess_db=20.0), C(baseband_hz=256000.0, excess_db=20.0)]
        self.assertEqual(G.visit_gate(far, CLEAN, self.OFFS)["reasons"], ["CANARY_NOT_DETECTED"])

    def test_max_excess_among_matches_is_used(self):
        cs = _cands(5.0, 14.0) + [C(baseband_hz=-255990.0, excess_db=13.0)]
        self.assertTrue(G.visit_gate(cs, CLEAN, self.OFFS)["passed"])


class TuningGate(unittest.TestCase):
    P = {"passed": True}
    F = {"passed": False}

    def test_every_visit_must_pass(self):
        self.assertTrue(G.tuning_gate([self.P] * 3, 3)["scored"])
        r = G.tuning_gate([self.P, self.F, self.P], 3)
        self.assertFalse(r["scored"])
        self.assertEqual(r["visits_passed"], 2)

    def test_missing_visit_is_not_scored(self):
        self.assertEqual(G.tuning_gate([self.P] * 2, 3)["reasons"], ["VISIT_COUNT_MISMATCH"])


class Tier(unittest.TestCase):
    def test_valid_tier(self):
        self.assertEqual(G.tier_offsets(_tier()), [-255996.0, 255996.0])

    def test_unhashed_altered_thin_or_empty_tier_refused(self):
        with self.assertRaises(G.CanaryRefused) as e:
            G.tier_offsets(_tier(hashed=False))
        self.assertEqual(e.exception.code, "TIER_UNHASHED")
        t = _tier()
        t["intrinsic"][0]["offset_hz"] = 1.0
        with self.assertRaises(G.CanaryRefused) as e:
            G.tier_offsets(t)
        self.assertEqual(e.exception.code, "TIER_HASH_MISMATCH")
        with self.assertRaises(G.CanaryRefused) as e:
            G.tier_offsets(_tier(sessions=2))
        self.assertEqual(e.exception.code, "TIER_TOO_FEW_SESSIONS")
        with self.assertRaises(G.CanaryRefused) as e:
            G.tier_offsets(_tier(offsets=()))
        self.assertEqual(e.exception.code, "TIER_EMPTY")


class ProposalNumbers(unittest.TestCase):
    """The excess rule must reproduce the counts quoted in the owner-accepted proposal."""

    def setUp(self):
        self.R = json.loads(TABLE.read_text())

    @staticmethod
    def _ok(r):
        return (r["neg"] is not None and r["pos"] is not None
                and min(r["neg"], r["pos"]) >= G.CANARY_MIN_EXCESS_DB)

    def test_counts(self):
        R = self.R
        good = R["terminated_rerun1"] + R["quiet_520"]
        deg = R["hwy99_strong_bands"] + R["mukilteo_intermediate"]
        self.assertEqual((sum(map(self._ok, good)), len(good)), (224, 224))
        self.assertEqual(sum(map(self._ok, R["hwy99_gain29"])), 32)
        self.assertEqual((sum(map(self._ok, deg)), len(deg)), (24, 68))


if __name__ == "__main__":
    unittest.main()
