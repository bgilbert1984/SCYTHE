"""The v5 authorization is retired for new runs, still valid as history, and its file is untouched."""
import copy, hashlib, json, os, tempfile, unittest

import rf_spur_catalogue_sequencer as seq

HERE = os.path.dirname(os.path.abspath(__file__))
V5 = os.path.join(HERE, "docs", "521_AUTHORIZED_RUN_DECLARATION.json")
V5_SHA256 = "1168f24841f500f771a7e446de963216a00ff2045022180e48d87ee92a1b2e67"


class FakeSocket:
    def __init__(self): self.sent = []
    def recv(self, n): return b""
    def sendall(self, d): self.sent.append(bytes(d))
    def settimeout(self, _t): pass
    def close(self): pass


class V5Retirement(unittest.TestCase):
    def setUp(self):
        with open(V5) as h: self.v5 = json.load(h)

    def test_v5_authorization_file_is_byte_identical(self):
        with open(V5, "rb") as h: self.assertEqual(hashlib.sha256(h.read()).hexdigest(), V5_SHA256)

    def test_new_run_for_the_v5_is_refused_as_retired(self):
        with self.assertRaises(seq.SequencerRefused) as cm: seq.check_declaration_authorized(self.v5)
        self.assertEqual(cm.exception.code, "RECEIVER_RETIRED")
        self.assertIn("14530058", str(cm.exception))

    def test_history_still_validates_against_the_v5_authorization(self):
        seq.check_declaration_authorized(self.v5, historical=True)
        bad = copy.deepcopy(self.v5); bad["gain_db"] = 20.7
        with self.assertRaises(seq.SequencerRefused) as cm: seq.check_declaration_authorized(bad, historical=True)
        self.assertEqual(cm.exception.code, "DECLARATION_NOT_AUTHORIZED")

    def test_historical_flag_does_not_authorize_the_xtr_chain_on_the_wrong_receiver(self):
        d = copy.deepcopy(self.v5); d["receiver"]["sensor_id"] = "71874552"
        with self.assertRaises(seq.SequencerRefused) as cm: seq.check_declaration_authorized(d)
        self.assertEqual(cm.exception.code, "DECLARATION_NOT_AUTHORIZED")

    def test_run_epoch_refuses_a_v5_declaration_before_any_contact_or_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            decl = copy.deepcopy(self.v5)
            decl.update(rtl_tcp_host="127.0.0.1", rtl_tcp_port=1234, output_dir=os.path.join(tmp, "out"))
            fake = FakeSocket()
            tuner = seq.RtlTcpTuner("fake", 1, socket_factory=lambda _a: fake)
            with self.assertRaises(seq.SequencerRefused) as cm: seq.run_epoch(decl, tuner)
            self.assertEqual(cm.exception.code, "RECEIVER_RETIRED")
            self.assertEqual(fake.sent, []); self.assertFalse(os.path.exists(decl["output_dir"]))

    def test_the_xtr_authorization_is_still_accepted_for_new_runs(self):
        with open(os.path.join(HERE, "docs", "521_AUTHORIZED_RUN_DECLARATION_XTR.json")) as h:
            seq.check_declaration_authorized(json.load(h))


if __name__ == "__main__":
    unittest.main()
