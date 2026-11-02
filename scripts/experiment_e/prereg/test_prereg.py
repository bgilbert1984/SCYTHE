#!/usr/bin/env python3
"""Tests for the preregistered evaluator, blinding tools and simulator. Run: python3 -I test_prereg.py (needs numpy)."""
import os, sys, json, copy, tempfile, unittest, hashlib
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import prereg as pr, simulate as sm, blind

TMP = tempfile.mkdtemp(prefix="prereg_test_")
PATH = os.path.join(HERE, "prereg_v1.json")
CFG, H, _ = pr.load_prereg(PATH, None, True)


def run(scn, s2=8.0, seed=1, ctl="clean", trials=True, **kw):
    rng = np.random.default_rng(seed)
    sw = sm.make_sweeps(rng, scn, s2=s2, **{k: v for k, v in kw.items() if k in ("drift_db", "p_hw_bad", "schedule", "leak_db")})
    ct = sm.make_controls(rng, ctl, s2=max(s2, 1e-9), **{k: v for k, v in kw.items() if k in ("n", "k")})
    est, tr = sm.make_trials(rng, CFG, 20, s2, scn) if trials else (None, None)
    return pr.evaluate(sw, ct, est, tr, CFG, H, False, "simulated", schedule_ok=kw.get("schedule_ok", True),
                       blinding=kw.get("blinding", dict(mode="simulated", verified=True)))


class Freeze(unittest.TestCase):
    def test_hash_and_refusal(self):
        fz = os.path.join(TMP, "freeze.json"); json.dump({"prereg_sha256": H}, open(fz, "w"))
        cfg, h, frozen = pr.load_prereg(PATH, fz); self.assertTrue(frozen)
        bad = copy.deepcopy(CFG); bad["tests"]["selectivity_db"] = 3.0
        pb = os.path.join(TMP, "edited.json"); json.dump(bad, open(pb, "w"))
        with self.assertRaises(SystemExit): pr.load_prereg(pb, fz)                      # edited after freeze
        with self.assertRaises(SystemExit): pr.load_prereg(PATH, os.path.join(TMP, "none.json"))   # not frozen, no override
        self.assertEqual(h, pr.sha256_text(pr.canonical(json.load(open(PATH)))))

    def test_frozen_result_is_stamped(self):
        R = run("H0a", 0.0); self.assertIn("WARNING", R); self.assertFalse(R["frozen"])

    def test_holm(self):
        self.assertEqual(pr.holm({"a": 0.002, "b": 0.004, "c": 1.0}, 0.01), {"a": True, "b": True, "c": False})
        self.assertEqual(pr.holm({"a": 0.004, "b": 0.0049, "c": 1.0}, 0.01), {"a": False, "b": False, "c": False})   # 0.004 > 0.01/3
        self.assertEqual(pr.holm({"a": 0.0001, "b": 0.02, "c": 0.03}, 0.01), {"a": True, "b": False, "c": False})


