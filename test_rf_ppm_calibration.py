#!/usr/bin/env python3
"""XTR ppm calibration tooling: plan, estimator, fit, decision, capture gates, end to end.

Everything runs on synthetic bytes served through the sequencer's fake rtl_tcp socket. Nothing here
is a measurement of any receiver; it shows the tooling recovers a known injected error and refuses
what the procedure says to refuse.
"""
import copy
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rf_ppm_calibration as cal
import rf_ppm_calibration_capture as cap
import rf_spur_catalogue_sequencer as seq
from test_rf_spur_catalogue_sequencer import SETTLE_BYTES, _dongle_info, _encode, _tuner

def _load(path):
    with open(path) as handle:
        return json.load(handle)


FS = seq.SAMPLE_RATE_HZ
N = seq.WINDOW_BYTES // 2
FEATURE_OFFSET = 50e3


def base_plan(**over):
    plan = {
        "schema": "scythe.ppm_calibration_plan.draft",
        "receiver_serial": "71874552", "gain_db": 29.7, "sample_rate_hz": FS, "n_windows": 8,
        "sessions_min": 3,
        "carriers": [
            {"name": f"c{i}", "operator": "synthetic", "channel": f"ch{i}", "nominal_hz": f,
             "nominal_source": "synthetic", "feature_offset_hz": FEATURE_OFFSET,
             "search_hz": [-300e3, 300e3]}
            for i, f in enumerate((7.0e8, 9.0e8, 1.5e9, 1.9e9))],
        "estimator": {"name": "narrow_line", "version": 1},
        "thresholds": dict(cal.THRESHOLD_DEFAULTS, se_floor_hz=2.0),
        "reference_accuracy": {"ppm": 0.05, "source": "synthetic", "confirmed": True},
        "precision_ppm": 1.0, "catalogue_conditions": ["warm", "after_power_cycle"],
        "validation": {"seed": 3, "snr_db": [10.0, 20.0], "trials": 6,
                       "line_hz_range": [20e3, 120e3], "bias_limit_ppm": 0.05},
    }
    plan.update(over)
    return plan


def frozen(**over):
    return cal.freeze_plan(base_plan(**over))


class PlanTests(unittest.TestCase):
    def refuses(self, code, **over):
        with self.assertRaises(cal.CalibrationRefused) as ctx:
            cal.freeze_plan(base_plan(**over))
        self.assertEqual(ctx.exception.code, code)

    def test_incomplete_plan_is_refused(self):
        p = base_plan(); del p["validation"]
        with self.assertRaises(cal.CalibrationRefused):
            cal.freeze_plan(p)

    def test_thresholds_must_be_stated_not_defaulted(self):
        th = dict(cal.THRESHOLD_DEFAULTS); del th["consistency_k"]
        self.refuses("PLAN_INCOMPLETE", thresholds=th)

    def test_structure_refusals(self):
        base = base_plan()["carriers"]
        self.refuses("PLAN_TOO_FEW_CARRIERS", carriers=base[:2])
        dup = copy.deepcopy(base); dup[1]["name"] = "c0"
        self.refuses("PLAN_DUPLICATE_CARRIER", carriers=dup)
        flat = copy.deepcopy(base)
        for c in flat:
            c["nominal_hz"] = 9.0e8
        self.refuses("PLAN_NO_FREQUENCY_SPREAD", carriers=flat)
        unsourced = copy.deepcopy(base); unsourced[0]["nominal_source"] = ""
        self.refuses("PLAN_CARRIER_INCOMPLETE", carriers=unsourced)
        self.refuses("PLAN_TOO_FEW_SESSIONS", sessions_min=2)
        self.refuses("PLAN_WINDOWS", n_windows=4)

    def test_freeze_then_tamper(self):
        plan = frozen()
        cal.verify_frozen_plan(plan)
        tampered = copy.deepcopy(plan); tampered["gain_db"] = 20.7
        with self.assertRaises(cal.CalibrationRefused) as ctx:
            cal.verify_frozen_plan(tampered)
        self.assertEqual(ctx.exception.code, "PLAN_HASH_MISMATCH")

    def test_plan_is_bound_to_the_analysis_source(self):
        plan = frozen()
        other = copy.deepcopy(plan)
        other["analysis_source_sha256"] = "0" * 64
        body = {k: v for k, v in other.items() if k != "plan_sha256"}
        other["plan_sha256"] = cal.sha256_text(cal.canonical(body))
        with self.assertRaises(cal.CalibrationRefused) as ctx:
            cal.verify_frozen_plan(other)
        self.assertEqual(ctx.exception.code, "ANALYSIS_SOURCE_CHANGED")


