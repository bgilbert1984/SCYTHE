"""The antenna is declared, never detected."""

import unittest

from graphops_rf_antenna import (
    AUTODETECT_REASON, AntennaDeclarationRefused, AntennaDeclarationStore,
    catalogue, declaration_receipt, validate_declaration,
)


class AntennaDeclarationTests(unittest.TestCase):
    def test_catalogue_states_detection_is_impossible(self):
        entry = catalogue()
        self.assertFalse(entry["autoDetectable"])
        self.assertIn("NO BIAS TEE", entry["autoDetectionNote"])
        self.assertIn("REFLECTOMETER", entry["autoDetectionNote"])

    def test_vendor_omissions_are_preserved_as_null(self):
        antennas = {item["id"]: item for item in catalogue()["antennas"]}
        self.assertIsNone(antennas["nesdr-smart-uhf"]["resonance_hz"])
        self.assertIsNone(antennas["nesdr-smart-telescopic"]["resonance_hz"])
        self.assertEqual(antennas["nesdr-smart-433-ism"]["resonance_hz"], 433e6)

    def test_declaration_is_operator_authority(self):
        record = validate_declaration({"antenna_id": "nesdr-smart-433-ism"})
        self.assertEqual(record["authority"], "OPERATOR_DECLARED")
        self.assertFalse(record["auto_detected"])
        self.assertEqual(record["auto_detection_note"], AUTODETECT_REASON)
        self.assertEqual(record["resonance_authority"], "VENDOR_DECLARED")

    def test_an_undeclared_feedline_is_the_default_not_a_direct_connection(self):
        """A default is configuration convenience, not physical evidence.

        Nothing in a receive-only path can distinguish a mast screwed onto the
        SMA from the same mast on 2 m of RG58, so defaulting to "direct" would
        publish a cable path nobody attested to.
        """
        record = validate_declaration({"antenna_id": "nesdr-smart-uhf"})
        self.assertEqual(record["feedline_id"], "undeclared")
        self.assertEqual(record["feedline_label"], "FEEDLINE UNDECLARED")
        self.assertIsNone(record["feedline_length_m"])
        self.assertEqual(record["feedline_authority"], "UNDECLARED")
        # The antenna itself is still a declaration; only the cable is unknown.
        self.assertEqual(record["authority"], "OPERATOR_DECLARED")

    def test_a_stated_feedline_is_recorded_as_operator_declared(self):
        record = validate_declaration({"antenna_id": "nesdr-smart-uhf",
                                       "feedline_id": "nesdr-magnetic-base-rg58-2m"})
        self.assertEqual(record["feedline_label"], "MAGNETIC BASE \u00b7 2 m RG58")
        self.assertEqual(record["feedline_length_m"], 2.0)
        self.assertEqual(record["feedline_authority"], "OPERATOR_DECLARED")

    def test_unknown_antenna_or_feedline_is_refused(self):
        with self.assertRaises(AntennaDeclarationRefused):
            validate_declaration({"antenna_id": "discone"})
        with self.assertRaises(AntennaDeclarationRefused):
            validate_declaration({"antenna_id": "nesdr-smart-uhf", "feedline_id": "fibre"})
        with self.assertRaises(AntennaDeclarationRefused):
            validate_declaration({"antenna_id": "nesdr-smart-uhf", "gain_dbi": 3})
        with self.assertRaises(AntennaDeclarationRefused):
            validate_declaration("nesdr-smart-uhf")

    def test_extension_derives_a_quarter_wave_labelled_as_derived(self):
        record = validate_declaration(
            {"antenna_id": "nesdr-smart-telescopic", "extension_mm": 750})
        self.assertEqual(record["quarter_wave_hz"], round(299_792_458 / 3))
        self.assertEqual(record["quarter_wave_authority"], "DERIVED_INFERENCE")

    def test_a_fixed_mast_refuses_an_extension_it_cannot_have(self):
        with self.assertRaises(AntennaDeclarationRefused):
            validate_declaration({"antenna_id": "nesdr-smart-uhf", "extension_mm": 750})
        with self.assertRaises(AntennaDeclarationRefused):
            validate_declaration(
                {"antenna_id": "nesdr-smart-telescopic", "extension_mm": 9000})

    def test_receipt_is_never_retroactive(self):
        record = validate_declaration({"antenna_id": "nesdr-smart-uhf"})
        receipt = declaration_receipt(record)
        self.assertFalse(receipt["retroactive"])
        self.assertFalse(receipt["autoDetected"])
        self.assertEqual(len(receipt["declarationHash"]), 64)
        joined = " ".join(receipt["boundaries"])
        self.assertIn("APPLIES FORWARD ONLY", joined)
        self.assertIn("PRODUCTS ALREADY EMITTED", joined)

    def test_changing_the_antenna_marks_the_signal_chain_as_changed(self):
        first = validate_declaration({"antenna_id": "nesdr-smart-uhf"})
        second = validate_declaration({"antenna_id": "nesdr-smart-433-ism"})
        unchanged = declaration_receipt(first)
        self.assertFalse(unchanged["signalChainChanged"])
        changed = declaration_receipt(second, previous=first)
        self.assertTrue(changed["signalChainChanged"])
        self.assertEqual(changed["previousAntennaId"], "nesdr-smart-uhf")
        self.assertIn("NOT DIRECTLY COMPARABLE", " ".join(changed["boundaries"]))

    def test_the_same_declaration_hashes_identically(self):
        one = declaration_receipt(validate_declaration({"antenna_id": "nesdr-smart-uhf"}))
        two = declaration_receipt(validate_declaration({"antenna_id": "nesdr-smart-uhf"}))
        self.assertEqual(one["declarationHash"], two["declarationHash"])

    def test_store_starts_undeclared_and_records_a_declaration(self):
        store = AntennaDeclarationStore()
        self.assertEqual(store.current()["state"], "UNDECLARED")
        self.assertFalse(store.current()["declared"])
        record, receipt = store.declare({"antenna_id": "nesdr-smart-433-ism"})
        current = store.current()
        self.assertTrue(current["declared"])
        self.assertEqual(current["antenna"]["antenna_id"], record["antenna_id"])
        self.assertEqual(current["receipt"]["declarationHash"], receipt["declarationHash"])
        store.clear()
        self.assertEqual(store.current()["state"], "UNDECLARED")

    def test_a_refused_declaration_leaves_the_previous_one_standing(self):
        store = AntennaDeclarationStore()
        store.declare({"antenna_id": "nesdr-smart-uhf"})
        with self.assertRaises(AntennaDeclarationRefused):
            store.declare({"antenna_id": "discone"})
        self.assertEqual(store.current()["antenna"]["antenna_id"], "nesdr-smart-uhf")

    def test_unspecified_env_value_is_not_promoted_into_a_declaration(self):
        import os
        store = AntennaDeclarationStore()
        previous = os.environ.get("SDRPP_ANTENNA_ID")
        try:
            os.environ["SDRPP_ANTENNA_ID"] = "unspecified"
            self.assertIsNone(store.bootstrap_from_env())
            self.assertEqual(store.current()["state"], "UNDECLARED")
            os.environ["SDRPP_ANTENNA_ID"] = "nesdr-smart-uhf"
            self.assertIsNotNone(store.bootstrap_from_env())
            self.assertEqual(store.current()["antenna"]["antenna_id"], "nesdr-smart-uhf")
        finally:
            if previous is None:
                os.environ.pop("SDRPP_ANTENNA_ID", None)
            else:
                os.environ["SDRPP_ANTENNA_ID"] = previous


if __name__ == "__main__":
    unittest.main()