class Verdicts(unittest.TestCase):
    def test_h1_rung5(self):
        R = run("H1", 8.0); self.assertEqual(R["verdict"], "H1_SUPPORTED_RUNG5", R.get("tests"))
        self.assertTrue(R["tests"]["C1"]["passed"] and R["tests"]["C2"]["passed"] and R["tests"]["C5"]["passed"])

    def test_h1_rung4_without_stage4(self):
        R = run("H1", 8.0, trials=False); self.assertEqual(R["verdict"], "H1_SUPPORTED_RUNG4")

    def test_h0a(self): self.assertEqual(run("H0a", 0.0)["verdict"], "H0a_NO_RESPONSE")

    def test_h0b_flat_response(self): self.assertEqual(run("H0b", 8.0)["verdict"], "H0b_NOT_SELECTIVE")

    def test_h0c_fixed_chain_response_does_not_pass(self):
        R = run("H0c", 8.0); self.assertNotIn(R["verdict"], ("H1_SUPPORTED_RUNG4", "H1_SUPPORTED_RUNG5"), R["verdict"])

    def test_underpowered_selective_channel_is_not_called_non_selective(self):
        # a genuine 25 dB-selective channel at s2=1 (far too weak): must never be reported as H0b
        bad = [run("H1", 1.0, seed=300 + i, trials=False, n=12, k=400)["verdict"] for i in range(30)]
        self.assertNotIn("H0b_NOT_SELECTIVE", bad, sorted(set(bad)))
        self.assertTrue(all(v in ("INCONCLUSIVE", "H0a_NO_RESPONSE", "INCONCLUSIVE_CONTROLS") for v in bad), sorted(set(bad)))

    def test_flat_response_with_modest_power_is_h0b_affirmatively(self):
        v = [run("H0b", 2.0, seed=400 + i, trials=False, n=12, k=400)["verdict"] for i in range(20)]
        self.assertGreaterEqual(v.count("H0b_NOT_SELECTIVE"), 16, sorted(set(v)))

    def test_reports_sensitivity_and_detection_limit(self):
        R = run("H1", 8.0, trials=False); d = R["per_fc"][7500000.0]["detection"]
        self.assertIn("detection_limit_power", d); self.assertFalse(d["below_sensitivity_requirement"]); self.assertGreater(d["s2_hat"], 5)
        R2 = run("H1", 2.0, trials=False, n=12, k=400); self.assertTrue(R2["per_fc"][7500000.0]["detection"]["below_sensitivity_requirement"])

    def test_leaky_controls_fail(self): self.assertEqual(run("H1", 8.0, ctl="leaky")["verdict"], "CONTROLS_FAIL")

    def test_underpowered_controls_are_not_a_pass(self):
        R = run("H1", 8.0, n=4, k=1); self.assertEqual(R["verdict"], "INCONCLUSIVE_CONTROLS", R["gates"]["controls"])

    def test_too_many_exclusions(self): self.assertEqual(run("H1", 8.0, p_hw_bad=0.30)["verdict"], "INCONCLUSIVE_INCOMPLETE")

    def test_missing_fc_incomplete(self):
        rng = np.random.default_rng(2); sw = sm.make_sweeps(rng, "H1", s2=8.0)
        m = sw["fc_hz"] != 12_000_000.0; sw = {k: v[m] for k, v in sw.items()}
        R = pr.evaluate(sw, None, None, None, CFG, H, False, "simulated"); self.assertEqual(R["verdict"], "INCONCLUSIVE_INCOMPLETE")

    def test_protocol_deviation(self): self.assertEqual(run("H1", 8.0, schedule_ok=False)["verdict"], "INCONCLUSIVE_PROTOCOL")

    def test_drift_invalidates_dwells(self):
        rng = np.random.default_rng(3); sw = sm.make_sweeps(rng, "H1", s2=8.0, drift_db=6.0)   # 6 dB baseline drift
        R = pr.evaluate(sw, None, None, None, CFG, H, False, "simulated")
        self.assertEqual(R["verdict"], "INCONCLUSIVE_INCOMPLETE"); self.assertGreater(R["per_fc"][7500000.0]["exclusions"]["n_drift_excluded"], 0)

    def test_deterministic(self):
        a, b = run("H1", 4.0, seed=5), run("H1", 4.0, seed=5)
        self.assertEqual(json.dumps(a, default=str, sort_keys=True), json.dumps(b, default=str, sort_keys=True))


class Stage4(unittest.TestCase):
    def test_perfect_and_random_estimates(self):
        rng = np.random.default_rng(1); truth = {f"t{i:02d}": float(rng.integers(200, 1201) * 10000) for i in range(20)}
        good = pr.test_c5(dict(truth), truth, CFG, np.random.default_rng(1)); self.assertTrue(good["floors_ok"]); self.assertLess(good["p"], 1e-3)
        shuffled = dict(zip(truth, rng.permutation(list(truth.values()))))
        bad = pr.test_c5(shuffled, truth, CFG, np.random.default_rng(1)); self.assertFalse(bad["floors_ok"])

    def test_too_few_trials_cannot_pass(self):
        t = {f"t{i}": 5e6 + 1e5 * i for i in range(10)}; r = pr.test_c5(dict(t), t, CFG, np.random.default_rng(1)); self.assertFalse(r["floors_ok"])


