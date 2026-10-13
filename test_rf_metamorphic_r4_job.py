"""Evidence-custody controls for the r4 job runner: terminal status first, manifest sealed LAST, nothing appended afterwards."""
import importlib.util, os, re, shutil, tempfile, unittest

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
        done = self.idx(r'st "DONE'); seal = [i for i in self.idx(r"seal_job\.py") if i > max(done)]
        self.assertEqual(len(done), 1); self.assertTrue(seal)
        self.assertLess(done[0], seal[0])
        after = SCRIPT[seal[0] + 1:]
        for line in after:
            self.assertNotRegex(line, r'st "|STATUS\.txt|exit_codes\.txt|>>', f"writes to the job directory after sealing: {line!r}")

    def test_exit_codes_are_recorded_before_sealing(self):
        seal_last = max(self.idx(r"seal_job\.py")); codes = self.idx(r'exit_codes\.txt')
        self.assertTrue(codes); self.assertTrue(all(c < seal_last for c in codes))

    def test_the_control_count_is_not_hard_coded(self):
        text = "\n".join(SCRIPT)
        self.assertNotIn("12 bounded controls", text)
        self.assertRegex(text, r'bounded controls \(\$\(grep -E')

    def test_every_failure_path_still_seals_a_terminal_record(self):
        fail_fn = [l for l in SCRIPT if l.startswith("fail()")][0]
        self.assertIn('st "FAILED', fail_fn); self.assertLess(fail_fn.index('st "FAILED'), fail_fn.index("seal_job.py"))


if __name__ == "__main__":
    unittest.main()
