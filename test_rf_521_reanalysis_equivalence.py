#!/usr/bin/env python3
"""§7.1 reanalysis: does recorded-IQ reanalysis decide what live processing decided?

`reanalyze_from_spool` exists so an analyzer fix can be rerun over preserved
evidence. That is only worth anything if, on the same evidence, it reaches the
same candidates and the same retention decisions as the live path did. The
older reanalysis tests (test_rf_521_live_repair.py) run it over flat IQ with
detection mocked out, so the candidate loop in `_recompute_visit_data` never
executed with a candidate in it.

The evidence here is not synthesized twice. One live epoch is run through the
real `run_epoch` and a fake rtl_tcp socket; the sequencer spools the exact
bytes it received. Reanalysis then reads that spool. The tests prove the spool
holds the bytes the fake receiver served, and compare reanalysis against the
journal the live path wrote while it held the windows in memory.

The fixture is built so retention has a boundary to be wrong about: every
tone visit carries a strong tone, and one visit also carries a marginal tone
that is detected but not persistent (below 7 of 8 repeats), while another
carries a marginal tone that is. Both decisions must come back unchanged.

No production code is touched by this module.
"""

import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rf_spur_catalogue_sequencer as seq
from rf_promotion_envelope import PLAN_PERSISTENCE_MARGIN_DB, Tuning, Visit
from rf_spur_catalogue_sequencer import (
    ACQUISITION_JOURNAL_NAME,
    BIN_HZ,
    SAMPLE_RATE_HZ,
    SequencerRefused,
    WINDOWS_PER_VISIT,
    WINDOW_BYTES,
)
from test_rf_spur_catalogue_sequencer import (
    SETTLE_BYTES,
    _declaration,
    _dongle_info,
    _encode,
    _tuner,
)

SR = 2048000
N = int(0.256 * SR)
FLOAT_TOLERANCE = 1e-9   # the equivalence tolerance declared for this estimator

TUNINGS = (Tuning(tuning_index=0, center_frequency_hz=100e6, band_id="test"),
           Tuning(tuning_index=1, center_frequency_hz=200e6, band_id="test"))
DELTAS = (-200e3, -50e3, 100e3)
SCHEDULE = tuple(
    Visit(position=i, tuning_index=ti, retune_delta_hz=d)
    for i, (ti, d) in enumerate([(0, DELTAS[0]), (0, DELTAS[1]),
                                 (0, DELTAS[2]), (1, DELTAS[0]),
                                 (1, DELTAS[1]), (1, DELTAS[2])]))
# A slope-2 internal-mixing spur, so the catalogue document has an entry to
# compare, not just an empty one.
STRONG_TONE_HZ = {DELTAS[0]: 100e3, DELTAS[1]: 400e3, DELTAS[2]: 700e3}
# visit position -> (marginal tone baseband Hz, amplitude)
# Amplitudes are calibrated against the seeded noise below (seed = position + 1),
# not against a general SNR: each sits where the decision is close, so a wrong
# retention reading changes the answer.
MARGINAL_TONE = {0: (-310e3, 0.00025),  # detected, 5 of 8 repeats: not persistent
                 1: (610e3, 0.0004)}    # detected, 8 of 8 repeats: persistent
STRONG_AMPLITUDE = 0.2
NOISE_SIGMA = 0.05


def _visit_windows(position, tuning_index, retune_delta_hz):
    """Eight windows, seeded by position. No sample clips, so the CU8 encoding
    adds quantization noise and nothing else."""
    rng = np.random.default_rng(position + 1)
    t = np.arange(N) / SR
    windows = []
    for _ in range(WINDOWS_PER_VISIT):
        noise = ((rng.standard_normal(N) + 1j * rng.standard_normal(N))
                 * NOISE_SIGMA / math.sqrt(2.0))
        signal = np.zeros(N, dtype=np.complex128)
        if tuning_index == 0:
            signal += STRONG_AMPLITUDE * np.exp(
                2j * math.pi * STRONG_TONE_HZ[retune_delta_hz] * t)
            if position in MARGINAL_TONE:
                hz, amplitude = MARGINAL_TONE[position]
                signal += amplitude * np.exp(2j * math.pi * hz * t)
        windows.append((signal + noise).astype(np.complex64))
    return windows


