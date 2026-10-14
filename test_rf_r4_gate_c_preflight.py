"""Controls for tools/r4_package/gate_c_preflight.py on a synthetic package (the real 2b2e3251 archive is not in the repository). Nothing here touches another host."""
import hashlib, io, json, os, pathlib, shutil, subprocess, sys, tarfile, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); TOOL = os.path.join(HERE, "tools", "r4_package", "gate_c_preflight.py")
REF = os.path.join(HERE, "docs", "evidence", "r4_full_local_2026-10-13", "preflight", "record_prelaunch_3", "PREFLIGHT_RECORD.json")
sha = lambda b: hashlib.sha256(b).hexdigest()


def snapshot(root):
    return sorted(os.path.relpath(os.path.join(r, f), root) for r, ds, fs in os.walk(root) for f in fs + ds)


class GateC(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="gatec-"); cls.n = 0
        files = {"tools/r4_job/run_job.sh": b"#!/bin/sh\n", "tools/rf_metamorphic_harness.py": b"# harness\n", "README": b"x\n"}
        files["SHA256SUMS"] = "".join(f"{sha(b)}  {p}\n" for p, b in sorted(files.items())).encode()
        cls.arc = os.path.join(cls.tmp, "pkg.tar.gz")
        with tarfile.open(cls.arc, "w:gz") as tf:
            for p, b in sorted(files.items()):
                ti = tarfile.TarInfo(f"pkg/{p}"); ti.size = len(b); tf.addfile(ti, io.BytesIO(b))
        pathlib.Path(cls.arc + ".sha256").write_text(f"{sha(pathlib.Path(cls.arc).read_bytes())}  pkg.tar.gz\n")
        cls.decl = {"package_sha256": sha(pathlib.Path(cls.arc).read_bytes()), "run_job_sha256": sha(files["tools/r4_job/run_job.sh"]), "harness_sha256": sha(files["tools/rf_metamorphic_harness.py"])}
        cls.declf = os.path.join(cls.tmp, "decl.json"); pathlib.Path(cls.declf).write_text(json.dumps(cls.decl))
        cls.work = os.path.join(cls.tmp, "work"); os.makedirs(cls.work)

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.tmp, True)

    def run_tool(self, mode="CHECK_ONLY", python=None, job=None, extra=(), decl=None, archive=None, record=None):
        type(self).n += 1; rec = record or os.path.join(self.work, f"rec{self.n}"); job = job or os.path.join(self.work, f"job{self.n}")
        args = [sys.executable, TOOL, "--mode", mode, "--archive", archive or self.arc, "--python", python or sys.executable, "--job-dir", job, "--record-dir", rec, *extra]
        if decl is not False: args += ["--declaration", decl or self.declf]
        r = subprocess.run(args, capture_output=True, text=True); return r, rec, job

    def items(self, rec): return {i["name"][:3].strip(): i for i in json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())["items"]}
    def status(self, rec, prefix): return next(i["status"] for i in json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())["items"] if i["name"].startswith(prefix))
    def verdict(self, rec): return json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())["verdict"]

    def test_a_clean_synthetic_package_passes_but_is_marked_invalid_for_gate_c_under_an_override(self):
        before = snapshot(self.work); r, rec, job = self.run_tool(extra=["--operator-confirms-window", "--reference-preflight", REF]); self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.verdict(rec), "INVALID_FOR_GATE_C_DECLARATION_OVERRIDE_PASS")
        self.assertFalse(os.path.exists(job)); added = [p for p in snapshot(self.work) if p not in before]; self.assertTrue(all(p == os.path.basename(rec) or p.startswith(os.path.basename(rec) + os.sep) for p in added), added)
        txt = pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text()
        for s in ("ENVIRONMENT TABLE", "ATTEMPT_ID_CANDIDATE: R4REPLAY-", "LAUNCH: NOT_PERFORMED", "<- differs"): self.assertIn(s, txt)
        lines = pathlib.Path(rec, "PREFLIGHT_SHA256SUMS").read_text().splitlines(); self.assertTrue(lines)
        for l in lines: d, name = l.split(None, 1); self.assertEqual(sha(pathlib.Path(rec, name).read_bytes()), d)

    def test_the_frozen_constants_bite_without_the_override(self):
        r, rec, job = self.run_tool(decl=False); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "1 "), "FAIL"); self.assertEqual(self.verdict(rec), "FAIL")
        self.assertIn("2b2e3251b678a5e83735ee663176f7763482a6948a7d5cb57f244dac1142874a", pathlib.Path(TOOL).read_text())

    def test_the_operator_item_stays_pending_without_a_statement(self):
        r, rec, _ = self.run_tool(); self.assertEqual(r.returncode, 0); self.assertEqual(self.status(rec, "5 "), "PENDING"); self.assertIn("OPERATOR_CONFIRMATION_PENDING", self.verdict(rec))

    def test_a_wrong_or_tampered_package_is_refused(self):
        bad = os.path.join(self.tmp, "wrong.json"); pathlib.Path(bad).write_text(json.dumps({**self.decl, "package_sha256": "0" * 64}))
        r, rec, _ = self.run_tool(decl=bad); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "1 "), "FAIL")
        t = os.path.join(self.tmp, "tampered.tar.gz"); shutil.copy(self.arc, t); shutil.copy(self.arc + ".sha256", t + ".sha256"); b = bytearray(pathlib.Path(t).read_bytes()); b[len(b) // 2] ^= 1; pathlib.Path(t).write_bytes(bytes(b))
        r, rec, _ = self.run_tool(archive=t); self.assertEqual(r.returncode, 1)
        h = os.path.join(self.tmp, "wrongrunner.json"); pathlib.Path(h).write_text(json.dumps({**self.decl, "run_job_sha256": "1" * 64}))
        r, rec, _ = self.run_tool(decl=h); self.assertEqual(self.status(rec, "1d"), "FAIL")

    def test_a_bad_interpreter_is_refused(self):
        r, rec, _ = self.run_tool(python="/bin/true"); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "2a"), "FAIL")
        r = subprocess.run([sys.executable, TOOL, "--mode", "CHECK_ONLY", "--archive", self.arc, "--python", "relative/python", "--job-dir", "/x/y", "--record-dir", os.path.join(self.work, "rel")], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertFalse(os.path.exists(os.path.join(self.work, "rel")))

    def test_an_existing_job_directory_or_record_directory_and_other_modes_are_refused(self):
        existing = os.path.join(self.work, "existing_job"); os.makedirs(existing)
        r, rec, _ = self.run_tool(job=existing); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "3 "), "FAIL")
        r, rec, _ = self.run_tool(); r2, _, _ = self.run_tool(record=rec); self.assertNotEqual(r2.returncode, 0); self.assertIn("refusing to overwrite", r2.stderr + r2.stdout)
        r, rec, _ = self.run_tool(mode="LAUNCH"); self.assertNotEqual(r.returncode, 0); self.assertFalse(os.path.exists(rec))

    def test_insufficient_space_or_memory_is_refused(self):
        big = os.path.join(self.tmp, "big.json"); pathlib.Path(big).write_text(json.dumps({**self.decl, "min_free_gb": 10 ** 9, "min_mem_gb": 10 ** 6}))
        r, rec, _ = self.run_tool(decl=big); self.assertEqual(r.returncode, 1)
        for p in ("3d", "3e", "3f"): self.assertEqual(self.status(rec, p), "FAIL", p)

    def test_a_competing_r4_job_process_is_refused(self):
        p = subprocess.Popen(["bash", "-c", "exec -a run_job.sh sleep 30"])
        try: r, rec, _ = self.run_tool(); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "4 "), "FAIL")
        finally: p.kill(); p.wait()

    def test_recheck_requires_the_environment_to_equal_the_recorded_one(self):
        r, prior, job = self.run_tool(extra=["--operator-confirms-window"]); self.assertEqual(r.returncode, 0)
        r2, rec2, _ = self.run_tool(mode="RECHECK", job=job, extra=["--prior-record", prior, "--operator-confirms-window"]); self.assertEqual(r2.returncode, 0, r2.stdout); self.assertEqual(self.status(rec2, "6 "), "PASS")
        for field in ("numpy", "interpreter_binary_sha256", "kernel", "cpu_model"):
            alt = os.path.join(self.work, f"prior_{field}"); shutil.copytree(prior, alt, ignore=shutil.ignore_patterns("extracted_package"))
            o = json.loads(pathlib.Path(alt, "PREFLIGHT_RECORD.json").read_text()); o["environment"][field] = "changed"; pathlib.Path(alt, "PREFLIGHT_RECORD.json").write_text(json.dumps(o))
            r3, rec3, _ = self.run_tool(mode="RECHECK", job=job, extra=["--prior-record", alt]); self.assertEqual(r3.returncode, 1, field); self.assertEqual(self.status(rec3, "6 "), "FAIL", field)

    def test_a_tool_crash_is_not_a_pass(self):
        bad = os.path.join(self.tmp, "bad_ref.json"); pathlib.Path(bad).write_text("{not json")
        r, rec, _ = self.run_tool(extra=["--reference-preflight", bad]); self.assertEqual(r.returncode, 2); self.assertIn("GATE_C_TOOL_FAILURE", r.stdout)

    def test_the_tool_uses_only_the_standard_library_and_has_no_launch_or_delete_path(self):
        import ast
        src = pathlib.Path(TOOL).read_text(); tree = ast.parse(src)
        mods = {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else (n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}
        self.assertTrue(mods <= set(sys.stdlib_module_names), mods)
        for forbidden in ("rmtree", "os.remove", "os.unlink", "pip install", "run_job.sh\"]", "sudo"): self.assertNotIn(forbidden, src.replace("shutil.rmtree(cls", ""), forbidden)


if __name__ == "__main__":
    unittest.main()
