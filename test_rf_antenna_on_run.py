import copy
import datetime as dt
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import numpy as np

import rf_antenna_on_run as run
import rf_spur_catalogue_sequencer as seq

DOCS = run.DOCS
def _load(path):
    with open(path) as handle:
        return json.load(handle)


FROZEN = _load(os.path.join(DOCS, run.FROZEN_DECLARATION_FILE))
SEED = 7535194158483371716          # the E4000 declaration's seed; regenerates the frozen schedule digest
NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)
WINDOW = 16384                       # small windows keep the test fast; the gate only counts bytes


class FakeTuner:
    tuner_type = 1
    gain_count = 14

    def __init__(self, bad_lo=()):
        self.calls = []
        self.lo = None
        self.bad_lo = set(bad_lo)
        self.rng = np.random.default_rng(7)

    def set_manual_gain_db(self, gain_db, declared_family=None):
        self.calls.append(("gain", gain_db, declared_family))

    def set_lo_hz(self, lo_hz):
        self.calls.append(("lo", lo_hz))
        self.lo = lo_hz

    def discard(self, seconds):
        self.calls.append(("discard", seconds))

    def read_window_raw(self):
        self.calls.append(("read",))
        if self.lo in self.bad_lo:
            raise seq.VisitRefused("short read (test)")
        return self.rng.integers(1, 255, WINDOW, dtype=np.uint8).tobytes()


def fake_decode(raw):
    return np.zeros(2, dtype=np.complex64)      # the gate and these tests never read the decoded samples


def patched(detect=None):
    """Patch detection and decoding so full-size windows (1 MiB each) are not needed."""
    stack = mock.patch.multiple(run.seq, detect_candidates=detect or (lambda w: good_candidates(w)), _decode_iq=fake_decode)
    return stack


def cand(offset_hz, excess_db):
    return seq.CandidateFeature(baseband_hz=offset_hz, excess_db=excess_db, peak_bin=0)


def good_candidates(_windows):
    return [cand(-255996.0, 15.0), cand(255996.0, 15.0)]


def make_run_decl(tmp):
    return {"run_id": "521-UHF-XTR-E4K-ANTENNA-OBS-TEST", "site": "test venue (operator statement)",
            "output_dir": os.path.join(tmp, "out"), "rtl_tcp_host": "127.0.0.1", "rtl_tcp_port": 1234,
            "receiver": copy.deepcopy(FROZEN["receiver"]), "gain_db": 29.0, "bands": copy.deepcopy(FROZEN["bands"]),
            "seed": SEED, "mode": "OBSERVATION_ONLY",
            "antenna": {"connected": True, "note": "operator states the antenna is connected"},
            "frozen_declaration_sha256": run.FROZEN_DECLARATION_SHA256}


def authorize(tmp, decl, **overrides):
    """Write a launch authorization for `decl` into tmp and name it in `decl`."""
    digest = run.canonical_sha256({k: v for k, v in decl.items() if k != "launch_authorization"})
    auth = {"status": "AUTHORIZED", "frozen_declaration_sha256": run.FROZEN_DECLARATION_SHA256,
            "run_declaration_sha256": digest,
            "window": {"not_before": "2026-11-20T00:00:00Z", "not_after": "2026-11-21T00:00:00Z"},
            "permitted_steps": ["acquire the 192 scheduled visits", "apply the visit and tuning gates"],
            "stop_conditions": ["stream not live", "device drops off the host"],
            "operator_confirmation": f"Run declaration digest confirmation is now established for: {digest}"}
    auth.update(overrides)
    if auth.get("window") is None:
        auth.pop("window")
    path = os.path.join(tmp, "launch_authorization.json")
    with open(path, "w") as handle:
        json.dump(auth, handle)
    decl["launch_authorization"] = {"path": path, "sha256": run._file_sha256(path)}
    return auth


def ready(tmp):
    decl = make_run_decl(tmp)
    authorize(tmp, decl)
    return decl


