"""Energy accounting for a sparse decomposition, against the real estimator."""

import inspect
import json
import unittest

import numpy as np

import rf_sparse_accounting as accounting_module
import rf_sparse_analyzer as analyzer
from rf_sparse_accounting import (
    EXCLUDED_FROM_THIS_LEDGER, SUPPORT_ACCOUNTING_INCONSISTENT,
    SparseEnergyLedger, accounting_status, check_decomposition,
    decomposition_contract, energy_ledger, support_refusal,
)
from scythe_invariant_ledger import (
    INVARIANTS_SATISFIED, NUMERIC_BALANCE_EXCEEDED, LedgerContractError,
)


def _correlated_basis(separation_hz=0.05, samples=128):
    """Two nearly parallel atoms: the double-counting setup."""
    t = np.linspace(0.0, 1.0, samples)
    first = np.cos(2 * np.pi * 3.0 * t)
    second = np.cos(2 * np.pi * (3.0 + separation_hz) * t)
    return np.column_stack([first / np.linalg.norm(first),
                            second / np.linalg.norm(second)])


def _real_decomposition(seed=7, samples=64):
    """Driven through the estimator's own dictionary and OMP."""
    rng = np.random.default_rng(seed)
    dt = 0.05
    t = np.arange(samples)
    y = (np.cos(2 * np.pi * 0.6 * t * dt) + 0.4 * np.cos(2 * np.pi * 0.63 * t * dt)
         + 0.05 * rng.standard_normal(samples))
    y = y - np.median(y)
    dictionary, _meta = analyzer._build_dictionary(y, dt)
    selected, coefficients, _leftover = analyzer._omp(y, dictionary, 3)
    return y, dictionary[:, selected], coefficients[selected]


class IdentityTests(unittest.TestCase):
    """Both balances are algebraic identities, so their slack is numerical."""

    def test_the_projection_identity_holds_on_the_real_estimator(self):
        ledger = energy_ledger(*_real_decomposition())
        gap = abs(ledger.energy_observed
                  - ledger.energy_explained - ledger.energy_residual)
        self.assertLess(gap, ledger.numerical_tolerance())
        self.assertEqual(check_decomposition(ledger).verdict, INVARIANTS_SATISFIED)

    def test_the_gram_identity_holds_on_the_real_estimator(self):
        ledger = energy_ledger(*_real_decomposition())
        gap = abs(ledger.energy_explained - ledger.coefficient_energy
                  - ledger.gram_offdiagonal_energy)
        self.assertLess(gap, ledger.numerical_tolerance())

    def test_several_seeds_all_balance(self):
        for seed in range(1, 8):
            with self.subTest(seed=seed):
                ledger = energy_ledger(*_real_decomposition(seed=seed))
                self.assertEqual(check_decomposition(ledger).verdict,
                                 INVARIANTS_SATISFIED)


class CoefficientEnergyIsNotExplainedEnergyTests(unittest.TestCase):
    """The distinction the whole ledger exists to make visible."""

    def test_correlated_atoms_put_a_third_of_the_energy_in_the_cross_term(self):
        basis = _correlated_basis()
        y = 1.0 * basis[:, 0] + 0.2 * basis[:, 1]
        coefficients, *_ = np.linalg.lstsq(basis, y, rcond=None)
        ledger = energy_ledger(y, basis, coefficients)
        self.assertGreater(float(basis[:, 0] @ basis[:, 1]), 0.9,
                           "the atoms really are nearly parallel")
        self.assertGreater(ledger.gram_offdiagonal_energy,
                           0.2 * ledger.energy_explained,
                           "the cross term is a large share, not a rounding")
        self.assertNotAlmostEqual(ledger.coefficient_energy,
                                  ledger.energy_explained, places=3)
        self.assertEqual(check_decomposition(ledger).verdict, INVARIANTS_SATISFIED)

    def test_orthonormal_atoms_collapse_the_cross_term_to_zero(self):
        basis = np.eye(4)[:, :2]
        y = np.array([3.0, 4.0, 0.0, 0.0])
        coefficients, *_ = np.linalg.lstsq(basis, y, rcond=None)
        ledger = energy_ledger(y, basis, coefficients)
        self.assertAlmostEqual(ledger.gram_offdiagonal_energy, 0.0, places=12)
        self.assertAlmostEqual(ledger.coefficient_energy, ledger.energy_explained,
                               places=12)

    def test_per_support_shares_sum_to_the_explained_energy(self):
        """No atom claims a cross term alone, and none is left unattributed."""
        basis = _correlated_basis()
        y = 1.0 * basis[:, 0] + 0.2 * basis[:, 1]
        coefficients, *_ = np.linalg.lstsq(basis, y, rcond=None)
        ledger = energy_ledger(y, basis, coefficients)
        self.assertAlmostEqual(sum(ledger.per_support_energy),
                               ledger.energy_explained, places=9)