class EstimatorTests(unittest.TestCase):
    def test_recovers_an_injected_offset_and_ignores_a_stronger_line_outside_the_search_band(self):
        rng = np.random.default_rng(1)
        n = 131072
        w = cal.synthetic_window(n, FS, 71234.567, 15.0, rng, fading=False)
        t = np.arange(n) / FS
        w = w + 3.0 * np.exp(2j * np.pi * 500e3 * t)            # stronger, but out of band
        r = cal.estimate_line(w, FS, (-300e3, 300e3))
        self.assertAlmostEqual(r["baseband_hz"], 71234.567, delta=0.2)

    def test_scatter_is_consistent_with_the_cramer_rao_bound_at_every_snr(self):
        """Hann loses about 1.5x to the bound; the point is that the error scales with SNR as the
        bound does (no floor from the interpolation) and is never better than the bound."""
        rng = np.random.default_rng(2)
        n = 65536
        for snr in (-10.0, 0.0, 10.0):
            errs, crbs = [], []
            for _ in range(80):
                true = rng.uniform(30e3, 90e3)
                r = cal.estimate_line(cal.synthetic_window(n, FS, true, snr, rng, False), FS, (0, 200e3))
                errs.append(r["baseband_hz"] - true); crbs.append(r["crb_hz"])
            ratio = float(np.std(errs)) / float(np.mean(crbs))
            self.assertGreater(ratio, 0.8, snr)
            self.assertLess(ratio, 2.5, snr)

    def test_empty_search_band_is_refused(self):
        with self.assertRaises(cal.CalibrationRefused):
            cal.estimate_line(np.zeros(4096, complex), FS, (1.0, 2.0))

    def test_validation_passes_and_catches_a_biased_estimator(self):
        plan = frozen()
        res = cal.validate_estimator(plan, n=131072)
        self.assertTrue(res["passes"], res["cells"])
        original = cal.estimate_line
        def biased(*a, **k):
            r = original(*a, **k); r["baseband_hz"] += 100.0; return r
        cal.estimate_line = biased
        try:
            self.assertFalse(cal.validate_estimator(plan, n=131072)["passes"])
        finally:
            cal.estimate_line = original


PATTERN = (1.0, -1.0, 1.0, -1.0)   # residuals of about one standard error, so chi-square is near 1


def carriers_for(b_ppm, a_hz=0.0, se_ppm=0.02, noise=None, freqs=(7.0e8, 9.0e8, 1.5e9, 1.9e9)):
    out = []
    for i, f in enumerate(freqs):
        se = se_ppm * 1e-6 * f
        n = PATTERN[i] * se if noise is None else noise[i]
        out.append({"name": f"c{i}", "f_nominal_hz": f, "offset_hz": a_hz + b_ppm * 1e-6 * f + n,
                    "se_hz": se})
    return out


TH = dict(cal.THRESHOLD_DEFAULTS)


