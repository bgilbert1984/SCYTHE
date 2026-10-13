"""Evidence-custody controls for the r4 job runner: terminal status first, manifest sealed LAST, nothing appended afterwards."""
import contextlib, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tarfile, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); JOBDIR = os.path.join(HERE, "tools", "r4_job")
_spec = importlib.util.spec_from_file_location("seal_job", os.path.join(JOBDIR, "seal_job.py"))
SJ = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(SJ)
with open(os.path.join(JOBDIR, "run_job.sh")) as _fh:
    SCRIPT = _fh.read().splitlines()


def make_job(terminal=True):
    d = tempfile.mkdtemp(prefix="r4job-")
    os.makedirs(os.path.join(d, "logs")); os.makedirs(os.path.join(d, "results_full"))
    def put(rel, text):
        with open(os.path.join(d, rel), "w") as fh: fh.write(text)
    put("env.txt", "env\n"); put("logs/full.log", "log\n"); put("results_full/summary.json", "{}\n")
    put("STATUS.txt", "2026-10-13T00:00:00 START\n2026-10-13T00:01:00 OK stage\n"
        + ("2026-10-13T00:02:00 DONE harness_rc=0 gate_rc=0\n" if terminal else ""))
    return d


class SealOrderAndFailClosed(unittest.TestCase):
    def test_a_job_sealed_after_the_terminal_record_verifies_and_covers_status_txt(self):
        d = make_job(); self.addCleanup(shutil.rmtree, d, True)
        SJ.seal(d); self.assertEqual(SJ.verify(d), [])
        with open(os.path.join(d, "JOB_SHA256SUMS")) as fh: self.assertIn("STATUS.txt", fh.read())

    def test_appending_after_sealing_is_detected_which_was_the_r3_defect(self):
        d = make_job(); self.addCleanup(shutil.rmtree, d, True)
        SJ.seal(d)
        with open(os.path.join(d, "STATUS.txt"), "a") as fh: fh.write("2026-10-13T00:03:00 late line\n")
        self.assertIn("mismatch STATUS.txt", SJ.verify(d))

    def test_sealing_without_a_terminal_record_is_refused(self):
        d = make_job(terminal=False); self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(SystemExit): SJ.seal(d)
        self.assertFalse(os.path.exists(os.path.join(d, "JOB_SHA256SUMS")))

    def test_an_existing_manifest_is_never_overwritten(self):
        d = make_job(); self.addCleanup(shutil.rmtree, d, True)
        SJ.seal(d)
        with self.assertRaises(SystemExit): SJ.seal(d)

    def test_unlisted_and_missing_files_are_reported(self):
        d = make_job(); self.addCleanup(shutil.rmtree, d, True)
        SJ.seal(d)
        with open(os.path.join(d, "extra.txt"), "w") as fh: fh.write("x")
        os.remove(os.path.join(d, "env.txt"))
        problems = SJ.verify(d); self.assertIn("unlisted extra.txt", problems); self.assertIn("missing env.txt", problems)


class RunnerScriptOrdering(unittest.TestCase):
    def idx(self, pattern):
        return [i for i, l in enumerate(SCRIPT) if re.search(pattern, l) and not l.lstrip().startswith("#")]

    def test_the_terminal_status_is_written_before_the_manifest_is_sealed_and_nothing_follows(self):
        terminal = self.idx(r'st "DONE') + self.idx(r'st "FAILED harness_rc')
        final = [i for i in self.idx(r"^finalize ") if i > max(terminal)]
        self.assertEqual(len(self.idx(r'st "DONE')), 1); self.assertEqual(len(self.idx(r'st "FAILED harness_rc')), 1); self.assertTrue(final)
        for line in SCRIPT[final[0] + 1:]:
            self.assertNotRegex(line, r'st "|STATUS\.txt|exit_codes\.txt|>>|\$JOB/', f"writes to the job directory after sealing: {line!r}")

    def test_inside_finalize_the_sealer_runs_before_any_report_and_reports_go_outside_the_job_directory(self):
        start = self.idx(r"^finalize\(\)")[0]; end = next(i for i in range(start, len(SCRIPT)) if SCRIPT[i] == "}")
        body = SCRIPT[start:end]
        seal = [i for i, l in enumerate(body) if "seal_job.py" in l]; verify = [i for i, l in enumerate(body) if "verify_job.py" in l]
        report = [i for i, l in enumerate(body) if ".disposition.txt" in l]
        self.assertTrue(seal and verify and report); self.assertLess(max(seal), min(verify)); self.assertLess(max(verify), min(report))
        for l in body: self.assertNotRegex(l, r'>>? *"\$JOB/')

    def test_done_is_conditional_on_both_return_codes_and_is_not_acceptance(self):
        i = self.idx(r'st "DONE')[0]
        self.assertRegex(SCRIPT[i - 1], r'"\$rc" -eq 0 \] && \[ "\$grc" -eq 0 \]; then')
        self.assertIn("COMPUTATION_COMPLETED", SCRIPT[i]); self.assertIn("not JOB_ACCEPTED", SCRIPT[i])
        self.assertRegex(SCRIPT[i + 2], r'else'); self.assertIn("FAILED harness_rc=$rc gate_rc=$grc", SCRIPT[i + 3])

    def test_exit_codes_are_recorded_before_sealing(self):
        seal_last = max(self.idx(r"^finalize ")); codes = self.idx(r'exit_codes\.txt')
        self.assertTrue(codes); self.assertTrue(all(c < seal_last for c in codes))

    def test_the_counts_are_not_hard_coded(self):
        text = "\n".join(SCRIPT)
        self.assertNotIn("12 bounded controls", text); self.assertNotIn("/36 hashes", text)
        self.assertRegex(text, r'bounded controls \(\$\(grep -E')

    def test_every_failure_path_still_seals_a_terminal_record(self):
        fail_fn = [l for l in SCRIPT if l.startswith("fail()")][0]
        self.assertIn('st "FAILED', fail_fn); self.assertLess(fail_fn.index('st "FAILED'), fail_fn.index("finalize"))


