"""Energy accounting for one sparse decomposition. Bounded, and derived.

The estimator fits a slow-time vector ``y`` with a small basis chosen from a
non-orthogonal dictionary, by least squares. That gives two exact identities and
one thing they are commonly confused with:

    ||y||^2      = ||y_hat||^2 + ||r||^2          orthogonal projection
    ||y_hat||^2  = c'Gc = sum(c^2) + gram_offdiag  G = basis' basis

``sum(c^2)`` is **not** explained energy. It equals it only when the selected
atoms are orthonormal, and this dictionary's atoms are normalised cosines at
different rates over a short window -- close to orthogonal, not orthogonal. The
difference is exactly the off-diagonal Gram energy, and publishing it as a
coordinate is what stops two supports each claiming energy the pair explains
once.

Where the tolerance comes from
------------------------------
Not from observed residuals. A tolerance fitted to data that passes today makes
the balance decorative: it would widen until nothing failed. Both balances here
are algebraic identities, so their only slack is numerical, and the tolerance is
derived from float64 machine epsilon, the basis condition number and the term
count. A badly conditioned basis legitimately earns a wider tolerance, and the
published accounting basis says so rather than hiding it in a constant.

What is deliberately NOT modelled
---------------------------------
This is the slow-time decomposition, not the channelizer. Window coherent gain,
equivalent noise bandwidth, decimation, discarded FIR transients, DC exclusion
and clipping all transform power upstream of here, intentionally, and an exact
identity across them would be false by construction. They are named in the
accounting basis as *excluded*, so a reader knows this ledger does not span
them.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from scythe_invariant_ledger import (
    BoundedBalance, Coordinate, InvariantVerdict, TransitionContract,
    check_transition, signature,
)


SCHEMA = "scythe.rf-sparse-accounting.v1"

# The domain refusal. It prevents classification and promotion of the affected
# supports; it does not touch the FFT product, which was measured and remains
# whatever it was.
SUPPORT_ACCOUNTING_INCONSISTENT = "SUPPORT_ACCOUNTING_INCONSISTENT"
REFUSAL_SCOPE = (
    "PREVENTS CLASSIFICATION AND PROMOTION OF THESE SUPPORTS. THE UNDERLYING "
    "FFT PRODUCT IS UNAFFECTED: IT WAS MEASURED, AND AN ACCOUNTING FAULT IN A "
    "DECOMPOSITION LAID OVER IT IS NOT A FAULT IN THE MEASUREMENT"
)

# What each balance accounts for, and what it does not.
PROJECTION_BASIS: Tuple[str, ...] = (
    "LEAST_SQUARES_ORTHOGONAL_PROJECTION",
    "FLOAT64_MACHINE_EPSILON",
    "BASIS_CONDITION_NUMBER",
    "TERM_COUNT_ACCUMULATION",
)
GRAM_BASIS: Tuple[str, ...] = (
    "NON_ORTHOGONAL_DICTIONARY_ATOMS",
    "OFF_DIAGONAL_GRAM_ENERGY_PUBLISHED_AS_A_COORDINATE",
    "FLOAT64_MACHINE_EPSILON",
    "BASIS_CONDITION_NUMBER",
)
EXCLUDED_FROM_THIS_LEDGER: Tuple[str, ...] = (
    "WINDOW_COHERENT_GAIN",
    "EQUIVALENT_NOISE_BANDWIDTH",
    "DECIMATION",
    "DISCARDED_FIR_TRANSIENTS",
    "DC_EXCLUSION",
    "CLIPPED_SAMPLES",
    "BINS_OUTSIDE_THE_RETAINED_PRODUCT",
)

# Accumulation allowance across the terms of a dot product. Small, declared, and
# not tuned: it exists because n additions of eps-sized error accumulate, and 16
# is a conventional slack rather than a number chosen to make a case pass.
ACCUMULATION_FACTOR = 16.0
# Below this, energies are indistinguishable from zero at float64 and a relative
# tolerance is meaningless.
ENERGY_FLOOR = 1e-18


@dataclass(frozen=True)
class SparseEnergyLedger:
    """One decomposition's energy, as coordinates rather than as a summary."""

    energy_observed: float
    energy_explained: float
    energy_residual: float
    coefficient_energy: float
    gram_offdiagonal_energy: float
    condition_number: float
    term_count: int
    per_support_energy: Tuple[float, ...]

    def numerical_tolerance(self) -> float:
        """Derived, not chosen. Wider for a worse-conditioned basis, and it says so."""
        eps = float(np.finfo(np.float64).eps)
        scale = max(self.energy_observed, ENERGY_FLOOR)
        conditioning = min(self.condition_number, 1e8) ** 2
        return eps * conditioning * scale * ACCUMULATION_FACTOR * max(self.term_count, 1)

    def as_signature(self) -> Dict[str, Coordinate]:
        return signature(
            energy_observed=self.energy_observed,
            energy_explained=self.energy_explained,
            energy_residual=self.energy_residual,
            coefficient_energy=self.coefficient_energy,
            gram_offdiagonal_energy=self.gram_offdiagonal_energy,
            condition_number=self.condition_number,
            term_count=self.term_count,
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "energy_observed": self.energy_observed,
            "energy_explained": self.energy_explained,
            "energy_residual": self.energy_residual,
            "coefficient_energy": self.coefficient_energy,
            "gram_offdiagonal_energy": self.gram_offdiagonal_energy,
            "condition_number": self.condition_number,
            "term_count": self.term_count,
            "per_support_energy": list(self.per_support_energy),
            "numerical_tolerance": self.numerical_tolerance(),
            "tolerance_authority": "DERIVED_FROM_EPS_CONDITION_AND_TERM_COUNT",
            "coefficient_energy_note": (
                "sum(c^2) IS NOT EXPLAINED ENERGY UNLESS THE SELECTED ATOMS ARE "
                "ORTHONORMAL. THE DIFFERENCE IS THE OFF-DIAGONAL GRAM ENERGY "
                "ABOVE, WHICH IS WHY IT IS A COORDINATE AND NOT A ROUNDING"),
            "excluded_from_this_ledger": list(EXCLUDED_FROM_THIS_LEDGER),
        }


