"""Production-path test of the usable-span boundary (r4, bounded and offline).

What this exercises -- and what it does not
-------------------------------------------
Driven for real: `RtlTcpTuner` (reading from an injected `socket_factory`), `acquire_visit`, the byte-exact admission step
`_decode_iq` (1,048,576 bytes, offset-binary uint8), `detect_candidates`, `measure_persistence`, the spool, the acquisition
journal and `_analyze_epoch`, all through the real entry point `run_epoch`. The observable is the journal record of the visit:
its `candidates` (with `peak_bin`) and `retained` lists.

Patched, exactly as test_rf_521_reanalysis_equivalence.py patches them: the four ADMINISTRATIVE gates
(`generate_tunings`, `generate_visit_schedule`, `check_declaration_authorized`, `admit_schedule`), so that a single visit can run
without the 64-tuning admission invariant and the authorization binding, which have their own tests. These gates are not part of
the detection path. The "mutant" runs patch `seq.detect_candidates` with a single-edit variant of its own source; nothing else changes.

No receiver hardware is involved: the in-process byte server is the repo's own `FakeSocket` test seam. Production code is not modified.
"""
import importlib.util, json, os, shutil, tempfile, unittest
from unittest import mock

import numpy as np

import rf_spur_catalogue_sequencer as seq
from rf_promotion_envelope import Tuning, Visit
from rf_spur_catalogue_sequencer import (ACQUISITION_JOURNAL_NAME, SAMPLE_RATE_HZ, WINDOW_BYTES, WINDOWS_PER_VISIT,
                                         VisitRefused, _decode_iq)
from test_rf_spur_catalogue_sequencer import SETTLE_BYTES, _declaration, _dongle_info, _encode, _tuner

_spec = importlib.util.spec_from_file_location(
    "rf_metamorphic_harness",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "rf_metamorphic_harness.py"))
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)

N = 524_288
TUNINGS = (Tuning(tuning_index=0, center_frequency_hz=100e6, band_id="test"),)
SCHEDULE = (Visit(position=0, tuning_index=0, retune_delta_hz=100e3),)
AMPLITUDE, SIGMA = 0.2, 0.05            # the amplitudes the repo's own equivalence test uses; CU8 adds only quantization noise
USABLE = float(seq.usable_half_span_hz())
INSIDE, OUTSIDE = 235_929, 235_930      # last bin with the predicate True / first with it False, production length
VARIANTS = {"production": None,
            "removed": H.MUTATIONS["span_limit_removed"] if "span_limit_removed" in H.MUTATIONS else H.MUTATIONS.get("span_limit_removed"),
            "strict": H.MUTATIONS.get("span_limit_strict")}


def windows_with_tone_at_bin(b, seed=1):
    """Eight complex64 windows, a tone exactly on FFT bin b (integer-modulo phase), seeded noise."""
    m = np.arange(N, dtype=np.int64)
    tone = AMPLITUDE * np.exp(2j * np.pi * ((b * m) % N) / N)
    out = []
    for i in range(WINDOWS_PER_VISIT):
        rng = np.random.default_rng(seed * 100 + i)
        noise = (rng.standard_normal(N) + 1j * rng.standard_normal(N)) * SIGMA / np.sqrt(2.0)
        out.append((tone + noise).astype(np.complex64))
    return out


def run_epoch_with_tone(bin_, variant="production"):
    """Serve CU8 bytes for a one-visit epoch through the real run_epoch; return (journal record, served bytes, document)."""
    edit = VARIANTS[variant]
    raw = _encode(windows_with_tone_at_bin(bin_))
    payload = _dongle_info() + b"\x00" * SETTLE_BYTES + raw
    root = tempfile.mkdtemp(prefix="r4-prodpath-")
    patches = [mock.patch.object(seq, "generate_tunings", lambda **_k: TUNINGS),
               mock.patch.object(seq, "generate_visit_schedule", lambda **_k: SCHEDULE),
               mock.patch.object(seq, "check_declaration_authorized", lambda decl: None),
               mock.patch.object(seq, "admit_schedule", lambda *a, **k: "test-schedule-digest")]
    if edit is not None:
        fn = H.build_variant(edit)
        assert fn is not None, f"mutation {variant} did not match the production source exactly once"
        patches.append(mock.patch.object(seq, "detect_candidates", fn))
    try:
        for p in patches: p.start()
        decl = _declaration(root); tuner = _tuner(payload)
        with tuner:
            document = seq.run_epoch(decl, tuner)
        with open(os.path.join(decl["output_dir"], ACQUISITION_JOURNAL_NAME)) as fh:
            records = [json.loads(l) for l in fh if l.strip()]
        rec = [r for r in records if r["type"] == "visit_acquired"]
        return (rec[0] if rec else None), raw, document, records
    finally:
        for p in reversed(patches): p.stop()
        shutil.rmtree(root, ignore_errors=True)


