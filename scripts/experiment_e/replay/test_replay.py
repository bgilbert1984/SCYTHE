#!/usr/bin/env python3
"""Tests for the replay compiler, mock device and runner. Run: python3 -I test_replay.py (stdlib only)."""
import os, re, sys, json, ast, copy, hashlib, shutil, subprocess, tempfile, unittest, datetime, time, select
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import mock_device as hw, replay_compiler as rc, replay_run as rr

INO = os.path.join(HERE, "..", "keyed_source", "keyed_source.ino")
HOST = os.path.join(HERE, "..", "keyed_source", "host_test")
LIM = dict(freq_min_hz=100_000, freq_max_hz=14_000_000, max_duration_s=120, channels=1)
HDR = "t_s,dur_s,freq_hz,modulation,key_mhz,duty_pct\n"
TMP = tempfile.mkdtemp(prefix="replay_test_")


def write(name, text):
    p = os.path.join(TMP, name); open(p, "w").write(text); return p


def comp(rows, lim=None, hdr=HDR):
    return rc.compile_manifest(write("c.csv", hdr + rows), "csv", "synthetic", lim or LIM)


class Compiler(unittest.TestCase):
    EX = os.path.join(HERE, "examples", "example.csv")

    def test_deterministic_and_sensitive(self):
        a = rc.compile_manifest(self.EX, "csv", "synthetic", LIM); b = rc.compile_manifest(self.EX, "csv", "synthetic", LIM)
        self.assertEqual(a["manifest_sha256"], b["manifest_sha256"])
        self.assertEqual(rc.canonical(a), rc.canonical(b)); self.assertTrue(rc.verify_hash(a))
        c = copy.deepcopy(a); c["commands"][3]["line"] = "F 7074001"
        self.assertFalse(rc.verify_hash(c))
        d = comp("1.0,10,7074001,cw,,\n"); e = comp("1.0,10,7074000,cw,,\n")
        self.assertNotEqual(d["manifest_sha256"], e["manifest_sha256"])
        f = rc.compile_manifest(self.EX, "csv", "recorded", LIM)           # provenance is part of the identity
        self.assertNotEqual(a["manifest_sha256"], f["manifest_sha256"])

    def rej(self, rows, msg, lim=None, hdr=HDR):
        with self.assertRaisesRegex(rc.CompileError, msg): comp(rows, lim, hdr)

    def test_rejections(self):
        self.rej("1.0,10,7074000,fm,,\n", "prohibited")
        self.rej("1.0,10,7074000,wobble,,\n", "unsupported modulation")
        self.rej("1.0,10,14000001,cw,,\n", "outside explicit bounds")
        self.rej("1.0,10,50000,cw,,\n", "outside explicit bounds")
        self.rej("1.0,10,7074000,cw,,\n", "channels", dict(LIM, channels=2))
        self.rej("1.0,10,7074000,cw,,\n", r"hardware envelope", dict(LIM, freq_max_hz=40_000_000))
        self.rej("1.0,10,7074000,cw,,\n10.5,5,7074000,cw,,\n", "overlaps")
        self.rej("1.0,10,7074000,cw,,\n11.0,5,7074000,cw,,\n", "gap 0 ms")
        self.rej("1.0,10,7074000,cw,,\n11.05,5,7074000,cw,,\n", "gap 50 ms")
        self.rej("1.0005,10,7074000,cw,,\n", "exact to 1 ms")
        self.rej("1.0,10,7074000.5,cw,,\n", "whole number")
        self.rej("1.0,10,7074000,ook,,\n", "needs key_mhz")
        self.rej("1.0,0.2,7074000,ook,7000,50\n", "two key periods")
        self.rej("1.0,10.05,7074000,ook,50,50\n", "key_mhz")
        self.rej("1.0,10,7074000,ook,7000,100\n", "duty_pct")
        self.rej("1.0,10,7074000,ook,7000,50\n", "key edge")        # 70 periods exactly: end races the final edge
        self.rej("1.0,10,7074000,cw,7000,\n", "given for cw")
        self.rej("0.1,10,7074000,cw,,\n", "first segment")
        self.rej("1.0,200,7074000,cw,,\n", "max_duration_s")
        self.rej("1.0,10,7074000,cw,,5\n", "unknown columns", hdr="t_s,dur_s,freq_hz,modulation,key_mhz,gain\n")
        with self.assertRaisesRegex(rc.CompileError, "required"):
            comp("1.0,10,7074000,cw,,\n", dict(LIM, freq_max_hz=None))
        with self.assertRaisesRegex(rc.CompileError, "provenance"):
            rc.compile_manifest(self.EX, "csv", "measured", LIM)

    def sigmf(self, ann, g=None, data=None):
        meta = {"global": dict({"core:sample_rate": 2048000, "core:datatype": "cu8"}, **(g or {})), "captures": [], "annotations": ann}
        p = os.path.join(TMP, "x.sigmf-meta"); json.dump(meta, open(p, "w"))
        dp = os.path.join(TMP, "x.sigmf-data")
        if os.path.exists(dp): os.remove(dp)
        if data is not None: open(dp, "wb").write(data)
        return p

    def test_sigmf(self):
        a = {"core:sample_start": 2048000, "core:sample_count": 4096000, "core:freq_lower_edge": 7073000,
             "core:freq_upper_edge": 7075000, "scythe:modulation": "cw"}
        m = rc.compile_manifest(self.sigmf([a]), "sigmf", "recorded", LIM, max_bw_hz=5000)
        s = m["segments"][0]; self.assertEqual((s["t_ms"], s["dur_ms"], s["freq_hz"]), (1000, 2000, 7074000))
        self.assertFalse(m["source"]["notes"]["dataset_sha512_verified"])
        odd = dict(a, **{"core:sample_start": 2049000})                      # not on a ms boundary
        m = rc.compile_manifest(self.sigmf([odd]), "sigmf", "recorded", LIM, max_bw_hz=5000)
        self.assertGreater(m["source"]["notes"]["time_quantisation_error_ms_max"], 0)
        no_mod = {k: v for k, v in a.items() if k != "scythe:modulation"}
        with self.assertRaisesRegex(rc.CompileError, "no scythe:modulation"): rc.compile_manifest(self.sigmf([no_mod]), "sigmf", "recorded", LIM, 5000)
        with self.assertRaisesRegex(rc.CompileError, "bandwidth"): rc.compile_manifest(self.sigmf([dict(a, **{"core:freq_upper_edge": 7173000})]), "sigmf", "recorded", LIM, 5000)
        with self.assertRaisesRegex(rc.CompileError, "prohibited"): rc.compile_manifest(self.sigmf([dict(a, **{"scythe:modulation": "fm"})]), "sigmf", "recorded", LIM, 5000)
        with self.assertRaisesRegex(rc.CompileError, "max-bw-hz"): rc.compile_manifest(self.sigmf([a]), "sigmf", "recorded", LIM, None)
        data = b"abc"; good = hashlib.sha512(data).hexdigest()
        m = rc.compile_manifest(self.sigmf([a], {"core:sha512": good}, data), "sigmf", "recorded", LIM, 5000)
        self.assertTrue(m["source"]["notes"]["dataset_sha512_verified"])
        with self.assertRaisesRegex(rc.CompileError, "sha512"): rc.compile_manifest(self.sigmf([a], {"core:sha512": good}, b"abd"), "sigmf", "recorded", LIM, 5000)


