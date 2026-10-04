#!/usr/bin/env python3
"""Tests for the §7.1 tuner-sequencing module.

No hardware anywhere: a fake socket serves synthetic IQ bytes, and the
association / classification paths are exercised directly on synthetic
detections. A mini end-to-end run patches the schedule generators down to
two tunings and serves six synthetic visits.
"""

import json
import math
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rf_spur_catalogue_sequencer as seq
from rf_spur_catalogue_sequencer import (
    AcquiredVisit,
    AssociatedTrack,
    CandidateFeature,
    RtlTcpTuner,
    SequencerRefused,
    TuningVisitData,
    VisitRefused,
    WINDOW_BYTES,
    acquire_visit,
    associate_tracks,
    check_declaration,
    classify_track,
    detect_candidates,
    run_epoch,
    write_artefacts,
)
from rf_promotion_envelope import (
    CONSISTENT_WITH_INTERNAL_MIXING,
    CONSISTENT_WITH_INTERNAL_REFERENCE,
    PLAN_PERSISTENCE_MARGIN_DB,
    ReferenceComb,
    SpurPersistenceObservation,
    SPUR_CANDIDATE_UNRESOLVED,
    Tuning,
    Visit,
    usable_half_span_hz,
)

SR = 2048000
N = int(0.256 * SR)
SETTLE_BYTES = int(0.3 * SR) * 2