def present(rec, b):
    return rec is not None and any(c["peak_bin"] == b for c in rec["candidates"])


class ProductionPathBoundary(unittest.TestCase):
    """An eligible inside-bin candidate propagates through the real entry point; its equivalent outside-bin counterpart does not."""

    @classmethod
    def setUpClass(cls):
        cls.R = {}
        for name, b in (("inside", INSIDE), ("outside", OUTSIDE), ("inside_neg", N - INSIDE), ("outside_neg", N - OUTSIDE)):
            cls.R[("production", name)] = run_epoch_with_tone(b)
        for name, b in (("outside", OUTSIDE), ("outside_neg", N - OUTSIDE)):
            cls.R[("removed", name)] = run_epoch_with_tone(b, "removed")
        for name, b in (("inside", INSIDE), ("outside", OUTSIDE)):
            cls.R[("strict", name)] = run_epoch_with_tone(b, "strict")

    B = {"inside": INSIDE, "outside": OUTSIDE, "inside_neg": N - INSIDE, "outside_neg": N - OUTSIDE}

    def test_the_real_path_carried_the_bytes_that_were_served(self):
        for key, (rec, raw, doc, records) in self.R.items():
            with self.subTest(key=key):
                self.assertIsNotNone(rec, "no visit_acquired record: the visit was refused")
                self.assertEqual(rec["window_bytes"], WINDOWS_PER_VISIT * WINDOW_BYTES)
                self.assertEqual(len(raw), WINDOWS_PER_VISIT * WINDOW_BYTES)
                self.assertEqual(doc["n_visits_acquired"], 1); self.assertEqual(doc["n_visits_refused"], 0)

    def test_an_eligible_inside_bin_candidate_is_admitted_and_retained(self):
        for name in ("inside", "inside_neg"):
            rec = self.R[("production", name)][0]; b = self.B[name]
            self.assertTrue(present(rec, b), f"{name}: bin {b} not admitted")
            idx = [i for i, c in enumerate(rec["candidates"]) if c["peak_bin"] == b][0]
            self.assertIn(idx, rec["retained"], f"{name}: admitted but not retained")
            self.assertLessEqual(abs(np.fft.fftfreq(N, 1 / SAMPLE_RATE_HZ)[b]), USABLE)

    def test_the_outside_counterpart_is_not_admitted(self):
        for name in ("outside", "outside_neg"):
            self.assertFalse(present(self.R[("production", name)][0], self.B[name]), f"{name} was admitted")
            self.assertGreater(abs(np.fft.fftfreq(N, 1 / SAMPLE_RATE_HZ)[self.B[name]]), USABLE)

    def test_attribution_the_outside_candidate_is_present_when_only_the_span_rule_is_removed(self):
        """Another rule cannot explain the disappearance: removing ONLY the span predicate admits it."""
        for name in ("outside", "outside_neg"):
            rec = self.R[("removed", name)][0]
            self.assertTrue(present(rec, self.B[name]), f"{name}: absent even with the span rule removed -> another rule removed it")

    def test_the_strict_mutant_is_an_equivalent_mutant_on_the_production_geometry(self):
        for name in ("inside", "outside"):
            self.assertEqual(present(self.R[("strict", name)][0], self.B[name]),
                             present(self.R[("production", name)][0], self.B[name]), name)

    def test_retention_does_not_apply_the_span_rule(self):
        """Only admission is observable for the span predicate: the outside tone is never a candidate, yet retention would measure it."""
        from rf_spur_retention_estimator import measure_persistence
        f = float(np.fft.fftfreq(N, 1 / SAMPLE_RATE_HZ)[OUTSIDE])
        obs = measure_persistence(tuning_id="r4", windows=windows_with_tone_at_bin(OUTSIDE), sample_rate_hz=float(SAMPLE_RATE_HZ),
                                  feature_baseband_hz=f)
        self.assertEqual(obs.qualifying(), WINDOWS_PER_VISIT)


class AcquisitionBoundary(unittest.TestCase):
    def test_a_500000_sample_window_cannot_occur_on_the_production_acquisition_path(self):
        """n = 500,000 is function-level only: the real admission step refuses any window that is not byte-exact."""
        with self.assertRaises(VisitRefused):
            _decode_iq(b"\x7f" * (2 * 500_000))

    def test_the_byte_exact_length_is_the_524288_sample_window(self):
        self.assertEqual(WINDOW_BYTES, 2 * 524_288)
        self.assertEqual(len(_decode_iq(b"\x7f" * WINDOW_BYTES)), 524_288)


if __name__ == "__main__":
    unittest.main()
