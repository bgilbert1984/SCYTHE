#!/usr/bin/env python3
"""LTE carrier estimator (Tier A) and its place in the ppm calibration plan.

Everything is synthetic: a 5 MHz normal-CP LTE downlink generated here, seen through a 2.048 MS/s front
end. The generator and the estimator share one PSS definition, which has NOT been confirmed against the
normative 3GPP text, so these tests show the signal processing recovers a known offset; they cannot show
that the definition matches a real eNodeB.
"""
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rf_lte_carrier_estimator as lte
import rf_ppm_calibration as cal
import rf_ppm_calibration_capture as cap
import rf_spur_catalogue_sequencer as seq
from test_rf_ppm_calibration import FS, N, base_plan
from test_rf_spur_catalogue_sequencer import SETTLE_BYTES, _dongle_info, _encode, _tuner

BAND = (-200e3, 200e3)
# Observed in development: up to ~15 Hz plain, ~30 Hz with multipath and an 80 ppm sample-clock error.
ERROR_BUDGET_HZ = 35.0


def clean_for(cfo, seed, **kw):
    return lte.synthetic_lte_clean(N, cfo, np.random.default_rng(seed), **kw)


class PssDefinitionTests(unittest.TestCase):
    def test_shape(self):
        for u in lte.PSS_ROOTS:
            p = lte.pss_sequence(u)
            self.assertEqual(len(p), 62)
            self.assertNotIn(0, p)                              # DC is not used
            self.assertEqual(sorted(p), list(range(-31, 0)) + list(range(1, 32)))
            self.assertTrue(np.allclose([abs(v) for v in p.values()], 1.0))

    def test_roots_29_and_34_are_conjugates_and_25_is_not(self):
        a, b, c = (lte.pss_sequence(u) for u in (29, 34, 25))
        self.assertTrue(all(abs(a[k] - np.conj(b[k])) < 1e-9 for k in a))
        self.assertFalse(all(abs(c[k] - np.conj(a[k])) < 1e-9 for k in a))

    def test_slot_structure_adds_up(self):
        self.assertEqual(sum(lte.SYM_LEN), lte.SLOT)
        self.assertEqual(lte.BLOCK, 10 * lte.SLOT)


class EstimatorTests(unittest.TestCase):
    def test_recovers_offsets_across_the_range_including_integer_subcarrier_shifts(self):
        rng = np.random.default_rng(11)
        for cfo in (-150e3, -60.4e3, -7.4e3, 0.0, 12.3e3, 100e3, 160e3):
            w = lte.add_noise(clean_for(cfo, 3), 10.0, rng)
            r = lte.estimate_lte_carrier(w, FS, BAND)
            self.assertLess(abs(r["baseband_hz"] - cfo), ERROR_BUDGET_HZ, cfo)
            self.assertEqual(r["integer_subcarrier_shift"], round(cfo / 15e3), cfo)
            self.assertGreater(r["pss_vote_share"], 0.6)    # the plan gate is 0.5

    def test_each_of_the_three_roots_is_identified(self):
        rng = np.random.default_rng(12)
        for root in lte.PSS_ROOTS:
            w = lte.add_noise(clean_for(41e3, root, pss_root=root), 10.0, rng)
            self.assertEqual(lte.estimate_lte_carrier(w, FS, BAND)["pss_root"], root)

    def test_sign_convention_is_the_signal_above_the_lo_being_positive(self):
        rng = np.random.default_rng(13)
        up = lte.estimate_lte_carrier(lte.add_noise(clean_for(+30e3, 5), 10.0, rng), FS, BAND)
        down = lte.estimate_lte_carrier(lte.add_noise(clean_for(-30e3, 5), 10.0, rng), FS, BAND)
        self.assertGreater(up["baseband_hz"], 0)
        self.assertLess(down["baseband_hz"], 0)

    def test_multipath_and_receiver_clock_error_stay_inside_the_budget(self):
        rng = np.random.default_rng(14)
        for ppm in (-80.0, 80.0):
            w = lte.add_noise(clean_for(55e3, 6, multipath=True, sample_clock_ppm=ppm), 10.0, rng)
            self.assertLess(abs(lte.estimate_lte_carrier(w, FS, BAND)["baseband_hz"] - 55e3), ERROR_BUDGET_HZ)

    def test_reported_error_is_the_right_order(self):
        rng = np.random.default_rng(15)
        errs, stds = [], []
        for seed in range(8):
            r = lte.estimate_lte_carrier(lte.add_noise(clean_for(20e3, 7), 10.0, rng), FS, BAND)
            errs.append(r["baseband_hz"] - 20e3); stds.append(r["crb_hz"])
        self.assertLess(np.std(errs), 4 * np.mean(stds))
        self.assertLess(np.mean(stds), 20.0)

    def test_noise_alone_is_refused(self):
        rng = np.random.default_rng(16)
        w = (rng.standard_normal(N) + 1j * rng.standard_normal(N)) / np.sqrt(2)
        with self.assertRaises(lte.LteNotDetected):
            lte.estimate_lte_carrier(w, FS, BAND)

    def test_a_steady_tone_has_cp_structure_but_is_refused_at_the_pss_stage(self):
        t = np.arange(N) / FS
        rng = np.random.default_rng(17)
        w = np.exp(2j * np.pi * 33e3 * t) + 0.05 * (rng.standard_normal(N) + 1j * rng.standard_normal(N))
        with self.assertRaises(lte.LteNotDetected) as ctx:
            lte.estimate_lte_carrier(w, FS, BAND)
        self.assertEqual(ctx.exception.code, "NO_PSS")

    def test_an_offset_outside_the_allowed_range_is_refused(self):
        rng = np.random.default_rng(18)
        w = lte.add_noise(clean_for(150e3, 8), 10.0, rng)
        with self.assertRaises(lte.LteNotDetected) as ctx:
            lte.estimate_lte_carrier(w, FS, (-50e3, 50e3))
        self.assertEqual(ctx.exception.code, "OUT_OF_RANGE")

    def test_unsupported_window_is_refused(self):
        with self.assertRaises(lte.LteNotDetected):
            lte.estimate_lte_carrier(np.zeros(1000, complex), FS, BAND)
        with self.assertRaises(lte.LteNotDetected):
            lte.estimate_lte_carrier(np.zeros(N, complex), 1.0e6, BAND)

    def test_a_weak_signal_is_refused_not_guessed(self):
        rng = np.random.default_rng(19)
        w = lte.add_noise(clean_for(20e3, 9), -12.0, rng)
        try:
            r = lte.estimate_lte_carrier(w, FS, BAND)
        except lte.LteNotDetected:
            return
        self.assertLess(abs(r["baseband_hz"] - 20e3), ERROR_BUDGET_HZ)   # if it answers, it is right