class Mock(unittest.TestCase):
    def dev(self): return hw.MockKeyedSource()

    def test_unarmed_refusals(self):
        d = self.dev()
        for c in ("E 1", "K 7000 50", "S 7000000 7100000 1000 60"): self.assertEqual(d.send(c), ["ERR not armed"])
        d.advance_to(5_000_000); self.assertFalse(d.out_on); self.assertEqual(d.gate_log, [])

    def test_limits(self):
        d = self.dev()
        self.assertEqual(d.send("F 99999"), ["ERR freq out of range"]); self.assertEqual(d.send("F 30000001"), ["ERR freq out of range"])
        self.assertEqual(d.send("F 100000"), ["OK"]); self.assertEqual(d.send("D 3"), ["ERR drive 2|4|6|8"]); self.assertEqual(d.send("D 8"), ["OK"])
        d.send("ARM 1")
        for c in ("K 99 50", "K 20001 50", "K 7000 0", "K 7000 100"): self.assertEqual(d.send(c), ["ERR key range"], c)
        self.assertEqual(d.send("S 200000 100000 1000 60"), ["ERR sweep range"]); self.assertEqual(d.send("S 200000 300000 1000 49"), ["ERR sweep range"])
        self.assertEqual(d.send("BOGUS"), ["ERR unknown (H for help)"])

    def test_deadman_and_kill(self):
        d = self.dev(); d.send("ARM 1"); d.send("K 7000 50")
        d.advance_to(30_000_000); self.assertTrue(d.armed)
        d.advance_to(30_002_000); self.assertFalse(d.armed); self.assertFalse(d.out_on); self.assertFalse(d.keying)
        self.assertTrue(any("DEADMAN" in l for _, l in d.async_out))
        d = self.dev(); d.send("ARM 1"); d.send("K 7000 50"); d.advance_to(1_000_000); d.send("X"); n = len(d.gate_log)
        d.advance_to(10_000_000); self.assertEqual(len(d.gate_log), n); self.assertFalse(d.out_on)

    def test_pty(self):
        import tty, termios
        p = subprocess.Popen([sys.executable, "-I", os.path.join(HERE, "mock_device.py"), "--pty"], stdout=subprocess.PIPE, text=True)
        try:
            path = p.stdout.readline().strip(); fd = os.open(path, os.O_RDWR | os.O_NOCTTY); tty.setraw(fd, termios.TCSANOW)
            def readline(timeout=3.0):
                buf = b""; end = time.time() + timeout
                while time.time() < end and not buf.endswith(b"\n"):
                    if select.select([fd], [], [], 0.1)[0]: buf += os.read(fd, 256)
                return buf.decode().strip()
            self.assertEqual(readline(), "READY unarmed, outputs off")
            os.write(fd, b"ARM 1\n"); self.assertEqual(readline(), "OK armed")
            os.write(fd, b"?\n"); self.assertTrue(readline().startswith("OK armed=1 det=1"))
            os.write(fd, b"X\n"); self.assertEqual(readline(), "OK off")
        finally:
            p.terminate(); p.wait()


