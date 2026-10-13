"""Evidence-custody controls for the r4 job runner: terminal status first, manifest sealed LAST, nothing appended afterwards."""
import importlib.util, os, re, shutil, subprocess, sys, tempfile, unittest

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
        seal = [i for i in self.idx(r"seal_job\.py") if i > max(terminal)]
        self.assertEqual(len(self.idx(r'st "DONE')), 1); self.assertEqual(len(self.idx(r'st "FAILED harness_rc')), 1); self.assertTrue(seal)
        self.assertLess(max(terminal), seal[0])
        for line in SCRIPT[seal[0] + 1:]:
            self.assertNotRegex(line, r'st "|STATUS\.txt|exit_codes\.txt|>>', f"writes to the job directory after sealing: {line!r}")

    def test_done_is_conditional_on_both_return_codes(self):
        i = self.idx(r'st "DONE')[0]
        self.assertRegex(SCRIPT[i - 1], r'"\$rc" -eq 0 \] && \[ "\$grc" -eq 0 \]; then')
        self.assertRegex(SCRIPT[i + 1], r'else'); self.assertIn("FAILED harness_rc=$rc gate_rc=$grc", SCRIPT[i + 2])

    def test_exit_codes_are_recorded_before_sealing(self):
        seal_last = max(self.idx(r"seal_job\.py")); codes = self.idx(r'exit_codes\.txt')
        self.assertTrue(codes); self.assertTrue(all(c < seal_last for c in codes))

    def test_the_counts_are_not_hard_coded(self):
        text = "\n".join(SCRIPT)
        self.assertNotIn("12 bounded controls", text); self.assertNotIn("/36 hashes", text)
        self.assertRegex(text, r'bounded controls \(\$\(grep -E')

    def test_every_failure_path_still_seals_a_terminal_record(self):
        fail_fn = [l for l in SCRIPT if l.startswith("fail()")][0]
        self.assertIn('st "FAILED', fail_fn); self.assertLess(fail_fn.index('st "FAILED'), fail_fn.index("seal_job.py"))


class FixtureCustody(unittest.TestCase):
    """The fixture bytes are not in JOB_SHA256SUMS; their hash LIST is, and the bytes are re-verified at seal time and at independent verification."""
    FILES = {"fx01.npy": b"fixture 01\n", "fx02.npy": b"fixture 02\n", "fx03.npy": b"fixture 03\n"}

    def job(self, with_holdout=True):
        d = make_job(); self.addCleanup(shutil.rmtree, d, True)
        import hashlib
        with open(os.path.join(d, "fixture_hashes.sha256"), "w") as fh:
            for n, b in self.FILES.items(): fh.write(f"{hashlib.sha256(b).hexdigest()}  {n}\n")
        if with_holdout:
            os.makedirs(os.path.join(d, "holdout"))
            for n, b in self.FILES.items():
                with open(os.path.join(d, "holdout", n), "wb") as fh: fh.write(b)
        return d

    def test_the_fixture_list_is_sealed_and_the_bytes_are_verified_against_it(self):
        d = self.job(); SJ.seal(d, require_fixtures=True)
        with open(os.path.join(d, "JOB_SHA256SUMS")) as fh: m = fh.read()
        self.assertIn("fixture_hashes.sha256", m); self.assertNotIn("holdout/", m)
        self.assertEqual(SJ.verify(d), []); self.assertEqual(SJ.check_fixtures(d)[0], "VERIFIED_AGAINST_SEALED_HASH_LIST")

    def test_a_fixture_changed_after_sealing_cannot_pass_independent_verification(self):
        d = self.job(); SJ.seal(d, require_fixtures=True)
        with open(os.path.join(d, "holdout", "fx02.npy"), "ab") as fh: fh.write(b"x")
        state, problems = SJ.check_fixtures(d)
        self.assertEqual(state, "FAILED"); self.assertIn("fixture mismatch fx02.npy", problems)
        self.assertEqual(SJ.verify(d), [])                                       # the manifest alone cannot see it: that is why the re-check exists
        r = subprocess.run([sys.executable, os.path.join(JOBDIR, "seal_job.py"), d, "--verify", "--require-fixtures"], capture_output=True, text=True)
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
        base = [sys.executable, os.path.join(JOBDIR, "seal_job.py"), d, "--verify"]
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


