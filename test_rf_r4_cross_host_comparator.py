"""Gate B' controls for tools/r4_package/compare_replay.py: synthetic perturbations of the retained local reference job (protocol section 8)."""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); TOOL = os.path.join(HERE, "tools", "r4_package", "compare_replay.py")
EV = os.path.join(HERE, "docs", "evidence", "r4_full_local_2026-10-13"); sha = lambda b: hashlib.sha256(b).hexdigest()
PRE = os.path.join(EV, "preflight", "record_prelaunch_3", "PREFLIGHT_RECORD.json")
SIZE_LOST = "results_full/phaseB/{}"


def seal(job):
    """Write JOB_SHA256SUMS over every file except holdout/ and itself (what seal_job.py does), so a perturbed copy is still a 'sealed' job."""
    lines = []
    for r, _, fs in os.walk(job):
        for f in fs:
            rel = os.path.relpath(os.path.join(r, f), job)
            if rel != "JOB_SHA256SUMS": lines.append(f"{sha(pathlib.Path(r, f).read_bytes())}  {rel}")
    pathlib.Path(job, "JOB_SHA256SUMS").write_text("\n".join(sorted(lines, key=lambda l: l.split()[1])) + "\n")


class Comparator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="r4cmp-"); cls.n = 0
        cls.base = os.path.join(cls.tmp, "base"); shutil.copytree(os.path.join(EV, "job"), cls.base); os.remove(os.path.join(cls.base, "JOB_SHA256SUMS")); seal(cls.base)
        cls.disp = os.path.join(cls.tmp, "disp.txt"); pathlib.Path(cls.disp).write_text("COMPUTATION_COMPLETED: YES\nEVIDENCE_SEALED: YES\nJOB_ACCEPTED: YES\n--- sealer\n")
        cls.phaseb = sorted(os.listdir(os.path.join(cls.base, "results_full", "phaseB")))

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.tmp, True)

    def replay(self, mutate=None, reseal=True):
        type(self).n += 1; d = os.path.join(self.tmp, f"r{self.n}"); shutil.copytree(self.base, d)
        if mutate: mutate(d)
        if reseal: seal(d)
        return d

    def edit_json(self, d, rel, fn):
        p = pathlib.Path(d, rel); o = json.loads(p.read_text()); fn(o); p.write_text(json.dumps(o, indent=1, sort_keys=True))

    def compare(self, d, pre=PRE, disp=None, ref=None):
        rep = os.path.join(self.tmp, f"report{self.n}.json")
        r = subprocess.run([sys.executable, TOOL, ref or self.base, d, "--ref-preflight", PRE, "--replay-preflight", pre, "--ref-disposition", self.disp,
                            "--replay-disposition", disp or self.disp, "--report", rep], capture_output=True, text=True)
        return r, (json.loads(pathlib.Path(rep).read_text()) if os.path.exists(rep) else None)

    def result(self, d, **k):
        r, rep = self.compare(d, **k); return r.stdout.splitlines()[0].replace("RESULT: ", "") if r.returncode in (0, 1) else r.stdout, r, rep

    # ---- equivalence
    def test_a_job_compared_with_itself_is_bitwise_equivalent(self):
        res, r, rep = self.result(self.replay()); self.assertEqual(res, "EQUIVALENT_BITWISE", r.stdout); self.assertEqual(r.returncode, 0)
        self.assertEqual(rep["overall"], "R4_1_CROSS_HOST_REPLAY_EQUIVALENT"); self.assertEqual(rep["counts"]["WITHIN_TOLERANCE"], 0)
        self.assertGreater(rep["counts"]["IDENTICAL"], 10000); self.assertTrue(rep["numeric_field_statistics"])

    def anchor_edit(self, delta):
        def m(d): self.edit_json(d, "results_full/layerB/" + sorted(os.listdir(os.path.join(d, "results_full/layerB")))[0], lambda o: o["cases"][0].__setitem__("anchor_excess_db", o["cases"][0]["anchor_excess_db"] + delta))
        return m

    def test_a_numeric_leaf_just_inside_and_just_outside_the_tolerance(self):
        res, r, rep = self.result(self.replay(self.anchor_edit(5e-10))); self.assertEqual(res, "EQUIVALENT_WITHIN_DECLARED_TOLERANCE", r.stdout)
        self.assertEqual(rep["counts"]["WITHIN_TOLERANCE"], 1)
        res, r, rep = self.result(self.replay(self.anchor_edit(2e-9))); self.assertEqual(res, "NUMERICAL_DIVERGENCE", r.stdout); self.assertEqual(r.returncode, 1)
        self.assertEqual(rep["overall"], "R4_1_CROSS_HOST_REPLAY_NOT_ESTABLISHED")

    def lost_edit(self, delta):
        def m(d):
            name = next(f for f in sorted(os.listdir(os.path.join(d, "results_full/phaseB"))) if f.startswith("absolute_floor"))
            def fn(o):
                x = o["relations"]["NOISE weak"]["lost"][0]; x[0] = x[0] + delta
            self.edit_json(d, "results_full/phaseB/" + name, fn)
        return m

    def test_a_large_frequency_difference_is_judged_against_the_capped_tolerance(self):
        res, r, rep = self.result(self.replay(self.lost_edit(9e-9))); self.assertEqual(res, "EQUIVALENT_WITHIN_DECLARED_TOLERANCE", r.stdout)   # uncapped relative slack would be ~9e-7
        res, r, rep = self.result(self.replay(self.lost_edit(2e-8))); self.assertEqual(res, "NUMERICAL_DIVERGENCE", r.stdout)                   # 2e-8 is far inside the uncapped slack, outside the cap

    # ---- semantic divergence
    def test_changed_verdicts_credits_statuses_bins_and_counts_are_semantic(self):
        lb = lambda d: "results_full/layerB/" + sorted(os.listdir(os.path.join(d, "results_full/layerB")))[0]
        cases = {
            "verdict": lambda d: self.edit_json(d, lb(d), lambda o: o["cases"][0]["judgments"]["production"].__setitem__("verdict", "RELATION_VIOLATED")),
            "credit": lambda d: self.edit_json(d, lb(d), lambda o: o["cases"][0]["judgments"]["span_limit_removed"].__setitem__("credit", "MUTANT_DETECTED" if o["cases"][0]["judgments"]["span_limit_removed"]["credit"] != "MUTANT_DETECTED" else "MUTANT_SURVIVED_EQUIVALENT_ON_GRID")),
            "status": lambda d: self.edit_json(d, "results_full/phaseB/" + self.phaseb[0], lambda o: o.__setitem__("status", "RESOURCE_LIMIT_REFUSAL")),
            "bin": lambda d: self.edit_json(d, lb(d), lambda o: o["cases"][0]["expected_bins"].__setitem__(0, o["cases"][0]["expected_bins"][0] + 1)),
            "count": lambda d: self.edit_json(d, lb(d), lambda o: o.__setitem__("base_candidates", o["base_candidates"] + 1)),
        }
        for name, m in cases.items():
            res, r, rep = self.result(self.replay(m)); self.assertEqual(res, "SEMANTIC_DIVERGENCE", f"{name}: {r.stdout}")

    def test_an_int_turned_into_a_float_and_a_changed_exact_constant_are_semantic(self):
        lb = lambda d: "results_full/layerB/" + sorted(os.listdir(os.path.join(d, "results_full/layerB")))[0]
        res, r, rep = self.result(self.replay(lambda d: self.edit_json(d, lb(d), lambda o: o.__setitem__("base_candidates", float(o["base_candidates"]))))); self.assertEqual(res, "SEMANTIC_DIVERGENCE", r.stdout)
        res, r, rep = self.result(self.replay(lambda d: self.edit_json(d, "results_full/layerA.json", lambda o: o.__setitem__("usable_hz", o["usable_hz"] + 1e-9)))); self.assertEqual(res, "SEMANTIC_DIVERGENCE", r.stdout)

    def test_a_residual_that_crosses_a_verdict_threshold_is_semantic_even_within_tolerance(self):
        def m(vals):
            def f(d):
                fx = sorted(os.listdir(os.path.join(d, "results_full/phaseA")))[0]
                self.edit_json(d, "results_full/phaseA/" + fx, lambda o: o["relations"]["CONJ"].__setitem__("max_freq_dev_hz", vals))
            return f
        self.edit_json(self.base, "results_full/phaseA/" + sorted(os.listdir(os.path.join(self.base, "results_full/phaseA")))[0], lambda o: o["relations"]["CONJ"].__setitem__("max_freq_dev_hz", 9e-10)); seal(self.base)
        try:
            res, r, rep = self.result(self.replay(m(9.5e-10))); self.assertEqual(res, "EQUIVALENT_WITHIN_DECLARED_TOLERANCE", r.stdout)        # same band
            res, r, rep = self.result(self.replay(m(1.2e-9))); self.assertEqual(res, "SEMANTIC_DIVERGENCE", r.stdout)                                # 3e-10 apart (inside A) but across 1e-9
            self.assertIn("straddles", r.stdout)
        finally:
            self.edit_json(self.base, "results_full/phaseA/" + sorted(os.listdir(os.path.join(self.base, "results_full/phaseA")))[0], lambda o: o["relations"]["CONJ"].__setitem__("max_freq_dev_hz", 1.1641532182693481e-10)); seal(self.base)

    # ---- structural
    def test_missing_extra_records_keys_and_list_lengths_are_incomparable(self):
        res, r, _ = self.result(self.replay(lambda d: os.remove(os.path.join(d, "results_full/phaseB", self.phaseb[0])))); self.assertEqual(res, "INCOMPARABLE", r.stdout)
        res, r, _ = self.result(self.replay(lambda d: pathlib.Path(d, "results_full/phaseB/extra.json").write_text("{}"))); self.assertEqual(res, "INCOMPARABLE", r.stdout)
        res, r, _ = self.result(self.replay(lambda d: self.edit_json(d, "results_full/phaseB/" + self.phaseb[0], lambda o: o.__setitem__("added_key", 1)))); self.assertEqual(res, "INCOMPARABLE", r.stdout)
        res, r, _ = self.result(self.replay(lambda d: self.edit_json(d, "results_full/layerB/" + sorted(os.listdir(os.path.join(d, "results_full/layerB")))[0], lambda o: o["cases"].pop()))); self.assertEqual(res, "INCOMPARABLE", r.stdout)
        res, r, _ = self.result(self.replay(lambda d: self.edit_json(d, "results_full/layerBprime.json", lambda o: o.__setitem__("note", [o["note"]])))); self.assertEqual(res, "INCOMPARABLE", r.stdout)   # string -> array: node kind
        # precedence: a structural difference outranks a semantic one in the same replay
        def both(d):
            os.remove(os.path.join(d, "results_full/phaseB", self.phaseb[0]))
            self.edit_json(d, "results_full/phaseB/" + self.phaseb[1], lambda o: o.__setitem__("status", "X"))
        res, r, _ = self.result(self.replay(both)); self.assertEqual(res, "INCOMPARABLE", r.stdout)

    # ---- class X
    def test_environmental_fields_may_differ_without_effect(self):
        def m(d):
            for sub in ("results_full", "results_smoke"):
                self.edit_json(d, f"{sub}/run_header.json", lambda o: (o.__setitem__("argv", ["/elsewhere/python", "--holdout", "/other"]), o.__setitem__("numpy", "2.5.3")))
            pathlib.Path(d, "STATUS.txt").write_text(pathlib.Path(d, "STATUS.txt").read_text().replace("2026-10-13", "2026-12-01"))
            pathlib.Path(d, "env.txt").write_text("uname: other host\npython: 3.12.14\n")
            (pathlib.Path(d, "results_full/MANIFEST.sha256")).write_text("0" * 64 + "  run_header.json\n")
        res, r, rep = self.result(self.replay(m)); self.assertEqual(res, "EQUIVALENT_BITWISE", r.stdout)
        self.assertIn("run_header.json:numpy", rep["environment_class_X"]); self.assertIn("run_header.json:argv", rep["environment_class_X"])

    # ---- provenance refusals
    def refused(self, d, **k):
        res, r, rep = self.result(d, **k); self.assertEqual(res, "COMPARISON_REFUSED_PROVENANCE_MISMATCH", r.stdout); self.assertEqual(r.returncode, 1); return rep

    def test_a_different_package_digest_fixture_list_or_declared_value_or_harness_is_refused(self):
        bad = os.path.join(self.tmp, "bad_pre.json"); o = json.loads(pathlib.Path(PRE).read_text()); o["package_sha256"] = "0" * 64; pathlib.Path(bad).write_text(json.dumps(o))
        self.assertTrue(any("package digest" in w for w in self.refused(self.replay(), pre=bad)["provenance_failures"]))
        self.assertTrue(any("fixture hash list" in w for w in self.refused(self.replay(lambda d: pathlib.Path(d, "fixture_hashes.sha256").write_text("0" * 64 + "  x.npy\n")))["provenance_failures"]))
        self.refused(self.replay(lambda d: self.edit_json(d, "results_full/run_header.json", lambda o: o["declared"].__setitem__("tol_hz", 1e-8))))
        self.refused(self.replay(lambda d: self.edit_json(d, "results_full/run_header.json", lambda o: o.__setitem__("harness_sha256", "1" * 64))))
        self.refused(self.replay(lambda d: self.edit_json(d, "results_smoke/run_header.json", lambda o: o["thread_env"].__setitem__("OMP_NUM_THREADS", "4"))))

    def test_an_unsealed_tampered_or_unaccepted_job_is_refused(self):
        self.refused(self.replay(lambda d: os.remove(os.path.join(d, "JOB_SHA256SUMS")), reseal=False))
        d = self.replay(); self.edit_json(d, "results_full/layerA.json", lambda o: o.__setitem__("usable_hz", 1.0))           # changed after sealing
        self.assertTrue(any("digest mismatch" in w for w in self.refused(d)["provenance_failures"]))
        d = self.replay(); pathlib.Path(d, "extra_after_seal.txt").write_text("x"); self.assertTrue(any("unlisted" in w for w in self.refused(d)["provenance_failures"]))
        self.refused(self.replay(lambda d: pathlib.Path(d, "STATUS.txt").write_text(pathlib.Path(d, "STATUS.txt").read_text().replace(" DONE ", " FAILED "))))
        no = os.path.join(self.tmp, "no.txt"); pathlib.Path(no).write_text("JOB_ACCEPTED: NO\n--- sealer\n"); self.refused(self.replay(), disp=no)

    # ---- failure is never equivalence
    def test_a_crash_is_a_comparator_failure_not_a_result(self):
        r = subprocess.run([sys.executable, TOOL, os.path.join(self.tmp, "nowhere"), self.base, "--ref-preflight", PRE, "--replay-preflight", PRE, "--ref-disposition", self.disp, "--replay-disposition", self.disp], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2); self.assertIn("COMPARATOR_FAILURE", r.stdout); self.assertNotIn("RESULT:", r.stdout)
        d = self.replay(lambda d: pathlib.Path(d, "results_full/phaseB", self.phaseb[0]).write_text("{not json"))
        r, rep = self.compare(d); self.assertEqual(r.returncode, 2); self.assertIn("COMPARATOR_FAILURE", r.stdout); self.assertIsNone(rep)

    def test_the_comparator_shares_no_code_with_the_harness_or_other_tools(self):
        import ast
        tree = ast.parse(pathlib.Path(TOOL).read_text()); mods = {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else (n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}
        self.assertTrue(mods <= set(sys.stdlib_module_names), mods)

    def test_the_frozen_constants_match_the_merged_protocol(self):
        doc = pathlib.Path(HERE, "docs/research/metamorphic_r4_cross_host_replay_protocol.md").read_text()
        for s in ("A    = 1e-9", "Rr   = 1e-12", "Cap  = 1e-8", "2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a", "8d34de9ac0b1f4758cda3412b364582b4271ffe1"): self.assertIn(s, doc)
        src = pathlib.Path(TOOL).read_text(); self.assertIn("A, RR, CAP = 1e-9, 1e-12, 1e-8", src)


if __name__ == "__main__":
    unittest.main()