class Precontact(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def refused(self, code, mutate=None, rebind=True, **kw):
        decl = make_run_decl(self.tmp)
        if mutate:
            mutate(decl)
        if rebind:
            authorize(self.tmp, decl)
        tuner = FakeTuner()
        with self.assertRaises(run.RunRefused) as ctx:
            run.run_observation(decl, tuner, now=NOW, **kw)
        self.assertEqual(ctx.exception.code, code)
        self.assertEqual(tuner.calls, [], "a refused run must send no tuner command")

    def test_a_complete_run_declaration_passes(self):
        pre = run.precontact(ready(self.tmp), now=NOW)
        self.assertEqual(pre["visits_per_tuning"], 3)
        self.assertEqual(len(pre["schedule"]), 192)

    def test_scope_refusals_send_no_tuner_command(self):
        self.refused("AOR_MODE_NOT_OBSERVATION_ONLY", lambda d: d.update(mode="CATALOGUE"))
        self.refused("AOR_ANTENNA_NOT_STATED_CONNECTED", lambda d: d["antenna"].update(connected=False))
        self.refused("AOR_GAIN_OUT_OF_SCOPE", lambda d: d.update(gain_db=34.0))
        self.refused("AOR_RECEIVER_OUT_OF_SCOPE", lambda d: d["receiver"].update(tuner="R820T2"))
        self.refused("AOR_RECEIVER_OUT_OF_SCOPE", lambda d: d["receiver"].update(sensor_id="14530058"))
        self.refused("AOR_BANDS_OUT_OF_SCOPE", lambda d: d.update(bands=[{"band_id": "FM", "low_hz": 88000000, "high_hz": 108000000}]))
        self.refused("AOR_SCHEDULE_DIGEST_MISMATCH", lambda d: d.update(seed=1))
        self.refused("AOR_SEED_NOT_INTEGER", lambda d: d.update(seed="7535194158483371716"))
        self.refused("AOR_SITE_UNDECLARED", lambda d: d.update(site="  "))
        self.refused("AOR_RUN_NAMES_WRONG_FROZEN_DECLARATION", lambda d: d.update(frozen_declaration_sha256="0" * 64))
        self.refused("AOR_RUN_DECLARATION_ABSENT", lambda d: d.pop("run_id"), rebind=False)

    def test_an_altered_frozen_file_refuses(self):
        for name, code in ((run.FROZEN_DECLARATION_FILE, "AOR_FROZEN_DECLARATION_HASH_MISMATCH"),
                           (run.FROZEN_TIER_FILE, "AOR_FROZEN_TIER_HASH_MISMATCH")):
            docs = os.path.join(self.tmp, "docs_" + name)
            os.makedirs(docs)
            for f in (run.FROZEN_DECLARATION_FILE, run.FROZEN_TIER_FILE):
                shutil.copy(os.path.join(DOCS, f), docs)
            with open(os.path.join(docs, name), "a") as handle:
                handle.write(" ")
            self.refused(code, docs_dir=docs)


class LaunchAuthorization(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def code(self, **overrides):
        decl = make_run_decl(self.tmp)
        authorize(self.tmp, decl, **overrides)
        with self.assertRaises(run.RunRefused) as ctx:
            run.precontact(decl, now=NOW)
        return ctx.exception.code

    def test_refusals(self):
        decl = make_run_decl(self.tmp)
        decl["launch_authorization"] = {}
        with self.assertRaises(run.RunRefused) as ctx:
            run.precontact(decl, now=NOW)
        self.assertEqual(ctx.exception.code, "AOR_LAUNCH_AUTHORIZATION_UNNAMED")
        decl["launch_authorization"] = {"path": os.path.join(self.tmp, "nope.json"), "sha256": "0" * 64}
        with self.assertRaises(run.RunRefused) as ctx:
            run.precontact(decl, now=NOW)
        self.assertEqual(ctx.exception.code, "AOR_LAUNCH_AUTHORIZATION_MISSING")
        self.assertEqual(self.code(status="DRAFT"), "AOR_LAUNCH_AUTHORIZATION_NOT_AUTHORIZED")
        self.assertEqual(self.code(frozen_declaration_sha256="0" * 64), "AOR_LAUNCH_AUTHORIZATION_WRONG_FROZEN_DECLARATION")
        self.assertEqual(self.code(run_declaration_sha256="0" * 64), "AOR_LAUNCH_AUTHORIZATION_WRONG_RUN_DECLARATION")
        self.assertEqual(self.code(operator_confirmation="authorization granted"), "AOR_LAUNCH_AUTHORIZATION_CONFIRMATION_MISSING")
        self.assertEqual(self.code(operator_confirmation=""), "AOR_LAUNCH_AUTHORIZATION_CONFIRMATION_MISSING")
        self.assertEqual(self.code(window={"not_before": "2026-11-01T00:00:00Z", "not_after": "2026-11-02T00:00:00Z"}),
                         "AOR_LAUNCH_AUTHORIZATION_WINDOW_NOT_CURRENT")
        self.assertEqual(self.code(window={"not_before": "later", "not_after": "never"}),
                         "AOR_LAUNCH_AUTHORIZATION_WINDOW_UNPARSEABLE")
        self.assertEqual(self.code(permitted_steps=[]), "AOR_LAUNCH_AUTHORIZATION_STEPS_UNENUMERATED")
        self.assertEqual(self.code(stop_conditions=[]), "AOR_LAUNCH_AUTHORIZATION_STEPS_UNENUMERATED")

    def test_the_time_window_is_optional_but_enforced_when_given(self):
        decl = make_run_decl(self.tmp)
        authorize(self.tmp, decl, window=None)
        run.precontact(decl, now=NOW)                      # no window: accepted
        self.assertEqual(self.code(window={"not_before": "2026-11-01T00:00:00Z", "not_after": "2026-11-02T00:00:00Z"}),
                         "AOR_LAUNCH_AUTHORIZATION_WINDOW_NOT_CURRENT")

    def test_a_changed_file_breaks_the_named_hash(self):
        decl = make_run_decl(self.tmp)
        authorize(self.tmp, decl)
        with open(decl["launch_authorization"]["path"], "a") as handle:
            handle.write(" ")
        with self.assertRaises(run.RunRefused) as ctx:
            run.precontact(decl, now=NOW)
        self.assertEqual(ctx.exception.code, "AOR_LAUNCH_AUTHORIZATION_HASH_MISMATCH")

    def test_editing_the_run_declaration_after_authorizing_refuses(self):
        decl = ready(self.tmp)
        decl["site"] = "a different venue"
        with self.assertRaises(run.RunRefused) as ctx:
            run.precontact(decl, now=NOW)
        self.assertEqual(ctx.exception.code, "AOR_LAUNCH_AUTHORIZATION_WRONG_RUN_DECLARATION")


class Observation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def go(self, detect=good_candidates, tuner=None):
        decl = ready(self.tmp)
        tuner = tuner or FakeTuner()
        with patched(detect):
            return decl, tuner, run.run_observation(decl, tuner, now=NOW)

    def test_clean_run_scores_every_tuning_and_makes_no_catalogue(self):
        decl, tuner, doc = self.go()
        self.assertEqual(doc["evidence_class"], "ANTENNA_ON_OBSERVATION_ONLY")
        self.assertIs(doc["not_catalogue_evidence"], True)
        self.assertEqual(doc["summary"], {"visits_scheduled": 192, "visits_acquired": 192, "visits_refused": 0,
                                          "visits_passed_gate": 192, "tunings": 64, "tunings_scored": 64})
        for forbidden in ("entries", "classification_refusals", "epoch_status"):
            self.assertNotIn(forbidden, doc)
        self.assertEqual(tuner.calls[0], ("gain", 29.0, "E4000"))
        written = _load(os.path.join(decl["output_dir"], run.OBSERVATION_NAME))
        self.assertEqual(written["frozen_declaration_sha256"], run.FROZEN_DECLARATION_SHA256)
        self.assertEqual(written["run_declaration_sha256"], run.canonical_sha256({k: v for k, v in decl.items() if k != "launch_authorization"}))
        self.assertEqual(len(os.listdir(os.path.join(decl["output_dir"], "spool"))), 192)

    def test_one_failed_visit_unscores_only_its_tuning(self):
        calls = {"n": 0}

        def detect(windows):
            calls["n"] += 1
            return [cand(255996.0, 15.0)] if calls["n"] == 10 else good_candidates(windows)   # visit 10: one side missing
        _, _, doc = self.go(detect=detect)
        self.assertEqual(doc["summary"]["visits_passed_gate"], 191)
        self.assertEqual(doc["summary"]["tunings_scored"], 63)
        bad = [t for t in doc["tunings"] if not t["scored"]]
        self.assertEqual(len(bad), 1)
        self.assertIn("A_VISIT_FAILED_THE_GATE", bad[0]["reasons"])

    def test_low_excess_visit_fails_the_gate(self):
        def detect(windows):
            return [cand(-255996.0, 10.9), cand(255996.0, 15.0)]
        _, _, doc = self.go(detect=detect)
        self.assertEqual(doc["summary"]["visits_passed_gate"], 0)
        self.assertEqual(doc["summary"]["tunings_scored"], 0)

    def test_a_refused_visit_leaves_its_tuning_unscored(self):
        pre = run.precontact(ready(self.tmp), now=NOW)
        t0 = pre["tunings"][pre["schedule"][0].tuning_index]
        first = pre["schedule"][0]
        bad_lo = t0.center_frequency_hz + first.retune_delta_hz
        decl, tuner, doc = self.go(tuner=FakeTuner(bad_lo=[bad_lo]))
        self.assertEqual(doc["summary"]["visits_refused"], 1)
        self.assertEqual(doc["summary"]["visits_acquired"], 191)
        unscored = [t for t in doc["tunings"] if not t["scored"]]
        self.assertEqual([t["tuning_index"] for t in unscored], [first.tuning_index])
        self.assertIn("VISIT_COUNT_MISMATCH", unscored[0]["reasons"])

    def test_clipped_visits_are_refused_by_the_gate(self):
        tuner = FakeTuner()
        original = tuner.read_window_raw
        tuner.read_window_raw = lambda: original().replace(b"\x80", b"\xff", 40)   # ~40 clipped bytes per window, far over 0.001%
        _, _, doc = self.go(tuner=tuner)
        self.assertEqual(doc["summary"]["tunings_scored"], 0)
        reasons = {r for v in doc["visits"] for r in v["gate"]["reasons"]}
        self.assertIn("CLIPPING_ABOVE_LIMIT", reasons)

    def test_a_non_live_stream_aborts_the_run(self):
        tuner = FakeTuner()
        original = tuner.read_window_raw
        tuner.read_window_raw = lambda: bytes(WINDOW)      # all zeros -> not live, aborts the run
        decl = ready(self.tmp)
        with patched():
            with self.assertRaises(seq.SequencerRefused):
                run.run_observation(decl, tuner, now=NOW)
        tuner.read_window_raw = original

    def test_output_directory_is_new_per_run(self):
        decl, _, _ = self.go()
        with patched():
            with self.assertRaises(seq.SequencerRefused) as ctx:
                run.run_observation(decl, FakeTuner(), now=NOW)
        self.assertEqual(ctx.exception.code, "OUTPUT_DIR_NOT_EMPTY")


class Templates(unittest.TestCase):
    def test_run_declaration_template_passes_once_filled_and_authorized(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        decl = _load(os.path.join(DOCS, "521_ANTENNA_ON_RUN_DECLARATION.TEMPLATE.json"))
        self.assertEqual(decl["frozen_declaration_sha256"], run.FROZEN_DECLARATION_SHA256)
        decl.update(run_id="521-UHF-XTR-E4K-ANTENNA-OBS-TEST", site="test venue", output_dir=os.path.join(tmp, "out"))
        authorize(tmp, decl)
        run.precontact(decl, now=NOW)

    def test_unfilled_template_is_refused(self):
        decl = _load(os.path.join(DOCS, "521_ANTENNA_ON_RUN_DECLARATION.TEMPLATE.json"))
        with self.assertRaises(run.RunRefused):
            run.precontact(decl, now=NOW)

    def test_launch_authorization_template_is_a_draft_with_no_confirmation(self):
        auth = _load(os.path.join(DOCS, "521_ANTENNA_ON_LAUNCH_AUTHORIZATION.TEMPLATE.json"))
        self.assertEqual(auth["status"], "DRAFT")
        self.assertEqual(auth["operator_confirmation"], "")
        self.assertEqual(auth["run_declaration_sha256"], "")
        self.assertEqual(auth["frozen_declaration_sha256"], run.FROZEN_DECLARATION_SHA256)
        self.assertTrue(auth["permitted_steps"] and auth["stop_conditions"])


class CatalogueRunUntouched(unittest.TestCase):
    def test_the_catalogue_run_still_refuses_an_unterminated_declaration(self):
        auth = _load(os.path.join(DOCS, "521_AUTHORIZED_RUN_DECLARATION_XTR_E4000.json"))
        decl = dict(auth)
        decl.update(output_dir="/nonexistent", rtl_tcp_host="127.0.0.1", rtl_tcp_port=1234, epoch=1,
                    termination={"part": "x", "connector": "y", "fitted": False})
        with self.assertRaises(seq.SequencerRefused) as ctx:
            seq.check_declaration(decl)
        self.assertEqual(ctx.exception.code, "TERMINATION_UNDECLARED")

    def test_the_catalogue_authorization_does_not_accept_the_antenna_on_declaration(self):
        decl = copy.deepcopy(FROZEN)
        with self.assertRaises(Exception):
            seq.check_declaration_authorized(decl)


if __name__ == "__main__":
    unittest.main()
