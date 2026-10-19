"""Bounded controls for the r4 span-boundary logic (tools/rf_metamorphic_harness.py, revision r4).

These test the harness's oracle, attribution gate, credit rules and layer separation, not the detector. They are light
(the one orchestration test uses fake detectors and a pure-tone window; no FFT of real data).
"""
import importlib.util, inspect, os, time, unittest

import numpy as np

import rf_spur_catalogue_sequencer as seq

_spec = importlib.util.spec_from_file_location(
    "rf_metamorphic_harness", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "rf_metamorphic_harness.py"))
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)
P0, N0, NX = 524288, 524280, 500000
S, V, P = H.S, H.V, H.P


def obs(n, bins, target):
    return {"n_candidates": n, "bins": bins, "target_present": target in set(bins or [])}


class OracleFidelity(unittest.TestCase):
    def test_boundary_bins_on_the_three_geometries(self):
        cases = [(P0, 235929, True), (P0, 235930, False), (P0, P0 - 235929, True), (P0, P0 - 235930, False),
                 (N0, 235925, True), (N0, 235926, False),            # the floating-point trap: ideal bin, computed value above usable
                 (NX, 224999, True), (NX, 225000, True), (NX, 225001, False), (NX, NX - 225000, True)]
        for n, b, want in cases:
            with self.subTest(n=n, bin=b):
                self.assertEqual(H.span_ok(n, b), want)

    def test_the_oracle_is_bit_identical_to_the_production_predicate_on_the_whole_grid(self):
        for n in (P0, N0, NX):
            prod_grid = np.fft.fftfreq(n, d=1.0 / seq.SAMPLE_RATE_HZ)          # the exact expression detect_candidates uses
            want = np.abs(prod_grid) <= float(seq.usable_half_span_hz())
            got = np.array([H.span_ok(n, b) for b in range(0, n, 997)] + [H.span_ok(n, b) for b in range(n - 5, n)])
            idx = list(range(0, n, 997)) + list(range(n - 5, n))
            self.assertTrue(np.array_equal(got, want[idx]), n)

    def test_no_epsilon_or_idealised_frequency_in_the_oracle(self):
        import ast, textwrap
        code = ""
        for fn in (H.span_ok, H.grid):                       # CODE only: docstrings and comments may discuss epsilons
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.body and isinstance(node.body[0], ast.Expr) \
                        and isinstance(getattr(node.body[0], "value", None), ast.Constant) and isinstance(node.body[0].value.value, str):
                    node.body = node.body[1:]
            code += ast.unparse(tree)
        for forbidden in ("isclose", "allclose", "finfo", "eps", "1e-", "Fraction", "round("):
            self.assertNotIn(forbidden, code, forbidden)
        self.assertIn("<= USABLE", code)                      # the production predicate, evaluated on the computed value

    def test_applicability_uses_the_bin_centre_not_the_reported_sub_bin_frequency(self):
        # a candidate whose REPORTED frequency exceeds usable while its bin centre (235,929) is inside: r3 would have skipped it
        reported = 921601.0
        self.assertGreater(reported, H.USABLE); self.assertTrue(H.span_ok(P0, 235929))
        base = [(reported, 20.0, 235929)]
        v, d = H.keyed_compare(base, [(reported, 20.0, 235929)], n=P0, bin_map=lambda b: b, inv_bin_map=lambda b: b,
                               f_map=lambda f: f, preserves_cardinality=False, span_checked=True)
        self.assertEqual(d["not_applicable"], 0); self.assertEqual(v, S)

    def test_the_documented_geometry_is_what_layer_a_computes(self):
        la = H.layer_a(); g = la["geometries"]
        self.assertFalse(la["detector_involved"])
        self.assertEqual(g[str(P0)]["computed_exactly_on_boundary"], []); self.assertEqual(g[str(P0)]["ideal_on_boundary_bins"], [])
        self.assertEqual(g[str(N0)]["ideal_on_boundary_bins"], [235926]); self.assertEqual(g[str(N0)]["computed_exactly_on_boundary"], [])
        self.assertEqual(g[str(NX)]["computed_exactly_on_boundary"], [225000])