def _tone_windows(offset_hz, snr_db, n_windows=8, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(N) / SR
    out = []
    for _ in range(n_windows):
        noise = (rng.standard_normal(N)
                 + 1j * rng.standard_normal(N)) / math.sqrt(2.0)
        tone = 10.0 ** (snr_db / 20.0) * np.exp(2j * math.pi * offset_hz * t)
        out.append((tone + noise).astype(np.complex64))
    return out


def _encode(windows):
    parts = []
    for w in windows:
        i = np.clip(np.round(w.real * 127.5 + 127.5), 0, 255).astype(np.uint8)
        q = np.clip(np.round(w.imag * 127.5 + 127.5), 0, 255).astype(np.uint8)
        iq = np.empty(2 * len(w), dtype=np.uint8)
        iq[0::2] = i
        iq[1::2] = q
        parts.append(iq.tobytes())
    return b"".join(parts)


def _dongle_info(tuner_type=5, gain_count=29):
    return b"RTL0" + struct.pack(">I", tuner_type) + struct.pack(">I", gain_count)


class FakeSocket:
    """A socket-shaped byte server. `payload` is consumed in order."""

    def __init__(self, payload: bytes):
        self._payload = payload
        self._pos = 0
        self.sent = []

    def recv(self, n):
        if self._pos >= len(self._payload):
            return b""
        end = min(len(self._payload), self._pos + n)
        out = self._payload[self._pos:end]
        self._pos = end
        return out

    def sendall(self, data):
        self.sent.append(bytes(data))

    def settimeout(self, _t):
        pass

    def close(self):
        pass


def _tuner(payload: bytes) -> RtlTcpTuner:
    fake = FakeSocket(payload)
    tuner = RtlTcpTuner("fake", 1, socket_factory=lambda _addr: fake)
    tuner._fake = fake  # the socket, kept after the context manager closes
    return tuner


def _declaration(tmpdir, **overrides):
    decl = {
        "run_id": "test-run",
        "epoch": 1,
        "receiver": {"sensor_id": "test-sensor",
                     "description": "synthetic NESDR"},
        "reference_hz": 28.8e6,
        "reference_ppm": 100.0,
        "ppm_justification": "synthetic: uncalibrated, earns a cap of 3",
        "termination": {"part": "test-50R", "connector": "SMA",
                        "fitted": "2026-09-28T00:00:00Z"},
        "site": "synthetic bench",
        "bands": [{"band_id": "test", "low_hz": 90e6, "high_hz": 110e6}],
        "seed": 7,
        "gain_db": 20.7,
        "rtl_tcp_host": "fake",
        "rtl_tcp_port": 1,
        "output_dir": os.path.join(tmpdir, "out"),
    }
    decl.update(overrides)
    return decl


def _persistent_obs(tuning_id="tuning-000"):
    return SpurPersistenceObservation(
        tuning_id=tuning_id, repeat_excess_db=(12.0,) * 8)


class DecodeTests(unittest.TestCase):
    def test_short_window_is_refused_not_padded(self):
        tuner = _tuner(_dongle_info() + b"\x7f" * (WINDOW_BYTES - 1))
        with tuner:
            with self.assertRaises(VisitRefused):
                tuner.read_window()

    def test_decode_roundtrip_centres_on_zero(self):
        raw = _encode([np.zeros(N, dtype=np.complex64)])
        tuner = _tuner(_dongle_info() + raw)
        with tuner:
            w = tuner.read_window()
        self.assertEqual(len(w), N)
        self.assertLess(float(np.max(np.abs(w))), 0.01)


class TunerControlTests(unittest.TestCase):
    def test_bad_magic_is_refused(self):
        with self.assertRaises(SequencerRefused) as caught:
            with _tuner(b"NOPE" + struct.pack(">II", 5, 29)):
                pass
        self.assertEqual(caught.exception.code, "TUNER_NOT_RTL_TCP")

    def test_set_lo_sends_opcode_0x01_big_endian(self):
        tuner = _tuner(_dongle_info())
        with tuner:
            tuner.set_lo_hz(100_000_000.0)
        sent = tuner._fake.sent
        self.assertEqual(sent[0], b"\x01" + struct.pack(">I", 100_000_000))

    def test_gain_outside_the_table_is_refused(self):
        tuner = _tuner(_dongle_info())
        with tuner:
            with self.assertRaises(SequencerRefused) as caught:
                tuner.set_manual_gain_db(20.0)
        self.assertEqual(caught.exception.code, "GAIN_NOT_SUPPORTED")

    def test_gain_pins_manual_mode_then_gain(self):
        tuner = _tuner(_dongle_info())
        with tuner:
            tuner.set_manual_gain_db(20.7)
        sent = tuner._fake.sent
        self.assertEqual(sent[0], b"\x03" + struct.pack(">I", 1))
        self.assertEqual(sent[1], b"\x04" + struct.pack(">I", 207))


class AcquireTests(unittest.TestCase):
    def test_visit_acquires_eight_windows(self):
        windows = _tone_windows(100e3, 20.0)
        payload = (_dongle_info() + b"\x00" * SETTLE_BYTES
                   + _encode(windows))
        tuner = _tuner(payload)
        with tuner:
            acquired = acquire_visit(tuner, 0, "tuning-000", 0,
                                     100_000_000.0, 50_000.0)
        self.assertEqual(len(acquired.windows), 8)
        self.assertEqual(acquired.attempt, 1)

    def test_visit_refused_after_retries_on_short_stream(self):
        payload = _dongle_info() + b"\x00" * SETTLE_BYTES + b"\x7f" * 100
        tuner = _tuner(payload)
        with tuner:
            with self.assertRaises(VisitRefused):
                acquire_visit(tuner, 0, "tuning-000", 0,
                              100_000_000.0, 50_000.0)


class DetectTests(unittest.TestCase):
    def test_injected_tone_is_found(self):
        found = detect_candidates(_tone_windows(100e3, 20.0))
        self.assertTrue(found)
        best = found[0]
        self.assertLess(abs(best.baseband_hz - 100e3), 2 * seq.BIN_HZ)
        self.assertGreater(best.excess_db, PLAN_PERSISTENCE_MARGIN_DB)

    def test_tone_beyond_the_usable_span_is_not_detected(self):
        edge = usable_half_span_hz() + 50e3
        found = detect_candidates(_tone_windows(edge, 30.0))
        self.assertEqual(
            [c for c in found if abs(c.baseband_hz - edge) < 1e3], [])


def _visit_data(position, delta, candidates):
    return TuningVisitData(
        visit=AcquiredVisit(position=position, tuning_index=0,
                            tuning_id="tuning-000", lo_hz=100e6 + delta,
                            retune_delta_hz=delta, windows=[],
                            raw_windows=[],
                            elapsed_s=2.0, attempt=1),
        candidates=candidates,
        persistence={i: _persistent_obs() for i in range(len(candidates))},
        retained=list(range(len(candidates))))


def _cand(offset_hz, excess=30.0):
    return CandidateFeature(baseband_hz=offset_hz, excess_db=excess,
                            peak_bin=int(offset_hz / seq.BIN_HZ))


class AssociateTests(unittest.TestCase):
    DELTAS = (-200e3, -50e3, 100e3)

    def test_integer_slope_track_is_recovered(self):
        # slope 2 through a 100 kHz seed at the first visit.
        visits = [
            _visit_data(0, self.DELTAS[0], [_cand(100e3)]),
            _visit_data(1, self.DELTAS[1], [_cand(400e3),
                                            _cand(700e3, excess=10.0)]),
            _visit_data(2, self.DELTAS[2], [_cand(700e3)]),
        ]
        tracks, unassociated, ambiguous = associate_tracks(
            "tuning-000", 100e6, visits)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].slope_hypothesis, 2)
        self.assertEqual(ambiguous, [])
        # The distractor at visit 1 (700 kHz, weak) is unassociated.
        self.assertEqual(len(unassociated), 1)

    def test_seed_explained_by_two_slopes_is_ambiguous(self):
        # The seed at 0 is matched by slope 0 (0, 0, 0) and by slope 1
        # (0, 150e3, 300e3): one detection, two slopes, refused.
        visits = [
            _visit_data(0, self.DELTAS[0], [_cand(0.0)]),
            _visit_data(1, self.DELTAS[1], [_cand(0.0), _cand(150e3)]),
            _visit_data(2, self.DELTAS[2], [_cand(0.0), _cand(300e3)]),
        ]
        tracks, _unassociated, ambiguous = associate_tracks(
            "tuning-000", 100e6, visits)
        self.assertEqual(tracks, [])
        self.assertEqual(len(ambiguous), 1)
        self.assertGreater(len(ambiguous[0]["slopes"]), 1)

    def test_seed_with_no_binding_is_unassociated(self):
        visits = [
            _visit_data(0, self.DELTAS[0], [_cand(100e3)]),
            _visit_data(1, self.DELTAS[1], [_cand(900e3)]),
            _visit_data(2, self.DELTAS[2], [_cand(100e3)]),
        ]
        tracks, unassociated, _ambiguous = associate_tracks(
            "tuning-000", 100e6, visits)
        self.assertEqual(tracks, [])
        self.assertTrue(unassociated)