class Blinding(unittest.TestCase):
    def test_schedule_deterministic_and_checked(self):
        a = blind.make_schedule("freeze-commit-abc", [3e6, 7.5e6, 12e6], [200000, 14000000, 100000]); b = blind.make_schedule("freeze-commit-abc", [3e6, 7.5e6, 12e6], [200000, 14000000, 100000])
        c = blind.make_schedule("another", [3e6, 7.5e6, 12e6], [200000, 14000000, 100000])
        self.assertEqual(a["schedule_sha256"], b["schedule_sha256"]); self.assertNotEqual(a["schedule_sha256"], c["schedule_sha256"])
        sw = sm.make_sweeps(np.random.default_rng(1), "H1", s2=4.0, schedule=a); self.assertTrue(blind.schedule_matches(sw, a))
        sw2 = {k: v.copy() for k, v in sw.items()}; sw2["f_hz"][5], sw2["f_hz"][6] = sw2["f_hz"][6], sw2["f_hz"][5]
        self.assertFalse(blind.schedule_matches(sw2, a))
        tam = copy.deepcopy(a); tam["dwells"][3]["f_hz"] += 100000; self.assertFalse(blind.schedule_matches(sw, tam))

    def test_schedule_is_actually_randomised(self):
        a = blind.make_schedule("x", [3e6], [200000, 14000000, 100000]); fs = [d["f_hz"] for d in a["dwells"] if not d["is_ref"]]
        self.assertNotEqual(fs, sorted(fs)); self.assertEqual(sorted(fs), list(np.arange(200000, 14000001, 100000.0)))
        refs = [d["seq"] for d in a["dwells"] if d["is_ref"]]; self.assertEqual(refs[:3], [0, 11, 22])

    def test_commit_reveal(self):
        sec, com = blind.commit(20, 2e6, 12e6, 10000, "human_second_operator")
        self.assertEqual(len(sec["trials"]), 20); self.assertTrue(all(2e6 <= v <= 12e6 and v % 10000 == 0 for v in sec["trials"].values()))
        est = {k: v + 1000 for k, v in sec["trials"].items()}; ep = os.path.join(TMP, "est.json"); json.dump(est, open(ep, "w"))
        eh = hashlib.sha256(open(ep, "rb").read()).hexdigest()
        tr, rec = blind.reveal(sec, com, ep, eh, operator="Bo", analyst="Ann"); self.assertEqual(tr, sec["trials"]); self.assertEqual(rec["mode"], "human_second_operator")
        t = copy.deepcopy(sec); t["trials"]["t00"] += 10000
        with self.assertRaisesRegex(ValueError, "commitment"): blind.reveal(t, com, ep, eh, operator="Bo", analyst="Ann")          # secret changed after commit
        with self.assertRaisesRegex(ValueError, "estimates"): blind.reveal(sec, com, ep, "0" * 64, operator="Bo", analyst="Ann")    # estimates changed / not pre-hashed
        sec2, com2 = blind.commit(20, 2e6, 12e6, 10000); self.assertNotEqual(com["commitment_sha256"], com2["commitment_sha256"])


class BlindingModes(unittest.TestCase):
    def setUp(self):
        self.ep = os.path.join(TMP, "estm.json")

    def _est(self, sec):
        json.dump({k: v + 500 for k, v in sec["trials"].items()}, open(self.ep, "w")); return hashlib.sha256(open(self.ep, "rb").read()).hexdigest()

    def test_human_mode_rejects_same_person_or_missing_names(self):
        sec, com = blind.commit(20, 2e6, 12e6, 10000, "human_second_operator"); eh = self._est(sec)
        with self.assertRaisesRegex(ValueError, "same person"): blind.reveal(sec, com, self.ep, eh, operator="Ann", analyst="ann ")
        with self.assertRaisesRegex(ValueError, "needs"): blind.reveal(sec, com, self.ep, eh)

    def test_external_commitment_needs_a_publication_record(self):
        sec, com = blind.commit(20, 2e6, 12e6, 10000, "self_with_external_commitment"); eh = self._est(sec)
        with self.assertRaisesRegex(ValueError, "published-to"): blind.reveal(sec, com, self.ep, eh)
        tr, rec = blind.reveal(sec, com, self.ep, eh, published_to="git commit abc123"); self.assertEqual(rec["attestation"]["published_to"], "git commit abc123")

    def test_os_account_separation_checks_file_owners(self):
        sec, com = blind.commit(20, 2e6, 12e6, 10000, "os_account_separation"); eh = self._est(sec)
        sp = os.path.join(TMP, "secret_same_owner.json"); json.dump(sec, open(sp, "w"))
        with self.assertRaisesRegex(ValueError, "same OS user"): blind.reveal(sec, com, self.ep, eh, secret_path=sp)   # both files are ours: not separated

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError): blind.commit(20, 2e6, 12e6, 10000, "trust_me")

    def test_record_verification_detects_swapped_files(self):
        sec, com = blind.commit(20, 2e6, 12e6, 10000, "human_second_operator"); eh = self._est(sec)
        tr, rec = blind.reveal(sec, com, self.ep, eh, operator="Bo", analyst="Ann"); tp = os.path.join(TMP, "truths.json"); json.dump(tr, open(tp, "w"))
        rec["truths_sha256"] = blind.file_sha(tp); self.assertTrue(blind.verify_record(rec, self.ep, tp, com))
        json.dump({k: v + 1 for k, v in tr.items()}, open(tp, "w")); self.assertFalse(blind.verify_record(rec, self.ep, tp, com))   # truths edited afterwards
        other = dict(com, commitment_sha256="0" * 64); json.dump(tr, open(tp, "w")); self.assertFalse(blind.verify_record(rec, self.ep, tp, other))

    def test_unverified_blinding_caps_the_verdict_at_rung4(self):
        R = run("H1", 8.0, blinding=dict(mode="human_second_operator", verified=False))
        self.assertEqual(R["verdict"], "H1_SUPPORTED_RUNG4", R["verdict"]); self.assertIn("note", R["stage4_blinding"])
        R2 = run("H1", 8.0, blinding=None); self.assertEqual(R2["verdict"], "H1_SUPPORTED_RUNG4")
        R3 = run("H1", 8.0); self.assertEqual(R3["verdict"], "H1_SUPPORTED_RUNG5")