class AttributionGateAndCredit(unittest.TestCase):
    T_OUT = 235930

    def obs_set(self, **present):
        base = {"production": obs(0, [], self.T_OUT), "span_limit_removed": obs(1, [self.T_OUT], self.T_OUT),
                "span_limit_widened": obs(1, [self.T_OUT], self.T_OUT), "span_limit_strict": obs(0, [], self.T_OUT)}
        base.update(present); return base

    def test_removed_and_widened_are_detected_by_an_out_of_span_candidate_and_strict_is_equivalent(self):
        o = self.obs_set()
        r = {v: H.judge(v, o, [], self.T_OUT, P0, True) for v in o}
        self.assertEqual(r["production"][0], S)
        self.assertEqual(r["span_limit_removed"][1], "MUTANT_DETECTED"); self.assertEqual(r["span_limit_widened"][1], "MUTANT_DETECTED")
        self.assertEqual(r["span_limit_strict"][1], "MUTANT_SURVIVED_EQUIVALENT_ON_GRID")

    def test_the_guard_band_point_separates_removed_from_a_one_percent_widened_span(self):
        t = 249037
        o = {"production": obs(0, [], t), "span_limit_removed": obs(1, [t], t), "span_limit_widened": obs(0, [], t), "span_limit_strict": obs(0, [], t)}
        self.assertEqual(H.judge("span_limit_removed", o, [], t, P0, True)[1], "MUTANT_DETECTED")
        self.assertEqual(H.judge("span_limit_widened", o, [], t, P0, True)[1], "MUTANT_SURVIVED_EQUIVALENT_ON_GRID")

    def test_if_another_rule_removed_the_candidate_no_variant_gets_credit_or_blame(self):
        o = self.obs_set(span_limit_removed=obs(0, [], self.T_OUT))        # absent even with the span rule removed
        for v in ("span_limit_removed", "span_limit_widened", "span_limit_strict"):
            vd, credit, attr = H.judge(v, o, [], self.T_OUT, P0, True)
            self.assertFalse(attr); self.assertEqual(credit, "NOT_COMPARABLE", v)

    def test_a_failed_paired_control_removes_all_credit(self):
        for v in ("span_limit_removed", "span_limit_widened"):
            self.assertEqual(H.judge(v, self.obs_set(), [], self.T_OUT, P0, False)[1], "NOT_COMPARABLE")

    def test_a_production_violation_blocks_span_mutant_credit(self):
        o = self.obs_set(production=obs(1, [self.T_OUT], self.T_OUT))      # production admits it although the predicate says no
        self.assertEqual(H.judge("production", o, [], self.T_OUT, P0, True)[0], V)
        self.assertEqual(H.judge("span_limit_removed", o, [], self.T_OUT, P0, True)[1], "NOT_COMPARABLE")

    def test_strict_is_detected_only_where_the_computed_bin_centre_equals_the_boundary(self):
        t = 225000                                                           # n = 500,000: computed exactly 921600.0, predicate True
        o = {"production": obs(1, [t], t), "span_limit_removed": obs(1, [t], t), "span_limit_widened": obs(1, [t], t), "span_limit_strict": obs(0, [], t)}
        self.assertEqual(H.judge("span_limit_strict", o, [t], t, NX, True)[1], "MUTANT_DETECTED")
        t = 235926                                                           # n = 524,280: ideal bin, computed above usable, predicate False
        o = {"production": obs(0, [], t), "span_limit_removed": obs(1, [t], t), "span_limit_widened": obs(1, [t], t), "span_limit_strict": obs(0, [], t)}
        self.assertEqual(H.judge("span_limit_strict", o, [], t, N0, True)[1], "MUTANT_SURVIVED_EQUIVALENT_ON_GRID")
        t = 235929                                                           # production length: no bin on the boundary
        o = {"production": obs(1, [t], t), "span_limit_removed": obs(1, [t], t), "span_limit_widened": obs(1, [t], t), "span_limit_strict": obs(1, [t], t)}
        self.assertEqual(H.judge("span_limit_strict", o, [t], t, P0, True)[1], "MUTANT_SURVIVED_EQUIVALENT_ON_GRID")

    def test_non_span_mutants_are_not_credited_by_the_boundary_relation(self):
        o = self.obs_set(); o["absolute_floor"] = obs(139958, None, self.T_OUT)
        self.assertEqual(H.judge("absolute_floor", o, [], self.T_OUT, P0, True)[1], "NOT_COMPARABLE")

    def test_a_139958_candidate_observation_is_judged_by_cardinality_without_matching(self):
        t0 = time.time()
        o = H.observation([(float(b), 9.0, b) for b in range(1, 139_959)], 5)
        self.assertIsNone(o["bins"]); self.assertEqual(H.verdict_vs_expected(o, []), V)
        self.assertLess(time.time() - t0, 2.0)