def _track(deltas, offsets, anchor=100e6):
    return AssociatedTrack(
        tuning_id="tuning-000", anchor_center_hz=anchor,
        deltas=tuple(deltas), offsets=tuple(offsets),
        visit_positions=tuple(range(len(deltas))),
        slope_hypothesis=0, seed_excess_db=30.0,
        persistence=_persistent_obs())


class ClassifyTests(unittest.TestCase):
    DELTAS = (-200e3, -50e3, 100e3)

    def test_slope_two_is_internal_mixing(self):
        track = _track(self.DELTAS, (100e3, 400e3, 700e3))
        comb = ReferenceComb(reference_hz=28.8e6, reference_ppm=100.0)
        spur, finding = classify_track(track, comb, 0)
        self.assertIsNone(finding)
        self.assertEqual(spur.classification,
                         CONSISTENT_WITH_INTERNAL_MIXING)
        self.assertIsNone(spur.reference_harmonic)

    def test_non_integer_slope_is_unresolved(self):
        track = _track(self.DELTAS, (100e3, 325e3, 550e3))  # slope 1.5
        comb = ReferenceComb(reference_hz=28.8e6, reference_ppm=100.0)
        spur, finding = classify_track(track, comb, 0)
        self.assertIsNone(finding)
        self.assertEqual(spur.classification, SPUR_CANDIDATE_UNRESOLVED)

    def test_slope_minus_one_on_the_comb_is_reference(self):
        # rf = anchor + C = 57.6 MHz = 2 x 28.8 MHz, inside the cap of 3.
        track = _track(self.DELTAS, (700e3, 550e3, 400e3), anchor=57.1e6)
        comb = ReferenceComb(reference_hz=28.8e6, reference_ppm=100.0)
        spur, finding = classify_track(track, comb, 0)
        self.assertIsNone(finding)
        self.assertEqual(spur.classification,
                         CONSISTENT_WITH_INTERNAL_REFERENCE)
        self.assertEqual(spur.reference_harmonic, 2)

    def test_slope_minus_one_off_the_comb_is_an_ingress_finding(self):
        track = _track(self.DELTAS, (817.3e3, 667.3e3, 517.3e3),
                       anchor=57.1e6)
        comb = ReferenceComb(reference_hz=28.8e6, reference_ppm=100.0)
        spur, finding = classify_track(track, comb, 0)
        self.assertIsNone(spur)
        self.assertIsNotNone(finding)
        self.assertIn("ingress", finding["reason"])


