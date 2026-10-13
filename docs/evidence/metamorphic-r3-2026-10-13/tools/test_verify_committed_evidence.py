#!/usr/bin/env python3
"""Tamper tests for verify_committed_evidence.py: the verifier must REJECT undeclared alterations.
Runs against a temporary copy of the committed evidence directory; the evidence itself is never modified.
    python3 tools/test_verify_committed_evidence.py
"""
import os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); EV = os.path.dirname(HERE)
VER = os.path.join(HERE, "verify_committed_evidence.py")


def run(ev, *extra):
    r = subprocess.run([sys.executable, VER, ev, *extra], capture_output=True, text=True)
    return r.returncode, r.stdout


class Tamper(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.ev = os.path.join(self.d, "ev"); shutil.copytree(EV, self.ev)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def append(self, rel, text=" "):
        with open(os.path.join(self.ev, rel), "a") as fh: fh.write(text)

    def test_untouched_copy_verifies_and_does_not_claim_the_originals(self):
        rc, out = run(self.ev)
        self.assertEqual(rc, 0, out)
        self.assertIn("COMMITTED_EVIDENCE_INTEGRITY: VERIFIED", out)
        self.assertIn("CONSISTENT", out)
        self.assertIn("ORIGINAL_JOB_INTEGRITY: NOT_REVERIFIED_FROM_THIS_CHECKOUT", out)
        self.assertNotIn("VERIFIED_FROM_RETAINED_ORIGINALS", out)

    def test_altering_an_unredacted_result_is_rejected(self):
        self.append("dedirock/results_full/phaseA/HA_s+f0.05a0.075.npy.json"); rc, out = run(self.ev)
        self.assertNotEqual(rc, 0); self.assertIn("outer mismatch", out)

    def test_altering_a_redacted_file_is_rejected(self):
        self.append("local/results_full/run_header.json"); rc, out = run(self.ev)
        self.assertNotEqual(rc, 0)

    def test_adding_an_undeclared_file_is_rejected(self):
        open(os.path.join(self.ev, "dedirock", "extra.json"), "w").write("{}"); rc, out = run(self.ev)
        self.assertNotEqual(rc, 0); self.assertIn("undeclared file dedirock/extra.json", out)

    def test_removing_a_file_is_rejected(self):
        os.remove(os.path.join(self.ev, "local", "results_full", "summary.json")); rc, out = run(self.ev)
        self.assertNotEqual(rc, 0)

    def test_an_undeclared_inner_manifest_mismatch_is_rejected_even_if_the_outer_manifest_is_refreshed(self):
        # change a result AND recompute the outer manifest: only the inner (original) manifest can still object
        self.append("dedirock/results_full/identity.json")
        import hashlib
        lines = []
        for line in open(os.path.join(self.ev, "SHA256SUMS")):
            d, rel = line.split(None, 1); rel = rel.strip()
            p = os.path.join(self.ev, rel); lines.append(f"{hashlib.sha256(open(p, 'rb').read()).hexdigest()}  {rel}\n")
        open(os.path.join(self.ev, "SHA256SUMS"), "w").writelines(lines)
        rc, out = run(self.ev)
        self.assertNotEqual(rc, 0); self.assertIn("UNDECLARED mismatch", out); self.assertIn("INCONSISTENT", out)

    def test_a_wrong_recorded_original_digest_is_rejected(self):
        p = os.path.join(self.ev, "REDACTIONS.txt"); t = open(p).read().splitlines(); out = []
        for l in t:
            f = l.split()
            if len(f) == 3 and f[2] == "dedirock/results_full/run_header.json": f[0] = "0" * 64; l = "  ".join(f)
            out.append(l)
        open(p, "w").write("\n".join(out) + "\n")
        import hashlib   # keep the outer manifest honest so only the redaction record is wrong
        lines = []
        for line in open(os.path.join(self.ev, "SHA256SUMS")):
            d, rel = line.split(None, 1); rel = rel.strip()
            lines.append(f"{hashlib.sha256(open(os.path.join(self.ev, rel), 'rb').read()).hexdigest()}  {rel}\n")
        open(os.path.join(self.ev, "SHA256SUMS"), "w").writelines(lines)
        rc, o = run(self.ev); self.assertNotEqual(rc, 0); self.assertIn("recorded ORIGINAL digest", o)

    def test_a_declared_redaction_that_changed_nothing_is_rejected(self):
        p = os.path.join(self.ev, "REDACTIONS.txt"); t = open(p).read().splitlines()
        t = [(" ".join([l.split()[1], l.split()[1], l.split()[2]]) if len(l.split()) == 3 and l.split()[2] == "local/STATUS.txt" else l) for l in t]
        open(p, "w").write("\n".join(t) + "\n"); rc, o = run(self.ev); self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
