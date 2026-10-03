#!/usr/bin/env python3
"""Discriminating tests for the 2026-10-03 live repair (§5.21).

Five coupled changes, each with tests that fail on the pre-repair code:
1. Schedule v2: three distinct magnitudes per tuning, by construction.
2. Schedule admission: independent pre-contact invariant.
3. Classification refusal: undersampled tracks are refused, not unresolved.
4. Durable acquisition: spool + journal before classification.
5. Declaration digest binding: authorized values or pre-contact refusal.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rf_promotion_envelope as env
import rf_spur_catalogue_sequencer as seq
from rf_promotion_envelope import (
    Band,
    EnvelopeRefused,
    Tuning,
    Visit,
    admit_schedule,
    generate_tunings,
    generate_visit_schedule,
    SCHEDULE_GENERATOR_REVISION,
    PLAN_RETUNE_DELTAS_HZ,
    PLAN_VISITS_PER_TUNING,
)
from rf_spur_catalogue_sequencer import (
    AcquiredVisit,
    AssociatedTrack,
    CandidateFeature,
    SequencerRefused,
    TuningVisitData,
    check_declaration_authorized,
)

FROZEN_SEED = 7535194158483371716
TEST_BANDS = (Band(band_id="UHF-400-470", low_hz=400_000_000,
                   high_hz=470_000_000),)


def _tunings(seed=FROZEN_SEED):
    return generate_tunings(seed=seed, bands=TEST_BANDS)


def _authorized_declaration(tmpdir):
    """A declaration matching the frozen authorization."""
    with open(seq._AUTHORIZED_DECLARATION_PATH) as handle:
        authorized = json.load(handle)
    decl = dict(authorized)
    decl.update({
        "run_id": "test-run",
        "epoch": 1,
        "ppm_justification": "test",
        "site": "test bench",
        "rtl_tcp_host": "fake",
        "rtl_tcp_port": 1,
        "output_dir": os.path.join(tmpdir, "out"),
    })
    # termination needs the fitted marker for check_declaration
    decl["termination"] = dict(decl["termination"])
    decl["termination"]["fitted"] = True
    return decl


class ScheduleV2Tests(unittest.TestCase):
    """Change 1: v2 makes three distinct deltas per tuning true by construction."""

    def test_revision_is_v2(self):
        self.assertEqual(SCHEDULE_GENERATOR_REVISION, "rf-visit-schedule.v2")

    def test_every_tuning_three_distinct_deltas(self):
        tunings = _tunings()
        schedule = generate_visit_schedule(seed=FROZEN_SEED, tunings=tunings)
        per_tuning = defaultdict(set)
        for visit in schedule:
            per_tuning[visit.tuning_index].add(visit.retune_delta_hz)
        self.assertEqual(len(per_tuning), 64)
        for tuning_index, deltas in per_tuning.items():
            self.assertEqual(
                len(deltas), 3,
                f"tuning {tuning_index} has {len(deltas)} distinct deltas")

    def test_every_tuning_all_magnitudes(self):
        tunings = _tunings()
        schedule = generate_visit_schedule(seed=FROZEN_SEED, tunings=tunings)
        per_tuning = defaultdict(list)
        for visit in schedule:
            per_tuning[visit.tuning_index].append(visit.retune_delta_hz)
        want = sorted(PLAN_RETUNE_DELTAS_HZ)
        for tuning_index, deltas in per_tuning.items():
            self.assertEqual(sorted(abs(d) for d in deltas), want,
                             f"tuning {tuning_index} magnitudes {deltas}")

    def test_deterministic(self):
        tunings = _tunings()
        a = generate_visit_schedule(seed=FROZEN_SEED, tunings=tunings)
        b = generate_visit_schedule(seed=FROZEN_SEED, tunings=tunings)
        self.assertEqual(
            [(v.position, v.tuning_index, v.retune_delta_hz) for v in a],
            [(v.position, v.tuning_index, v.retune_delta_hz) for v in b])

    def test_v1_revision_refused(self):
        tunings = _tunings()
        with self.assertRaises(EnvelopeRefused):
            generate_visit_schedule(seed=FROZEN_SEED, tunings=tunings,
                                    generator_revision="rf-visit-schedule.v1")

    def test_magnitude_not_confounded_with_round(self):
        # The three magnitudes must not march in lockstep with the round:
        # different tunings put different magnitudes in round 0.
        tunings = _tunings()
        schedule = generate_visit_schedule(seed=FROZEN_SEED, tunings=tunings)
        order = [v.tuning_index for v in schedule]
        # first occurrence of each tuning = its round-0 visit
        seen = set()
        round0_mags = []
        for visit in schedule:
            if visit.tuning_index not in seen:
                seen.add(visit.tuning_index)
                round0_mags.append(abs(visit.retune_delta_hz))
        self.assertEqual(len(set(round0_mags)), 3,
                         "round 0 uses a single magnitude for every tuning")


class ScheduleAdmissionTests(unittest.TestCase):
    """Change 2: the independent pre-contact invariant."""

    def test_admits_v2_schedule(self):
        tunings = _tunings()
        schedule = generate_visit_schedule(seed=FROZEN_SEED, tunings=tunings)
        digest = admit_schedule(schedule, tunings, FROZEN_SEED)
        self.assertEqual(len(digest), 64)  # sha256 hex

    def test_refuses_duplicate_deltas(self):
        tunings = _tunings()
        schedule = list(generate_visit_schedule(seed=FROZEN_SEED,
                                                tunings=tunings))
        # force tuning 0's third visit to reuse its first visit's delta
        v0 = [v for v in schedule if v.tuning_index == 0]
        idx = schedule.index(v0[2])
        schedule[idx] = Visit(position=v0[2].position, tuning_index=0,
                              retune_delta_hz=v0[0].retune_delta_hz)
        with self.assertRaises(EnvelopeRefused) as ctx:
            admit_schedule(tuple(schedule), tunings, FROZEN_SEED)
        self.assertEqual(ctx.exception.code, "PLAN_SCHEDULE_NOT_ADMITTED")

    def test_refuses_missing_magnitude(self):
        tunings = _tunings()
        schedule = list(generate_visit_schedule(seed=FROZEN_SEED,
                                                tunings=tunings))
        v0 = [v for v in schedule if v.tuning_index == 0]
        # replace the 200 kHz visit with a second 50 kHz (different sign)
        target = next(v for v in v0 if abs(v.retune_delta_hz) == 200_000.0)
        idx = schedule.index(target)
        schedule[idx] = Visit(position=target.position, tuning_index=0,
                              retune_delta_hz=-50_000.0
                              if target.retune_delta_hz > 0 else 50_000.0)
        with self.assertRaises(EnvelopeRefused) as ctx:
            admit_schedule(tuple(schedule), tunings, FROZEN_SEED)
        self.assertEqual(ctx.exception.code, "PLAN_SCHEDULE_NOT_ADMITTED")

    def test_refuses_wrong_tuning_count(self):
        tunings = _tunings()[:2]
        schedule = generate_visit_schedule(seed=FROZEN_SEED,
                                           tunings=_tunings())[:6]
        with self.assertRaises(EnvelopeRefused):
            admit_schedule(schedule, tunings, FROZEN_SEED)


class AuthorizationBindingTests(unittest.TestCase):
    """Change 5: the frozen authorization binds the run-time declaration."""

    def test_authorized_declaration_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            decl = _authorized_declaration(tmpdir)
            # should not raise
            check_declaration_authorized(decl)

    def test_wrong_gain_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            decl = _authorized_declaration(tmpdir)
            decl["gain_db"] = 20.7  # the 2026-10-03 mistake
            with self.assertRaises(SequencerRefused) as ctx:
                check_declaration_authorized(decl)
            self.assertEqual(ctx.exception.code,
                             "DECLARATION_NOT_AUTHORIZED")
            self.assertIn("29.7", str(ctx.exception))

    def test_wrong_seed_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            decl = _authorized_declaration(tmpdir)
            decl["seed"] = 375934030  # the 2026-10-03 mistake
            with self.assertRaises(SequencerRefused) as ctx:
                check_declaration_authorized(decl)
            self.assertEqual(ctx.exception.code,
                             "DECLARATION_NOT_AUTHORIZED")


class ClassificationRefusalTests(unittest.TestCase):
    """Change 3: an undersampled track is refused, never unresolved."""

    def _two_delta_track(self):
        return AssociatedTrack(
            tuning_id="tuning-000",
            anchor_center_hz=400e6,
            deltas=(50_000.0, -50_000.0),  # two distinct: no §5.21 slope
            offsets=(1_000.0, 2_000.0),
            visit_positions=(0, 64),
            slope_hypothesis=1,
            seed_excess_db=15.0,
            persistence=None)

    def _decl(self, tmpdir):
        return _authorized_declaration(tmpdir)

    def test_two_delta_track_refused_in_analysis(self):
        track = self._two_delta_track()
        tunings = (Tuning(tuning_index=0, center_frequency_hz=400e6,
                          band_id="UHF-400-470"),)
        real_associate = seq.associate_tracks
        seq.associate_tracks = lambda *a, **k: ([track], [], [])
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                decl = self._decl(tmpdir)
                comb = seq.ReferenceComb(
                    reference_hz=decl["reference_hz"],
                    reference_ppm=decl["reference_ppm"])
                document = seq._analyze_epoch(
                    decl, tunings, comb, {0: []}, [], [], "digest",
                    FROZEN_SEED, 3, tuner_type=5)
        finally:
            seq.associate_tracks = real_associate
        self.assertEqual(document["epoch_status"], "INCOMPLETE")
        self.assertEqual(len(document["classification_refusals"]), 1)
        refusal = document["classification_refusals"][0]
        self.assertEqual(refusal["refusal_code"], "PLAN_SLOPE_NOT_ESTIMATED")
        self.assertEqual(refusal["tuning_id"], "tuning-000")
        self.assertEqual(refusal["visit_positions"], [0, 64])
        # never laundered into the catalogue entries, and never "unresolved"
        self.assertEqual(len(document["entries"]), 0)
        self.assertIn("incomplete_reason", document)

    def test_classify_track_raises_on_two_deltas(self):
        from rf_spur_catalogue_sequencer import classify_track
        track = self._two_delta_track()
        comb = seq.ReferenceComb(reference_hz=28.8e6, reference_ppm=100.0)
        with self.assertRaises(EnvelopeRefused) as ctx:
            classify_track(track, comb, 0)
        self.assertEqual(ctx.exception.code, "PLAN_SLOPE_NOT_ESTIMATED")


class DurableAcquisitionTests(unittest.TestCase):
    """Change 4: spool + journal, byte-exact and fsynced."""

    def test_spool_is_byte_exact(self):
        import types
        visit = types.SimpleNamespace(position=7)
        windows = [np.arange(1_048_576, dtype=np.uint8),
                   np.zeros(1_048_576, dtype=np.uint8)]
        with tempfile.TemporaryDirectory() as tmpdir:
            rel, digest, nbytes = seq._spool_visit_windows(
                tmpdir, visit, windows)
            self.assertEqual(nbytes, 2 * 1_048_576)
            full = os.path.join(tmpdir, rel)
            self.assertTrue(os.path.exists(full))
            with open(full, "rb") as handle:
                raw = handle.read()
            self.assertEqual(raw, b"".join(w.tobytes() for w in windows))
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
            # no temp file left behind
            self.assertEqual(os.listdir(os.path.join(tmpdir, "spool")),
                             ["visit_0007.iq"])

    def test_journal_record_roundtrip(self):
        from rf_promotion_envelope import SpurPersistenceObservation
        rec = {
            "type": "visit_acquired",
            "position": 3,
            "tuning_index": 5,
            "tuning_id": "tuning-005",
            "lo_hz": 400_050_000.0,
            "retune_delta_hz": 50_000.0,
            "attempt": 1,
            "elapsed_s": 2.1,
            "spool_path": "spool/visit_0003.iq",
            "window_sha256": "abc",
            "window_bytes": 8_388_608,
            "candidates": [{"baseband_hz": 1000.0, "excess_db": 15.0,
                            "peak_bin": 256}],
            "persistence": {"0": {"tuning_id": "tuning-005",
                                  "repeat_excess_db": [12.0] * 8,
                                  "qualifying": 8,
                                  "persistent": True}},
            "retained": [0],
        }
        vd = seq._visit_data_from_record(rec)
        self.assertEqual(vd.visit.position, 3)
        self.assertEqual(vd.visit.retune_delta_hz, 50_000.0)
        self.assertEqual(len(vd.candidates), 1)
        self.assertAlmostEqual(vd.candidates[0].baseband_hz, 1000.0)
        self.assertEqual(vd.retained, [0])
        self.assertTrue(vd.persistence[0].persistent())


if __name__ == "__main__":
    unittest.main()