class FitTests(unittest.TestCase):
    def test_recovers_slope_and_offset(self):
        fit = cal.fit_session(carriers_for(-37.0, a_hz=12.0), TH)
        self.assertTrue(fit["accepted"], fit["reasons"])
        self.assertAlmostEqual(fit["b_ppm"], -37.0, delta=0.03)
        self.assertAlmostEqual(fit["a_hz"], 12.0, delta=30.0)
        exact = cal.fit_session(carriers_for(-37.0, a_hz=12.0, noise=[0, 0, 0, 0]), TH)
        self.assertAlmostEqual(exact["b_ppm"], -37.0, places=6)    # the algebra, with no noise
        self.assertAlmostEqual(exact["a_hz"], 12.0, places=3)
        self.assertFalse(exact["accepted"])    # residuals far smaller than the stated errors are refused too

    def test_fewer_than_three_carriers_is_refused(self):
        self.assertFalse(cal.fit_session(carriers_for(5.0)[:2], TH)["accepted"])

    def test_a_large_fixed_offset_is_not_a_pure_ppm_error(self):
        fit = cal.fit_session(carriers_for(5.0, a_hz=2000.0), TH)
        self.assertFalse(fit["accepted"])
        self.assertTrue(any("pure-ppm" in r for r in fit["reasons"]))

    def test_a_carrier_with_the_wrong_nominal_frequency_fails_consistency(self):
        cs = carriers_for(5.0)
        cs[2]["offset_hz"] += 40 * 1e-6 * cs[2]["f_nominal_hz"]      # 40 ppm off: a mis-identified cell
        fit = cal.fit_session(cs, TH)
        self.assertFalse(fit["accepted"])

    def test_understated_errors_fail_chi_square(self):
        cs = carriers_for(5.0)
        for c, d in zip(cs, (1.0, -1.0, 1.5, -1.5)):
            c["offset_hz"] += d * 1e-6 * c["f_nominal_hz"] * 0.3     # scatter ~0.3 ppm
        self.assertFalse(cal.fit_session(cs, TH)["accepted"])

    def test_a_carrier_that_is_too_noisy_is_refused(self):
        cs = carriers_for(5.0, se_ppm=0.5)
        fit = cal.fit_session(cs, TH)
        self.assertFalse(fit["accepted"])
        self.assertTrue(any("standard error" in r for r in fit["reasons"]))


def session(sid, b, u=0.01, accepted=True, cond=("warm",)):
    fit = {"accepted": accepted, "b_ppm": b, "u_b_ppm": u, "reasons": []}
    return {"session_id": sid, "conditions": list(cond), "fit": fit}


class DecisionTests(unittest.TestCase):
    def plan(self, **over):
        p = base_plan(); p.update(over); return p

    def good(self):
        return [session("s1", -3.00, cond=("warm",)), session("s2", -3.05, cond=("after_power_cycle",)),
                session("s3", -2.98, cond=("warm",))]

    def test_a_proposal_follows_the_fixed_rule(self):
        d = cal.combine_sessions(self.good(), self.plan())
        self.assertTrue(d["amendment_permitted"], d["blockers"])
        m, U, dd = d["mean_ppm"], d["expanded_uncertainty_ppm"], d["drift_allowance_ppm"]
        self.assertEqual(d["reference_ppm_proposed"], math.ceil(abs(m) + U + dd))
        self.assertLess(d["reference_ppm_proposed"], 100)

    def test_an_unconfirmed_reference_accuracy_blocks_any_proposal(self):
        ra = {"ppm": 0.05, "source": "3GPP TS 36.104 (unchecked)", "confirmed": False}
        d = cal.combine_sessions(self.good(), self.plan(reference_accuracy=ra))
        self.assertFalse(d["amendment_permitted"])
        self.assertIsNone(d["reference_ppm_proposed"])
        self.assertTrue(any("not confirmed" in b for b in d["blockers"]))

    def test_each_blocker_keeps_the_bound_at_100(self):
        cases = {
            "failed session": [session("s1", -3.0), session("s2", -3.0, accepted=False), session("s3", -3.0)],
            "too few sessions": self.good()[:2],
            "disagree": [session("s1", -3.0), session("s2", 1.5), session("s3", -3.0, cond=("after_power_cycle",))],
            "uncertain": [session("s1", -3.0, u=6.0), session("s2", -3.1, u=6.0),
                          session("s3", -2.9, u=6.0, cond=("after_power_cycle",))],
            "unsampled": [session("s1", -3.0), session("s2", -3.0), session("s3", -3.0)],  # no power-cycle condition
        }
        for name, ses in cases.items():
            d = cal.combine_sessions(ses, self.plan())
            self.assertFalse(d["amendment_permitted"], name)
            self.assertEqual(d["reference_ppm_current"], 100, name)
            self.assertIsNone(d["reference_ppm_proposed"], name)
            self.assertTrue(d["blockers"], name)

    def test_the_proposal_is_never_below_the_precision_step(self):
        d = cal.combine_sessions([session("s1", 0.0), session("s2", 0.0), session("s3", 0.0)],
                                 self.plan(catalogue_conditions=["warm"], precision_ppm=2.0))
        self.assertTrue(d["amendment_permitted"], d["blockers"])
        self.assertGreaterEqual(d["reference_ppm_proposed"], 2.0)