def energy_ledger(y: Sequence[float], basis: np.ndarray,
                  coefficients: Sequence[float]) -> SparseEnergyLedger:
    """Build the ledger from a completed decomposition. Pure; no fitting here."""
    y = np.asarray(y, dtype=np.float64)
    basis = np.asarray(basis, dtype=np.float64)
    c = np.asarray(coefficients, dtype=np.float64)
    if basis.ndim != 2 or basis.shape[1] != c.size:
        raise ValueError("basis columns and coefficients must correspond")
    fitted = basis @ c
    residual = y - fitted
    gram = basis.T @ basis
    coefficient_energy = float(c @ c)
    explained = float(c @ gram @ c)
    offdiagonal = explained - coefficient_energy
    condition = (float(np.linalg.cond(basis)) if c.size and basis.size
                 else 1.0)
    if not math.isfinite(condition):
        condition = 1e8
    # Each atom's share, including its half of every cross term it participates
    # in. Splitting the off-diagonal evenly is the only attribution that sums to
    # the explained energy without any atom claiming a cross term alone.
    per_support: Tuple[float, ...] = ()
    if c.size:
        shares = c * (gram @ c)
        per_support = tuple(float(value) for value in shares)
    return SparseEnergyLedger(
        energy_observed=float(y @ y),
        energy_explained=explained,
        energy_residual=float(residual @ residual),
        coefficient_energy=coefficient_energy,
        gram_offdiagonal_energy=offdiagonal,
        condition_number=condition,
        term_count=int(c.size),
        per_support_energy=per_support,
    )


def decomposition_contract(ledger: SparseEnergyLedger) -> TransitionContract:
    """The contract for one decomposition, with its own derived tolerance."""
    tolerance = ledger.numerical_tolerance()
    return TransitionContract(
        name="DECOMPOSE_SLOW_TIME",
        may_change=("energy_observed", "energy_explained", "energy_residual",
                    "coefficient_energy", "gram_offdiagonal_energy",
                    "condition_number", "term_count"),
        balances=(
            BoundedBalance(
                name="orthogonal_projection",
                total_field="energy_observed",
                component_fields=("energy_explained", "energy_residual"),
                tolerance=tolerance,
                accounting_basis=PROJECTION_BASIS),
            BoundedBalance(
                name="gram_attribution",
                total_field="energy_explained",
                component_fields=("coefficient_energy", "gram_offdiagonal_energy"),
                tolerance=tolerance,
                accounting_basis=GRAM_BASIS),
        ),
    )


def check_decomposition(ledger: SparseEnergyLedger) -> InvariantVerdict:
    """Whether this decomposition's energy accounts for itself. Pure."""
    return check_transition({}, "DECOMPOSE_SLOW_TIME", ledger.as_signature(),
                            decomposition_contract(ledger))


def support_refusal(verdict: InvariantVerdict) -> Optional[Dict[str, Any]]:
    """The domain refusal, or None. Never touches the spectrum product."""
    if verdict.satisfied:
        return None
    return {
        "refusal": SUPPORT_ACCOUNTING_INCONSISTENT,
        "scope": REFUSAL_SCOPE,
        "verdict": verdict.verdict,
        "fields": [finding.field for finding in verdict.findings],
        "blocks_classification": True,
        "blocks_promotion": True,
        "invalidates_fft_product": False,
    }


def accounting_status() -> Dict[str, Any]:
    """The ledger's span, published rather than assumed."""
    return {
        "schema": SCHEMA,
        "balances": ("orthogonal_projection", "gram_attribution"),
        "projection_basis": list(PROJECTION_BASIS),
        "gram_basis": list(GRAM_BASIS),
        "excluded_from_this_ledger": list(EXCLUDED_FROM_THIS_LEDGER),
        "tolerance_authority": "DERIVED_FROM_EPS_CONDITION_AND_TERM_COUNT",
        "tolerance_is_fitted_to_data": False,
        "tolerance_note": (
            "BOTH BALANCES ARE ALGEBRAIC IDENTITIES, SO THEIR ONLY SLACK IS "
            "NUMERICAL. A TOLERANCE FITTED TO RESIDUALS THAT PASS TODAY WOULD "
            "WIDEN UNTIL NOTHING FAILED, WHICH IS A DECORATIVE BALANCE"),
        "refusal": SUPPORT_ACCOUNTING_INCONSISTENT,
        "refusal_scope": REFUSAL_SCOPE,
        "empty_decomposition_is_valid": True,
        "empty_note": (
            "A NOISE-COMPATIBLE WINDOW EXPLAINS NOTHING AND BALANCES EXACTLY. "
            "NO FICTIONAL CARRIER IS NEEDED TO MAKE THE LEDGER CLOSE"),
        "mutates": False,
        "side_effects": "NONE",
    }