def lte_plan(confirmed=False, **over):
    p = base_plan()
    for c in p["carriers"]:
        c["feature_offset_hz"] = 0.0
        c["search_hz"] = [-250e3, 250e3]
    p["estimator"] = {"name": "lte_cp_pss", "version": 1, "min_cp_coeff": 0.05,
                      "min_pss_vote_share": 0.5, "pss_definition_confirmed": confirmed}
    p["thresholds"] = dict(cal.THRESHOLD_DEFAULTS, se_floor_hz=5.0)
    p["validation"] = {"seed": 4, "snr_db": [10.0], "trials": 5, "line_hz_range": [0, 0],
                       "bias_limit_ppm": 0.05}
    p.update(over)
    return p


class PlanIntegrationTests(unittest.TestCase):
    def refuses(self, code, plan):
        with self.assertRaises(cal.CalibrationRefused) as ctx:
            cal.freeze_plan(plan)
        self.assertEqual(ctx.exception.code, code)

    def test_plan_rules(self):
        cal.freeze_plan(lte_plan())
        self.refuses("PLAN_ESTIMATOR_UNKNOWN", lte_plan(estimator={"name": "magic"}))
        bad = lte_plan(); del bad["estimator"]["min_pss_vote_share"]
        self.refuses("PLAN_INCOMPLETE", bad)
        bad = lte_plan(); del bad["estimator"]["pss_definition_confirmed"]
        self.refuses("PLAN_INCOMPLETE", bad)
        bad = lte_plan(); bad["carriers"][0]["feature_offset_hz"] = 50e3
        self.refuses("PLAN_CARRIER_INCOMPLETE", bad)

    def test_the_plan_is_bound_to_the_estimator_source(self):
        plan = cal.freeze_plan(lte_plan())
        self.assertEqual(plan["estimator_source_sha256"], cal.file_sha256(cal.LTE_SOURCE_PATH))
        other = copy.deepcopy(plan)
        other["estimator_source_sha256"] = "0" * 64
        body = {k: v for k, v in other.items() if k != "plan_sha256"}
        other["plan_sha256"] = cal.sha256_text(cal.canonical(body))
        with self.assertRaises(cal.CalibrationRefused) as ctx:
            cal.verify_frozen_plan(other)
        self.assertEqual(ctx.exception.code, "ANALYSIS_SOURCE_CHANGED")

    def test_an_unconfirmed_pss_definition_blocks_any_proposal(self):
        def sess(sid, b, cond):
            return {"session_id": sid, "conditions": cond,
                    "fit": {"accepted": True, "b_ppm": b, "u_b_ppm": 0.01, "reasons": []}}
        ses = [sess("s1", 3.00, ["warm"]), sess("s2", 3.05, ["after_power_cycle"]), sess("s3", 2.98, ["warm"])]
        blocked = cal.combine_sessions(ses, lte_plan(confirmed=False))
        self.assertFalse(blocked["amendment_permitted"])
        self.assertTrue(any("PSS definition" in b for b in blocked["blockers"]))
        self.assertTrue(cal.combine_sessions(ses, lte_plan(confirmed=True))["amendment_permitted"])

    def test_validation_passes_and_catches_a_biased_estimator(self):
        plan = cal.freeze_plan(lte_plan())
        res = cal.validate_estimator(plan)
        self.assertTrue(res["passes"], res["cells"])
        original = lte.estimate_lte_carrier
        def biased(*a, **k):
            r = original(*a, **k); r["baseband_hz"] += 100.0; return r
        lte.estimate_lte_carrier = biased
        try:
            self.assertFalse(cal.validate_estimator(plan)["passes"])
        finally:
            lte.estimate_lte_carrier = original


