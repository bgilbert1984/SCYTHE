"""Tests for scythe_mission.py (PR A: Mission/Session/ObservationSet types and serialization
only). Domain-agnostic per docs/research/scythe_mission_definition.md and
docs/research/scythe_architectural_invariants.md; joins the CI test_scythe_*.py glob. STDLIB
ONLY, no network, no other module touched or imported."""
import dataclasses
import json
import unittest

import scythe_mission as sm

SHA_A = "a" * 64
SHA_B = "b" * 64


class TestMissionParameters(unittest.TestCase):
    def test_defaults_and_round_trip(self):
        mp = sm.MissionParameters()
        self.assertEqual(mp.to_dict(), {})
        self.assertEqual(sm.MissionParameters.from_dict({}), mp)

    def test_round_trip_with_fields(self):
        mp = sm.MissionParameters(sdr="RTL-SDR v5", antenna_sha256=SHA_A, gps_cadence="1 Hz")
        back = sm.MissionParameters.from_dict(mp.to_dict())
        self.assertEqual(mp, back)

    def test_bad_antenna_sha_rejected(self):
        with self.assertRaises(sm.MissionError):
            sm.MissionParameters(antenna_sha256="not-a-digest")

    def test_bad_rf_posture_rejected(self):
        with self.assertRaises(sm.MissionError):
            sm.MissionParameters(rf_posture="SOMETHING_ELSE")

    def test_unknown_field_rejected(self):
        with self.assertRaises(sm.MissionError):
            sm.MissionParameters.from_dict({"not_a_real_field": 1})

    def test_frequency_plan_is_shallowly_immutable_not_just_the_attribute(self):
        # Review found: frozen=True blocks mp.frequency_plan = {...} but does nothing to stop
        # mp.frequency_plan["start"] = ... mutating the dict object in place, which would
        # silently change digest() afterwards. Regression test for the MappingProxyType fix.
        mp = sm.MissionParameters(frequency_plan={"center_mhz": 915, "span_mhz": 2})
        with self.assertRaises(TypeError):
            mp.frequency_plan["start"] = 900e6
        self.assertEqual(dict(mp.frequency_plan), {"center_mhz": 915, "span_mhz": 2})

    def test_frequency_plan_must_be_an_object(self):
        with self.assertRaises(sm.MissionError):
            sm.MissionParameters(frequency_plan=["not", "an", "object"])

    def test_frequency_plan_round_trips_as_a_plain_dict(self):
        mp = sm.MissionParameters(frequency_plan={"center_mhz": 915})
        d = mp.to_dict()
        self.assertIsInstance(d["frequency_plan"], dict)  # not a mappingproxy leaking out
        back = sm.MissionParameters.from_dict(d)
        self.assertEqual(mp, back)
        self.assertEqual(json.loads(json.dumps(d))["frequency_plan"], {"center_mhz": 915})