def _patched_plan():
    """The two-tuning plan the older mini-run uses: the 64-tuning admission
    invariant and the authorization binding have their own tests."""
    return (
        mock.patch.object(seq, "generate_tunings", lambda **_k: TUNINGS),
        mock.patch.object(seq, "generate_visit_schedule",
                          lambda **_k: SCHEDULE),
        mock.patch.object(seq, "check_declaration_authorized",
                          lambda decl: None),
        mock.patch.object(seq, "admit_schedule",
                          lambda *a, **k: "test-schedule-digest"),
    )


class _Plan:
    def __enter__(self):
        self._patches = _patched_plan()
        for patch in self._patches:
            patch.start()

    def __exit__(self, *exc):
        for patch in reversed(self._patches):
            patch.stop()


def _read_journal(out):
    with open(os.path.join(out, ACQUISITION_JOURNAL_NAME)) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_journal(out, records):
    with open(os.path.join(out, ACQUISITION_JOURNAL_NAME), "w") as handle:
        for rec in records:
            handle.write(json.dumps(rec, sort_keys=True) + "\n")


class ReanalysisEquivalenceBase(unittest.TestCase):
    """One live epoch, run once. Tests that damage evidence work on copies."""

    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="reanalysis-equivalence-")
        cls.addClassCleanup(shutil.rmtree, cls.root, ignore_errors=True)
        cls.served = {}
        payload = bytearray(_dongle_info())
        for visit in SCHEDULE:
            payload += b"\x00" * SETTLE_BYTES
            raw = _encode(_visit_windows(
                visit.position, visit.tuning_index, visit.retune_delta_hz))
            cls.served[visit.position] = raw
            payload += raw
        with _Plan():
            decl = _declaration(cls.root)
            tuner = _tuner(bytes(payload))
            with tuner:
                cls.live_document = seq.run_epoch(decl, tuner)
        cls.out = decl["output_dir"]
        cls.journal = [r for r in _read_journal(cls.out)
                       if r["type"] == "visit_acquired"]

    def copy_of_evidence(self):
        destination = os.path.join(
            tempfile.mkdtemp(prefix="damaged-", dir=self.root), "out")
        shutil.copytree(self.out, destination)
        return destination

    def assertSameResult(self, live, again, path="result"):
        """Whole-structure equality. Floats within the declared tolerance;
        everything else exact, including key sets and list order."""
        if isinstance(live, dict):
            self.assertIsInstance(again, dict, path)
            self.assertEqual(sorted(live), sorted(again), f"{path} keys")
            for key in live:
                self.assertSameResult(live[key], again[key], f"{path}.{key}")
        elif isinstance(live, (list, tuple)):
            self.assertIsInstance(again, (list, tuple), path)
            self.assertEqual(len(live), len(again), f"{path} length")
            for i, (a, b) in enumerate(zip(live, again)):
                self.assertSameResult(a, b, f"{path}[{i}]")
        elif isinstance(live, float) and not isinstance(live, bool):
            self.assertIsInstance(again, (int, float), path)
            self.assertLessEqual(abs(live - again), FLOAT_TOLERANCE, path)
        else:
            self.assertEqual(live, again, path)


