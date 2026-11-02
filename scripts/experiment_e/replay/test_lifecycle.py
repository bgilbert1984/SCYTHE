#!/usr/bin/env python3
"""Lifecycle / kill-switch simulation tests. Run: python3 -I test_lifecycle.py  (stdlib only, no RF)."""
import os, sys, random, unittest, tempfile, copy, collections
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import mock_device as hw, replay_compiler as rc, replay_run as rr, lifecycle as lc

LIM = dict(freq_min_hz=100_000, freq_max_hz=14_000_000, max_duration_s=300, channels=1)
HDR = "t_s,dur_s,freq_hz,modulation,key_mhz,duty_pct\n"
TMP = tempfile.mkdtemp(prefix="lifecycle_")


def manifest(rows):
    p = os.path.join(TMP, "m.csv")
    with open(p, "w") as fh: fh.write(HDR + rows)
    return rc.compile_manifest(p, "csv", "synthetic", LIM)

EX = manifest("1.0,10,7074000,cw,,\n12.0,20.05,7074000,ook,7000,50\n33.5,5.05,12500000,ook,7000,50\n")   # ends ~38.6 s
S = 1_000_000


def run(events=(), approval=None, **kw):
    r = lc.Run(EX, approval or lc.make_approval(EX), events, **kw).run()
    return r, lc.audit(r)


class Lifecycle(unittest.TestCase):
    def test_clean_run_completes(self):
        r, v = run(); self.assertEqual((r.state, v), ("COMPLETED", []))
        self.assertFalse(r.dev.armed); self.assertFalse(r.dev.true_on)
        self.assertGreater(len(r.dev.true_intervals(r.horizon)), 150)

    def test_unapproved_never_touches_device(self):
        cases = {"no approval for this manifest": lc.make_approval(EX, sha="0" * 64),
                 "expired before start": lc.make_approval(EX, approved_ms=-5000, expires_ms=-1),
                 "not yet valid": lc.make_approval(EX, approved_ms=5000),
                 "wrong scope": lc.make_approval(EX, scope="radiated"),
                 "expires mid-run (does not cover the run)": lc.make_approval(EX, expires_ms=20_000)}
        for name, ap in cases.items():
            r, v = run(approval=ap)
            self.assertEqual(r.state, "DRAFT", name); self.assertEqual(r.link.sent, [], name)   # not even V 1
            self.assertEqual(r.dev.gate_log, [], name); self.assertEqual(v, [], name)
            self.assertIsNotNone(r.preflight_error)
        bad = copy.deepcopy(EX); bad["commands"][3]["line"] = "F 29000000"        # tampered after hashing
        r = lc.Run(bad, lc.make_approval(EX)).run(); self.assertEqual((r.state, r.link.sent), ("DRAFT", []))

    def kill(self, events, cause, bound_us, **kw):
        r, v = run(events, **kw)
        self.assertEqual(v, [], f"{cause}: {v}")
        self.assertEqual((r.state, r.cause), ("ABORTED", cause))
        lat = lc.kill_latency_us(r); self.assertIsNotNone(lat); self.assertLessEqual(lat, bound_us, f"{cause} latency {lat} us")
        self.assertFalse(r.dev.armed)
        return r, lat

    def test_operator_kill_mid_segment(self):
        r, lat = self.kill([(20 * S, "operator_kill")], "operator_kill", 10_000)
        self.assertLess(lat, 5_000)
        self.assertEqual([l for (_, l, _) in r.link.sent if l == "X"][:1], ["X"])

    def test_revocation_mid_keying_is_noticed_without_any_command(self):
        # K runs autonomously 20 s: only the periodic re-authorisation can stop it
        r, lat = self.kill([(20 * S + 37_000, "revoke")], "approval", 130_000)
        self.assertGreater(lat, 0)

    def test_expiry_mid_run(self):
        self.kill([(25 * S, "expire_at")], "approval", 130_000)

    def test_tamper_detected(self):
        self.kill([(15 * S, "tamper")], "manifest_tamper", 130_000)

    def test_device_refusal(self):
        r, v = run([(5 * S, "device_err")]); self.assertEqual(v, [])
        self.assertEqual((r.state, r.cause), ("ABORTED", "device_refused")); self.assertFalse(r.dev.true_on)

    def test_host_crash_relies_on_device_deadman(self):
        r, v = run([(20 * S, "host_crash")]); self.assertEqual(v, [])
        self.assertTrue(r.alive is False); lat = lc.kill_latency_us(r)
        self.assertLessEqual(lat, 30_000_000 + 1_000_000)
        self.assertGreater(lat, 25_000_000)                      # documents the real worst case: ~30 s unattended
        self.assertIn("DEADMAN off", [l for _, l in r.dev.async_out])

    def test_link_drop_during_keying(self):
        r, v = run([(20 * S, "link_drop", 200 * S)]); self.assertEqual(v, [])
        self.assertEqual(r.cause, "link_lost"); lat = lc.kill_latency_us(r)
        self.assertLessEqual(lat, 31_000_000)                    # X cannot be delivered; dead-man is the only kill

    def test_short_link_drop_loses_k0_but_is_retried(self):
        # drop covers the end of segment 2 (K 0 at 32.05 s): the command must be retried, not forgotten
        r, v = run([(31.99 * S, "link_drop", 32.4 * S)])
        self.assertEqual(v, [], v)
        self.assertFalse(r.dev.true_on); self.assertLessEqual(r.dev.true_intervals(r.horizon)[-1][1], 32.4 * S + 300_000)   # off soon after the link returns

    def test_stuck_output_is_invisible_over_serial(self):
        # firmware believes it is off; RF stays on. Serial supervisor alone cannot see it (documented limit).
        r, v = run([(5 * S, "stuck_rf")])
        self.assertTrue(r.dev.true_on)                           # RF genuinely still on at the end
        self.assertTrue(any(x.startswith("I3") or x.startswith("I4") for x in v), v)   # the auditor flags it
        self.assertEqual(r.state, "COMPLETED")                   # ... while the supervisor believed all was well

    def test_detector_and_hard_cut_contain_a_stuck_output(self):
        r, v = run([(5 * S, "stuck_rf")], detector=True, hard_kill=True)
        self.assertEqual(v, [], v); self.assertFalse(r.dev.true_on); self.assertEqual(r.cause, "rf_outside_plan")
        self.assertIsNotNone(r.hard_killed_us)
        r2, v2 = run([(5 * S, "stuck_rf")], detector=True, hard_kill=False)       # detector alone cannot cut
        self.assertTrue(r2.dev.true_on); self.assertTrue(v2)

    def test_firmware_has_no_notion_of_approval(self):
        # documented limit: governance is host-side only
        d = hw.MockKeyedSource(); self.assertEqual(d.send("ARM 1"), ["OK armed"])
        self.assertEqual(d.send("K 7000 50"), ["OK key on"])

    def test_auditor_can_fail(self):
        # guard the guard: a supervisor that stops re-authorising must be caught
        class Blind(lc.Run):
            def _periodic(self, t): pass
        r = Blind(EX, lc.make_approval(EX), [(20 * S, "revoke")]).run(); v = lc.audit(r)
        self.assertTrue(any(x.startswith("I1") for x in v), v)
        class NoRetry(lc.Run):
            def _command(self, t, line):
                rep = self.link.send(line, t)
                if rep is None: return
                if line == "X": self.state = "COMPLETED"
        r = NoRetry(EX, lc.make_approval(EX), [(31.99 * S, "link_drop", 32.4 * S)]).run(); v = lc.audit(r)
        self.assertTrue(any(x.startswith("I4") for x in v), v)


