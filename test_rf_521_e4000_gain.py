"""E4000 gain table, tuner-family binding, and the DRAFT XTR freeze (not registered in the sequencer)."""
import copy, json, os, struct, unittest

import rf_bridge
import rf_spur_catalogue_sequencer as seq

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = os.path.join(HERE, "docs", "521_AUTHORIZED_RUN_DECLARATION_XTR_E4000.json")
FROZEN_SHA256 = "cefa295d8905ea247281f4a5f0ecf647de470410ef1c8444db0bb79fad5078b2"


def load(name):
    with open(os.path.join(HERE, "docs", name)) as h:
        return json.load(h)


class FakeSocket:
    def __init__(self, payload): self._p, self._i, self.sent = payload, 0, []
    def recv(self, n):
        out = self._p[self._i:self._i + n]; self._i += len(out); return out
    def sendall(self, d): self.sent.append(bytes(d))
    def settimeout(self, _t): pass
    def close(self): pass


def tuner(code, count):
    fake = FakeSocket(b"RTL0" + struct.pack(">II", code, count))
    t = seq.RtlTcpTuner("fake", 1, socket_factory=lambda _a: fake)
    t._fake = fake
    return t


class Tables(unittest.TestCase):
    def test_tables_agree_with_the_bridge_driver_tables(self):
        tenths = lambda t: tuple(int(round(g * 10)) for g in t)
        self.assertEqual(tenths(seq._E4000_GAINS_DB), rf_bridge._TUNER_GAINS_TENTHS_DB["E4000"])

    def test_known_r820t2_table_discrepancy_is_recorded_not_hidden(self):
        # PRE-EXISTING, out of scope for the E4000 slice: the sequencer's R820T2 table has 23.1, 25.0, 27.8
        # where the bridge driver table has 22.9, 25.4, 28.0. 29.7 (the v5 gain) is a step in both.
        tenths = lambda t: tuple(int(round(g * 10)) for g in t)
        diff = sorted(set(tenths(seq._R820T2_GAINS_DB)) ^ set(rf_bridge._TUNER_GAINS_TENTHS_DB["R820T"]))
        self.assertEqual(diff, [229, 231, 250, 254, 278, 280])
        self.assertIn(297, tenths(seq._R820T2_GAINS_DB))

    def test_29_7_is_not_an_e4000_step_and_29_0_is(self):
        self.assertNotIn(29.7, seq._E4000_GAINS_DB)
        self.assertIn(29.0, seq._E4000_GAINS_DB)
        self.assertEqual(len(seq._E4000_GAINS_DB), 14)


class DeviceGate(unittest.TestCase):
    def test_e4000_header_accepts_an_e4000_step(self):
        t = tuner(1, 14)
        with t: t.set_manual_gain_db(29.0, declared_family="E4000")
        self.assertEqual(t._fake.sent[-1], bytes([4]) + (290).to_bytes(4, "big"))

    def test_negative_step_travels_as_twos_complement(self):
        t = tuner(1, 14)
        with t: t.set_manual_gain_db(-1.0, declared_family="E4000")
        self.assertEqual(t._fake.sent[-1], bytes([4]) + ((-10) & 0xFFFFFFFF).to_bytes(4, "big"))

    def test_e4000_header_refuses_29_7_before_any_command(self):
        t = tuner(1, 14)
        with t, self.assertRaises(seq.SequencerRefused) as cm:
            t.set_manual_gain_db(29.7, declared_family="E4000")
        self.assertEqual(cm.exception.code, "GAIN_NOT_SUPPORTED"); self.assertEqual(t._fake.sent, [])

    def test_legacy_r820t2_declaration_on_an_e4000_device_is_refused(self):
        t = tuner(1, 14)
        with t, self.assertRaises(seq.SequencerRefused) as cm:
            t.set_manual_gain_db(29.7, declared_family="R820T2")
        self.assertEqual(cm.exception.code, "TUNER_FAMILY_MISMATCH"); self.assertEqual(t._fake.sent, [])

    def test_r820t2_device_still_takes_29_7(self):
        t = tuner(5, 29)
        with t: t.set_manual_gain_db(29.7)
        self.assertEqual(t._fake.sent[-1], bytes([4]) + (297).to_bytes(4, "big"))

    def test_gain_count_disagreeing_with_the_table_is_refused(self):
        t = tuner(1, 29)
        with t, self.assertRaises(seq.SequencerRefused) as cm: t.set_manual_gain_db(29.0)
        self.assertEqual(cm.exception.code, "TUNER_GAIN_COUNT_MISMATCH"); self.assertEqual(t._fake.sent, [])

    def test_unsupported_tuner_type_is_refused(self):
        t = tuner(6, 29)
        with t, self.assertRaises(seq.SequencerRefused) as cm: t.set_manual_gain_db(29.7)
        self.assertEqual(cm.exception.code, "TUNER_FAMILY_UNSUPPORTED")