class TestMission(unittest.TestCase):
    def make(self, **over):
        base = dict(mission_id="RFMAP-20261017-001", mission_type="RF_SURVEY",
                    purpose="Locate intermittent 915 MHz emitter", operator="Ben Gilbert")
        base.update(over)
        return sm.Mission(**base)

    def test_minimal_mission_round_trips_through_dict(self):
        m = self.make()
        d = m.to_dict()
        self.assertEqual(d["schema"], "mission-definition/1")
        back = sm.Mission.from_dict(d)
        self.assertEqual(m, back)
        self.assertEqual(m.digest(), back.digest())

    def test_round_trips_through_json_text(self):
        m = self.make(mission_parameters=sm.MissionParameters(sdr="RTL-SDR v5", capture_profile="urban_passive"))
        back = sm.Mission.from_json(m.to_json())
        self.assertEqual(m, back)
        self.assertEqual(m.digest(), back.digest())
        # a genuine serialization round trip, not just a dict copy
        self.assertIsInstance(m.to_json(), str)
        self.assertEqual(json.loads(m.to_json())["mission_id"], "RFMAP-20261017-001")

    def test_digest_is_stable_for_an_immutable_instance(self):
        m = self.make()
        self.assertEqual(m.digest(), m.digest())

    def test_digest_independent_of_key_order_in_the_source_dict(self):
        m = self.make()
        d = m.to_dict()
        reordered = dict(reversed(list(d.items())))
        self.assertEqual(sm.Mission.from_dict(d).digest(), sm.Mission.from_dict(reordered).digest())

    def test_two_missions_with_different_purpose_have_different_digests(self):
        a = self.make(purpose="Locate emitter A")
        b = self.make(purpose="Locate emitter B")
        self.assertNotEqual(a.digest(), b.digest())

    def test_mission_id_is_immutable_after_creation(self):
        m = self.make()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            m.mission_id = "SOMETHING-ELSE"

    def test_unknown_mission_type_rejected(self):
        with self.assertRaises(sm.MissionError):
            self.make(mission_type="NOT_A_REAL_TYPE")

    def test_empty_purpose_or_operator_rejected(self):
        with self.assertRaises(sm.MissionError):
            self.make(purpose="   ")
        with self.assertRaises(sm.MissionError):
            self.make(operator="")

    def test_rf_posture_requires_rf_survey_mission_type(self):
        with self.assertRaises(sm.MissionError):
            self.make(mission_type="PCAP_COLLECTION", mission_parameters=sm.MissionParameters(rf_posture="WALK"))
        ok = self.make(mission_type="RF_SURVEY", mission_parameters=sm.MissionParameters(rf_posture="WALK"))
        self.assertEqual(ok.mission_parameters.rf_posture, "WALK")

    def test_gate_d_request_sha256_requires_replay_mission_type(self):
        with self.assertRaises(sm.MissionError):
            self.make(mission_type="RF_SURVEY", gate_d_request_sha256=SHA_A)
        ok = self.make(mission_type="REPLAY", gate_d_request_sha256=SHA_A)
        self.assertEqual(ok.gate_d_request_sha256, SHA_A)

    def test_gate_d_request_sha256_must_look_like_a_digest(self):
        with self.assertRaises(sm.MissionError):
            self.make(mission_type="REPLAY", gate_d_request_sha256="not-a-digest")

    def test_unknown_schema_rejected(self):
        d = self.make().to_dict(); d["schema"] = "mission-definition/2"
        with self.assertRaises(sm.MissionError):
            sm.Mission.from_dict(d)

    def test_unknown_top_level_field_rejected(self):
        d = self.make().to_dict(); d["not_a_real_field"] = 1
        with self.assertRaises(sm.MissionError):
            sm.Mission.from_dict(d)

    def test_region_is_shallowly_immutable_not_just_the_attribute(self):
        m = self.make(region={"type": "point", "coordinates": [47.6, -122.3]})
        with self.assertRaises(TypeError):
            m.region["type"] = "polygon"
        self.assertEqual(dict(m.region)["type"], "point")

    def test_region_must_be_an_object(self):
        with self.assertRaises(sm.MissionError):
            self.make(region=["not", "an", "object"])

    def test_region_round_trips_as_a_plain_dict_and_through_json(self):
        m = self.make(region={"type": "point", "coordinates": [47.6, -122.3]})
        d = m.to_dict()
        self.assertIsInstance(d["region"], dict)  # not a mappingproxy leaking out
        self.assertEqual(json.loads(m.to_json())["region"], {"type": "point", "coordinates": [47.6, -122.3]})
        back = sm.Mission.from_dict(d)
        self.assertEqual(m, back)
        self.assertEqual(m.digest(), back.digest())

    def test_malformed_mission_id_rejected(self):
        with self.assertRaises(sm.MissionError):
            self.make(mission_id="")
        with self.assertRaises(sm.MissionError):
            self.make(mission_id="has a space")


class TestSession(unittest.TestCase):
    def make(self, **over):
        base = dict(session_id="RFMAP-20261017-001-S01", mission_id="RFMAP-20261017-001",
                    started_utc="2026-10-20T14:00:00+00:00", operator="Ben Gilbert")
        base.update(over)
        return sm.Session(**base)

    def test_round_trip_through_dict_and_json(self):
        s = self.make(receiver="RTL-SDR v5", sensor_declaration_sha256=SHA_A, capture_profile_used="urban_passive")
        self.assertEqual(sm.Session.from_dict(s.to_dict()), s)
        self.assertEqual(sm.Session.from_json(s.to_json()), s)
        self.assertEqual(s.digest(), sm.Session.from_json(s.to_json()).digest())

    def test_session_id_is_immutable_after_creation(self):
        s = self.make()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            s.session_id = "SOMETHING-ELSE"

    def test_bad_sensor_declaration_sha_rejected(self):
        with self.assertRaises(sm.MissionError):
            self.make(sensor_declaration_sha256="short")

    def test_malformed_ids_rejected(self):
        with self.assertRaises(sm.MissionError):
            self.make(session_id="")
        with self.assertRaises(sm.MissionError):
            self.make(mission_id="has a space")

    def test_unknown_field_rejected(self):
        d = self.make().to_dict(); d["not_a_real_field"] = 1
        with self.assertRaises(sm.MissionError):
            sm.Session.from_dict(d)


