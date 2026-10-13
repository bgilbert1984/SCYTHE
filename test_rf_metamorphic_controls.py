"""Bounded controls for the metamorphic harness r2 (tools/rf_metamorphic_harness.py).

These test the harness's comparators and output lifecycle, not the detector. They are
deliberately light (no large FFTs): the defect that interrupted r1 was the comparator's
O(N^2) scaling, so the first control is a timing-bounded reproduction of that exact case.
"""
import hashlib, importlib.util, os, random, shutil, tempfile, time, unittest

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "rf_metamorphic_harness",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "rf_metamorphic_harness.py"))
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)

N = 524288
IDENT = dict(bin_map=lambda b: b, inv_bin_map=lambda b: b, f_map=lambda f: f, n=N, span_checked=False)


def cands(count, start=10_000):
    return [(float(b) * H.BIN_HZ, 7.0 + (b % 13) * 0.1, b) for b in range(start, start + count)]


class CardinalityGuard(unittest.TestCase):
    def test_one_candidate_versus_139958_returns_promptly_without_matching(self):
        base, mutant = cands(1), cands(139_958)
        t = time.time()
        verdict, detail = H.keyed_compare(base, mutant, preserves_cardinality=True, **IDENT)
        self.assertLess(time.time() - t, 2.0)
        self.assertEqual(verdict, H.V)
        self.assertEqual(detail["matching"], "NOT_ATTEMPTED")
        self.assertEqual((detail["n_base"], detail["n_other"]), (1, 139_958))

    def test_a_relation_that_does_not_require_cardinality_never_manufactures_a_violation(self):
        # Same sizes, but SHIFT-like (cardinality not required): linear time, no pairwise matching.
        t = time.time()
        verdict, detail = H.keyed_compare(cands(1), cands(139_958), preserves_cardinality=False, **IDENT)
        self.assertLess(time.time() - t, 5.0)
        self.assertNotIn("matching", detail)         # the guard did not fire; the bin-keyed path ran
        self.assertEqual(verdict, H.V)               # extra bins with no excuse are still a finding
        self.assertEqual(detail["n_extra"], 139_957)


class DeterministicMatching(unittest.TestCase):
    def test_equal_cardinality_reordered_candidates_compare_correctly(self):
        base = cands(2000)
        shuffled = base[:]; random.Random(7).shuffle(shuffled)
        verdict, detail = H.keyed_compare(base, shuffled, preserves_cardinality=True, **IDENT)
        self.assertEqual(verdict, H.S, detail)

    def test_perturbations_are_detected_at_the_declared_tolerance(self):
        base = cands(50)
        def with_change(k, f=0.0, e=0.0):
            other = base[:]; freq, ex, b = other[k]; other[k] = (freq + f, ex + e, b); return other
        self.assertEqual(H.keyed_compare(base, with_change(3, e=1e-12), preserves_cardinality=True, **IDENT)[0], H.S)
        self.assertEqual(H.keyed_compare(base, with_change(3, e=1e-7), preserves_cardinality=True, **IDENT)[0], H.A)
        self.assertEqual(H.keyed_compare(base, with_change(3, e=1e-4), preserves_cardinality=True, **IDENT)[0], H.V)
        self.assertEqual(H.keyed_compare(base, with_change(3, f=1e-3), preserves_cardinality=True, **IDENT)[0], H.V)

    def test_ambiguous_candidate_identities_refuse_comparison(self):
        base = cands(5)
        dup = base[:] + [(base[0][0] + 1.0, 9.0, base[0][2])]          # two candidates in one bin
        for a, b in ((base, dup), (dup, base)):
            verdict, detail = H.keyed_compare(a, b, preserves_cardinality=False, **IDENT)
            self.assertEqual(verdict, H.U)
            self.assertEqual(detail["reason"], "AMBIGUOUS_CANDIDATE_IDENTITY")

    def test_lists_are_never_truncated(self):
        base = cands(300); other = base[:-1]                             # one missing
        verdict, detail = H.keyed_compare(base, other, preserves_cardinality=False, **IDENT)
        self.assertEqual(verdict, H.V)
        self.assertEqual(detail["n_missing"], 1)


class DurableOutput(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); os.makedirs(os.path.join(self.d, "phaseA"))
        self.mp = os.path.join(self.d, "MANIFEST.sha256")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_an_interruption_leaves_completed_results_recoverable_and_checksummed(self):
        for i in range(3):
            H.durable_write(os.path.join(self.d, "phaseA", f"f{i}.json"), {"i": i}, self.mp)
        # simulated crash while writing the fourth: a partial temp file, no manifest line
        with open(os.path.join(self.d, "phaseA", "f3.json.tmp"), "wb") as fh:
            fh.write(b'{"i": 3, "trunc')
        ok, bad, missing = H.verify_manifest(self.d)
        self.assertEqual((len(ok), bad, missing), (3, [], []))
        self.assertFalse(os.path.exists(os.path.join(self.d, "phaseA", "f3.json")))

    def test_tampering_and_loss_are_detected(self):
        for i in range(2):
            H.durable_write(os.path.join(self.d, "phaseA", f"f{i}.json"), {"i": i}, self.mp)
        with open(os.path.join(self.d, "phaseA", "f0.json"), "ab") as fh:
            fh.write(b" ")
        os.remove(os.path.join(self.d, "phaseA", "f1.json"))
        ok, bad, missing = H.verify_manifest(self.d)
        self.assertEqual((ok, bad, missing), ([], ["phaseA/f0.json"], ["phaseA/f1.json"]))


class IdentityStopsInterpretation(unittest.TestCase):
    def test_identity_failure_stops_the_run_and_writes_stopped_json(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "phaseA"))
        mp = os.path.join(d, "MANIFEST.sha256")
        rng = np.random.default_rng(0)
        windows = [rng.normal(size=4096) + 1j * rng.normal(size=4096) for _ in range(8)]
        real = H.build_variant
        try:
            H.build_variant = lambda edit: (lambda w: [object()])        # an "identity" that is not
            ok = H.identity_phase(["fake"], lambda n: windows, mp, d)
        finally:
            H.build_variant = real
        try:
            self.assertFalse(ok)
            self.assertTrue(os.path.exists(os.path.join(d, "STOPPED.json")))
            with open(os.path.join(d, "identity.json")) as fh:
                self.assertIn("IDENTITY_FAILED", fh.read())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_a_faithful_identity_variant_is_verified(self):
        d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "phaseA"))
        rng = np.random.default_rng(1)
        windows = [rng.normal(size=4096) + 1j * rng.normal(size=4096) for _ in range(8)]
        try:
            self.assertTrue(H.identity_phase(["fake"], lambda n: windows, os.path.join(d, "MANIFEST.sha256"), d))
            self.assertFalse(os.path.exists(os.path.join(d, "STOPPED.json")))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class MutationEdits(unittest.TestCase):
    def test_every_mutation_matches_production_source_exactly_once(self):
        for name, edit in H.MUTATIONS.items():
            with self.subTest(mutant=name):
                self.assertIsNotNone(H.build_variant(edit))

    def test_an_edit_that_does_not_match_is_unverified_not_silently_used(self):
        self.assertIsNone(H.build_variant(("this text is not in the source", "x")))


if __name__ == "__main__":
    unittest.main()
