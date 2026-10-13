"""Controls for the reproducible r4 packaging tools (tools/r4_package/).

They build a real package from the commit under test (HEAD), verify it from an independently extracted clean archive, and check that damaged packages, crashes and
metadata tricks cannot pass. The historical r4.1 package 2b2e3251... is reproduced from its source commit when that commit is present in the clone.
"""
import ast, hashlib, json, os, shutil, subprocess, sys, tarfile, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); TOOLS = os.path.join(HERE, "tools", "r4_package")
EVID = os.path.join(HERE, "docs", "evidence"); PROV = os.path.join(EVID, "r4_1_packaging_provenance_2026-10-13"); G3 = os.path.join(EVID, "r4_1_gate3_2026-10-13")
HIST_SHA = "8d34de9ac0b1f4758cda3412b364582b4271ffe1"; HIST_ARCHIVE = "2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a"
sha = lambda b: hashlib.sha256(b).hexdigest()
def run(*a, **k): return subprocess.run(list(a), capture_output=True, text=True, **k)
def git(*a): return run("git", "-C", HERE, *a)


class PackagingTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="r4pkgtools-")
        r = git("rev-parse", "HEAD"); cls.full = r.stdout.strip()
        if r.returncode or len(cls.full) != 40: raise unittest.SkipTest("not a git checkout")
        cls.a, cls.b = os.path.join(cls.tmp, "build_a"), os.path.join(cls.tmp, "build_b")
        for out in (cls.a, cls.b):
            r = run(sys.executable, os.path.join(TOOLS, "build_package.py"), HERE, cls.full, out); assert r.returncode == 0, r.stderr
        cls.name = f"scythe_r4_1_pkg_{cls.full[:12]}.tar.gz"; cls.arc = os.path.join(cls.a, cls.name)
        cls.vout = run(sys.executable, os.path.join(TOOLS, "verify_package.py"), HERE, cls.full, cls.arc, os.path.join(cls.a, "extract_final"), "--python", sys.executable)
        cls._neg = None

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.tmp, True)

    @classmethod
    def neg(cls):
        if cls._neg is None:
            cls._neg = run(sys.executable, os.path.join(TOOLS, "neg_controls.py"), sys.executable, cls.full, HERE, cls.arc, os.path.join(cls.tmp, "negwork"))
        return cls._neg

    # ---- reproducibility
    def test_two_builds_from_the_same_commit_are_byte_identical(self):
        with open(self.arc, "rb") as x, open(os.path.join(self.b, self.name), "rb") as y: self.assertEqual(x.read(), y.read())

    def test_the_historical_r4_1_package_is_reproduced_exactly_when_its_commit_is_available(self):
        if git("cat-file", "-e", HIST_SHA + "^{commit}").returncode: self.skipTest("the historical source commit is not present in this (shallow?) clone; byte identity is therefore NOT claimed here")
        out = os.path.join(self.tmp, "hist"); r = run(sys.executable, os.path.join(TOOLS, "build_package.py"), HERE, HIST_SHA, out); self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(out, "scythe_r4_1_pkg_8d34de9ac0b1.tar.gz"), "rb") as fh: self.assertEqual(sha(fh.read()), HIST_ARCHIVE)

    def test_the_repository_builder_is_byte_identical_to_the_external_builder_that_made_2b2e3251(self):
        with open(os.path.join(TOOLS, "build_package.py"), "rb") as x, open(os.path.join(PROV, "external_tools", "build_package.py"), "rb") as y: a, b = x.read(), y.read()
        self.assertEqual(a, b); self.assertEqual(sha(a), "b78aee2ed803b357b8e091664b45584c7568e67053d9f1656d2f169f74af0ef8")

    # ---- verification from an independently extracted clean archive
    def test_verification_passes_from_an_independently_extracted_clean_archive(self):
        self.assertEqual(self.vout.returncode, 0, self.vout.stdout[-1500:])
        self.assertRegex(self.vout.stdout, r"VERIFICATION PASS \(\d+/\d+ checks\)"); self.assertIn("PASS 6 CLEAN ARCHIVE COMPARISON", self.vout.stdout)
        self.assertNotIn("FAIL ", self.vout.stdout)

    def test_the_source_closure_resolves_without_the_developers_checkout(self):
        root = os.path.join(self.a, "extract_final", "scythe_r4_1_pkg"); other = tempfile.mkdtemp(prefix="elsewhere-", dir=self.tmp)
        code = ("import sys, os, importlib; pkg=sys.argv[1]; sys.path[:0]=[pkg, os.path.join(pkg,'tools')]\n"
                "mods=['test_rf_metamorphic_controls','test_rf_metamorphic_r4_controls','test_rf_metamorphic_r4_job','test_rf_metamorphic_r4_production_path']\n"
                "[importlib.import_module(m) for m in mods]\n"
                "import rf_spur_catalogue_sequencer as s; print(os.path.abspath(s.__file__))")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code, root], cwd=other, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr[-800:]); self.assertTrue(r.stdout.strip().startswith(root + os.sep)); self.assertFalse(r.stdout.strip().startswith(HERE + os.sep))

    # ---- damaged packages, metadata tricks and crashes
    def test_damaged_packages_are_all_rejected_through_named_checks(self):
        r = self.neg(); self.assertEqual(r.returncode, 0, r.stdout[-2500:])
        rows = [l for l in r.stdout.splitlines() if "exit=" in l]; self.assertGreaterEqual(len(rows), 11)
        self.assertTrue(all("-> REJECTED" in l and "failed checks: []" not in l for l in rows), r.stdout)
        for kind in ("byte_tampered", "closure_module_removed", "undeclared_file", "absolute_path", "bytecode_cache", "credential_pattern", "verifier_removed", "wrong_index_pin", "sealer_byte_tampered", "index_pin_removed"):
            self.assertTrue(any(kind in l for l in rows), kind)

    def test_refreshed_manifests_cannot_conceal_a_source_difference(self):
        rows = {l.split()[0]: l for l in self.neg().stdout.splitlines() if "exit=" in l}
        for name in ("B_byte_tampered_manifests_refreshed", "J_sealer_byte_tampered_manifests_refreshed", "D_undeclared_file_manifests_refreshed"):
            self.assertIn("CLEAN ARCHIVE COMPARISON", rows[name]); self.assertNotIn("sha256sum -c", rows[name])        # the manifests were consistent; only the comparison with the commit caught it
        self.assertIn("sha256sum -c", rows["A_byte_tampered_manifests_untouched"])

    def test_a_verifier_crash_is_a_failure_never_a_rejection(self):
        r = run(sys.executable, os.path.join(TOOLS, "verify_package.py"), HERE, self.full, os.path.join(self.tmp, "no_such_archive.tar.gz"), os.path.join(self.tmp, "x1"))
        self.assertEqual(r.returncode, 2); self.assertIn("VERIFIER_CRASH", r.stdout); self.assertNotIn("VERIFICATION", r.stdout)
        for body, expect in (("raise RuntimeError('boom')", "CONTROL_FAILURE"), ("import sys; sys.exit(1)", "ACCEPTED (BAD)"), ("import sys; print('VERIFICATION FAIL'); sys.exit(1)", "ACCEPTED (BAD)")):
            fake = tempfile.mkdtemp(prefix="fakeverifier-", dir=self.tmp); shutil.copy(os.path.join(TOOLS, "neg_controls.py"), fake)
            with open(os.path.join(fake, "verify_package.py"), "w") as fh: fh.write(body + "\n")
            r = run(sys.executable, os.path.join(fake, "neg_controls.py"), sys.executable, self.full, HERE, self.arc, os.path.join(fake, "work"))
            self.assertNotEqual(r.returncode, 0, body); self.assertNotIn("-> REJECTED", r.stdout, body); self.assertIn(expect, r.stdout, body)

    def test_a_package_with_a_wrongly_pinned_index_or_missing_custody_file_is_rejected(self):
        rows = {l.split()[0]: l for l in self.neg().stdout.splitlines() if "exit=" in l}
        self.assertIn("10b", rows["I_wrong_index_pin_manifests_refreshed"]); self.assertIn("10 r4.1 custody", rows["H_independent_verifier_removed_manifests_refreshed"]); self.assertIn("10 r4.1 custody", rows["K_index_pin_removed_manifests_refreshed"])

    # ---- structure
    def test_builder_and_verifier_share_no_integrity_decision_code(self):
        def imports(path):
            with open(path) as fh: tree = ast.parse(fh.read())
            return {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else (n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}
        local = {"build_package", "verify_package", "neg_controls", "make_record"}
        for tool in ("build_package", "verify_package", "neg_controls", "make_record"):
            self.assertFalse(imports(os.path.join(TOOLS, tool + ".py")) & local, tool)                       # no tool imports another
            self.assertTrue(imports(os.path.join(TOOLS, tool + ".py")) <= set(sys.stdlib_module_names), tool)   # standard library only
        with open(os.path.join(TOOLS, "verify_package.py")) as fh: tree = ast.parse(fh.read())
        doc = tree.body[0].value if isinstance(tree.body[0], ast.Expr) else None
        strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and n is not doc]
        self.assertFalse([x for x in strings if "build_summary" in x or "build_package" in x])               # the verifier (code, not its docstring) reads no builder output and does not call it

    @staticmethod
    def read_text(p):
        with open(p) as fh: return fh.read()

    def test_the_record_generator_states_the_provenance_chain(self):
        final = os.path.join(self.tmp, "final"); os.makedirs(final)
        with open(os.path.join(final, "verification_output.txt"), "w") as fh: fh.write(self.vout.stdout)
        with open(os.path.join(final, "negative_controls_output.txt"), "w") as fh: fh.write(self.neg().stdout)
        shutil.copy(os.path.join(self.a, "verification_summary.json"), final)
        r = run(sys.executable, os.path.join(TOOLS, "make_record.py"), final, self.full, sys.executable, self.a); self.assertEqual(r.returncode, 0, r.stderr[-800:])
        rec = self.read_text(os.path.join(final, "PACKAGE_RECORD.txt"))
        for field in ("EXPERIMENT: SCYTHE_METAMORPHIC_RF_R4", f"SOURCE_COMMIT: {self.full}", "SOURCE_CLOSURE: VERIFIED", "PACKAGE_SHA256:", "CLEAN_ARCHIVE_COMPARISON: PASS",
                      "INDEPENDENT_EXTRACTION_VERIFICATION: PASS", "EXECUTION_AUTHORIZED: NO", "DEDIROCK_TRANSFER_AUTHORIZED: NO", "PROVENANCE CHAIN", "INVENTORY sha256", "TOOLS_REVISION"):
            self.assertIn(field, rec, field)