class Parity(unittest.TestCase):
    """The mock must not drift from the firmware it imitates."""
    @classmethod
    def setUpClass(cls):
        cls.ino = open(INO).read()

    def test_constants(self):
        g = lambda n: int(re.search(rf"{n}\s*=\s*(\d+)UL", self.ino).group(1))
        self.assertEqual((g("MIN_HZ"), g("MAX_HZ"), g("DEADMAN_MS")), (hw.MIN_HZ, hw.MAX_HZ, hw.DEADMAN_MS))
        m = re.search(r"mhz < (\d+) \|\| mhz > (\d+) \|\| duty < (\d+) \|\| duty > (\d+)", self.ino).groups()
        self.assertEqual(tuple(map(int, m)), (*hw.KEY_MHZ_RANGE, *hw.DUTY_RANGE))
        self.assertEqual(int(re.search(r"swDwellMs < (\d+)", self.ino).group(1)), hw.SWEEP_MIN_DWELL_MS)
        self.assertEqual(int(re.search(r"TICK_US = (\d+)", self.ino).group(1)), hw.TICK_US)
        self.assertIn("100000UL * duty", self.ino); self.assertIn("100000UL * (100 - duty)", self.ino)

    def test_reply_strings(self):
        tree = ast.parse(open(os.path.join(HERE, "mock_device.py")).read())
        lits = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and re.match(r"(OK|ERR|READY|DEADMAN|SWEEP DONE|STEP|GATE)", n.value)}
        self.assertGreater(len(lits), 15)
        for l in lits: self.assertIn(l.rstrip(), self.ino, f"mock reply {l!r} is not in the firmware")
        fw = set(re.findall(r'F\("((?:ERR|OK|READY|DEADMAN|SWEEP)[^"]*)"\)', self.ino))
        src = open(os.path.join(HERE, "mock_device.py")).read()
        for l in fw: self.assertIn(l, src, f"firmware reply {l!r} is not mirrored in the mock")

    def test_edge_times_match_compiled_firmware(self):
        if not shutil.which("g++"): self.skipTest("no g++")
        exe = os.path.join(TMP, "ks_sim")
        subprocess.run(["g++", "-std=c++17", "-w", f"-I{HOST}/stubs", f"-I{HOST}", "-o", exe, f"{HOST}/sim.cpp"], check=True)
        for mhz, duty in ((7000, 50), (20000, 1), (100, 99), (3333, 37), (12345, 62)):
            out = subprocess.run([exe, "--dump-keying", str(mhz), str(duty), str(max(6, int(4e3 / mhz) + 1))], capture_output=True, text=True, check=True).stdout
            ev = [tuple(map(int, re.match(r"GATE \d+ (\d) req=(\d+) done=(\d+)", l).groups())) for l in out.splitlines() if l.startswith("GATE")]
            self.assertGreaterEqual(len(ev), 4)
            d = hw.MockKeyedSource(); d.send("ARM 1"); d.send("V 1"); d.advance_to(ev[0][1]); d.send(f"K {mhz} {duty}")
            t_end = ev[-1][2] + 1000
            for tp in range(int(ev[0][1]) + 10_000_000, t_end, 10_000_000): d.advance_to(tp); d.send("P")   # same pings as the dump
            d.advance_to(t_end)
            mine = [(int(g["on"]), g["req_us"], g["done_us"]) for g in d.gate_log if g["req_us"] >= ev[0][1]]
            self.assertEqual(mine[:len(ev)], ev, f"K {mhz} {duty}: mock edge times differ from the firmware")


