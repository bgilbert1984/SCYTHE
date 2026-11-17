import copy
import hashlib
import json
import pathlib
import unittest

import rf_antenna_on_declaration as aod
import rf_canary_gate as gate

DOCS = pathlib.Path(__file__).parent / "docs"
DECL = json.load(open(DOCS / "521_ANTENNA_ON_OBSERVATION_DECLARATION_XTR_E4000.json"))
TIER = json.load(open(DOCS / "521_INTRINSIC_TIER_XTR_E4000.json"))


DECL_FILE_SHA256 = "94b86ebe99f88c4963d62553516d02af92703f2c4e33af36a56d9dfb032d795c"
TIER_FILE_SHA256 = "c46f56a6a29026c4c8baf7ce55e195d0e67b5018d95404e88f6ac1e183dd699a"
STAGED_DECL_SHA256 = "da92f287249ceb362c1e56599b11eac4b7a1973254ca23fc290dca7064bfec15"


def _sha(name):
    return hashlib.sha256((DOCS / name).read_bytes()).hexdigest()


class FrozenDeclaration(unittest.TestCase):
    def test_frozen_files_are_byte_pinned(self):
        self.assertEqual(_sha("521_ANTENNA_ON_OBSERVATION_DECLARATION_XTR_E4000.json"), DECL_FILE_SHA256)
        self.assertEqual(_sha("521_INTRINSIC_TIER_XTR_E4000.json"), TIER_FILE_SHA256)

    def test_the_freeze_records_what_the_owner_accepted(self):
        self.assertEqual(DECL["frozen"]["accepted_tier_artifact_sha256"], TIER_FILE_SHA256)
        self.assertEqual(DECL["frozen"]["accepted_staged_declaration_sha256"], STAGED_DECL_SHA256)
        self.assertEqual(len(DECL["accepted_assumptions"]), 3)

    def test_the_frozen_declaration_is_consistent_and_registered(self):
        r = aod.check_declaration(DECL, TIER)
        self.assertTrue(r["ok"], r["codes"])
        self.assertTrue(r["authorizes"])
        self.assertEqual(DECL["status"], "FROZEN")
        self.assertEqual(DECL["mode"], "OBSERVATION_ONLY")

    def test_tier_offsets_are_plus_minus_256_khz_bins(self):
        self.assertEqual(sorted(gate.tier_offsets(TIER)), [-255996.0, 255996.0])

    def test_authorization_follows_status_and_flag_not_either_alone(self):
        d = copy.deepcopy(DECL)
        d["authorizes"] = False
        self.assertFalse(aod.check_declaration(d, TIER)["authorizes"])
        d = copy.deepcopy(DECL)
        d["status"] = "DRAFT"
        self.assertFalse(aod.check_declaration(d, TIER)["authorizes"])


class DraftNeverAuthorizes(unittest.TestCase):
    def test_draft_status_with_authorizes_true_does_not_authorize(self):
        d = copy.deepcopy(DECL)
        d["status"], d["authorizes"] = "DRAFT", True
        self.assertFalse(aod.check_declaration(d, TIER)["authorizes"])


class Refusals(unittest.TestCase):
    def codes(self, mutate, tier=None):
        d = copy.deepcopy(DECL)
        mutate(d)
        return aod.check_declaration(d, tier if tier is not None else TIER)["codes"]

    def test_scope_fields(self):
        self.assertIn("AOD_MODE_NOT_OBSERVATION_ONLY", self.codes(lambda d: d.update(mode="CATALOGUE")))
        self.assertIn("AOD_GAIN_OUT_OF_SCOPE", self.codes(lambda d: d.update(gain_db=34.0)))
        self.assertIn("AOD_TUNER_OUT_OF_SCOPE", self.codes(lambda d: d["receiver"].update(tuner="R820T2")))
        self.assertIn("AOD_TERMINATION_CLAIM_PRESENT", self.codes(lambda d: d["termination"].update(fitted=True)))

    def test_bands_outside_400_470_are_refused(self):
        for lo, hi in ((88_000_000, 108_000_000), (700_000_000, 760_000_000), (860_000_000, 900_000_000), (380_000_000, 440_000_000)):
            self.assertIn("AOD_BAND_OUT_OF_SCOPE", self.codes(lambda d: d.update(bands=[{"band_id": "X", "low_hz": lo, "high_hz": hi}])))

    def test_gate_constants_must_equal_the_code(self):
        self.assertIn("AOD_GATE_CONSTANTS_DIFFER", self.codes(lambda d: d["gate"].update(canary_min_excess_db=10.0)))
        self.assertIn("AOD_GATE_CONSTANTS_DIFFER", self.codes(lambda d: d["gate"].update(clip_limit_pct=0.002)))
        self.assertIn("AOD_TUNING_GATE_NOT_EVERY_VISIT", self.codes(lambda d: d["gate"].update(tuning_gate="2_OF_3")))

    def test_tier_must_be_the_bound_one(self):
        self.assertIn("AOD_TIER_HASH_NOT_BOUND", self.codes(lambda d: d["intrinsic_tier"].update(content_sha256="0" * 64)))
        self.assertIn("AOD_TIER_OFFSETS_DIFFER", self.codes(lambda d: d["intrinsic_tier"].update(offsets_hz=[255996.0])))
        altered = copy.deepcopy(TIER)
        altered["intrinsic"].append({"offset_hz": 512000.0, "tier": "INTRINSIC_REPRODUCIBLE"})
        self.assertIn("AOD_TIER_TIER_HASH_MISMATCH", self.codes(lambda d: None, tier=altered))
        unhashed = {k: v for k, v in TIER.items() if k != "content_sha256"}
        self.assertIn("AOD_TIER_TIER_UNHASHED", self.codes(lambda d: None, tier=unhashed))

    def test_schedule_digest_must_match_the_tier_sessions(self):
        self.assertIn("AOD_SCHEDULE_DIGEST_NOT_BOUND", self.codes(lambda d: d.update(schedule_digest="0" * 64)))
        self.assertIn("AOD_SCHEDULE_DIGEST_NOT_BOUND", self.codes(lambda d: d.pop("schedule_digest")))

    def test_a_json_boolean_is_not_a_revalidation_interval(self):
        self.assertIn("AOD_REVALIDATION_INTERVAL_INVALID", self.codes(lambda d: d["revalidation"].update(interval_months=True)))

    def test_revalidation_is_bounded_and_keeps_its_triggers(self):
        self.assertIn("AOD_REVALIDATION_INTERVAL_INVALID", self.codes(lambda d: d["revalidation"].update(interval_months=24)))
        self.assertIn("AOD_REVALIDATION_INTERVAL_INVALID", self.codes(lambda d: d["revalidation"].update(interval_months=0)))
        self.assertIn("AOD_REVALIDATION_TRIGGERS_MISSING", self.codes(lambda d: d["revalidation"].update(triggers=["HARDWARE_CHANGE"])))


if __name__ == "__main__":
    unittest.main()