class Profiles(unittest.TestCase):
    def test_standard_profile_is_the_default_rule(self):
        import profiles as pf
        b = pf.build("standard"); ref = json.load(open(PATH))
        for k in ("profile", "profile_use", "fc_set"): b.pop(k)
        self.assertEqual(json.dumps(b, sort_keys=True), json.dumps(ref, sort_keys=True))

    def test_all_profiles_valid_and_ordered_by_strictness(self):
        import profiles as pf
        c = {n: pf.build(n) for n in pf.PROFILES}
        self.assertTrue(all(not pf.validate(v) for v in c.values()))
        self.assertLess(c["stringent"]["tests"]["family_alpha"], c["standard"]["tests"]["family_alpha"], )
        self.assertLess(c["standard"]["tests"]["family_alpha"], c["pragmatic"]["tests"]["family_alpha"])
        self.assertGreater(pf.control_budget(c["stringent"])[1], pf.control_budget(c["standard"])[1])
        self.assertGreater(pf.control_budget(c["standard"])[1], pf.control_budget(c["pragmatic"])[1])
        self.assertAlmostEqual(pf.control_budget(c["standard"])[1], 8.0, delta=0.1)      # matches the simulated budget table

    def test_fc_sets_and_overrides(self):
        import profiles as pf
        for k in pf.FC_SETS: self.assertEqual(pf.validate(pf.build("standard", k)), [])
        self.assertEqual(pf.build("standard", "A", {"tests.selectivity_db": 15.0})["tests"]["selectivity_db"], 15.0)

    def test_inconsistent_rules_are_refused(self):
        import profiles as pf
        for ov, frag in (({"design.fc_hz": [3e6, 4e6, 12e6]}, "apart"), ({"design.fc_hz": [0.5e6, 7.5e6, 12e6]}, "leaves the swept range"),
                         ({"tests.permutation_B": 200}, "too coarse"), ({"gates.control_max_fraction_of_signal": 0.9}, "control fraction"),
                         ({"sets.in_band_halfwidth_hz": 1_500_000}, "inside the plateau"), ({"stage4.n_trials_min": 5}, "12 trials"), ({"tests.center_shift_tolerance_hz": 150000}, "quantised")):
            with self.assertRaises(SystemExit) as cm: pf.build("standard", "A", ov)
            self.assertIn(frag, str(cm.exception), ov)
        with self.assertRaises(KeyError): pf.build("standard", "A", {"tests.not_a_parameter": 1})
        with self.assertRaises(SystemExit): pf.build("stringent", "A", {"tests.permutation_B": 5000})   # alpha 0.001 cannot be resolved

    def test_profile_is_stamped_in_results(self):
        import profiles as pf
        cfg = pf.build("pragmatic"); h = pr.sha256_text(pr.canonical(cfg)); rng = np.random.default_rng(1)
        sw = sm.make_sweeps(rng, "H0a", s2=0.0); R = pr.evaluate(sw, None, None, None, cfg, h, False, "simulated")
        self.assertEqual(R["profile"], "pragmatic")


class ErrorRates(unittest.TestCase):
    def test_false_positive_rate_under_nulls(self):
        """Under H0a/H0b/H0c an H1 verdict must be rare (the family alpha is 0.01)."""
        n = 120; hits = {}
        for scn, s2 in (("H0a", 0.0), ("H0b", 8.0), ("H0c", 8.0)):
            hits[scn] = sum(run(scn, s2, seed=100 + i, trials=False)["verdict"].startswith("H1") for i in range(n))
        for scn, h in hits.items(): self.assertLessEqual(h, 3, f"{scn}: {h}/{n} false H1 verdicts")

    def test_power_when_there_is_a_selective_response(self):
        n = 40; ok = sum(run("H1", 8.0, seed=500 + i, trials=False)["verdict"] == "H1_SUPPORTED_RUNG4" for i in range(n))
        self.assertGreaterEqual(ok, 36, f"only {ok}/{n} detected at s2=8")


if __name__ == "__main__":
    unittest.main(verbosity=1)