class Runner(unittest.TestCase):
    EX = os.path.join(HERE, "examples", "example.csv")

    def test_example_verifies(self):
        m = rc.compile_manifest(self.EX, "csv", "synthetic", LIM); r = rr.run_mock(m)
        self.assertEqual(r["provenance"], "simulated"); self.assertEqual(r["command_errors"], [])
        v = r["verification"]; self.assertTrue(v["ok"], v["problems"]); self.assertLess(v["max_edge_error_us"], rr.TOL_US)
        self.assertGreater(v["intervals_mock"], 150)
        self.assertIn("armed=0", r["final_status"]); self.assertIn("out=0", r["final_status"])

    def test_tampered_manifest_refused(self):
        m = rc.compile_manifest(self.EX, "csv", "synthetic", LIM); m["commands"][5]["line"] = "F 9000000"
        with self.assertRaisesRegex(rr.RunError, "hash"): rr.run_mock(m)

    def test_verifier_catches_wrong_schedule(self):
        m = rc.compile_manifest(self.EX, "csv", "synthetic", LIM)
        class Bad(hw.MockKeyedSource):                      # a firmware bug: duty arithmetic off by 1 tick per edge
            def _key_edge(self):
                super()._key_edge(); self.next_edge_tick += 1
        orig = rr.hw.MockKeyedSource
        try:
            rr.hw.MockKeyedSource = Bad; r = rr.run_mock(m)
        finally:
            rr.hw.MockKeyedSource = orig
        self.assertFalse(r["verification"]["ok"])

    def test_verifier_catches_command_error(self):
        m = rc.compile_manifest(self.EX, "csv", "synthetic", LIM)
        m["commands"] = [c for c in m["commands"] if c["line"] != "ARM 1"]; m["manifest_sha256"] = rc.sha256_bytes(rc.canonical({k: v for k, v in m.items() if k != "manifest_sha256"}).encode())
        r = rr.run_mock(m); self.assertTrue(r["command_errors"]); self.assertFalse(r["verification"]["ok"])

    def test_approval(self):
        m = rc.compile_manifest(self.EX, "csv", "synthetic", LIM)
        now = datetime.datetime.now(datetime.timezone.utc); iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")
        ok = dict(manifest_sha256=m["manifest_sha256"], approver="bench-owner", approved_utc=iso(now - datetime.timedelta(minutes=1)),
                  expires_utc=iso(now + datetime.timedelta(hours=1)), scope="conducted-bench")
        self.assertTrue(rr.check_approval(m, ok))
        for bad, msg in ((dict(ok, manifest_sha256="0" * 64), "different manifest"),
                         (dict(ok, expires_utc=iso(now - datetime.timedelta(seconds=1))), "expired"),
                         (dict(ok, approved_utc=iso(now + datetime.timedelta(hours=1))), "not yet"),
                         (dict(ok, scope="radiated"), "scope"),
                         ({k: v for k, v in ok.items() if k != "approver"}, "missing")):
            with self.assertRaisesRegex(rr.RunError, msg): rr.check_approval(m, bad)
            with self.assertRaisesRegex(rr.RunError, msg): rr.SerialTransport("/dev/null", m, bad)   # refuses before opening anything


if __name__ == "__main__":
    unittest.main(verbosity=1)
