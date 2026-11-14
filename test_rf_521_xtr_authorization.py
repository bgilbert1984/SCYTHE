"""XTR authorizations: the E4000 freeze is current; the 2026-11-03 XTR file and the v5 file are history only."""
import copy, hashlib, json, os, unittest
import rf_spur_catalogue_sequencer as seq

HERE = os.path.dirname(os.path.abspath(__file__))
def load(name):
    with open(os.path.join(HERE, "docs", name)) as h: return json.load(h)


class XtrAuthorization(unittest.TestCase):
    def setUp(self):
        self.current = load("521_AUTHORIZED_RUN_DECLARATION_XTR_E4000.json")
        self.xtr = load("521_AUTHORIZED_RUN_DECLARATION_XTR.json")      # superseded 2026-11-14
        self.nesdr = load("521_AUTHORIZED_RUN_DECLARATION.json")        # retired

    def test_superseded_xtr_file_is_byte_identical(self):
        with open(os.path.join(HERE, "docs", "521_AUTHORIZED_RUN_DECLARATION_XTR.json"), "rb") as h:
            self.assertEqual(hashlib.sha256(h.read()).hexdigest(),
                             "2286f4cc0a92739b2e72d3d5014a9a672666043903f81b16ceaf31da03ec4a2a")

    def test_current_freeze_accepts_itself_for_new_runs(self):
        seq.check_declaration_authorized(self.current)

    def test_superseded_files_validate_history_only(self):
        seq.check_declaration_authorized(self.xtr, historical=True)
        seq.check_declaration_authorized(self.nesdr, historical=True)
        with self.assertRaises(seq.SequencerRefused) as cm: seq.check_declaration_authorized(self.xtr)
        self.assertEqual(cm.exception.code, "DECLARATION_NOT_AUTHORIZED")
        msg = str(cm.exception)
        self.assertIn("gain_db", msg); self.assertIn("29.0", msg); self.assertIn("tuner", msg)

    def test_xtr_shares_the_schedule_fields(self):
        for k in ("bands", "seed", "reference_hz", "reference_ppm"):
            self.assertEqual(self.current[k], self.nesdr[k]); self.assertEqual(self.xtr[k], self.nesdr[k])
        self.assertEqual(self.xtr["receiver"]["sensor_id"], "71874552")
        self.assertEqual(self.current["receiver"]["sensor_id"], "71874552")

    def test_current_chain_fields_are_bound(self):
        for mutate in (lambda d: d.update(gain_db=20.7),
                       lambda d: d.update(gain_db=29.7),
                       lambda d: d.update(seed=1),
                       lambda d: d.update(reference_ppm=0.5),
                       lambda d: d["termination"].update(part="other"),
                       lambda d: d["receiver"].update(tuner="R820T2"),
                       lambda d: d["receiver"].update(sensor_id="99999999")):
            d = copy.deepcopy(self.current); mutate(d)
            with self.assertRaises(seq.SequencerRefused) as cm:
                seq.check_declaration_authorized(d)
            self.assertEqual(cm.exception.code, "DECLARATION_NOT_AUTHORIZED")

    def test_history_chain_fields_are_still_bound(self):
        d = copy.deepcopy(self.xtr); d["gain_db"] = 20.7
        with self.assertRaises(seq.SequencerRefused):
            seq.check_declaration_authorized(d, historical=True)

    def test_operational_fields_are_not_bound(self):
        d = copy.deepcopy(self.current); d.update(run_id="x", output_dir="/elsewhere")
        seq.check_declaration_authorized(d)

    def test_declarations_are_well_formed(self):
        run = dict(rtl_tcp_host="127.0.0.1", rtl_tcp_port=1234, output_dir="/x")
        seq.check_declaration(self.current | run)
        seq.check_declaration(self.xtr | run)


if __name__ == "__main__":
    unittest.main()