class ToleranceTests(unittest.TestCase):
    """Derived from the transform, never fitted to what passes."""

    def test_the_tolerance_is_declared_as_derived(self):
        status = accounting_status()
        self.assertFalse(status["tolerance_is_fitted_to_data"])
        self.assertEqual(status["tolerance_authority"],
                         "DERIVED_FROM_EPS_CONDITION_AND_TERM_COUNT")
        self.assertIn("DECORATIVE", status["tolerance_note"])

    def test_a_worse_conditioned_basis_earns_a_wider_tolerance(self):
        wide = energy_ledger(*_pair(_correlated_basis(separation_hz=0.01)))
        narrow = energy_ledger(*_pair(_correlated_basis(separation_hz=1.5)))
        self.assertGreater(wide.condition_number, narrow.condition_number)
        self.assertGreater(wide.numerical_tolerance(), narrow.numerical_tolerance())

    def test_the_tolerance_scales_with_the_energy_it_bounds(self):
        y, basis, coefficients = _real_decomposition()
        small = energy_ledger(y, basis, coefficients)
        large = energy_ledger(y * 10.0, basis, coefficients * 10.0)
        self.assertGreater(large.numerical_tolerance(), small.numerical_tolerance())

    def test_the_tolerance_is_far_below_any_physical_effect(self):
        """It bounds float64 arithmetic, not signal processing."""
        ledger = energy_ledger(*_real_decomposition())
        self.assertLess(ledger.numerical_tolerance(),
                        1e-6 * ledger.energy_observed)


class ViolationTests(unittest.TestCase):
    """A ledger that does not account for itself is refused."""

    def _tampered(self, **overrides):
        ledger = energy_ledger(*_real_decomposition())
        fields = {**ledger.__dict__, **overrides}
        return SparseEnergyLedger(**fields)

    def test_a_support_claiming_unexplained_energy_is_caught(self):
        verdict = check_decomposition(self._tampered(coefficient_energy=99.0))
        self.assertEqual(verdict.verdict, NUMERIC_BALANCE_EXCEEDED)
        self.assertEqual(verdict.findings[0].field, "energy_explained")

    def test_a_residual_that_does_not_close_the_projection_is_caught(self):
        verdict = check_decomposition(self._tampered(energy_residual=0.0))
        self.assertEqual(verdict.verdict, NUMERIC_BALANCE_EXCEEDED)
        self.assertEqual(verdict.findings[0].field, "energy_observed")

    def test_the_refusal_blocks_promotion_and_leaves_the_product_alone(self):
        refusal = support_refusal(check_decomposition(self._tampered(energy_residual=0.0)))
        self.assertEqual(refusal["refusal"], SUPPORT_ACCOUNTING_INCONSISTENT)
        self.assertTrue(refusal["blocks_classification"])
        self.assertTrue(refusal["blocks_promotion"])
        self.assertFalse(refusal["invalidates_fft_product"])
        self.assertIn("NOT A FAULT IN THE MEASUREMENT", refusal["scope"])

    def test_a_satisfied_ledger_produces_no_refusal(self):
        self.assertIsNone(support_refusal(
            check_decomposition(energy_ledger(*_real_decomposition()))))


class EmptyDecompositionTests(unittest.TestCase):
    """A quiet window needs no fictional carrier to make the ledger close."""

    def test_an_empty_support_set_is_valid(self):
        y = np.array([0.4, -0.2, 0.1, -0.3])
        ledger = energy_ledger(y, np.zeros((4, 0)), np.zeros(0))
        self.assertEqual(ledger.term_count, 0)
        self.assertAlmostEqual(ledger.energy_explained, 0.0)
        self.assertAlmostEqual(ledger.energy_residual, float(y @ y))
        self.assertEqual(check_decomposition(ledger).verdict, INVARIANTS_SATISFIED)
        self.assertTrue(accounting_status()["empty_decomposition_is_valid"])


class ScopeTests(unittest.TestCase):
    """What this ledger spans, and what it deliberately does not."""

    def test_the_channelizer_effects_are_named_as_excluded(self):
        for effect in ("WINDOW_COHERENT_GAIN", "EQUIVALENT_NOISE_BANDWIDTH",
                       "DECIMATION", "DISCARDED_FIR_TRANSIENTS", "DC_EXCLUSION",
                       "CLIPPED_SAMPLES"):
            self.assertIn(effect, EXCLUDED_FROM_THIS_LEDGER)

    def test_both_balances_publish_an_accounting_basis(self):
        contract = decomposition_contract(energy_ledger(*_real_decomposition()))
        self.assertEqual(len(contract.balances), 2)
        for balance in contract.balances:
            self.assertTrue(balance.accounting_basis, balance.name)

    def test_a_balance_without_a_basis_still_cannot_be_built(self):
        from scythe_invariant_ledger import BoundedBalance
        with self.assertRaises(LedgerContractError):
            BoundedBalance(name="x", total_field="a", component_fields=("b",),
                           tolerance=1.0, accounting_basis=())

    def test_the_module_mutates_nothing(self):
        with open(inspect.getsourcefile(accounting_module), encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("writebus", "GraphOp", "h3", "sqlite", "flask",
                          "socket", "subprocess", "os.environ"):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertFalse(accounting_status()["mutates"])

    def test_payloads_are_serialisable(self):
        json.dumps(accounting_status())
        json.dumps(energy_ledger(*_real_decomposition()).as_dict())

    def test_a_mismatched_basis_and_coefficient_count_is_refused(self):
        with self.assertRaises(ValueError):
            energy_ledger(np.zeros(4), np.zeros((4, 2)), np.zeros(3))


def _pair(basis):
    y = 1.0 * basis[:, 0] + 0.2 * basis[:, 1]
    coefficients, *_ = np.linalg.lstsq(basis, y, rcond=None)
    return y, basis, coefficients


if __name__ == "__main__":
    unittest.main()