class FixtureIsNotVacuousTests(ReanalysisEquivalenceBase):
    """The comparison below means something only if the loop under test has
    candidates to loop over and both retention outcomes occur."""

    def test_live_run_acquired_every_visit(self):
        self.assertEqual(self.live_document["n_visits_acquired"], 6)
        self.assertEqual(self.live_document["n_visits_refused"], 0)
        self.assertEqual([r["position"] for r in self.journal], list(range(6)))

    def test_spool_holds_the_bytes_the_receiver_served(self):
        """Live and reanalysis consume the same evidence: the spool is the
        wire bytes, not a second synthesis of the signal."""
        for rec in self.journal:
            spool = os.path.join(self.out, rec["spool_path"])
            with open(spool, "rb") as handle:
                on_disk = handle.read()
            served = self.served[rec["position"]]
            self.assertEqual(len(served), WINDOWS_PER_VISIT * WINDOW_BYTES)
            self.assertEqual(on_disk, served, f"position {rec['position']}")
            self.assertEqual(rec["window_sha256"],
                             hashlib.sha256(served).hexdigest())
            self.assertEqual(rec["window_bytes"], len(served))

    def test_strong_tone_is_found_and_retained_on_every_tone_visit(self):
        for rec in self.journal:
            if rec["tuning_index"] != 0:
                continue
            expected = STRONG_TONE_HZ[rec["retune_delta_hz"]]
            strong = [(i, c) for i, c in enumerate(rec["candidates"])
                      if abs(c["baseband_hz"] - expected) < BIN_HZ]
            self.assertEqual(len(strong), 1, f"position {rec['position']}")
            index, cand = strong[0]
            self.assertGreater(cand["excess_db"], 40.0)
            observation = rec["persistence"][str(index)]
            self.assertTrue(observation["persistent"])
            self.assertEqual(observation["qualifying"], WINDOWS_PER_VISIT)
            self.assertIn(index, rec["retained"])

    def test_marginal_tone_is_detected_but_not_retained_when_not_persistent(self):
        rec = self.journal[0]
        hz, _amplitude = MARGINAL_TONE[0]
        marginal = [(i, c) for i, c in enumerate(rec["candidates"])
                    if abs(c["baseband_hz"] - hz) < BIN_HZ]
        self.assertEqual(len(marginal), 1)
        index, _cand = marginal[0]
        observation = rec["persistence"][str(index)]
        self.assertFalse(observation["persistent"])
        self.assertLess(observation["qualifying"], 7)
        self.assertGreaterEqual(observation["qualifying"], 1)
        self.assertNotIn(index, rec["retained"])
        # not retained, yet its repeats straddle the declared margin
        excesses = observation["repeat_excess_db"]
        self.assertTrue(any(e >= PLAN_PERSISTENCE_MARGIN_DB for e in excesses))
        self.assertTrue(any(e < PLAN_PERSISTENCE_MARGIN_DB for e in excesses))

    def test_marginal_tone_is_retained_when_persistent(self):
        rec = self.journal[1]
        hz, _amplitude = MARGINAL_TONE[1]
        marginal = [(i, c) for i, c in enumerate(rec["candidates"])
                    if abs(c["baseband_hz"] - hz) < BIN_HZ]
        self.assertEqual(len(marginal), 1)
        index, _cand = marginal[0]
        observation = rec["persistence"][str(index)]
        self.assertTrue(observation["persistent"])
        self.assertGreaterEqual(observation["qualifying"], 7)
        self.assertIn(index, rec["retained"])

    def test_noise_only_visits_carry_no_candidates(self):
        for rec in self.journal:
            if rec["tuning_index"] == 1:
                self.assertEqual(rec["candidates"], [])
                self.assertEqual(rec["retained"], [])

    def test_live_catalogue_has_an_entry_to_compare(self):
        self.assertGreaterEqual(len(self.live_document["entries"]), 1)


class ReanalysisReproducesLiveTests(ReanalysisEquivalenceBase):

    def test_every_visit_recomputes_to_what_live_decided(self):
        for rec in self.journal:
            with self.subTest(position=rec["position"]):
                data = seq._recompute_visit_data(self.out, rec)
                again = {
                    "position": data.visit.position,
                    "tuning_index": data.visit.tuning_index,
                    "tuning_id": data.visit.tuning_id,
                    "lo_hz": data.visit.lo_hz,
                    "retune_delta_hz": data.visit.retune_delta_hz,
                    "attempt": data.visit.attempt,
                    "elapsed_s": data.visit.elapsed_s,
                    "candidates": [{"baseband_hz": c.baseband_hz,
                                    "excess_db": c.excess_db,
                                    "peak_bin": c.peak_bin}
                                   for c in data.candidates],
                    "persistence": {str(i): obs.to_dict()
                                    for i, obs in data.persistence.items()},
                    "retained": data.retained,
                }
                live = {key: rec[key] for key in again}
                self.assertSameResult(live, again)

    def test_candidate_loop_ran_once_per_journaled_candidate(self):
        total = sum(len(r["candidates"]) for r in self.journal)
        self.assertGreater(total, 0)
        with _Plan(), \
                mock.patch.object(seq, "measure_persistence",
                                  wraps=seq.measure_persistence) as measured, \
                mock.patch.object(seq, "detect_candidates",
                                  wraps=seq.detect_candidates) as detected:
            seq.reanalyze_from_spool(self.out)
        self.assertEqual(detected.call_count, len(self.journal))
        self.assertEqual(measured.call_count, total)

    def test_reanalysis_catalogue_matches_live_catalogue(self):
        with _Plan():
            document, _decl = seq.reanalyze_from_spool(self.out)
        # reanalysis has no tuner, and its events say where the data came from
        differs_by_design = {"events", "tuner_type"}
        live = {k: v for k, v in self.live_document.items()
                if k not in differs_by_design}
        again = {k: v for k, v in document.items()
                 if k not in differs_by_design}
        self.assertSameResult(live, again, "document")
        self.assertEqual(self.live_document["tuner_type"], 5)
        self.assertIsNone(document["tuner_type"])
        self.assertEqual(len(document["events"]), len(self.journal))
        self.assertTrue(all(e["recomputed_from_spool"]
                            for e in document["events"]))

    def test_reanalysis_ignores_what_the_journal_claims(self):
        """The journal's derived fields are not evidence. A journal that lies
        about candidates and retention must still reanalyze to what the
        spooled bytes say, which is what live decided."""
        out = self.copy_of_evidence()
        records = _read_journal(out)
        for rec in records:
            if rec["type"] != "visit_acquired":
                continue
            if rec["position"] == 0:
                rec["candidates"], rec["persistence"], rec["retained"] = \
                    [], {}, []
            elif rec["position"] == 1:
                rec["retained"] = []
            else:
                rec["candidates"] = [{"baseband_hz": 123456.0,
                                      "excess_db": 99.0, "peak_bin": 1}]
                rec["retained"] = [0]
        _write_journal(out, records)
        for original in self.journal:
            lied = next(r for r in _read_journal(out)
                        if r["type"] == "visit_acquired"
                        and r["position"] == original["position"])
            data = seq._recompute_visit_data(out, lied)
            with self.subTest(position=original["position"]):
                self.assertEqual(
                    [{"baseband_hz": c.baseband_hz, "excess_db": c.excess_db,
                      "peak_bin": c.peak_bin} for c in data.candidates],
                    original["candidates"])
                self.assertEqual(data.retained, original["retained"])
                self.assertEqual(
                    {str(i): obs.to_dict()
                     for i, obs in data.persistence.items()},
                    original["persistence"])