class RunnerBehaviour(unittest.TestCase):
    """Run the REAL run_job.sh with a stub interpreter: every outcome of the full-run stage is exercised, not just inspected."""
    STUB = r"""#!/usr/bin/env bash
case "$1" in
  -c) echo "3.12.0 numpy 2.5.0";;
  -m) echo "Ran ${FAKE_TESTS:-7} tests in 0.1s" >&2; echo OK >&2; exit ${FAKE_CONTROLS_RC:-0};;
  fixtures/generate_holdout.py) while [ $# -gt 0 ]; do [ "$1" = "--outdir" ] && out="$2"; shift; done
      mkdir -p "$out"; for i in 01 02 03; do echo "fixture $i" > "$out/fx$i.npy"; done; exit ${FAKE_GEN_RC:-0};;
  tools/rf_metamorphic_harness.py) while [ $# -gt 0 ]; do [ "$1" = "--results-dir" ] && d="$2"; shift; done
      mkdir -p "$d"; case "$d" in *smoke*) exit ${FAKE_SMOKE_RC:-0};; *) exit ${FAKE_FULL_RC:-0};; esac;;
  tools/r4_job/gate_results.py) case "$2" in *smoke*) echo "GATE smoke"; exit ${FAKE_SMOKE_GATE_RC:-0};; *) echo "GATE full"; exit ${FAKE_FULL_GATE_RC:-0};; esac;;
  tools/r4_job/seal_job.py) exec python3 "$@";;
esac
"""

    def package(self):
        d = tempfile.mkdtemp(prefix="r4pkg-"); self.addCleanup(shutil.rmtree, d, True)
        import hashlib
        for sub in ("tools/r4_job", "fixtures"): os.makedirs(os.path.join(d, sub))
        shutil.copy(os.path.join(JOBDIR, "run_job.sh"), os.path.join(d, "tools/r4_job/run_job.sh"))
        shutil.copy(os.path.join(JOBDIR, "seal_job.py"), os.path.join(d, "tools/r4_job/seal_job.py"))
        with open(os.path.join(d, "fixtures/synth_521_holdout_frozen.json"), "w") as fh: fh.write("{}\n")
        with open(os.path.join(d, "fixtures/fixture_hashes.sha256"), "w") as fh:
            for i in ("01", "02", "03"): fh.write(f"{hashlib.sha256(f'fixture {i}\n'.encode()).hexdigest()}  fx{i}.npy\n")
        with open(os.path.join(d, "SHA256SUMS"), "w") as fh:
            fh.write(f"{hashlib.sha256(b'{}\n').hexdigest()}  fixtures/synth_521_holdout_frozen.json\n")
        stub = os.path.join(d, "fakepy"); 
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

    def test_success_is_DONE_and_sealed_and_exits_zero(self):
        r, job, status, codes = self.run_job()
        self.assertEqual(r.returncode, 0, r.stderr); self.assertRegex(status[-1], r" DONE harness_rc=0 gate_rc=0")
        self.assertIn("full exit=0", codes); self.assertIn("full gate exit=0", codes); self.assertSealed(job)
        self.assertTrue(any("Ran 7 tests" in l for l in status))
        self.assertTrue(any("3/3 hashes verified" in l for l in status))

    def test_a_failed_full_run_is_FAILED_not_DONE_with_both_codes_preserved_and_still_sealed(self):
        r, job, status, codes = self.run_job(FAKE_FULL_RC=1)
        self.assertNotEqual(r.returncode, 0); self.assertRegex(status[-1], r" FAILED harness_rc=1 gate_rc=0")
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

    def test_a_fixture_corrupted_by_generation_is_not_sealed(self):
        r, job, status, codes = self.run_job(FAKE_GEN_RC=1)
        self.assertRegex(status[-1], r" FAILED fixture generation")


if __name__ == "__main__":
    unittest.main()