class Fuzz(unittest.TestCase):
    KINDS = ["operator_kill", "revoke", "expire_at", "tamper", "host_crash", "link_drop", "device_err"]

    def random_manifest(self, rng):
        rows, t = [], rng.uniform(0.3, 3)
        for _ in range(rng.randint(1, 3)):
            ook = rng.random() < 0.6; f = rng.choice([500_000, 1_000_000, 7_074_000, 12_500_000, 14_000_000])
            if ook:
                mh = rng.choice([500, 3000, 7000, 12345]); duty = rng.choice([10, 33, 50, 80]); d = rng.uniform(4, 15)
                rows.append(f"{t:.3f},{d:.3f},{f},ook,{mh},{duty}")
            else:
                d = rng.uniform(1, 12); rows.append(f"{t:.3f},{d:.3f},{f},cw,,")
            t += d + rng.uniform(0.2, 3)
        try: return manifest("\n".join(rows) + "\n")
        except rc.CompileError: return None

    def test_random_scenarios(self):
        rng = random.Random(20261103); n = bad = 0; lat = collections.defaultdict(list); states = collections.Counter()
        while n < 250:
            m = self.random_manifest(rng)
            if m is None: continue
            n += 1; end = m["timing"]["end_ms"]
            ap = lc.make_approval(m, expires_ms=rng.choice([10**9, 10**9, end + 2000, end - 5000]))
            ev = []
            for _ in range(rng.choice([0, 1, 1, 2])):
                k = rng.choice(self.KINDS); t = int(rng.uniform(0, end + 5000) * 1000)
                ev.append((t, k, t + int(rng.uniform(0.5, 60) * S)) if k == "link_drop" else (t, k))
            r = lc.Run(m, ap, ev).run(); v = lc.audit(r)
            states[r.state] += 1
            if v: bad += 1; print("VIOLATION", n, ev, v[:2], file=sys.stderr)
            if r.cause and (l := lc.kill_latency_us(r)) is not None: lat[r.cause].append(l)
        self.assertEqual(bad, 0)
        print("\nfuzz: 250 scenarios, states", dict(states))
        for c, xs in sorted(lat.items()): print(f"  kill latency {c:16s} n={len(xs):3d} max {max(xs)/1000:9.1f} ms  bound {lc.BOUND_US[c]/1000:9.1f} ms")


if __name__ == "__main__":
    unittest.main(verbosity=1)