def lte_visit_bytes(plan, name, e_ppm, enb_ppm, seed):
    """Eight windows of one cell as the receiver delivers it: LO at the channel, reference off by e."""
    c = next(c for c in plan["carriers"] if c["name"] == name)
    rng = np.random.default_rng(seed)
    cfo = (-e_ppm + enb_ppm) * 1e-6 * c["nominal_hz"]
    clean = lte.synthetic_lte_clean(N, cfo, rng, multipath=True)
    wins = []
    for _ in range(8):
        w = np.roll(clean, int(rng.integers(0, N)))
        wins.append((0.15 * lte.add_noise(w, 10.0, rng)).astype(np.complex64))
    return _encode(wins)


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="ltecal-")
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.plan = cal.freeze_plan(lte_plan(confirmed=True, catalogue_conditions=["warm", "after_power_cycle"]))
        enb = {"c0": 0.004, "c1": -0.006, "c2": 0.003, "c3": -0.002}      # each cell's own oscillator, ppm
        cls.dirs = []
        for sid, cond, e in (("s1", ["warm"], -37.00), ("s2", ["after_power_cycle"], -37.03),
                             ("s3", ["warm"], -36.97)):
            payload = bytearray(_dongle_info())
            for i, name in enumerate(cap.visit_order(cls.plan, sid)):
                payload += b"\x00" * SETTLE_BYTES + lte_visit_bytes(cls.plan, name, e, enb[name], 100 * len(sid) + i + ord(sid[-1]))
            out = os.path.join(cls.root, sid)
            with _tuner(bytes(payload)) as tuner:
                cap.capture_session(cls.plan, tuner, sid, cond, out,
                                    "the termination may be removed for the calibration and will be refitted")
            cls.dirs.append(out)
        cls.sessions = [cal.analyze_session(d, cls.plan) for d in cls.dirs]
        cls.decision = cal.combine_sessions(
            [{"session_id": s["session_id"], "conditions": s["conditions"], "fit": s["fit"]}
             for s in cls.sessions], cls.plan)

    def test_the_injected_reference_error_is_recovered(self):
        for s, e in zip(self.sessions, (-37.00, -37.03, -36.97)):
            self.assertTrue(s["fit"]["accepted"], s["fit"]["reasons"])
            self.assertAlmostEqual(s["fit"]["b_ppm"], -e, delta=0.06)

    def test_per_window_records_carry_the_detection_evidence(self):
        w = self.sessions[0]["per_window"][0]["windows"][0]
        for k in ("pss_root", "integer_subcarrier_shift", "pss_vote_share", "cp_coefficient"):
            self.assertIn(k, w)

    def test_the_decision_is_a_proposal_only_when_every_gate_passes(self):
        self.assertTrue(self.decision["amendment_permitted"], self.decision["blockers"])
        self.assertLess(self.decision["reference_ppm_proposed"], 100)

    def test_a_visit_with_no_cell_is_refused_at_analysis_not_averaged_in(self):
        damaged = os.path.join(self.root, "nocell")
        shutil.copytree(self.dirs[0], damaged)
        with open(os.path.join(damaged, "session.json")) as handle:
            rec = json.load(handle)
        rng = np.random.default_rng(1)
        noise = _encode([((rng.standard_normal(N) + 1j * rng.standard_normal(N)) * 0.1).astype(np.complex64)
                         for _ in range(8)])
        path = os.path.join(damaged, rec["visits"][0]["spool"])
        os.chmod(path, 0o644)
        with open(path, "wb") as handle:
            handle.write(noise)
        rec["visits"][0]["sha256"] = hashlib.sha256(noise).hexdigest()
        with open(os.path.join(damaged, "session.json"), "w") as handle:
            json.dump(rec, handle)
        with self.assertRaises(cal.CalibrationRefused) as ctx:
            cal.analyze_session(damaged, self.plan)
        self.assertEqual(ctx.exception.code, "CARRIER_NOT_DETECTED")


if __name__ == "__main__":
    unittest.main()
