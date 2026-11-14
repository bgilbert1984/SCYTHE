"""The XTR authorization is accepted, bound to its chain, and leaves the NESDR one intact."""
import copy, json, os, unittest
import rf_spur_catalogue_sequencer as seq

HERE = os.path.dirname(os.path.abspath(__file__))
def load(name):
    with open(os.path.join(HERE, "docs", name)) as h: return json.load(h)


class XtrAuthorization(unittest.TestCase):
    def setUp(self):
        self.xtr = load("521_AUTHORIZED_RUN_DECLARATION_XTR.json")
        self.nesdr = load("521_AUTHORIZED_RUN_DECLARATION.json")

    def test_xtr_accepts_itself_and_v5_validates_as_history(self):
        seq.check_declaration_authorized(self.xtr)
        # the v5 authorization is retired: a new run is refused, history still validates (test_rf_521_v5_retirement.py)
        seq.check_declaration_authorized(self.nesdr, historical=True)

    def test_xtr_shares_the_schedule_fields(self):
        for k in ("bands", "gain_db", "seed", "reference_hz", "reference_ppm"):
            self.assertEqual(self.xtr[k], self.nesdr[k])
        self.assertEqual(self.xtr["receiver"]["sensor_id"], "71874552")

    def test_xtr_chain_fields_are_bound(self):
        for mutate in (lambda d: d.update(gain_db=20.7),
                       lambda d: d.update(seed=1),
                       lambda d: d.update(reference_ppm=0.5),
                       lambda d: d["termination"].update(part="other"),
                       lambda d: d["receiver"].update(sensor_id="99999999")):
            d = copy.deepcopy(self.xtr); mutate(d)
            with self.assertRaises(seq.SequencerRefused) as cm:
                seq.check_declaration_authorized(d)
            self.assertEqual(cm.exception.code, "DECLARATION_NOT_AUTHORIZED")

    def test_operational_fields_are_not_bound(self):
        d = copy.deepcopy(self.xtr); d.update(run_id="x", output_dir="/elsewhere")
        seq.check_declaration_authorized(d)

    def test_declarations_are_well_formed(self):
        seq.check_declaration(self.xtr | dict(rtl_tcp_host="127.0.0.1", rtl_tcp_port=1234, output_dir="/x"))


if __name__ == "__main__":
    unittest.main()
