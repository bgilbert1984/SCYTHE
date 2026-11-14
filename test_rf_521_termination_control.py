"""R1: the measured termination control, its artifact verification, and the sequencer hook."""
import copy, hashlib, json, os, tempfile, time, unittest
from unittest import mock
import numpy as np
import rf_spur_catalogue_sequencer as seq
import rf_termination_control as rtc

HERE = os.path.dirname(os.path.abspath(__file__))
W = seq.WINDOW_BYTES
LIMITS = {c: (1.0, 9.0) for c in rtc.CENTRES_HZ}


def noise(seed, n=W):
    r = np.random.default_rng(seed)
    return r.choice(np.arange(125, 131), size=n, p=[.02, .09, .49, .33, .06, .01]).astype(np.uint8).tobytes()   # std ~0.8 LSB like a terminated input


def with_carrier(seed, offset_hz=300e3, amp=20.0):
    t = np.arange(W // 2) / rtc.SAMPLE_RATE_HZ
    z = amp * np.exp(2j * np.pi * offset_hz * t)
    x = np.empty(W)
    x[0::2], x[1::2] = z.real, z.imag
    base = np.frombuffer(noise(seed), dtype=np.uint8).astype(float) - 127.5
    return np.clip(np.round(127.5 + base + x), 0, 255).astype(np.uint8).tobytes()


class StubTuner:
    def __init__(self, make): self.make, self.n, self.sent = make, 0, []
    def set_manual_gain_db(self, g, declared_family=None): self.sent.append(("gain", g, declared_family))
    def set_lo_hz(self, lo): self.sent.append(("lo", lo))
    def discard(self, _s): pass
    def read_window_raw(self): self.n += 1; return self.make(self.n)


def decl():
    with open(os.path.join(HERE, "docs", "521_AUTHORIZED_RUN_DECLARATION_XTR_E4000.json")) as h: d = json.load(h)
    d.update(rtl_tcp_host="127.0.0.1", rtl_tcp_port=1234, output_dir="/nonexistent/out")
    return d


class Measure(unittest.TestCase):
    def test_noise_has_low_std_and_flat_spectrum(self):
        m = rtc.measure_window(noise(1)); self.assertLess(m["std_lsb"], 1.0); self.assertLess(m["peak_to_median_db"], 9.0)

    def test_a_carrier_stands_far_above_the_median(self):
        m = rtc.measure_window(with_carrier(2))
        self.assertGreater(m["peak_to_median_db"], 20.0); self.assertAlmostEqual(m["peak_offset_hz"], 300e3, delta=2e3)


class Control(unittest.TestCase):
    kw = dict(sensor_id="71874552", tuner_family="E4000", gain_db=29.0)

    def test_terminated_noise_passes_with_limits(self):
        a = rtc.run_control(StubTuner(lambda n: noise(n)), limits=LIMITS, **self.kw)
        self.assertEqual(a["verdict"], "TERMINATED"); self.assertEqual(len(a["centres"]), len(rtc.CENTRES_HZ))

    def test_without_limits_the_verdict_is_uncalibrated_never_terminated(self):
        self.assertEqual(rtc.run_control(StubTuner(lambda n: noise(n)), limits={}, **self.kw)["verdict"], "UNCALIBRATED")

    def test_a_carrier_makes_it_not_terminated(self):
        self.assertEqual(rtc.run_control(StubTuner(lambda n: with_carrier(n)), limits=LIMITS, **self.kw)["verdict"], "NOT_TERMINATED")

    def test_a_replayed_stream_is_stream_not_live(self):
        block = np.frombuffer(noise(9, 3760), dtype=np.uint8)
        self.assertEqual(rtc.run_control(StubTuner(lambda n: np.resize(block, W).tobytes()), limits=LIMITS, **self.kw)["verdict"], "STREAM_NOT_LIVE")

    def test_the_declared_family_and_gain_are_used(self):
        t = StubTuner(lambda n: noise(n)); rtc.run_control(t, limits=LIMITS, **self.kw)
        self.assertEqual(t.sent[0], ("gain", 29.0, "E4000"))


class Verify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.decl = decl()
        self.lim = mock.patch.dict(rtc.TERMINATED_LIMITS, LIMITS, clear=True); self.lim.start(); self.addCleanup(self.lim.stop)
        self.art = rtc.run_control(StubTuner(lambda n: noise(n)), limits=LIMITS, sensor_id="71874552", tuner_family="E4000", gain_db=29.0)

    def write(self, art):
        p = os.path.join(self.tmp.name, "c.json"); data = (json.dumps(art, indent=1, sort_keys=True) + "\n").encode()
        open(p, "wb").write(data); return p, hashlib.sha256(data).hexdigest()

    def refused(self, path, sha, d=None, now=None):
        with self.assertRaises(seq.SequencerRefused) as cm: rtc.verify_artifact(path, sha, d or self.decl, now=now)
        return cm.exception.code

    def test_a_good_control_verifies(self):
        p, h = self.write(self.art); self.assertEqual(rtc.verify_artifact(p, h, self.decl)["verdict"], "TERMINATED")

    def test_wrong_file_hash(self):
        p, _ = self.write(self.art); self.assertEqual(self.refused(p, "0" * 64), "TERMINATION_CONTROL_HASH_MISMATCH")

    def test_edited_artifact_with_a_recomputed_file_hash_is_altered(self):
        a = copy.deepcopy(self.art); a["centres"][0]["std_lsb"] = 0.1; p, h = self.write(a)
        self.assertEqual(self.refused(p, h), "TERMINATION_CONTROL_ALTERED")

    def test_not_terminated_verdict_refuses(self):
        a = copy.deepcopy(self.art); a["verdict"] = "NOT_TERMINATED"; a["artifact_sha256"] = rtc._canonical_hash(a); p, h = self.write(a)
        self.assertEqual(self.refused(p, h), "TERMINATION_CONTROL_NOT_TERMINATED")

    def test_other_gain_or_receiver_refuses(self):
        p, h = self.write(self.art)
        d = copy.deepcopy(self.decl); d["gain_db"] = 34.0; self.assertEqual(self.refused(p, h, d), "TERMINATION_CONTROL_CHAIN_MISMATCH")
        d = copy.deepcopy(self.decl); d["receiver"]["sensor_id"] = "1"; self.assertEqual(self.refused(p, h, d), "TERMINATION_CONTROL_CHAIN_MISMATCH")

    def test_stale_control_refuses(self):
        p, h = self.write(self.art)
        self.assertEqual(self.refused(p, h, now=self.art["created_unix"] + rtc.MAX_ARTIFACT_AGE_S + 5), "TERMINATION_CONTROL_STALE")

    def test_changed_limits_since_the_control_refuses(self):
        p, h = self.write(self.art)
        with mock.patch.dict(rtc.TERMINATED_LIMITS, {c: (2.0, 9.0) for c in rtc.CENTRES_HZ}, clear=True):
            self.assertEqual(self.refused(p, h), "TERMINATION_CONTROL_LIMITS")


class SequencerHook(unittest.TestCase):
    def test_no_control_named_is_none_and_still_runs_the_other_checks(self):
        self.assertIsNone(seq.check_termination_control(decl()))

    def test_a_bad_named_control_stops_run_epoch_before_any_contact_or_file(self):
        class Sock:
            sent = []
            def recv(self, n): return b""
            def sendall(self, d): self.sent.append(d)
            def settimeout(self, _t): pass
            def close(self): pass
        with tempfile.TemporaryDirectory() as tmp:
            d = decl(); d["output_dir"] = os.path.join(tmp, "out")
            d["termination_control"] = {"path": os.path.join(tmp, "missing.json"), "sha256": "0" * 64}
            sock = Sock(); tuner = seq.RtlTcpTuner("fake", 1, socket_factory=lambda _a: sock)
            with self.assertRaises((seq.SequencerRefused, FileNotFoundError)): seq.run_epoch(d, tuner)
            self.assertEqual(sock.sent, []); self.assertFalse(os.path.exists(d["output_dir"]))


if __name__ == "__main__":
    unittest.main()