JITTER_HZ = 0.6     # per-window frequency wander (phase noise, drift) so that scatter is a real quantity


def synth_visit_bytes(plan, name, e_ppm, rng):
    """Eight windows of a CW line as the receiver would deliver it: LO at nominal, reference off by e."""
    c = next(c for c in plan["carriers"] if c["name"] == name)
    true = c["nominal_hz"] + c["feature_offset_hz"]
    lo_actual = c["nominal_hz"] * (1.0 + e_ppm * 1e-6)
    baseband = true - lo_actual
    t = np.arange(N) / FS
    wins = []
    for _ in range(8):
        sig = 0.5 * np.exp(2j * np.pi * (baseband + rng.normal(0, JITTER_HZ)) * t + 1j * rng.uniform(0, 6.28))
        noise = (rng.standard_normal(N) + 1j * rng.standard_normal(N)) * 0.02
        wins.append((sig + noise).astype(np.complex64))
    return _encode(wins)


def make_tuner(plan, session_id, e_ppm, seed):
    rng = np.random.default_rng(seed)
    payload = bytearray(_dongle_info())
    for name in cap.visit_order(plan, session_id):
        payload += b"\x00" * SETTLE_BYTES + synth_visit_bytes(plan, name, e_ppm, rng)
    return _tuner(bytes(payload))


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="ppmcal-")
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.plan = frozen(catalogue_conditions=["warm", "after_power_cycle"],
                          thresholds=dict(cal.THRESHOLD_DEFAULTS, se_floor_hz=0.05))
        cls.dirs = []
        for sid, cond, e, seed in (("s1", ["warm"], -37.00, 11), ("s2", ["after_power_cycle"], -37.03, 12),
                                    ("s3", ["warm"], -36.97, 13)):
            out = os.path.join(cls.root, sid)
            with make_tuner(cls.plan, sid, e, seed=seed) as tuner:
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
            self.assertAlmostEqual(s["fit"]["b_ppm"], -e, delta=0.02)    # reference error is -b

    def test_the_decision_proposes_a_tighter_bound(self):
        self.assertTrue(self.decision["amendment_permitted"], self.decision["blockers"])
        self.assertLess(self.decision["reference_ppm_proposed"], 100)
        self.assertGreaterEqual(self.decision["reference_ppm_proposed"], 37)

    def test_the_session_record_keeps_the_owner_statement_and_the_plan_hash(self):
        rec = _load(os.path.join(self.dirs[0], "session.json"))
        self.assertIn("refitted", rec["owner_statement"])
        self.assertEqual(rec["plan_sha256"], self.plan["plan_sha256"])
        self.assertEqual(rec["temperature"], "not measured")
        self.assertEqual(sorted(rec["visit_order"]), sorted(c["name"] for c in self.plan["carriers"]))

    def test_visit_order_is_deterministic_and_session_specific(self):
        a = cap.visit_order(self.plan, "s1")
        self.assertEqual(a, cap.visit_order(self.plan, "s1"))
        orders = {tuple(cap.visit_order(self.plan, f"session-{i}")) for i in range(8)}
        self.assertGreater(len(orders), 1)

    def test_a_corrupt_spool_is_refused_at_analysis(self):
        copy_dir = os.path.join(self.root, "damaged")
        shutil.copytree(self.dirs[0], copy_dir)
        rec = _load(os.path.join(copy_dir, "session.json"))
        with open(os.path.join(copy_dir, rec["visits"][0]["spool"]), "r+b") as h:
            h.write(b"\xff\xff")
        with self.assertRaises(cal.CalibrationRefused) as ctx:
            cal.analyze_session(copy_dir, self.plan)
        self.assertEqual(ctx.exception.code, "SPOOL_CORRUPT")

    def test_a_session_from_another_plan_is_refused(self):
        other = frozen(gain_db=20.7, catalogue_conditions=["warm", "after_power_cycle"],
                       thresholds=dict(cal.THRESHOLD_DEFAULTS, se_floor_hz=0.05))
        with self.assertRaises(cal.CalibrationRefused) as ctx:
            cal.analyze_session(self.dirs[0], other)
        self.assertEqual(ctx.exception.code, "SESSION_PLAN_MISMATCH")

    def test_manifest_lists_every_file_with_its_hash(self):
        path = cal.write_manifest(self.dirs[1])
        man = _load(path)
        self.assertIn("session.json", man["files"])
        for rel, e in man["files"].items():
            with open(os.path.join(self.dirs[1], rel), "rb") as h:
                self.assertEqual(hashlib.sha256(h.read()).hexdigest(), e["sha256"])


class CaptureGateTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="ppmgate-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.plan = frozen()

    def tuner(self):
        return _tuner(_dongle_info())

    def refuses(self, code, plan=None, **kw):
        args = dict(session_id="s", conditions=["warm"], out_dir=os.path.join(self.root, "o"),
                    owner_statement="stated")
        args.update(kw)
        tuner = self.tuner()
        with tuner:
            with self.assertRaises((cap.CaptureRefused, cal.CalibrationRefused)) as ctx:
                cap.capture_session(plan or self.plan, tuner, **args)
        self.assertEqual(ctx.exception.code, code)
        self.assertEqual(tuner._fake.sent, [], "the receiver must not be commanded when a gate refuses")

    def test_no_owner_statement(self):
        self.refuses("OWNER_STATEMENT_MISSING", owner_statement="  ")

    def test_session_must_be_described(self):
        self.refuses("SESSION_UNDESCRIBED", conditions=[])

    def test_tampered_plan(self):
        bad = copy.deepcopy(self.plan); bad["gain_db"] = 0.0
        self.refuses("PLAN_HASH_MISMATCH", plan=bad)

    def test_non_empty_output_directory(self):
        os.makedirs(os.path.join(self.root, "o")); open(os.path.join(self.root, "o", "x"), "w").close()
        self.refuses("OUTPUT_DIR_NOT_EMPTY")

    def test_survey_ranks_a_channel_with_a_carrier_above_an_empty_one(self):
        rng = np.random.default_rng(9)
        t = np.arange(N) / FS
        def window(amp):
            sig = amp * np.exp(2j * np.pi * 20e3 * t)
            return _encode([((rng.standard_normal(N) + 1j * rng.standard_normal(N)) * 0.05 + sig).astype(np.complex64)])
        payload = (_dongle_info() + b"\x00" * SETTLE_BYTES + window(0.0)
                   + b"\x00" * SETTLE_BYTES + window(0.3))
        with _tuner(payload) as tuner:
            res = cap.survey([{"name": "empty", "nominal_hz": 8e8, "bandwidth_hz": 40e3},
                              {"name": "busy", "nominal_hz": 9e8, "bandwidth_hz": 100e3}], tuner, 29.7)
        self.assertGreater(res[1]["inband_over_floor_db"], res[0]["inband_over_floor_db"] + 10)


if __name__ == "__main__":
    unittest.main()