class ProvenanceRecords(unittest.TestCase):
    """The two provenance chains and the Gate 3 evidence are committed and self-consistent."""
    def read(self, *p):
        with open(os.path.join(*p), "rb") as fh: return fh.read()

    def test_the_external_tools_keep_their_original_digests(self):
        want = {"build_package.py": "b78aee2ed803b357b8e091664b45584c7568e67053d9f1656d2f169f74af0ef8", "verify_package.py": "dae1bfd5c517a28e9fd23705ba5575884e35ce1ba5234d4efe76ff2e88244371",
                "neg_controls.py": "3c09c6dcff396f9d1b7f48447d20850d5cb032fbf88216c8a838a4527f206c41", "make_record.py": "87ac5a36e080e1f83b035d4fa29cda007c3db08deb06ac0a54eca47af2a36299"}
        for n, d in want.items(): self.assertEqual(sha(self.read(PROV, "external_tools", n)), d, n)
        r4 = {"build_package.py": "3aadea492f8aabcb6089c75aa847f0190ae6571dba9e640e4fd9f5cc812ff64c", "verify_package.py": "2054065aea04f2169bd9dfc264146903d2cfa09482ce70491e081b3c0b171e73",
              "neg_controls.py": "61a3900fe91afc49ccec864f6c3307fc7c3355e8b01509a53d78e0e126712943", "make_record.py": "fefb020ea9d997defe87ff7f36e58398230fa939db487c36eb798780633a9f3a"}
        for n, d in r4.items(): self.assertEqual(sha(self.read(PROV, "external_tools_r4_for_a7fd10b1", n)), d, n)

    def test_the_redaction_manifest_matches_the_committed_files_and_unredacted_files_match_their_originals(self):
        n = 0
        for line in self.read(PROV, "REDACTION_MANIFEST.txt").decode().splitlines():
            if line.startswith("#") or not line.strip(): continue
            path, orig, committed, reps = (x.strip() for x in line.split("|"))
            self.assertEqual(sha(self.read(EVID, path)), committed, path)
            if reps == "0": self.assertEqual(orig, committed, path)
            n += 1
        self.assertGreaterEqual(n, 25)

    def test_the_provenance_document_records_the_digest_of_every_repository_tool(self):
        doc = self.read(HERE, "docs", "research", "r4_package_tools_provenance.md").decode()
        for n in ("build_package.py", "verify_package.py", "neg_controls.py", "make_record.py"):
            d = sha(self.read(TOOLS, n)); self.assertTrue(d in doc or d[:8] + "…" in doc, n)

    def test_the_gate3_record_digests_are_the_ones_reported(self):
        self.assertEqual(sha(self.read(G3, "GATE3_R41_SHA256SUMS")), "05785f94d76c54a132785842bf1f9a7b0b761c9b3957f48451ee1ae92d65c2fd")
        self.assertEqual(sha(self.read(G3, "job_ok", "JOB_SHA256SUMS")), "2104894ce26198e66b45fa30b7ac7c528691bfe2d27be83d903ac18621675882")
        rm = {l.split("|")[0].strip(): [x.strip() for x in l.split("|")] for l in self.read(PROV, "REDACTION_MANIFEST.txt").decode().splitlines() if "|" in l and not l.startswith("#")}
        for line in self.read(G3, "GATE3_R41_SHA256SUMS").decode().splitlines():
            digest, name = line.split(None, 1); entry = rm.get("r4_1_gate3_2026-10-13/" + name.strip())
            if name.strip() == "GATE3_R41_RECORD.txt": entry = None
            if entry: self.assertEqual(entry[1], digest, name)                                                       # recorded original digest
            else: self.assertEqual(sha(self.read(G3, name.strip())), digest, name)                                   # verbatim copy (GATE3_R41_RECORD.txt is hashed in the listing)

    def test_the_packaged_job_manifest_and_dispositions_state_the_passed_result(self):
        self.assertIn("JOB_ACCEPTED: YES", self.read(G3, "job_ok.disposition.txt").decode()); self.assertIn("JOB_ACCEPTED: NO", self.read(G3, "job_fail.disposition.txt").decode())
        rec = self.read(G3, "GATE3_R41_RECORD.txt").decode()
        for s in ("88 tests, 0 failures", "exactly 37 regular files", "R4_1_PACKAGED_CUSTODY_VALIDATION_PASS", "2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a"): self.assertIn(s, rec)
        self.assertIn("FAILED_PACKAGED_CUSTODY_INTEGRATION", self.read(HERE, "docs", "research", "metamorphic_r4_1_fixture_custody.md").decode())


if __name__ == "__main__":
    unittest.main()