class ReanalysisRefusalTests(ReanalysisEquivalenceBase):

    def test_missing_spool_is_refused_by_name(self):
        out = self.copy_of_evidence()
        os.remove(os.path.join(out, "spool", "visit_0000.iq"))
        with _Plan(), self.assertRaises(SequencerRefused) as caught:
            seq.reanalyze_from_spool(out)
        self.assertEqual(caught.exception.code, "SPOOL_MISSING")
        self.assertIn("position 0", str(caught.exception))
        self.assertIn("visit_0000.iq", str(caught.exception))

    def test_truncated_spool_is_refused_against_the_journal_byte_count(self):
        out = self.copy_of_evidence()
        path = os.path.join(out, "spool", "visit_0000.iq")
        with open(path, "rb") as handle:
            raw = handle.read()
        with open(path, "wb") as handle:
            handle.write(raw[:-1024])
        with _Plan(), self.assertRaises(SequencerRefused) as caught:
            seq.reanalyze_from_spool(out)
        self.assertEqual(caught.exception.code, "SPOOL_CORRUPT")
        self.assertIn(f"{len(raw) - 1024} bytes on disk", str(caught.exception))

    def test_internally_consistent_short_spool_is_still_refused(self):
        """Byte count and SHA-256 agree with the journal, but the object is
        seven windows, not eight. The check against the visit's definition is
        separate from the check against the journal, and must hold alone."""
        out = self.copy_of_evidence()
        path = os.path.join(out, "spool", "visit_0000.iq")
        with open(path, "rb") as handle:
            raw = handle.read()
        short = raw[:-WINDOW_BYTES]
        with open(path, "wb") as handle:
            handle.write(short)
        records = _read_journal(out)
        for rec in records:
            if rec["type"] == "visit_acquired" and rec["position"] == 0:
                rec["window_bytes"] = len(short)
                rec["window_sha256"] = hashlib.sha256(short).hexdigest()
        _write_journal(out, records)
        with _Plan(), self.assertRaises(SequencerRefused) as caught:
            seq.reanalyze_from_spool(out)
        self.assertEqual(caught.exception.code, "SPOOL_CORRUPT")
        self.assertIn(f"{WINDOWS_PER_VISIT} windows", str(caught.exception))

    def test_refusals_do_not_depend_on_evidence_order(self):
        """A bad spool on a later visit is refused too; earlier visits that
        reanalyzed cleanly do not make the epoch acceptable."""
        out = self.copy_of_evidence()
        os.remove(os.path.join(out, "spool", "visit_0002.iq"))
        with _Plan(), self.assertRaises(SequencerRefused) as caught:
            seq.reanalyze_from_spool(out)
        self.assertEqual(caught.exception.code, "SPOOL_MISSING")
        self.assertIn("position 2", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
