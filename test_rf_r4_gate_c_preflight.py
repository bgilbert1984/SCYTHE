"""Controls for tools/r4_package/gate_c_preflight.py on a synthetic package (the real 2b2e3251 archive is not in the repository). Nothing here touches another host."""
import datetime, hashlib, io, json, os, pathlib, shutil, socket, subprocess, sys, tarfile, tempfile, unittest

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

    SCOPE = "no planned maintenance, reboot or reclaim of the host during the window"

    def make_declaration(self, name=None, **over):
        """A structured operator declaration made by the test (the real one is made by the operator; the tool never creates one)."""
        t0 = datetime.datetime.now(datetime.timezone.utc); iso = lambda x: x.isoformat(timespec="seconds")
        o = {"schema": "gate-c-operator-declaration/1", "operator": "test-operator", "host": socket.gethostname(), "package_sha256": self.decl["package_sha256"], "scope": self.SCOPE,
             "window_start_utc": iso(t0 - datetime.timedelta(minutes=5)), "window_end_utc": iso(t0 - datetime.timedelta(minutes=5) + datetime.timedelta(hours=4)), "declared_at_utc": iso(t0 - datetime.timedelta(minutes=10))}
        o.update(over); type(self).n += 1; path = os.path.join(self.tmp, name or f"decl{self.n}.json"); pathlib.Path(path).write_text(json.dumps(o)); return path

    def at(self, minutes):
        return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")

    STANDING_SCOPE = "sole administrator of this temporary sandbox; this declaration applies until explicitly withdrawn (no expiry)"

    def make_standing_declaration(self, name=None, **over):
        """A structured gate-c-operator-declaration/2 (standing, sandbox_epoch-bound) made by the test."""
        t0 = datetime.datetime.now(datetime.timezone.utc)
        o = {"schema": "gate-c-operator-declaration/2", "operator": "test-operator", "host": socket.gethostname(), "package_sha256": self.decl["package_sha256"], "scope": self.STANDING_SCOPE,
             "status": "ACTIVE", "sandbox_epoch": 1, "declared_at_utc": (t0 - datetime.timedelta(minutes=10)).isoformat(timespec="seconds")}
        o.update(over); type(self).n += 1; path = os.path.join(self.tmp, name or f"sdecl{self.n}.json"); pathlib.Path(path).write_text(json.dumps(o)); return path

    def items(self, rec): return {i["name"][:3].strip(): i for i in json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())["items"]}
    def status(self, rec, prefix): return next(i["status"] for i in json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())["items"] if i["name"].startswith(prefix))
    def verdict(self, rec): return json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())["verdict"]

    def test_a_clean_synthetic_package_passes_but_is_marked_invalid_for_gate_c_under_an_override(self):
        before = snapshot(self.work); r, rec, job = self.run_tool(extra=["--operator-declaration", self.make_declaration(), "--reference-preflight", REF]); self.assertEqual(r.returncode, 0, r.stdout)
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

    def checked(self, **kw):
        """A passing CHECK_ONLY with a valid declaration; returns (record dir, job dir, declaration path)."""
        df = self.make_declaration(); r, rec, job = self.run_tool(extra=["--operator-declaration", df], **kw); self.assertEqual(r.returncode, 0, r.stdout); return rec, job, df

    def recheck(self, prior, job, df, extra=(), decl=None):
        args = ["--prior-record", prior, *extra] + (["--operator-declaration", df] if df else [])
        return self.run_tool(mode="RECHECK", job=job, extra=args, decl=decl)

    def test_recheck_passes_for_the_same_environment_and_declaration(self):
        prior, job, df = self.checked(); r, rec, _ = self.recheck(prior, job, df); self.assertEqual(r.returncode, 0, r.stdout)
        for p in ("6s", "6p", "6 ", "6r", "5 "): self.assertEqual(self.status(rec, p), "PASS", p)

    def test_recheck_requires_the_environment_to_equal_the_recorded_one(self):
        prior, job, df = self.checked()
        for field in ("numpy", "interpreter_binary_sha256", "kernel", "cpu_model"):
            alt = os.path.join(self.work, f"prior_{field}"); shutil.copytree(prior, alt, ignore=shutil.ignore_patterns("extracted_package"))
            o = json.loads(pathlib.Path(alt, "PREFLIGHT_RECORD.json").read_text()); o["environment"][field] = "changed"; pathlib.Path(alt, "PREFLIGHT_RECORD.json").write_text(json.dumps(o))
            # re-seal the altered copy so only the environment comparison (not the checksum) objects
            lines = [f"{sha(pathlib.Path(alt, n).read_bytes())}  {n}" for n in sorted(l.split(None, 1)[1] for l in pathlib.Path(prior, "PREFLIGHT_SHA256SUMS").read_text().splitlines())]
            pathlib.Path(alt, "PREFLIGHT_SHA256SUMS").write_text("\n".join(lines) + "\n")
            r3, rec3, _ = self.recheck(alt, job, df); self.assertEqual(r3.returncode, 1, field); self.assertEqual(self.status(rec3, "6 "), "FAIL", field)

    # ---- declaration identity and validity
    def test_a_valid_declaration_gives_pass_and_its_identity_is_recorded(self):
        rec, job, df = self.checked(); o = json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())
        self.assertEqual(o["schema_version"], 2); self.assertEqual(o["operator_declaration"]["sha256"], sha(pathlib.Path(df).read_bytes())); self.assertEqual(o["operator_declaration"]["operator"], "test-operator")
        self.assertEqual(sorted(o["field_classes"]), ["FROZEN", "RECONFIRMED", "REMEASURED"]); self.assertEqual(sorted(o["measurements"]), ["free_job_gb", "free_tmp_gb", "mem_available_gb"])
        self.assertIn("window_hours", o["requirements"]); self.assertEqual(o["verdict"], "INVALID_FOR_GATE_C_DECLARATION_OVERRIDE_PASS")

    def test_bad_declarations_fail_check_only(self):
        w = lambda **k: self.make_declaration(**k)
        cases = {"wrong host": w(host="somewhere-else"), "wrong package": w(package_sha256="0" * 64), "wrong scope": w(scope="anything"), "window not 4 h": w(window_end_utc=self.at(60)),
                 "declared in the future": w(declared_at_utc=self.at(30)), "already expired": w(window_start_utc=self.at(-300), window_end_utc=self.at(-60), declared_at_utc=self.at(-310))}
        for name, df in cases.items():
            r, rec, _ = self.run_tool(extra=["--operator-declaration", df]); self.assertEqual(r.returncode, 1, name); self.assertEqual(self.status(rec, "5 "), "FAIL", name)
        bad = os.path.join(self.tmp, "malformed.json"); pathlib.Path(bad).write_text("{not json")
        r, rec, _ = self.run_tool(extra=["--operator-declaration", bad]); self.assertEqual(self.status(rec, "5 "), "FAIL")
        r, rec, _ = self.run_tool(extra=["--operator-declaration", os.path.join(self.tmp, "missing.json")]); self.assertEqual(self.status(rec, "5 "), "FAIL")
        df = self.make_declaration(); pathlib.Path(df + ".withdrawn").write_text("withdrawn"); r, rec, _ = self.run_tool(extra=["--operator-declaration", df]); self.assertEqual(self.status(rec, "5 "), "FAIL"); self.assertIn("WITHDRAWN", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text())

    def test_recheck_refuses_a_changed_missing_withdrawn_or_expired_declaration(self):
        prior, job, df = self.checked()
        o = json.loads(pathlib.Path(df).read_text()); o["operator"] = "someone-else"; changed = self.make_declaration(**{k: v for k, v in o.items() if k not in ("schema",)})
        r, rec, _ = self.recheck(prior, job, changed); self.assertEqual(r.returncode, 1); self.assertIn("identity differs", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text())
        r, rec, _ = self.recheck(prior, job, None); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "5 "), "FAIL")                    # missing is a refusal at RECHECK, not pending
        r, rec, _ = self.recheck(prior, job, os.path.join(self.tmp, "gone.json")); self.assertEqual(self.status(rec, "5 "), "FAIL")
        pathlib.Path(df + ".withdrawn").write_text("w"); r, rec, _ = self.recheck(prior, job, df); self.assertEqual(r.returncode, 1); self.assertIn("WITHDRAWN", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text()); os.remove(df + ".withdrawn")
        late = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)).isoformat(timespec="seconds")
        r, rec, _ = self.recheck(prior, job, df, extra=["--now-utc", late]); self.assertEqual(r.returncode, 1); self.assertIn("outside the declared window", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text())
        early = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).isoformat(timespec="seconds")
        r, rec, _ = self.recheck(prior, job, df, extra=["--now-utc", early]); self.assertEqual(r.returncode, 1)                                                  # not yet open (and declared in the future)

    def test_recheck_refuses_a_pending_check_only_record(self):
        r, prior, job = self.run_tool(); self.assertEqual(self.status(prior, "5 "), "PENDING")
        r, rec, _ = self.recheck(prior, job, self.make_declaration()); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "6p"), "FAIL")

    # ---- standing declaration (schema /2): no window, bound to sandbox_epoch, valid until withdrawn
    def checked_standing(self, **kw):
        df = self.make_standing_declaration(); r, rec, job = self.run_tool(extra=["--operator-declaration", df], **kw); self.assertEqual(r.returncode, 0, r.stdout); return rec, job, df

    def test_a_standing_declaration_passes_check_only_with_no_expiry(self):
        rec, job, df = self.checked_standing(); self.assertEqual(self.status(rec, "5 "), "PASS")
        txt = pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text(); self.assertIn("standing, sandbox_epoch 1", txt)
        o = json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())
        self.assertEqual(o["operator_declaration"]["decl_schema"], "gate-c-operator-declaration/2"); self.assertEqual(o["operator_declaration"]["sandbox_epoch"], 1)

    def test_recheck_of_a_standing_declaration_passes_arbitrarily_far_in_the_future(self):
        prior, job, df = self.checked_standing()
        far = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=400)).isoformat(timespec="seconds")
        r, rec, _ = self.recheck(prior, job, df, extra=["--now-utc", far]); self.assertEqual(r.returncode, 0, r.stdout); self.assertEqual(self.status(rec, "5 "), "PASS")

    def test_bad_standing_declarations_fail_check_only(self):
        w = lambda **k: self.make_standing_declaration(**k)
        cases = {"wrong host": w(host="somewhere-else"), "wrong package": w(package_sha256="0" * 64), "wrong scope": w(scope="anything"),
                 "status not active": w(status="SUSPENDED"), "declared in the future": w(declared_at_utc=self.at(30)),
                 "epoch not an integer": w(sandbox_epoch="1"), "epoch zero": w(sandbox_epoch=0), "epoch a bool": w(sandbox_epoch=True)}
        for name, df in cases.items():
            r, rec, _ = self.run_tool(extra=["--operator-declaration", df]); self.assertEqual(r.returncode, 1, name); self.assertEqual(self.status(rec, "5 "), "FAIL", name)
        df = self.make_standing_declaration(); pathlib.Path(df + ".withdrawn").write_text("withdrawn")
        r, rec, _ = self.run_tool(extra=["--operator-declaration", df]); self.assertEqual(self.status(rec, "5 "), "FAIL"); self.assertIn("WITHDRAWN", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text())

    def test_recheck_refuses_a_changed_or_withdrawn_standing_declaration(self):
        prior, job, df = self.checked_standing()
        o = json.loads(pathlib.Path(df).read_text()); o["sandbox_epoch"] = 2; changed = self.make_standing_declaration(**{k: v for k, v in o.items() if k not in ("schema",)})
        r, rec, _ = self.recheck(prior, job, changed); self.assertEqual(r.returncode, 1); self.assertIn("identity differs", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text())
        pathlib.Path(df + ".withdrawn").write_text("w"); r, rec, _ = self.recheck(prior, job, df); self.assertEqual(r.returncode, 1); self.assertIn("WITHDRAWN", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text()); os.remove(df + ".withdrawn")

    def test_unknown_declaration_schema_is_refused(self):
        df = self.make_standing_declaration(schema="gate-c-operator-declaration/3")
        r, rec, _ = self.run_tool(extra=["--operator-declaration", df]); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "5 "), "FAIL")
        self.assertIn("unknown declaration schema", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text())

    # ---- frozen requirements vs remeasured quantities
    def test_remeasured_quantities_are_judged_against_the_frozen_thresholds_not_compared_for_equality(self):
        prior, job, df = self.checked(); alt_tmp = os.path.join(self.work, "alt_tmp"); os.makedirs(alt_tmp)
        args = [sys.executable, TOOL, "--mode", "RECHECK", "--archive", self.arc, "--python", sys.executable, "--job-dir", job, "--record-dir", os.path.join(self.work, "rc_tmp"),
                "--prior-record", prior, "--declaration", self.declf, "--operator-declaration", df]
        r = subprocess.run(args, capture_output=True, text=True, env=dict(os.environ, TMPDIR=alt_tmp)); self.assertEqual(r.returncode, 0, r.stdout)
        m = json.loads(pathlib.Path(self.work, "rc_tmp", "PREFLIGHT_RECORD.json").read_text())["measurements"]; self.assertEqual(sorted(m), ["free_job_gb", "free_tmp_gb", "mem_available_gb"])
        o = json.loads(pathlib.Path(prior, "PREFLIGHT_RECORD.json").read_text()); o["measurements"] = {"free_job_gb": 1234.5, "free_tmp_gb": 1234.5, "mem_available_gb": 99.9}    # a very different earlier measurement
        o["field_classes"]["REMEASURED"]  # classification is part of the schema
        alt = os.path.join(self.work, "prior_other_measurements"); shutil.copytree(prior, alt, ignore=shutil.ignore_patterns("extracted_package")); pathlib.Path(alt, "PREFLIGHT_RECORD.json").write_text(json.dumps(o))
        pathlib.Path(alt, "PREFLIGHT_SHA256SUMS").write_text("\n".join(f"{sha(pathlib.Path(alt, l.split(None, 1)[1].strip()).read_bytes())}  {l.split(None, 1)[1].strip()}" for l in pathlib.Path(prior, "PREFLIGHT_SHA256SUMS").read_text().splitlines()) + "\n")
        r, rec, _ = self.recheck(alt, job, df); self.assertEqual(r.returncode, 0, r.stdout)                                  # earlier numbers differ widely: still a pass, the thresholds are what bind

    def test_a_changed_requirement_is_refused_even_if_the_measurement_would_pass(self):
        prior, job, df = self.checked()
        for k, v in (("min_free_gb", 8), ("min_mem_gb", 2), ("window_hours", 5), ("harness_sha256", "9" * 64)):
            alt = os.path.join(self.tmp, f"alt_{k}.json"); pathlib.Path(alt).write_text(json.dumps({**self.decl, k: v}) if k != "harness_sha256" else json.dumps({**self.decl, k: v}))
            r, rec, _ = self.recheck(prior, job, df, decl=alt); self.assertEqual(r.returncode, 1, k); self.assertEqual(self.status(rec, "6r"), "FAIL", k)
        r, rec, _ = self.recheck(prior, os.path.join(self.work, "a_different_job_path"), df); self.assertEqual(self.status(rec, "6r"), "FAIL")

    def test_a_job_directory_that_appears_before_the_recheck_is_refused(self):
        prior, job, df = self.checked(); os.makedirs(job)
        r, rec, _ = self.recheck(prior, job, df); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "3 "), "FAIL")

    # ---- the prior record is an immutable, checksum-verified reference; older schemas are rejected
    def test_a_modified_or_older_schema_prior_record_is_rejected(self):
        prior, job, df = self.checked()
        alt = os.path.join(self.work, "prior_modified"); shutil.copytree(prior, alt, ignore=shutil.ignore_patterns("extracted_package")); pathlib.Path(alt, "PREFLIGHT_RECORD.txt").write_text("edited\n")
        r, rec, _ = self.recheck(alt, job, df); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "6s"), "FAIL")
        old = os.path.join(self.work, "prior_v1"); shutil.copytree(prior, old, ignore=shutil.ignore_patterns("extracted_package"))
        o = json.loads(pathlib.Path(old, "PREFLIGHT_RECORD.json").read_text()); del o["schema_version"]; del o["requirements"]; del o["operator_declaration"]; pathlib.Path(old, "PREFLIGHT_RECORD.json").write_text(json.dumps(o))
        pathlib.Path(old, "PREFLIGHT_SHA256SUMS").write_text("\n".join(f"{sha(pathlib.Path(old, l.split(None, 1)[1].strip()).read_bytes())}  {l.split(None, 1)[1].strip()}" for l in pathlib.Path(prior, "PREFLIGHT_SHA256SUMS").read_text().splitlines()) + "\n")
        r, rec, _ = self.recheck(old, job, df); self.assertEqual(r.returncode, 1); self.assertEqual(self.status(rec, "6s"), "FAIL"); self.assertIn("schema_version=None", pathlib.Path(rec, "PREFLIGHT_RECORD.txt").read_text())

    def test_the_now_hook_is_refused_without_the_override(self):
        r = subprocess.run([sys.executable, TOOL, "--mode", "CHECK_ONLY", "--archive", self.arc, "--python", sys.executable, "--job-dir", "/x/y", "--record-dir", os.path.join(self.work, "nohook"), "--now-utc", "2030-01-01T00:00:00+00:00"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0); self.assertFalse(os.path.exists(os.path.join(self.work, "nohook")))

    def test_the_interpreter_probe_cannot_write_bytecode_and_no_parent_directory_is_created(self):
        log = os.path.join(self.tmp, "fake_interpreter.log"); fake = os.path.join(self.tmp, "fake_python")
        pathlib.Path(fake).write_text("#!/bin/sh\necho \"ARGS: $@ | PYTHONDONTWRITEBYTECODE=$PYTHONDONTWRITEBYTECODE\" >> " + log + "\nexec " + sys.executable + " \"$@\"\n"); os.chmod(fake, 0o755)
        r, rec, _ = self.run_tool(python=fake); self.assertEqual(r.returncode, 0, r.stdout)
        line = pathlib.Path(log).read_text(); self.assertIn("-I -B", line); self.assertIn("PYTHONDONTWRITEBYTECODE=1", line)
        missing = os.path.join(self.work, "no_such_workspace", "record"); r2, _, _ = self.run_tool(record=missing)
        self.assertNotEqual(r2.returncode, 0); self.assertFalse(os.path.exists(os.path.dirname(missing)))

    def test_the_attempt_label_is_recorded_in_the_record_and_the_attempt_id(self):
        r, rec, _ = self.run_tool(extra=["--attempt-label", "312512"]); o = json.loads(pathlib.Path(rec, "PREFLIGHT_RECORD.json").read_text())
        self.assertEqual(o["attempt_label"], "312512"); self.assertTrue(o["attempt_id_candidate"].endswith("-ATTEMPT312512"))

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