class FrozenE4000(unittest.TestCase):
    def setUp(self):
        self.draft = load("521_AUTHORIZED_RUN_DECLARATION_XTR_E4000.json")
        self.old = load("521_AUTHORIZED_RUN_DECLARATION_XTR.json")
        self.run = {"rtl_tcp_host": "127.0.0.1", "rtl_tcp_port": 1234, "output_dir": "/x"}

    def test_frozen_file_is_unchanged(self):
        import hashlib
        with open(FROZEN, "rb") as h: self.assertEqual(hashlib.sha256(h.read()).hexdigest(), FROZEN_SHA256)

    def test_it_is_registered_for_new_runs_and_says_frozen_not_draft(self):
        self.assertEqual(seq._ADDITIONAL_AUTHORIZED_DECLARATION_PATHS, (FROZEN,))
        self.assertNotIn("DRAFT", self.draft["authorization"]); self.assertIn("FROZEN 2026-11-14", self.draft["authorization"])
        seq.check_declaration(self.draft | self.run)
        seq.check_declaration_authorized(self.draft)

    def test_draft_changes_only_gain_and_receiver_binding_among_chain_fields(self):
        a, b = seq._authorized_chain_subset(self.old), seq._authorized_chain_subset(self.draft)
        self.assertEqual({k for k in a if a[k] != b[k]}, {"tuner", "gain_db"})
        self.assertEqual((b["tuner"], b["gain_db"]), ("E4000", 29.0))
        for k in ("bands", "seed", "reference_hz", "reference_ppm", "termination_part", "sensor_id"):
            self.assertEqual(a[k], b[k])

    def test_chain_fields_are_bound(self):
        for mutate in (lambda d: d.update(gain_db=34.0),
                       lambda d: d["receiver"].update(tuner="R820T2")):
            d = copy.deepcopy(self.draft); mutate(d)
            with self.assertRaises(seq.SequencerRefused): seq.check_declaration_authorized(d)

    def test_29_7_with_the_e4000_family_fails_well_formedness(self):
        d = copy.deepcopy(self.draft) | self.run; d["gain_db"] = 29.7
        with self.assertRaises(seq.SequencerRefused) as cm: seq.check_declaration(d)
        self.assertEqual(cm.exception.code, "GAIN_NOT_SUPPORTED")

    def test_unknown_family_is_refused(self):
        d = copy.deepcopy(self.draft) | self.run; d["receiver"]["tuner"] = "FC0013"
        with self.assertRaises(seq.SequencerRefused) as cm: seq.check_declaration(d)
        self.assertEqual(cm.exception.code, "TUNER_FAMILY_UNKNOWN")

    def test_r820t2_chain_digests_are_unchanged_and_e4000_differs(self):
        old = copy.deepcopy(self.old) | self.run
        legacy = seq._chain_digest(old)
        explicit = copy.deepcopy(old); explicit["receiver"]["tuner"] = "R820T2"
        self.assertEqual(seq._chain_digest(explicit), legacy)
        self.assertNotEqual(seq._chain_digest(self.draft | self.run), legacy)


if __name__ == "__main__":
    unittest.main()
