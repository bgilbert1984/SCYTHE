"""The antenna is declared, never detected."""

import unittest

from graphops_rf_antenna import (
    AUTODETECT_REASON, AntennaDeclarationRefused, AntennaDeclarationStore,
    COMPARABILITY_FIELDS, DECLARATION_HASH_FIELDS, catalogue, declaration_hash,
    declaration_receipt, instrument_hash, validate_declaration,
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


class ExtensionComparabilityTests(unittest.TestCase):
    """Retracting a telescopic mast builds a different instrument out of the same part."""

    @staticmethod
    def _telescopic(extension_mm=None, feedline_id="nesdr-magnetic-base-rg58-2m"):
        payload = {"antenna_id": "nesdr-smart-telescopic", "feedline_id": feedline_id}
        if extension_mm is not None:
            payload["extension_mm"] = extension_mm
        return validate_declaration(payload)

    def test_retracting_the_mast_changes_the_signal_chain(self):
        fm = self._telescopic(730)
        ism = self._telescopic(165)
        receipt = declaration_receipt(ism, previous=fm)
        self.assertTrue(receipt["signalChainChanged"])
        self.assertEqual(receipt["changedFields"], ["extension_mm"])
        self.assertEqual(receipt["previousExtensionMm"], 730.0)
        self.assertIn("NOT DIRECTLY COMPARABLE", " ".join(receipt["boundaries"]))

    def test_the_boundary_names_the_extension_and_the_resonance_it_moved(self):
        receipt = declaration_receipt(self._telescopic(165), previous=self._telescopic(730))
        joined = " ".join(receipt["boundaries"])
        self.assertIn("730 mm", joined)
        self.assertIn("165 mm", joined)
        # Free-space quarter wave: c/4L is 102.7 MHz at 730 mm, 454.2 MHz at 165 mm.
        self.assertIn("102.7 MHz", joined)
        self.assertIn("454.2 MHz", joined)
        self.assertIn("THE MAST IS THE SAME PART; THE INSTRUMENT IS NOT", joined)

    def test_declaring_an_extension_for_the_first_time_is_a_change(self):
        receipt = declaration_receipt(self._telescopic(730), previous=self._telescopic())
        self.assertTrue(receipt["signalChainChanged"])
        self.assertIn("UNDECLARED", " ".join(receipt["boundaries"]))

    def test_the_boundary_agrees_with_the_hash_on_every_field_it_covers(self):
        """Whatever moves the declaration hash must also raise the boundary."""
        base = self._telescopic(730)
        variants = [
            self._telescopic(731),
            self._telescopic(730, feedline_id="direct"),
            validate_declaration({"antenna_id": "nesdr-smart-uhf"}),
        ]
        for variant in variants:
            with self.subTest(variant=variant["antenna_id"], ext=variant["extension_mm"]):
                receipt = declaration_receipt(variant, previous=base)
                hashed_differently = (receipt["declarationHash"]
                                      != declaration_receipt(base)["declarationHash"])
                self.assertTrue(hashed_differently)
                self.assertTrue(receipt["signalChainChanged"])

    def test_a_reworded_note_is_not_an_instrument_change(self):
        first = validate_declaration({"antenna_id": "nesdr-smart-telescopic",
                                      "extension_mm": 730, "note": "on the filing cabinet"})
        second = validate_declaration({"antenna_id": "nesdr-smart-telescopic",
                                       "extension_mm": 730, "note": "on a steel cabinet"})
        receipt = declaration_receipt(second, previous=first)
        self.assertNotIn("note", COMPARABILITY_FIELDS)
        self.assertFalse(receipt["signalChainChanged"])
        self.assertEqual(receipt["changedFields"], [])
        self.assertNotIn("NOT DIRECTLY COMPARABLE", " ".join(receipt["boundaries"]))

    def test_changing_the_feedline_changes_the_signal_chain(self):
        direct = self._telescopic(730, feedline_id="direct")
        cabled = self._telescopic(730)
        receipt = declaration_receipt(cabled, previous=direct)
        self.assertEqual(receipt["changedFields"], ["feedline_id"])
        self.assertEqual(receipt["previousFeedlineId"], "direct")
        self.assertIn("MAGNETIC BASE", " ".join(receipt["boundaries"]))

    def test_an_unchanged_declaration_raises_no_boundary(self):
        record = self._telescopic(730)
        receipt = declaration_receipt(self._telescopic(730), previous=record)
        self.assertFalse(receipt["signalChainChanged"])
        self.assertEqual(receipt["changedFields"], [])


class ExtensionSurvivesRestartTests(unittest.TestCase):
    """The store is volatile; the environment is what outlives a reboot."""

    def setUp(self):
        import os
        self.os = os
        self.saved = {key: os.environ.get(key) for key in
                      ("SDRPP_ANTENNA_ID", "SDRPP_FEEDLINE_ID", "SDRPP_ANTENNA_EXTENSION_MM")}

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                self.os.environ.pop(key, None)
            else:
                self.os.environ[key] = value

    def _env(self, **values):
        for key, value in values.items():
            self.os.environ[key] = value

    def test_the_configured_extension_is_adopted_at_startup(self):
        self._env(SDRPP_ANTENNA_ID="nesdr-smart-telescopic",
                  SDRPP_FEEDLINE_ID="nesdr-magnetic-base-rg58-2m",
                  SDRPP_ANTENNA_EXTENSION_MM="730")
        record = AntennaDeclarationStore().bootstrap_from_env()
        self.assertEqual(record["extension_mm"], 730.0)
        self.assertEqual(record["feedline_id"], "nesdr-magnetic-base-rg58-2m")
        self.assertEqual(record["quarter_wave_hz"], 102_668_650)
        self.assertEqual(record["quarter_wave_authority"], "DERIVED_INFERENCE")
        # Configuration is still the operator speaking, never a measurement.
        self.assertEqual(record["authority"], "OPERATOR_DECLARED")

    def test_an_unusable_extension_leaves_the_antenna_undeclared(self):
        """It must not degrade into the same mast with no extension and no complaint."""
        self._env(SDRPP_ANTENNA_ID="nesdr-smart-telescopic",
                  SDRPP_ANTENNA_EXTENSION_MM="0.73")  # metres, meant as millimetres
        store = AntennaDeclarationStore()
        self.assertIsNone(store.bootstrap_from_env())
        self.assertEqual(store.current()["state"], "UNDECLARED")

    def test_an_extension_on_a_fixed_mast_refuses_the_whole_bootstrap(self):
        self._env(SDRPP_ANTENNA_ID="nesdr-smart-uhf", SDRPP_ANTENNA_EXTENSION_MM="730")
        self.assertIsNone(AntennaDeclarationStore().bootstrap_from_env())

    def test_an_absent_extension_still_declares_the_mast(self):
        self.os.environ.pop("SDRPP_ANTENNA_EXTENSION_MM", None)
        self._env(SDRPP_ANTENNA_ID="nesdr-smart-telescopic")
        record = AntennaDeclarationStore().bootstrap_from_env()
        self.assertIsNone(record["extension_mm"])
        self.assertEqual(record["quarter_wave_authority"], "UNDECLARED")


class InstrumentVersusDeclarationTests(unittest.TestCase):
    """Two hashes, because one was answering two different questions.

    ``instrument_hash`` says what the products came through.
    ``declaration_hash`` says what the operator asserted about it.
    Prose belongs to the second and must never reach the first.
    """

    @staticmethod
    def _mast(extension_mm=730, note="", **extra):
        return validate_declaration({"antenna_id": "nesdr-smart-telescopic",
                                     "feedline_id": "nesdr-magnetic-base-rg58-2m",
                                     "extension_mm": extension_mm, "note": note, **extra})

    def test_note_is_declaration_prose_and_never_instrument_identity(self):
        self.assertNotIn("note", COMPARABILITY_FIELDS)
        self.assertIn("note", DECLARATION_HASH_FIELDS)

    def test_rewording_a_note_advances_only_the_declaration_hash(self):
        first = self._mast(note="on the filing cabinet")
        second = self._mast(note="on a steel filing cabinet")
        self.assertNotEqual(declaration_hash(first), declaration_hash(second))
        self.assertEqual(instrument_hash(first), instrument_hash(second))
        receipt = declaration_receipt(second, previous=first)
        self.assertFalse(receipt["signalChainChanged"])
        self.assertEqual(receipt["changedFields"], [])
        self.assertEqual(receipt["instrumentHash"], receipt["previousInstrumentHash"])

    def test_changing_the_extension_advances_both_hashes(self):
        fm, ism = self._mast(730), self._mast(165)
        self.assertNotEqual(instrument_hash(fm), instrument_hash(ism))
        self.assertNotEqual(declaration_hash(fm), declaration_hash(ism))
        receipt = declaration_receipt(ism, previous=fm)
        self.assertNotEqual(receipt["instrumentHash"], receipt["previousInstrumentHash"])

    def test_the_boundary_and_the_instrument_hash_never_disagree(self):
        """signalChainChanged is true exactly when the instrument hash moved."""
        base = self._mast(730, note="base")
        for label, variant in (
            ("same", self._mast(730, note="base")),
            ("note", self._mast(730, note="reworded")),
            ("extension", self._mast(165, note="base")),
            ("feedline", validate_declaration({"antenna_id": "nesdr-smart-telescopic",
                                               "feedline_id": "direct",
                                               "extension_mm": 730, "note": "base"})),
            ("antenna", validate_declaration({"antenna_id": "nesdr-smart-uhf",
                                              "note": "base"})),
            ("authority", self._mast(730, note="base",
                                     extension_authority="OPERATOR_MEASURED")),
        ):
            with self.subTest(change=label):
                receipt = declaration_receipt(variant, previous=base)
                moved = instrument_hash(variant) != instrument_hash(base)
                self.assertEqual(receipt["signalChainChanged"], moved)

    def test_how_the_extension_was_arrived_at_is_not_the_instrument(self):
        """A ruler instead of arithmetic is a better claim about the same geometry."""
        estimated = self._mast(730, extension_authority="OPERATOR_ESTIMATED")
        measured = self._mast(730, extension_authority="OPERATOR_MEASURED")
        self.assertEqual(instrument_hash(estimated), instrument_hash(measured))
        self.assertNotEqual(declaration_hash(estimated), declaration_hash(measured))
        self.assertFalse(declaration_receipt(measured, previous=estimated)["signalChainChanged"])

    def test_a_measurement_claim_is_never_the_default(self):
        self.assertEqual(self._mast(730)["extension_authority"], "OPERATOR_ESTIMATED")
        self.assertEqual(self._mast(None)["extension_authority"], "UNDECLARED")
        with self.assertRaises(AntennaDeclarationRefused):
            self._mast(730, extension_authority="MEASURED")
        with self.assertRaises(AntennaDeclarationRefused):
            validate_declaration({"antenna_id": "nesdr-smart-uhf",
                                  "extension_authority": "OPERATOR_MEASURED"})

    def test_the_derived_frequency_names_its_model_and_claims_no_resonance(self):
        record = self._mast(173)
        # 299792458 / (4 x 0.173) exactly. 433.92 MHz wants 172.723 mm, so a mast
        # set to a whole 173 mm derives 433.226 MHz -- 694 kHz below the target,
        # which is the rounding, not a defect. The number describes the geometry
        # that was declared, never the frequency that was intended.
        self.assertEqual(record["quarter_wave_hz"], 433_226_095)
        self.assertEqual(record["quarter_wave_model"], "IDEAL_FREE_SPACE")
        self.assertEqual(record["quarter_wave_authority"], "DERIVED_INFERENCE")
        self.assertEqual(record["resonance_claim"], "NOT_MEASURED")
        self.assertNotIn("MEASURED", record["quarter_wave_authority"])
        undeclared = self._mast(None)
        self.assertEqual(undeclared["quarter_wave_model"], "UNDECLARED")
        self.assertEqual(undeclared["resonance_claim"], "NOT_MEASURED")


class ComparabilityReachesTheProductHashTests(unittest.TestCase):
    """The receipt and the product chain identity must agree, end to end.

    A receipt that stays quiet while a downstream hash moves -- or complains
    while it does not -- is not one system. These two tests are the contract.
    """

    def setUp(self):
        import os
        from rf_iq_retention import signal_chain_hash
        self.os = os
        self.signal_chain_hash = signal_chain_hash
        self.saved = {key: os.environ.get(key) for key in
                      ("SDRPP_ANTENNA_ID", "SDRPP_FEEDLINE_ID", "SDRPP_ANTENNA_EXTENSION_MM")}
        os.environ["SDRPP_ANTENNA_ID"] = "nesdr-smart-telescopic"
        os.environ["SDRPP_FEEDLINE_ID"] = "nesdr-magnetic-base-rg58-2m"

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                self.os.environ.pop(key, None)
            else:
                self.os.environ[key] = value

    def _chain(self, extension_mm):
        self.os.environ["SDRPP_ANTENNA_EXTENSION_MM"] = str(extension_mm)
        return self.signal_chain_hash(sensor_id="NESDR-SMART-V5-14530058",
                                      sample_type="uint8", sample_rate_hz=2_048_000.0)

    @staticmethod
    def _declared(extension_mm, note):
        return validate_declaration({"antenna_id": "nesdr-smart-telescopic",
                                     "feedline_id": "nesdr-magnetic-base-rg58-2m",
                                     "extension_mm": extension_mm, "note": note})

    def test_changing_the_note_invalidates_nothing_anywhere(self):
        before, after = self._declared(730, "first wording"), self._declared(730, "second wording")
        receipt = declaration_receipt(after, previous=before)
        self.assertNotEqual(declaration_hash(before), declaration_hash(after))
        self.assertEqual(instrument_hash(before), instrument_hash(after))
        # The product chain identity does not read the note and cannot move for it.
        self.assertEqual(self._chain(730), self._chain(730))
        self.assertFalse(receipt["signalChainChanged"])
        self.assertNotIn("NOT DIRECTLY COMPARABLE", " ".join(receipt["boundaries"]))

    def test_changing_the_extension_invalidates_everywhere(self):
        before, after = self._declared(730, "same wording"), self._declared(165, "same wording")
        receipt = declaration_receipt(after, previous=before)
        self.assertNotEqual(instrument_hash(before), instrument_hash(after))
        self.assertNotEqual(self._chain(730), self._chain(165))
        self.assertTrue(receipt["signalChainChanged"])
        self.assertEqual(receipt["changedFields"], ["extension_mm"])
        self.assertIn("EXTENSION_MM", " ".join(receipt["changedFields"]).upper())
        self.assertIn("NOT DIRECTLY COMPARABLE", " ".join(receipt["boundaries"]))

    def test_an_unusable_configured_extension_is_its_own_chain(self):
        """It must not hash as though nothing had been configured."""
        self.os.environ["SDRPP_ANTENNA_EXTENSION_MM"] = ""
        undeclared = self.signal_chain_hash(sensor_id="S", sample_type="uint8",
                                            sample_rate_hz=2_048_000.0)
        self.os.environ["SDRPP_ANTENNA_EXTENSION_MM"] = "0.73"
        refused = self.signal_chain_hash(sensor_id="S", sample_type="uint8",
                                         sample_rate_hz=2_048_000.0)
        self.assertNotEqual(undeclared, refused)


if __name__ == "__main__":
    unittest.main()