class TestObservationSet(unittest.TestCase):
    def make(self, **over):
        base = dict(observation_set_id="RFMAP-20261017-001-S01-OS01", session_id="RFMAP-20261017-001-S01",
                    source_filename="rtl_power_20261020_1400.csv", source_sha256=SHA_A)
        base.update(over)
        return sm.ObservationSet(**base)

    def test_round_trip_through_dict_and_json(self):
        os_ = self.make(acquisition_tool_version="rtl_power 1.2")
        os_.record_observation(); os_.record_observation()
        back = sm.ObservationSet.from_dict(os_.to_dict())
        self.assertEqual(back.observation_count, 2)
        self.assertFalse(back.sealed)
        self.assertEqual(os_.digest(), back.digest())
        back2 = sm.ObservationSet.from_json(os_.to_json())
        self.assertEqual(back2.to_dict(), os_.to_dict())

    def test_append_only_after_sealing_is_enforced_not_just_documented(self):
        os_ = self.make()
        os_.record_observation()
        os_.seal()
        self.assertTrue(os_.sealed)
        with self.assertRaises(sm.SealedObservationSetError):
            os_.record_observation()
        self.assertEqual(os_.observation_count, 1)  # the rejected call did not increment it

    def test_preprocessing_history_is_a_copy_not_a_live_mutable_reference(self):
        # Review found: an earlier version exposed preprocessing_history as a plain public
        # list, so appending to it after seal() silently changed what digest() computed next
        # -- the seal was shallow. This is the regression test for that fix.
        os_ = self.make(preprocessing_history=["step1"])
        before = os_.digest()
        returned = os_.preprocessing_history
        returned.append("smuggled")  # mutating the returned list must not touch internal state
        self.assertEqual(os_.preprocessing_history, ["step1"])
        self.assertEqual(os_.digest(), before)

    def test_add_preprocessing_step_is_sealing_aware(self):
        os_ = self.make()
        os_.add_preprocessing_step("normalized")
        self.assertEqual(os_.preprocessing_history, ["normalized"])
        os_.seal()
        with self.assertRaises(sm.SealedObservationSetError):
            os_.add_preprocessing_step("too_late")
        self.assertEqual(os_.preprocessing_history, ["normalized"])  # rejected call had no effect

    def test_preprocessing_history_cannot_be_reassigned(self):
        os_ = self.make()
        with self.assertRaises(AttributeError):
            os_.preprocessing_history = ["replaced"]

    def test_sealed_state_round_trips(self):
        os_ = self.make(); os_.record_observation(); os_.seal()
        back = sm.ObservationSet.from_dict(os_.to_dict())
        self.assertTrue(back.sealed)
        with self.assertRaises(sm.SealedObservationSetError):
            back.record_observation()

    def test_digest_changes_as_observation_count_grows_before_sealing(self):
        os_ = self.make()
        d0 = os_.digest()
        os_.record_observation()
        d1 = os_.digest()
        self.assertNotEqual(d0, d1)

    def test_bad_source_sha_rejected(self):
        with self.assertRaises(sm.MissionError):
            self.make(source_sha256="not-a-digest")

    def test_negative_or_non_integer_observation_count_rejected_on_load(self):
        d = self.make().to_dict(); d["observation_count"] = -1
        with self.assertRaises(sm.MissionError):
            sm.ObservationSet.from_dict(d)
        d["observation_count"] = "two"
        with self.assertRaises(sm.MissionError):
            sm.ObservationSet.from_dict(d)

    def test_unknown_field_rejected(self):
        d = self.make().to_dict(); d["not_a_real_field"] = 1
        with self.assertRaises(sm.MissionError):
            sm.ObservationSet.from_dict(d)


class TestVocabularySingleSource(unittest.TestCase):
    """MISSION_TYPES in scythe_mission.py is a hand-copied constant of the table in
    docs/research/scythe_mission_definition.md section 6 -- nothing mechanically ties them
    together, so they can drift silently. This doesn't fix that (a generator would be
    over-engineering for one frozenset); it turns silent drift into a failing test, per review."""

    def test_mission_types_match_the_mission_definition_doc_section_6(self):
        import os
        import re
        doc_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "docs", "research", "scythe_mission_definition.md")
        with open(doc_path) as f:
            text = f.read()
        section = text.split("## 6. Mission types", 1)[1].split("\n## ", 1)[0]
        documented = set(re.findall(r"^\|\s*`([A-Z_]+)`\s*\|", section, re.MULTILINE))
        self.assertTrue(documented, "no mission types parsed out of the doc's section 6 table")
        self.assertEqual(documented, sm.MISSION_TYPES)


class TestNoCrossModuleCoupling(unittest.TestCase):
    def test_module_imports_only_stdlib_and_touches_no_other_scythe_module(self):
        import ast
        import os
        import sys
        src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scythe_mission.py")
        with open(src_path) as f:
            tree = ast.parse(f.read())
        mods = {n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else (n.module or "").split(".")[0]
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}
        self.assertTrue(mods <= set(sys.stdlib_module_names), mods)


if __name__ == "__main__":
    unittest.main()