class DeclarationTests(unittest.TestCase):
    def test_missing_key_is_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            decl = _declaration(tmpdir)
            del decl["site"]
            with self.assertRaises(SequencerRefused) as caught:
                check_declaration(decl)
        self.assertEqual(caught.exception.code, "DECLARATION_ABSENT")

    def test_empty_ppm_justification_is_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            decl = _declaration(tmpdir, ppm_justification="  ")
            with self.assertRaises(SequencerRefused):
                check_declaration(decl)

    def test_undeclared_termination_act_is_refused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            decl = _declaration(tmpdir)
            decl["termination"] = {"part": "x", "connector": "SMA",
                                   "fitted": ""}
            with self.assertRaises(SequencerRefused) as caught:
                check_declaration(decl)
        self.assertEqual(caught.exception.code, "TERMINATION_UNDECLARED")


class MiniRunTests(unittest.TestCase):
    """Two tunings, six visits, one synthetic slope-2 spur, end to end."""

    def test_mini_run_writes_artefacts(self):
        tunings = (Tuning(tuning_index=0, center_frequency_hz=100e6,
                          band_id="test"),
                   Tuning(tuning_index=1, center_frequency_hz=200e6,
                          band_id="test"))
        deltas = (-200e3, -50e3, 100e3)
        schedule = tuple(
            Visit(position=i, tuning_index=ti, retune_delta_hz=d)
            for i, (ti, d) in enumerate(
                [(0, deltas[0]), (0, deltas[1]), (0, deltas[2]),
                 (1, deltas[0]), (1, deltas[1]), (1, deltas[2])]))

        offsets = {deltas[0]: 100e3, deltas[1]: 400e3, deltas[2]: 700e3}
        payload = bytearray(_dongle_info())
        for visit in schedule:
            payload += b"\x00" * SETTLE_BYTES
            if visit.tuning_index == 0:
                windows = _tone_windows(offsets[visit.retune_delta_hz],
                                        20.0, seed=visit.position + 1)
            else:
                windows = _tone_windows(0.0, -100.0, seed=visit.position + 1)
            payload += _encode(windows)

        real_generators = (seq.generate_tunings, seq.generate_visit_schedule)
        real_preatcontact = (seq.check_declaration_authorized,
                             seq.admit_schedule)
        seq.generate_tunings = lambda **_k: tunings
        seq.generate_visit_schedule = lambda **_k: schedule
        # synthetic 2-tuning schedule: authorization and the 64-tuning
        # admission invariant are covered by their own tests
        seq.check_declaration_authorized = lambda decl: None
        seq.admit_schedule = lambda *a, **k: "test-schedule-digest"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                decl = _declaration(tmpdir)
                tuner = _tuner(bytes(payload))
                with tuner:
                    document = run_epoch(decl, tuner)
                self.assertEqual(document["n_visits_acquired"], 6)
                self.assertEqual(document["n_visits_refused"], 0)
                self.assertEqual(len(document["entries"]), 1)
                entry = document["entries"][0]
                self.assertEqual(entry["classification"],
                                 CONSISTENT_WITH_INTERNAL_MIXING)
                self.assertEqual(entry["stability_class"], "SESSION_SCOPED")
                paths = write_artefacts(document, decl, decl["output_dir"])
                for path in paths:
                    self.assertTrue(os.path.exists(path))
                with open(paths[0]) as handle:
                    on_disk = json.load(handle)
                self.assertEqual(len(on_disk["entries"]), 1)
        finally:
            (seq.generate_tunings,
             seq.generate_visit_schedule) = real_generators
            (seq.check_declaration_authorized,
             seq.admit_schedule) = real_preatcontact


if __name__ == "__main__":
    unittest.main()