class FixtureCustody(unittest.TestCase):
    """The fixture bytes are not in JOB_SHA256SUMS; their hash LIST is, and the bytes are re-verified at seal time and at independent verification."""
    FILES = {"fx01.npy": b"fixture 01\n", "fx02.npy": b"fixture 02\n", "fx03.npy": b"fixture 03\n"}
    INDEX = (json.dumps({"family_a": [{"name": "fx01", "seed": 1}, {"name": "fx02", "seed": 2}], "family_b": [{"name": "fx03", "seed": 3}]}, indent=2) + "\n").encode()

    def job(self, with_holdout=True, index=None):
        d = make_job(); self.addCleanup(shutil.rmtree, d, True); index = self.INDEX if index is None else index
        with open(os.path.join(d, "fixture_hashes.sha256"), "w") as fh:
            for n, b in self.FILES.items(): fh.write(f"{hashlib.sha256(b).hexdigest()}  {n}\n")
        with open(os.path.join(d, "generated_artifacts.sha256"), "w") as fh: fh.write(f"{hashlib.sha256(index).hexdigest()}  index.json\n")
        if with_holdout:
            os.makedirs(os.path.join(d, "holdout"))
            for n, b in {**self.FILES, "index.json": index}.items():
                with open(os.path.join(d, "holdout", n), "wb") as fh: fh.write(b)
        return d

    def cli(self):
        """A copy of seal_job.py outside the package, so no package-authority pin applies to the synthetic jobs."""
        d = tempfile.mkdtemp(prefix="r4cli-"); self.addCleanup(shutil.rmtree, d, True)
        shutil.copy(os.path.join(JOBDIR, "seal_job.py"), d); return os.path.join(d, "seal_job.py")

    def setUp(self):
        saved = dict(SJ.AUTHORITY); SJ.AUTHORITY.clear()                      # synthetic jobs carry their own pins; the package-authority rule has its own test below
        self.addCleanup(lambda: (SJ.AUTHORITY.clear(), SJ.AUTHORITY.update(saved)))

    def test_the_fixture_list_is_sealed_and_the_bytes_are_verified_against_it(self):
        d = self.job(); SJ.seal(d, require_fixtures=True)
        with open(os.path.join(d, "JOB_SHA256SUMS")) as fh: m = fh.read()
        self.assertIn("fixture_hashes.sha256", m); self.assertNotIn("holdout/", m)
        self.assertEqual(SJ.verify(d), []); self.assertEqual(SJ.check_fixtures(d)[0], "VERIFIED_AGAINST_SEALED_HASH_LIST")
        self.assertIn("generated_artifacts.sha256", m)

    def test_a_fixture_changed_after_sealing_cannot_pass_independent_verification(self):
        d = self.job(); SJ.seal(d, require_fixtures=True)
        with open(os.path.join(d, "holdout", "fx02.npy"), "ab") as fh: fh.write(b"x")
        state, problems = SJ.check_fixtures(d)
        self.assertEqual(state, "FAILED"); self.assertIn("fixture mismatch fx02.npy", problems)
        self.assertEqual(SJ.verify(d), [])                                       # the manifest alone cannot see it: that is why the re-check exists
        r = subprocess.run([sys.executable, self.cli(), d, "--verify", "--require-fixtures"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertIn("FIXTURE_BYTES: FAILED", r.stdout)

    def test_a_deleted_or_added_fixture_is_rejected(self):
        d = self.job(); SJ.seal(d, require_fixtures=True)
        os.remove(os.path.join(d, "holdout", "fx01.npy"))
        with open(os.path.join(d, "holdout", "fx09.npy"), "wb") as fh: fh.write(b"intruder")
        problems = SJ.check_fixtures(d)[1]
        self.assertIn("fixture missing fx01.npy", problems); self.assertIn("fixture not in the sealed list fx09.npy", problems)

    def test_a_tampered_sealed_list_fails_the_manifest(self):
        d = self.job(); SJ.seal(d, require_fixtures=True)
        with open(os.path.join(d, "fixture_hashes.sha256"), "a") as fh: fh.write("0" * 64 + "  fx99.npy\n")
        self.assertIn("mismatch fixture_hashes.sha256", SJ.verify(d))

    def test_absent_fixture_bytes_are_an_explicit_non_claim_and_fail_when_required(self):
        d = self.job(with_holdout=False); SJ.seal(d)
        self.assertEqual(SJ.check_fixtures(d)[0], "NOT_PRESENT_NOT_REVERIFIED")
        base = [sys.executable, self.cli(), d, "--verify"]
        soft = subprocess.run(base, capture_output=True, text=True); hard = subprocess.run(base + ["--require-fixtures"], capture_output=True, text=True)
        self.assertEqual(soft.returncode, 0); self.assertIn("NOT re-verified", soft.stdout); self.assertNotEqual(hard.returncode, 0)

    def test_sealing_is_refused_if_the_fixtures_do_not_match_the_list_at_seal_time(self):
        d = self.job()
        with open(os.path.join(d, "holdout", "fx03.npy"), "wb") as fh: fh.write(b"corrupt")
        with self.assertRaises(SystemExit): SJ.seal(d)
        self.assertFalse(os.path.exists(os.path.join(d, "JOB_SHA256SUMS")))

    def test_requiring_fixtures_refuses_a_job_without_the_list_or_the_bytes(self):
        d = make_job(); self.addCleanup(shutil.rmtree, d, True)
        with self.assertRaises(SystemExit): SJ.seal(d, require_fixtures=True)

    def test_the_index_is_a_separately_pinned_artifact_and_must_be_in_the_directory(self):
        d = self.job(); os.remove(os.path.join(d, "holdout", "index.json"))
        state, problems = SJ.check_fixtures(d); self.assertEqual(state, "FAILED"); self.assertIn("fixture missing index.json", problems)
        with self.assertRaises(SystemExit): SJ.seal(d)

    def test_a_modified_index_is_refused(self):
        d = self.job()
        with open(os.path.join(d, "holdout", "index.json"), "ab") as fh: fh.write(b" ")
        self.assertIn("fixture mismatch index.json", SJ.check_fixtures(d)[1])

    def test_an_unexpected_extra_file_or_a_non_regular_entry_is_refused(self):
        d = self.job()
        with open(os.path.join(d, "holdout", "stray.txt"), "w") as fh: fh.write("x")
        self.assertIn("fixture not in the sealed list stray.txt", SJ.check_fixtures(d)[1])
        os.remove(os.path.join(d, "holdout", "stray.txt")); os.symlink("fx01.npy", os.path.join(d, "holdout", "link.npy"))
        self.assertTrue(any("not a regular file link.npy" in p for p in SJ.check_fixtures(d)[1]))

    def test_duplicate_or_overlapping_pins_are_refused(self):
        d = self.job()
        with open(os.path.join(d, "fixture_hashes.sha256"), "a") as fh: fh.write(f"{hashlib.sha256(self.FILES['fx01.npy']).hexdigest()}  fx01.npy\n")
        self.assertIn("duplicate entry fx01.npy in fixture_hashes.sha256", SJ.check_fixtures(d)[1])
        d = self.job()
        with open(os.path.join(d, "generated_artifacts.sha256"), "a") as fh: fh.write(f"{hashlib.sha256(self.FILES['fx02.npy']).hexdigest()}  fx02.npy\n")
        self.assertIn("fx02.npy is pinned in both lists", SJ.check_fixtures(d)[1])

    def test_a_job_with_the_list_but_no_generated_pin_is_not_sealed(self):
        d = self.job(); os.remove(os.path.join(d, "generated_artifacts.sha256"))
        self.assertEqual(SJ.check_fixtures(d)[0], "GENERATED_PIN_MISSING")
        with self.assertRaises(SystemExit): SJ.seal(d)

    def test_a_hash_pinned_index_that_contradicts_its_fixtures_is_refused(self):
        wrong = (json.dumps({"family_a": [{"name": "fx01", "seed": 1}, {"name": "fx02", "seed": 2}], "family_b": [{"name": "fx99", "seed": 3}]}) + "\n").encode()
        d = self.job(index=wrong)                       # the wrong index IS pinned and its digest matches: only the cross-check can catch it
        state, problems = SJ.check_fixtures(d); self.assertEqual(state, "FAILED")
        self.assertIn("index does not describe fixture fx03.npy", problems); self.assertIn("index describes a fixture that is not pinned: fx99.npy", problems)
        with self.assertRaises(SystemExit): SJ.seal(d)
        dup = (json.dumps({"family_a": [{"name": "fx01", "seed": 1}, {"name": "fx01", "seed": 2}], "family_b": [{"name": "fx03", "seed": 3}]}) + "\n").encode()
        self.assertIn("index names fx01 more than once", SJ.check_fixtures(self.job(index=dup))[1])

    def test_a_sealed_pin_that_differs_from_the_package_pin_is_refused(self):
        d = self.job(); good = os.path.join(d, "generated_artifacts.sha256")
        SJ.AUTHORITY["generated_artifacts.sha256"] = good                        # the package's own copy
        self.assertEqual(SJ.check_fixtures(d)[0], "VERIFIED_AGAINST_SEALED_HASH_LIST")
        with open(good) as fh: authoritative = fh.read()
        auth = os.path.join(tempfile.mkdtemp(prefix="r4auth-"), "generated_artifacts.sha256"); self.addCleanup(shutil.rmtree, os.path.dirname(auth), True)
        with open(auth, "w") as fh: fh.write("0" * 64 + "  index.json\n")
        SJ.AUTHORITY["generated_artifacts.sha256"] = auth
        self.assertIn("generated_artifacts.sha256 differs from the package's own copy", SJ.check_fixtures(d)[1])

    def test_sealing_reports_evidence_sealed_which_is_not_job_accepted(self):
        d = self.job()
        r = subprocess.run([sys.executable, self.cli(), d], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn("EVIDENCE_SEALED: YES", r.stdout); self.assertNotIn("JOB_ACCEPTED: YES", r.stdout)


class RealGeneratorIntegration(unittest.TestCase):
    """The ACTUAL frozen fixture generator, the ACTUAL sealer and the separately implemented verifier, together (no stub).

    Gate 3 of the first r4 package failed because the generator also writes holdout/index.json, which the 36-hash list does not pin; stub-based tests never saw it.
    Generating the 36 fixtures takes about 40 s and 1.2 GB of temporary disk; it is done once for the class.
    """
    EVID = os.path.join(HERE, "docs", "evidence", "dedirock-equivalence-2026-10-12")
    FROZEN_LIST_SHA256 = "d42892ec15932b1883e8c59391625e1cdcd7d2171f588a275521b2b0d0b63e41"
    PINNED_INDEX_SHA256 = "f52adb1974dce642bc62bf748add6733036db1cbc7bcba482691a221ad88eca3"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="r4real-")
        pk = os.path.join(HERE, "fixtures")
        if os.path.exists(os.path.join(pk, "generate_holdout.py")):                                    # inside a frozen package
            cls.gen, cls.spec, cls.frozen_index, cls.hashes = (os.path.join(pk, n) for n in ("generate_holdout.py", "synth_521_holdout_frozen.json", "synth521_holdout_index.json", "fixture_hashes.sha256"))
        else:                                                                                           # in the repository: members of the merged equivalence package
            ex = os.path.join(cls.tmp, "equiv")
            with tarfile.open(os.path.join(cls.EVID, "dedirock_equiv_pkg.tar.gz")) as tf:
                tf.extractall(ex, filter="data", members=[m for m in tf.getmembers() if os.path.basename(m.name) in ("generate_holdout.py", "synth_521_holdout_frozen.json", "synth521_holdout_index.json")])
            cls.gen, cls.spec, cls.frozen_index = (os.path.join(ex, "dedirock_equiv_pkg", n) for n in ("generate_holdout.py", "synth_521_holdout_frozen.json", "synth521_holdout_index.json"))
            cls.hashes = os.path.join(cls.EVID, "fixture_hashes.sha256")
        cls.pin = os.path.join(JOBDIR, "generated_artifacts.sha256")
        cls.holdout = os.path.join(cls.tmp, "holdout")
        r = subprocess.run([sys.executable, cls.gen, "--spec", cls.spec, "--outdir", cls.holdout], capture_output=True, text=True)
        if r.returncode: raise RuntimeError("the real fixture generator failed: " + r.stderr[-500:])
        cls.saved_authority = dict(SJ.AUTHORITY); SJ.AUTHORITY.update({"fixture_hashes.sha256": cls.hashes, "generated_artifacts.sha256": cls.pin})

    @classmethod
    def tearDownClass(cls):
        SJ.AUTHORITY.clear(); SJ.AUTHORITY.update(cls.saved_authority); shutil.rmtree(cls.tmp, True)

    @classmethod
    def new_job(cls, word):
        d = tempfile.mkdtemp(prefix="r4job-", dir=cls.tmp); os.makedirs(os.path.join(d, "logs"))
        with open(os.path.join(d, "STATUS.txt"), "w") as fh: fh.write(f"2026-10-13T00:00:00 START\n2026-10-13T00:01:00 {word} harness_rc=0 gate_rc=0\n")
        with open(os.path.join(d, "env.txt"), "w") as fh: fh.write("env\n")
        shutil.copy(cls.hashes, os.path.join(d, "fixture_hashes.sha256")); shutil.copy(cls.pin, os.path.join(d, "generated_artifacts.sha256"))
        return (d,)

    @contextlib.contextmanager
    def holdout_in(self, job):
        """Move the one generated directory into a job (instant rename), then back, whatever happens."""
        os.rename(self.holdout, os.path.join(job, "holdout"))
        try: yield os.path.join(job, "holdout")
        finally: os.rename(os.path.join(job, "holdout"), self.holdout)

    def verify_cli(self, job, *extra):
        return subprocess.run([sys.executable, os.path.join(JOBDIR, "verify_job.py"), job, "--require-fixtures", "--fixture-list", self.hashes, *extra], capture_output=True, text=True)

    def packaged_verify(self, job):
        return subprocess.run([sys.executable, os.path.join(JOBDIR, "seal_job.py"), job, "--verify", "--require-fixtures"], capture_output=True, text=True)

    # ---- generation
    def test_01_the_real_generator_makes_exactly_36_fixtures_plus_the_index(self):
        names = sorted(os.listdir(self.holdout))
        self.assertEqual(len(names), 37); self.assertEqual(len([n for n in names if n.endswith(".npy")]), 36); self.assertIn("index.json", names)

    def test_02_the_existing_36_fixture_hashes_are_unchanged_and_all_match(self):
        with open(self.hashes, "rb") as fh: raw = fh.read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), self.FROZEN_LIST_SHA256); self.assertEqual(len(raw.decode().splitlines()), 36)
        want, probs = SJ.parse_list(self.hashes); self.assertEqual(probs, [])
        for n, d in want.items(): self.assertEqual(SJ.digest(os.path.join(self.holdout, n)), d, n)

    def test_03_the_generated_index_equals_the_frozen_digest_and_the_frozen_committed_index(self):
        want, probs = SJ.parse_list(self.pin); self.assertEqual((want, probs), ({"index.json": self.PINNED_INDEX_SHA256}, []))
        self.assertEqual(SJ.digest(os.path.join(self.holdout, "index.json")), self.PINNED_INDEX_SHA256)
        with open(os.path.join(self.holdout, "index.json"), "rb") as a, open(self.frozen_index, "rb") as b: self.assertEqual(a.read(), b.read())

    def test_04_the_index_accurately_describes_the_generated_fixtures(self):
        with open(os.path.join(self.holdout, "index.json")) as fh: ix = json.load(fh)
        want, _ = SJ.parse_list(self.hashes)
        self.assertEqual(SJ.index_problems(ix, set(want)), [])
        self.assertEqual((len(ix["family_a"]), len(ix["family_b"])), (20, 16))
        self.assertTrue(all(n.startswith("HA_") for n in (e["name"] for e in ix["family_a"]))); self.assertTrue(all(n.startswith("HB_") for n in (e["name"] for e in ix["family_b"])))
        gone = {"family_a": ix["family_a"][1:], "family_b": ix["family_b"]}
        self.assertEqual(SJ.index_problems(gone, set(want)), [f"index does not describe fixture {ix['family_a'][0]['name']}.npy"])

    # ---- sealing and independent verification of the unmodified directory, both terminal paths
    def test_05_an_unmodified_generated_directory_seals_and_verifies_independently_for_DONE(self):
        (job,) = self.new_job("DONE")
        with self.holdout_in(job):
            self.assertEqual(SJ.check_fixtures(job), ("VERIFIED_AGAINST_SEALED_HASH_LIST", []))
            SJ.seal(job, require_fixtures=True); self.assertEqual(SJ.verify(job), [])
            r = self.verify_cli(job); self.assertEqual(r.returncode, 0, r.stdout); self.assertIn("INDEPENDENT_VERIFICATION: PASS", r.stdout)
            self.assertIn("37 expected files", r.stdout); self.assertIn("37/37 match", r.stdout); self.assertIn("TERMINAL_STATUS: DONE", r.stdout); self.assertIn("JOB_ACCEPTED: YES", r.stdout)
            self.assertEqual(self.packaged_verify(job).returncode, 0)

    def test_06_a_FAILED_terminal_record_seals_and_verifies_but_is_never_accepted(self):
        (job,) = self.new_job("FAILED")
        with self.holdout_in(job):
            SJ.seal(job, require_fixtures=True)
            r = self.verify_cli(job); self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("INDEPENDENT_VERIFICATION: PASS", r.stdout); self.assertIn("TERMINAL_STATUS: FAILED", r.stdout); self.assertIn("JOB_ACCEPTED: NO", r.stdout)

    # ---- refusals: before sealing (sealer) and after sealing (both verifiers)
    def refused(self, mutate, restore, expect):
        (pre,) = self.new_job("DONE"); (post,) = self.new_job("DONE")
        with self.holdout_in(post): SJ.seal(post, require_fixtures=True)              # sealed from a clean directory
        for job, sealed in ((pre, False), (post, True)):
            with self.holdout_in(job) as h:
                mutate(h)
                try:
                    if sealed:
                        r = self.verify_cli(job); self.assertNotEqual(r.returncode, 0, r.stdout); self.assertIn("INDEPENDENT_VERIFICATION: FAIL", r.stdout); self.assertIn("JOB_ACCEPTED: NO", r.stdout)
                        self.assertIn(expect, r.stdout); self.assertNotEqual(self.packaged_verify(job).returncode, 0)
                    else:
                        state, problems = SJ.check_fixtures(job); self.assertEqual(state, "FAILED"); self.assertTrue(any(expect in p for p in problems), problems)
                        with self.assertRaises(SystemExit): SJ.seal(job, require_fixtures=True)
                        self.assertFalse(os.path.exists(os.path.join(job, "JOB_SHA256SUMS")))
                finally:
                    restore(h)
        self.assertEqual(SJ.digest(os.path.join(self.holdout, "index.json")), self.PINNED_INDEX_SHA256)   # the shared directory was restored

    def test_07_missing_index_is_refused(self):
        p = lambda h: os.path.join(h, "index.json")
        self.refused(lambda h: os.rename(p(h), p(h) + ".away"), lambda h: os.rename(p(h) + ".away", p(h)), "index.json")

    def test_08_modified_index_is_refused(self):
        p = lambda h: os.path.join(h, "index.json"); orig = {}
        def mutate(h):
            orig["b"] = open(p(h), "rb").read(); open(p(h), "wb").write(orig["b"].replace(b'"seed": 1000', b'"seed": 1001', 1))
        self.refused(mutate, lambda h: open(p(h), "wb").write(orig["b"]), "index.json")

    def test_09_unexpected_extra_file_is_refused(self):
        p = lambda h: os.path.join(h, "stray.txt")
        self.refused(lambda h: open(p(h), "w").write("x"), lambda h: os.remove(p(h)), "stray.txt")

    def test_10_a_modified_npy_after_sealing_is_refused(self):
        name = sorted(n for n in os.listdir(self.holdout) if n.endswith(".npy"))[17]; p = lambda h: os.path.join(h, name)
        def flip(h):
            with open(p(h), "r+b") as fh: fh.seek(123457); b = fh.read(1); fh.seek(123457); fh.write(bytes([b[0] ^ 1]))
        self.refused(flip, flip, name)

    def test_11_the_runner_reports_three_separate_dispositions(self):
        text = open(os.path.join(JOBDIR, "run_job.sh")).read()
        for word in ("COMPUTATION_COMPLETED", "EVIDENCE_SEALED", "JOB_ACCEPTED"): self.assertIn(word, text)