class Preconditions(unittest.TestCase):
    def test_refusals(self):
        n = P0
        self.assertIsNone(H.case_preconditions(n, 26214, 235929, [235929]))
        self.assertEqual(H.case_preconditions(n, 26214, 3, [3]), "TARGET_NEAR_DC_OR_NYQUIST")
        self.assertEqual(H.case_preconditions(n, 26214, n // 2 + 2, [n // 2 + 2]), "TARGET_NEAR_DC_OR_NYQUIST")
        self.assertEqual(H.case_preconditions(n, 26214, 235929, [235929, 235935]), "COLLISION_WITH_OTHER_MAPPED_CANDIDATE")
        self.assertIsNone(H.case_preconditions(n, 26214, 235929, [235929, 235929 + 1000]))
        self.assertEqual(H.case_preconditions(n, 26214, 235929, [235929, n - 1]), "MAPPED_BIN_OUTSIDE_SEARCH_RANGE")

    def test_every_mutation_matches_the_production_source_exactly_once(self):
        self.assertEqual(sorted(H.MUTATIONS), sorted(["absolute_floor", "sign_dropped", "first_repeat_only", "span_limit_removed",
                                                       "span_limit_widened", "span_limit_strict"]))
        for name, edit in H.MUTATIONS.items():
            with self.subTest(mutant=name):
                self.assertIsNotNone(H.build_variant(edit))


class LayerSeparationAndOrchestration(unittest.TestCase):
    """run_boundary_cases with FAKE detectors on a pure-tone window (the tone's bin is read back from its phase): no real FFT."""

    @staticmethod
    def fake_fns(drop_inside_in_removed=False):
        def bin_of(w):
            k = int(round(np.angle(w[0][1]) / (2 * np.pi) * P0)) % P0
            return k
        def make(name, omit=None):
            def fn(w):
                b = bin_of(w)
                if omit and omit(b): return []
                return [(float(H.grid(P0)[b]), 20.0, b)] if H.SPAN_PREDICATES[name](float(H.grid(P0)[b])) else []
            return fn
        omit = (lambda b: b in (235929, P0 - 235929)) if drop_inside_in_removed else None
        fns = {"production": make("production")}
        for v in H.SPAN_MUTANTS: fns[v] = make(v, omit if v == "span_limit_removed" else None)
        # the removed reference must admit everywhere (the fake has no other rule)
        fns["span_limit_removed"] = (lambda w: [] if (omit and omit(bin_of(w))) else [(float(H.grid(P0)[bin_of(w)]), 20.0, bin_of(w))])
        return fns

    def setUp(self):
        self.windows = [np.ones(P0, dtype=np.complex128) for _ in range(8)]
        self.base = [(0.0, 20.0, 0)]                                         # anchor on bin 0 so the shifted tone lands exactly on the target

    def test_every_case_is_judged_per_variant_with_the_expected_credits(self):
        # bin 0 is within NEAR_EDGE of DC, but the TARGET is what matters here (the anchor's own bin is not a target)
        rec = H.run_boundary_cases(self.fake_fns(), self.windows, self.base, P0)
        self.assertEqual(rec["layer"], "B_FUNCTION_LEVEL_PRODUCTION_LENGTH"); self.assertFalse(rec["production_path"])
        by = {(c["t0"], c["sign"]): c for c in rec["cases"]}
        self.assertEqual(len(by), 8)
        for key, c in by.items():
            self.assertEqual(c["status"], "EVALUATED", (key, c.get("reason")))
            self.assertEqual(c["judgments"]["production"]["verdict"], S)
        out = by[(235930, "+")]["judgments"]
        self.assertEqual(out["span_limit_removed"]["credit"], "MUTANT_DETECTED"); self.assertEqual(out["span_limit_widened"]["credit"], "MUTANT_DETECTED")
        self.assertEqual(out["span_limit_strict"]["credit"], "MUTANT_SURVIVED_EQUIVALENT_ON_GRID")
        inside = by[(235929, "-")]["judgments"]
        for v in H.SPAN_MUTANTS:
            self.assertIn(inside[v]["credit"], ("MUTANT_SURVIVED_EQUIVALENT_ON_GRID", "MUTANT_SURVIVED"))   # nothing distinguishes them inside
        guard = by[(249037, "+")]["judgments"]
        self.assertEqual(guard["span_limit_removed"]["credit"], "MUTANT_DETECTED")
        self.assertEqual(guard["span_limit_widened"]["credit"], "MUTANT_SURVIVED_EQUIVALENT_ON_GRID")

    def test_when_the_paired_inside_control_fails_the_edge_cases_receive_no_credit(self):
        rec = H.run_boundary_cases(self.fake_fns(drop_inside_in_removed=True), self.windows, self.base, P0)
        edge = [c for c in rec["cases"] if c["t0"] in (235929, 235930, 235931)]
        self.assertTrue(edge)
        for c in edge:
            self.assertEqual(c["status"], P); self.assertIn("PAIR_CONTROL_FAILED", c["reason"])
        guard = [c for c in rec["cases"] if c["t0"] == 249037]
        for c in guard: self.assertEqual(c["status"], "EVALUATED")           # the guard-band control stands alone

    def test_layers_are_reported_separately_and_never_as_production_path(self):
        self.assertFalse(H.layer_a()["detector_involved"])
        fns = {"production": H.prod}
        txt = H.boundary_matrix([{"layer": "B_PRIME_FUNCTION_LEVEL_NON_PRODUCTION_LENGTH", "production_path": False, "cases": []},
                                 {"layer": "B_FUNCTION_LEVEL_PRODUCTION_LENGTH", "n": P0, "production_path": False, "cases": []}])
        self.assertIn("B_PRIME_FUNCTION_LEVEL_NON_PRODUCTION_LENGTH", txt); self.assertIn("B_FUNCTION_LEVEL_PRODUCTION_LENGTH", txt)
        self.assertEqual(txt.count("production_path=False"), 2)
        self.assertEqual(H.BPRIME_PLAN[0][0], 500000)                         # the required exact geometry is the function-level one
        self.assertNotEqual(H.BPRIME_PLAN[0][0], H.PROD_N)


if __name__ == "__main__":
    unittest.main()
