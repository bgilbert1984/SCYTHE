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