class RunnerBehaviour(unittest.TestCase):
    """Run the REAL run_job.sh with a stub interpreter: every outcome of the full-run stage is exercised, not just inspected."""
    STUB = r"""#!/usr/bin/env bash
case "$1" in
  -c) echo "3.12.0 numpy 2.5.0";;
  -m) echo "Ran ${FAKE_TESTS:-7} tests in 0.1s" >&2; echo OK >&2; exit ${FAKE_CONTROLS_RC:-0};;
  fixtures/generate_holdout.py) while [ $# -gt 0 ]; do [ "$1" = "--outdir" ] && out="$2"; shift; done
      mkdir -p "$out"; for i in 01 02 03; do echo "fixture $i" > "$out/fx$i.npy"; done
      printf '%s\n' '{"family_a": [{"name": "fx01", "seed": 1}, {"name": "fx02", "seed": 2}], "family_b": [{"name": "fx03", "seed": 3}]}' > "$out/index.json"; exit ${FAKE_GEN_RC:-0};;
  tools/rf_metamorphic_harness.py) while [ $# -gt 0 ]; do [ "$1" = "--results-dir" ] && d="$2"; shift; done
      mkdir -p "$d"; case "$d" in *smoke*) exit ${FAKE_SMOKE_RC:-0};; *) exit ${FAKE_FULL_RC:-0};; esac;;
  tools/r4_job/gate_results.py) case "$2" in *smoke*) echo "GATE smoke"; exit ${FAKE_SMOKE_GATE_RC:-0};; *) echo "GATE full"; exit ${FAKE_FULL_GATE_RC:-0};; esac;;
  tools/r4_job/seal_job.py|tools/r4_job/verify_job.py) exec python3 "$@";;
esac
"""

    def setUp(self):
        saved = dict(SJ.AUTHORITY); SJ.AUTHORITY.clear()                      # each synthetic package carries its own pins (the subprocess sealer uses them as its authority)
        self.addCleanup(lambda: (SJ.AUTHORITY.clear(), SJ.AUTHORITY.update(saved)))

    def package(self):
        d = tempfile.mkdtemp(prefix="r4pkg-"); self.addCleanup(shutil.rmtree, d, True)
        import hashlib
        for sub in ("tools/r4_job", "fixtures"): os.makedirs(os.path.join(d, sub))
        shutil.copy(os.path.join(JOBDIR, "run_job.sh"), os.path.join(d, "tools/r4_job/run_job.sh"))
        for tool in ("seal_job.py", "verify_job.py"): shutil.copy(os.path.join(JOBDIR, tool), os.path.join(d, "tools/r4_job", tool))
        index = (b'{"family_a": [{"name": "fx01", "seed": 1}, {"name": "fx02", "seed": 2}], "family_b": [{"name": "fx03", "seed": 3}]}\n')
        with open(os.path.join(d, "tools/r4_job/generated_artifacts.sha256"), "w") as fh: fh.write(f"{hashlib.sha256(index).hexdigest()}  index.json\n")
        with open(os.path.join(d, "fixtures/synth_521_holdout_frozen.json"), "w") as fh: fh.write("{}\n")
        with open(os.path.join(d, "fixtures/fixture_hashes.sha256"), "w") as fh:
            for i in ("01", "02", "03"): fh.write(f"{hashlib.sha256(f'fixture {i}\n'.encode()).hexdigest()}  fx{i}.npy\n")
        with open(os.path.join(d, "SHA256SUMS"), "w") as fh:
            fh.write(f"{hashlib.sha256(b'{}\n').hexdigest()}  fixtures/synth_521_holdout_frozen.json\n")
        stub = os.path.join(d, "fakepy")
        with open(stub, "w") as fh: fh.write(self.STUB)
        os.chmod(stub, 0o755)
        return d, stub

    def run_job(self, **env):
        pkg, stub = self.package(); job = os.path.join(pkg, "job")
        e = dict(os.environ, PYTHON=stub, **{k: str(v) for k, v in env.items()})
        r = subprocess.run(["bash", os.path.join(pkg, "tools/r4_job/run_job.sh"), job], capture_output=True, text=True, env=e)
        def slurp(name):
            path = os.path.join(job, name)
            if not os.path.exists(path): return ""
            with open(path) as fh: return fh.read()
        return r, job, slurp("STATUS.txt").splitlines(), slurp("exit_codes.txt")

    def assertSealed(self, job):
        self.assertTrue(os.path.exists(os.path.join(job, "JOB_SHA256SUMS")), "not sealed")
        self.assertEqual(SJ.verify(job), [])
        self.assertEqual(SJ.check_fixtures(job)[0], "VERIFIED_AGAINST_SEALED_HASH_LIST")

    def disposition(self, job):
        with open(job + ".disposition.txt") as fh: text = fh.read().split("--- sealer")[0]
        return {k: v for k, v in (l.split(": ", 1) for l in text.splitlines() if re.match(r"(COMPUTATION_COMPLETED|EVIDENCE_SEALED|JOB_ACCEPTED): ", l))}

    def test_success_is_DONE_and_sealed_and_exits_zero(self):
        r, job, status, codes = self.run_job()
        self.assertEqual(r.returncode, 0, r.stderr); self.assertRegex(status[-1], r" DONE COMPUTATION_COMPLETED harness_rc=0 gate_rc=0")
        self.assertEqual(self.disposition(job), {"COMPUTATION_COMPLETED": "YES", "EVIDENCE_SEALED": "YES", "JOB_ACCEPTED": "YES"})
        self.assertFalse(os.path.exists(os.path.join(job, "disposition.txt")))                  # the report lives outside the sealed directory
        self.assertIn("full exit=0", codes); self.assertIn("full gate exit=0", codes); self.assertSealed(job)
        self.assertTrue(any("Ran 7 tests" in l for l in status))
        self.assertTrue(any("3/3 hashes verified" in l for l in status))

    def test_a_failed_full_run_is_FAILED_not_DONE_with_both_codes_preserved_and_still_sealed(self):
        r, job, status, codes = self.run_job(FAKE_FULL_RC=1)
        self.assertNotEqual(r.returncode, 0); self.assertRegex(status[-1], r" FAILED harness_rc=1 gate_rc=0")
        self.assertEqual(self.disposition(job), {"COMPUTATION_COMPLETED": "NO", "EVIDENCE_SEALED": "YES", "JOB_ACCEPTED": "NO"})
        self.assertFalse(any(" DONE " in l for l in status)); self.assertIn("full exit=1", codes); self.assertIn("full gate exit=0", codes); self.assertSealed(job)

    def test_a_failed_full_gate_is_FAILED(self):
        r, job, status, codes = self.run_job(FAKE_FULL_GATE_RC=1)
        self.assertNotEqual(r.returncode, 0); self.assertRegex(status[-1], r" FAILED harness_rc=0 gate_rc=1"); self.assertSealed(job)

    def test_both_failing_preserves_both_codes(self):
        r, job, status, codes = self.run_job(FAKE_FULL_RC=2, FAKE_FULL_GATE_RC=3)
        self.assertRegex(status[-1], r" FAILED harness_rc=2 gate_rc=3"); self.assertIn("full exit=2", codes); self.assertIn("full gate exit=3", codes); self.assertSealed(job)

    def test_a_failed_smoke_stops_before_the_full_run_and_seals_a_terminal_record(self):
        r, job, status, codes = self.run_job(FAKE_SMOKE_RC=1)
        self.assertNotEqual(r.returncode, 0); self.assertRegex(status[-1], r" FAILED smoke run"); self.assertFalse(os.path.exists(os.path.join(job, "results_full"))); self.assertSealed(job)

    def test_a_failed_smoke_gate_stops_before_the_full_run(self):
        r, job, status, codes = self.run_job(FAKE_SMOKE_GATE_RC=1)
        self.assertRegex(status[-1], r" FAILED smoke gate"); self.assertFalse(os.path.exists(os.path.join(job, "results_full"))); self.assertSealed(job)

    def test_failed_bounded_controls_seal_a_terminal_record(self):
        r, job, status, codes = self.run_job(FAKE_CONTROLS_RC=1)
        self.assertRegex(status[-1], r" FAILED bounded controls"); self.assertTrue(os.path.exists(os.path.join(job, "JOB_SHA256SUMS")))
        self.assertEqual(self.disposition(job), {"COMPUTATION_COMPLETED": "NO", "EVIDENCE_SEALED": "YES", "JOB_ACCEPTED": "NO"})

    def test_a_computation_that_completed_but_cannot_be_sealed_is_DONE_yet_not_accepted(self):
        """The Gate 3 failure shape: the harness and gate pass, then custody refuses (here: an unpinned file appears in holdout/)."""
        pkg, stub = self.package(); job = os.path.join(pkg, "job")
        with open(stub) as fh: body = fh.read()
        with open(stub, "w") as fh: fh.write(body.replace('> "$out/index.json"; exit', '> "$out/index.json"; echo extra > "$out/unpinned.txt"; exit'))
        r = subprocess.run(["bash", os.path.join(pkg, "tools/r4_job/run_job.sh"), job], capture_output=True, text=True, env=dict(os.environ, PYTHON=stub))
        self.assertEqual(r.returncode, 20)
        status = open(os.path.join(job, "STATUS.txt")).read().splitlines()
        self.assertRegex(status[-1], r" DONE COMPUTATION_COMPLETED harness_rc=0 gate_rc=0")
        self.assertEqual(self.disposition(job), {"COMPUTATION_COMPLETED": "YES", "EVIDENCE_SEALED": "NO", "JOB_ACCEPTED": "NO"})
        self.assertFalse(os.path.exists(os.path.join(job, "JOB_SHA256SUMS")))


if __name__ == "__main__":
    unittest.main()
