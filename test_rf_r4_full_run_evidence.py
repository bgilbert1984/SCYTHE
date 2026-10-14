"""Controls for the committed r4 full-run evidence: a pristine git archive verifies, every kind of tampering is refused, and the published counts are recomputable."""
import hashlib, os, pathlib, re, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); EV = "docs/evidence/r4_full_local_2026-10-13"; DOC = "docs/research/metamorphic_r4_full_local_results.md"
TOOL = os.path.join(HERE, "tools", "r4_package", "verify_full_run_evidence.py")
sha = lambda b: hashlib.sha256(b).hexdigest()
def run(*a): return subprocess.run(list(a), capture_output=True, text=True)


class FullRunEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="r4fullev-"); cls.n = 0
        a = subprocess.run(["git", "-C", HERE, "archive", "--format=tar", "HEAD", EV, DOC], capture_output=True)
        if a.returncode: raise unittest.SkipTest("not a git checkout")
        cls.pristine = os.path.join(cls.tmp, "pristine"); os.makedirs(cls.pristine); subprocess.run(["tar", "-x", "-C", cls.pristine], input=a.stdout, check=True)

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.tmp, True)

    def fresh(self):
        type(self).n += 1; d = os.path.join(self.tmp, f"c{self.n}"); shutil.copytree(self.pristine, d); return d

    def check(self, d, *extra): return run(sys.executable, TOOL, os.path.join(d, EV), os.path.join(d, DOC), *extra)
    def edit(self, d, rel, fn):
        p = pathlib.Path(d, rel); p.write_bytes(fn(p.read_bytes()))
    def read(self, d, rel): return pathlib.Path(d, rel).read_bytes()

    def test_a_pristine_git_archive_verifies_with_three_layers_reported_separately(self):
        r = self.check(self.fresh()); self.assertEqual(r.returncode, 0, r.stdout)
        for s in ("COMMITTED_EVIDENCE_INTEGRITY: VERIFIED", "ORIGINAL_MANIFEST_ENTRIES_VS_RECORDED_ORIGINAL_DIGESTS: CONSISTENT", "ORIGINAL_JOB_INTEGRITY: NOT_REVERIFIED_FROM_THIS_CHECKOUT", "EVIDENCE_VERIFICATION: PASS"): self.assertIn(s, r.stdout)
        self.assertIn("REDACTION_TRANSFORM_REVERSAL: NOT_EVALUATED", r.stdout)

    def refused(self, mutate, expect):
        d = self.fresh(); mutate(d); r = self.check(d)
        self.assertEqual(r.returncode, 1, r.stdout); self.assertIn("EVIDENCE_VERIFICATION: FAIL", r.stdout); self.assertIn(expect, r.stdout)

    def forge_manifest(self, d, fn):
        """Edit the redaction manifest AND its anchor so only the cross-checks can object."""
        man = f"{EV}/REDACTION_MANIFEST.txt"; old = sha(self.read(self.pristine, man)).encode()
        self.edit(d, man, fn); self.edit(d, DOC, lambda b: b.replace(old, sha(self.read(d, man)).encode()))

    def test_undeclared_changes_additions_and_removals_are_refused(self):
        self.refused(lambda d: self.edit(d, f"{EV}/job/results_full/summary.json", lambda b: b + b" "), "differs from its recorded committed digest")
        self.refused(lambda d: pathlib.Path(d, EV, "job", "extra.txt").write_text("x"), "undeclared evidence file added")
        self.refused(lambda d: os.remove(os.path.join(d, EV, "job/STATUS.txt")), "declared evidence file missing")

    def test_a_changed_original_manifest_is_refused(self):
        self.refused(lambda d: self.edit(d, f"{EV}/job/JOB_SHA256SUMS", lambda b: b.replace(b"0", b"1", 1)), "differs from its recorded committed digest")

    def test_an_altered_redaction_declaration_is_refused(self):
        man = f"{EV}/REDACTION_MANIFEST.txt"
        self.refused(lambda d: self.edit(d, man, lambda b: b.replace(b" | 1\n", b" | 0\n", 1)), "does not match the digest anchored")
        self.refused(lambda d: self.forge_manifest(d, lambda b: b.replace(b" | 1\n", b" | 0\n", 1)), "redaction declaration inconsistent")

    def test_an_incorrect_original_digest_is_refused_even_with_a_consistent_anchor(self):
        def forge(d):
            def fn(b):
                lines = b.decode().split("\n"); i = next(i for i, l in enumerate(lines) if l.startswith("job/STATUS.txt"))
                parts = lines[i].split(" | "); parts[1] = "0" * 64; lines[i] = " | ".join(parts); return "\n".join(lines).encode()
            self.forge_manifest(d, fn)
        self.refused(forge, "differs from the one recorded by")

    def test_a_job_file_not_listed_by_the_job_manifest_is_refused(self):
        def forge(d):
            p = pathlib.Path(d, EV, "job", "added.txt"); p.write_text("x"); row = f"job/added.txt | {sha(b'x')} | {sha(b'x')} | 0\n"
            self.forge_manifest(d, lambda b: b + row.encode())
        self.refused(forge, "job file not listed by the job manifest")

    def test_reversal_with_the_wrong_prefix_fails_and_a_crash_has_its_own_marker(self):
        r = self.check(self.fresh(), "--original-prefix", "/home/nobody"); self.assertIn("REDACTION_TRANSFORM_REVERSAL: FAILED", r.stdout); self.assertEqual(r.returncode, 1)
        r = run(sys.executable, TOOL, os.path.join(self.tmp, "nowhere"), os.path.join(self.tmp, "nodoc")); self.assertEqual(r.returncode, 2); self.assertIn("VERIFIER_CRASH", r.stdout)

    def test_the_published_counts_are_recomputable_from_the_committed_records(self):
        r = run(sys.executable, "-I", os.path.join(HERE, EV, "analysis", "summarize_counts.py"), os.path.join(self.pristine, EV, "job", "results_full"))
        self.assertEqual(r.returncode, 0, r.stderr); self.assertEqual(r.stdout, self.read(self.pristine, f"{EV}/analysis/COUNTS.txt").decode())
        for line in ("span_limit_removed | MUTANT_DETECTED: 216", "span_limit_widened | MUTANT_DETECTED: 144", "span_limit_strict | MUTANT_SURVIVED_EQUIVALENT_ON_GRID: 288", "HB | PRECONDITION_NOT_MET | COLLISION_WITH_OTHER_MAPPED_CANDIDATE: 128",
                     "span_limit_strict | MUTANT_DETECTED: 2"):
            self.assertIn(line, r.stdout)
        self.assertEqual(len(re.findall(r"RELATION_SATISFIED: 36\n", r.stdout)), 13)

    def test_the_results_document_carries_the_required_identifiers(self):
        doc = self.read(self.pristine, DOC).decode()
        for s in ("R4FULL-20261014T023003Z-2b2e3251b678-SCYTHE-30d8a2f0", "2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a", "8d34de9ac0b1f4758cda3412b364582b4271ffe1",
                  "8c515a32b1a5d3d807e53359901a4d09ec06819b488736641aabd6a12eefba63", "ff5f86a29971a1870d3040fbe03a8a3e4dfc4c63448b6848e853f1aadc2721d2", "abf1fb9a0368c542a69b590da601df88a6f981f2c7eb9b2e43c373cddf54b507",
                  "R4_1_SINGLE_HOST_SYNTHETIC_EXPERIMENT_VERIFIED", "PASS_OPERATOR_CONFIRMATION_PENDING"):
            self.assertIn(s, doc, s)
        self.assertEqual(sha(pathlib.Path(HERE, "tools/r4_package/verify_full_run_evidence.py").read_bytes()) in doc, True)
        self.assertEqual(sha(self.read(self.pristine, f"{EV}/job/JOB_SHA256SUMS")), "ff5f86a29971a1870d3040fbe03a8a3e4dfc4c63448b6848e853f1aadc2721d2")
        self.assertEqual(sha(self.read(self.pristine, f"{EV}/execution/EXEC_SHA256SUMS")), "abf1fb9a0368c542a69b590da601df88a6f981f2c7eb9b2e43c373cddf54b507")

    def test_the_authorization_discrepancy_is_recorded_without_being_resolved(self):
        g = self.read(self.pristine, f"{EV}/governance/LAUNCH_AUTHORIZATION_DISCREPANCY.md").decode()
        for s in ("USER_LAUNCH_AUTHORIZATION_REQUEST: PRESENT", "USER_REQUEST_TIMESTAMP: 2026-10-14T02:29:53.439Z", "OPERATOR_POWER_CONFIRMATION: RECORDED",
                  "ASSISTANT_LAUNCH_AUTHORIZATION: NOT_ESTABLISHED_IN_REVIEWED_RECORD", "GOVERNANCE_DISPOSITION: DISPUTED_AWAITING_RECONCILIATION_BY_THE_OWNER", "not itself an assistant-issued authorization", "dated addendum"):
            self.assertIn(s, g, s)
        self.assertNotRegex(g, r"GOVERNANCE_DISPOSITION: (AUTHORIZED|UNAUTHORIZED)")

    # ---- strict parsing of the retained original manifests, and explained omissions
    def forge_file(self, d, rel, fn):
        """Change a committed file AND its manifest row (both digests when unredacted) AND the anchor: only the verifier's own cross-checks can object."""
        full = f"{EV}/{rel}"; self.edit(d, full, fn); new = sha(self.read(d, full))
        def row(b):
            lines = b.decode().split("\n")
            for i, l in enumerate(lines):
                if l.startswith(rel + " | "): lines[i] = f"{rel} | {new} | {new} | 0"
            return "\n".join(lines).encode()
        self.forge_manifest(d, row)

    def test_a_malformed_line_in_a_retained_original_manifest_is_refused(self):
        self.refused(lambda d: self.forge_file(d, "execution/EXEC_SHA256SUMS", lambda b: b + b"not-a-digest  LAUNCH_RECORD.txt\n"), "malformed checksum line")
        self.refused(lambda d: self.forge_file(d, "execution/EXEC_SHA256SUMS", lambda b: b.replace(b"  ", b" ", 1)), "malformed checksum line")

    def test_a_duplicate_or_escaping_entry_in_a_retained_original_manifest_is_refused(self):
        self.refused(lambda d: self.forge_file(d, "execution/EXEC_SHA256SUMS", lambda b: b + b.split(b"\n")[0] + b"\n"), "duplicate checksum entry")
        self.refused(lambda d: self.forge_file(d, "execution/EXEC_SHA256SUMS", lambda b: b + b"0" * 64 + b"  ../job/STATUS.txt\n"), "malformed checksum line")

    def test_a_reference_that_is_neither_committed_nor_declared_omitted_is_refused(self):
        self.refused(lambda d: self.forge_file(d, "execution/EXEC_SHA256SUMS", lambda b: b + b"0" * 64 + b"  vanished_artifact.bin\n"), "unexplained reference")

    def test_declared_omissions_must_match_their_references_and_be_referenced(self):
        man = f"{EV}/REDACTION_MANIFEST.txt"
        self.refused(lambda d: self.forge_manifest(d, lambda b: b.replace(b"OMITTED | preflight/record_check_only_1/scythe_r4_1_pkg_8d34de9ac0b1.tar.gz | 2b2e", b"OMITTED | preflight/record_check_only_1/scythe_r4_1_pkg_8d34de9ac0b1.tar.gz | 0000", 1)), "records a different digest for the declared-omitted")
        self.refused(lambda d: self.forge_manifest(d, lambda b: b"\n".join(l for l in b.split(b"\n") if not l.startswith(b"OMITTED | preflight/record_check_only_1")) ), "unexplained reference")
        self.refused(lambda d: self.forge_manifest(d, lambda b: b.replace(b"OMITTED | ", b"OMITTED | preflight/never_referenced.bin | " + b"1" * 64 + b" | invented\nOMITTED | ", 1)), "is not referenced by any original manifest")

    def test_a_committed_file_that_no_original_manifest_lists_is_refused(self):
        def forge(d, where):
            p = pathlib.Path(d, EV, where); p.write_text("x")
            self.forge_manifest(d, lambda b: b + f"{where} | {sha(b'x')} | {sha(b'x')} | 0\n".encode())
        self.refused(lambda d: forge(d, "preflight/record_prelaunch_3/unlisted.txt"), "not listed by any original manifest")
        self.refused(lambda d: forge(d, "execution/unlisted.txt"), "not listed by any original manifest")
        self.refused(lambda d: forge(d, "operator/unlisted.txt"), "not listed by any original manifest")

    def test_the_declared_omissions_are_reported_and_original_integrity_stays_not_reverified(self):
        r = self.check(self.fresh()); self.assertIn("declared omissions referenced: 3/3", r.stdout); self.assertIn("pinned fixture corpus (37 files)", r.stdout)
        self.assertIn("ORIGINAL_JOB_INTEGRITY: NOT_REVERIFIED_FROM_THIS_CHECKOUT", r.stdout)
        d = self.fresh(); self.refused(lambda d: pathlib.Path(d, EV, "preflight/record_check_only_1/scythe_r4_1_pkg_8d34de9ac0b1.tar.gz").write_bytes(b"x"), "undeclared evidence file added")

    def test_the_pinned_fixture_corpus_must_be_exactly_37_distinct_entries(self):
        self.refused(lambda d: self.forge_file(d, "job/generated_artifacts.sha256", lambda b: b + b"1" * 64 + b"  extra.npy\n"), "not pinned by exactly 37")

    def test_the_fixture_corpus_is_not_committed_and_the_original_artifacts_are_preserved_unchanged(self):
        files = [os.path.join(r, f) for r, _, fs in os.walk(os.path.join(self.pristine, EV)) for f in fs]
        self.assertFalse([f for f in files if f.endswith(".npy")]); self.assertLess(sum(os.path.getsize(f) for f in files), 10_000_000)
        for rec in ("record_check_only_1", "record_check_only_2", "record_prelaunch_3"): self.assertTrue(os.path.isfile(os.path.join(self.pristine, EV, "preflight", rec, "PREFLIGHT_RECORD.txt")), rec)


if __name__ == "__main__":
    unittest.main()
