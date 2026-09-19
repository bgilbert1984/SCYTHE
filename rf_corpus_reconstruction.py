"""Turning persisted declarations back into the exact nominal objects. §5.27.

§5.26 read a manifest and held its **mapping**. Admission consuming a mapping
is the caller-supplied set §5.25 refused, wearing a different shape -- so the
scope factory reconstructs the exact nominal `InstrumentChainEnvelope`,
`CapturePlanDeclaration` and `PromotionCorpusLock` **once**, validates them, and
binds them in opaque state. Nothing reconstructs opportunistically inside an
entrypoint: the same fragile step in several places is several places for it to
drift.

Two halves, and they are not the same kind of thing:

    selected_trials   DERIVED. Regenerated from the eligible set, the seed and
                      the revision -- then checked, because derived does not
                      mean unbound.
    eligible_trials   OBSERVED. Read from the sidecar -- then checked, because
                      persisted does not mean authoritative by itself.

The last two steps are what make the rest safe. A reconstructed plan must
re-serialise to the stored mapping **byte-for-byte** and digest to the stored
`capture_plan_digest`. Every field-by-field decision below is therefore checked
by construction rather than by my having reasoned about it correctly.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence, Tuple

from rf_promotion_envelope import (
    Band, CapturePlanDeclaration, CataloguedSpur, ChainMember, EligibleSpurTrial,
    FrontEnd, InstrumentChainEnvelope, SpurAllocation, SpurPersistenceObservation,
    StratumTrialPlan, Tuning, Visit, declaration_digest, select_spur_trials,
)

RECONSTRUCTION_FIELD_NO_AUTHORITY = "RECONSTRUCTION_FIELD_NO_AUTHORITY"
RECONSTRUCTION_MAPPING_DISAGREES = "RECONSTRUCTION_MAPPING_DISAGREES"
RECONSTRUCTION_DIGEST_DISAGREES = "RECONSTRUCTION_DIGEST_DISAGREES"
RECONSTRUCTION_SELECTION_DISAGREES = "RECONSTRUCTION_SELECTION_DISAGREES"
RECONSTRUCTION_REFUSALS: Tuple[str, ...] = (
    RECONSTRUCTION_FIELD_NO_AUTHORITY, RECONSTRUCTION_MAPPING_DISAGREES,
    RECONSTRUCTION_DIGEST_DISAGREES, RECONSTRUCTION_SELECTION_DISAGREES,
)


class ReconstructionRefused(RuntimeError):
    """A declaration that does not rebuild into the object it describes."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# Keys each `to_dict()` adds that are **derived** and must not be fed back to a
# constructor. Listed per type rather than filtered by "whatever the dataclass
# does not declare", so a field that stops being derived is a refusal here
# instead of being silently dropped.
_DERIVED: Dict[str, Tuple[str, ...]] = {
    "ChainMember": ("chain_hash", "antenna", "extension_mm", "feedline",
                    "feedline_length_m"),
    "CataloguedSpur": ("required_confidence",),
    "SpurPersistenceObservation": ("qualifying", "persistent"),
    # All four are computed from the members, so the envelope is rebuilt from
    # the members alone. Listing them is what makes a NEW derived key a
    # refusal rather than something silently ignored.
    "InstrumentChainEnvelope": ("schema", "sensor_id",
                                "receiver_identity_authority", "member_count"),
}


def _declared(mapping: Mapping[str, Any], kind: str,
              fields: Sequence[str]) -> Dict[str, Any]:
    """The declared fields, refusing any key no authority accounts for."""
    allowed = set(fields) | set(_DERIVED.get(kind, ()))
    unexpected = sorted(set(mapping) - allowed)
    if unexpected:
        raise ReconstructionRefused(
            RECONSTRUCTION_FIELD_NO_AUTHORITY,
            f"{kind} carries {len(unexpected)} field(s) no authority declares: "
            f"{', '.join(unexpected)}")
    missing = sorted(set(fields) - set(mapping))
    if missing:
        raise ReconstructionRefused(
            RECONSTRUCTION_FIELD_NO_AUTHORITY,
            f"{kind} is missing {', '.join(missing)}")
    return {name: mapping[name] for name in fields}


def _tuple_of_pairs(rows: Any) -> Tuple[Tuple[Any, ...], ...]:
    return tuple(tuple(row) for row in rows)


# -- the instrument layer ---------------------------------------------------


def chain_member(mapping: Mapping[str, Any]) -> ChainMember:
    """`ChainMember.to_dict()` flattens the front end and adds the chain hash.

    The hash is derived, so it is dropped rather than fed back; the flattened
    front-end keys are re-nested. Both decisions are checked downstream by the
    byte-for-byte comparison, which is why they can be written plainly here.
    """
    declared = _declared(mapping, "ChainMember",
                         ("sensor_id", "receiver_identity_authority",
                          "sample_type", "sample_rate_hz", "gain_db"))
    front = _declared(
        {k: mapping[k] for k in ("antenna", "extension_mm", "feedline",
                                 "feedline_length_m") if k in mapping},
        "FrontEnd", ("antenna", "extension_mm", "feedline", "feedline_length_m"))
    return ChainMember(front_end=FrontEnd(**front), **declared)


def instrument_chain_envelope(mapping: Mapping[str, Any]) -> InstrumentChainEnvelope:
    declared = _declared(mapping, "InstrumentChainEnvelope", ("members",))
    return InstrumentChainEnvelope(
        members=tuple(chain_member(row) for row in declared["members"]))


# -- the capture plan -------------------------------------------------------


def _band(m: Mapping[str, Any]) -> Band:
    return Band(**_declared(m, "Band", ("band_id", "low_hz", "high_hz")))


def _tuning(m: Mapping[str, Any]) -> Tuning:
    return Tuning(**_declared(m, "Tuning",
                              ("tuning_index", "center_frequency_hz", "band_id")))


def _visit(m: Mapping[str, Any]) -> Visit:
    return Visit(**_declared(m, "Visit",
                             ("position", "tuning_index", "retune_delta_hz")))


def _trial_plan(m: Mapping[str, Any]) -> StratumTrialPlan:
    d = _declared(m, "StratumTrialPlan",
                  ("stratum", "source", "trials", "chain_hashes", "per_visit"))
    d["chain_hashes"] = tuple(d["chain_hashes"])
    d["per_visit"] = _tuple_of_pairs(d["per_visit"])
    return StratumTrialPlan(**d)


def _persistence(m: Mapping[str, Any]) -> SpurPersistenceObservation:
    d = _declared(m, "SpurPersistenceObservation",
                  ("tuning_id", "repeat_excess_db"))
    d["repeat_excess_db"] = tuple(d["repeat_excess_db"])
    return SpurPersistenceObservation(**d)


def _catalogued_spur(m: Mapping[str, Any]) -> CataloguedSpur:
    d = _declared(m, "CataloguedSpur",
                  ("spur_id", "classification", "stability_class", "persistence"))
    d["persistence"] = _persistence(d["persistence"])
    return CataloguedSpur(**d)


def eligible_trial(m: Mapping[str, Any]) -> EligibleSpurTrial:
    return EligibleSpurTrial(**_declared(
        m, "EligibleSpurTrial",
        ("spur_id", "tuning_id", "epoch_id", "chain_hash", "stability_class",
         "signed_baseband_hz", "confidence")))


def spur_allocation(mapping: Mapping[str, Any], *,
                    eligible_rows: Sequence[Mapping[str, Any]]) -> SpurAllocation:
    """The compact declaration plus the rows the sidecar carries.

    `selected_trials` is regenerated rather than read: it is derived state, and
    `SpurAllocation.__post_init__` re-derives and refuses disagreement anyway.
    The stored `selected_trials_digest` is what makes the regeneration checked
    rather than merely performed.
    """
    d = _declared(mapping, "SpurAllocation",
                  ("catalogue", "epochs", "per_stability_class",
                   "selection_seed", "selection_revision",
                   "selected_count", "eligible_trials_digest",
                   "selected_trials_digest", "cardinality_bound",
                   "catalogue_size", "distinct_trial_units",
                   "generalises_across_receiver_units",
                   "generalises_across_spur_types"))
    eligible = tuple(eligible_trial(row) for row in eligible_rows)
    selected = select_spur_trials(
        eligible=eligible, seed=d["selection_seed"],
        required=int(d["selected_count"]),
        selection_revision=d["selection_revision"])
    allocation = SpurAllocation(
        catalogue=tuple(_catalogued_spur(row) for row in d["catalogue"]),
        epochs=d["epochs"],
        per_stability_class=_tuple_of_pairs(d["per_stability_class"]),
        eligible_trials=eligible,
        selected_trials=selected,
        selection_seed=d["selection_seed"],
        selection_revision=d["selection_revision"])
    # The persisted set first, the derived selection second. A wrong eligible
    # set also produces a wrong selection, so checking the selection first
    # would report a derived symptom for a stored cause.
    if allocation.eligible_trials_digest() != d["eligible_trials_digest"]:
        raise ReconstructionRefused(
            RECONSTRUCTION_DIGEST_DISAGREES,
            "the reconstructed eligible set does not digest to the value the "
            "plan froze")
    if allocation.selected_trials_digest() != d["selected_trials_digest"]:
        raise ReconstructionRefused(
            RECONSTRUCTION_SELECTION_DISAGREES,
            "the regenerated selection does not digest to the value the plan "
            "froze; a revised selection would otherwise change the sample "
            "silently")
    return allocation


def capture_plan(mapping: Mapping[str, Any], *,
                 eligible_rows: Sequence[Mapping[str, Any]],
                 frozen_digest: str) -> CapturePlanDeclaration:
    """The exact nominal plan, or a refusal. Never a partial object."""
    declared = ("seed", "schedule_generator_revision", "bands", "tunings",
                "schedule", "trial_plans", "reference_hz", "reference_ppm")
    missing = sorted(set(declared) - set(mapping))
    if missing:
        raise ReconstructionRefused(
            RECONSTRUCTION_FIELD_NO_AUTHORITY,
            f"the capture plan is missing {', '.join(missing)}")
    allocation = None
    if mapping.get("spur_allocation") is not None:
        allocation = spur_allocation(mapping["spur_allocation"],
                                     eligible_rows=eligible_rows)
    plan = CapturePlanDeclaration(
        seed=mapping["seed"],
        schedule_generator_revision=mapping["schedule_generator_revision"],
        bands=tuple(_band(row) for row in mapping["bands"]),
        tunings=tuple(_tuning(row) for row in mapping["tunings"]),
        schedule=tuple(_visit(row) for row in mapping["schedule"]),
        trial_plans=tuple(_trial_plan(row) for row in mapping["trial_plans"]),
        spur_allocation=allocation,
        reference_hz=mapping["reference_hz"],
        reference_ppm=mapping["reference_ppm"])
    # The two checks that make every field decision above verifiable rather
    # than argued. A reconstruction that re-serialises to the stored mapping
    # and digests to the frozen value IS the object that was frozen.
    rebuilt = plan.to_dict()
    if rebuilt != dict(mapping):
        differing = sorted(
            k for k in set(rebuilt) | set(mapping)
            if rebuilt.get(k) != dict(mapping).get(k))
        raise ReconstructionRefused(
            RECONSTRUCTION_MAPPING_DISAGREES,
            f"the reconstructed plan does not re-serialise to its stored "
            f"declaration; {len(differing)} field(s) differ: "
            f"{', '.join(differing[:6])}")
    if plan.digest() != frozen_digest:
        raise ReconstructionRefused(
            RECONSTRUCTION_DIGEST_DISAGREES,
            "the reconstructed plan does not digest to capture_plan_digest")
    return plan


def envelope(mapping: Mapping[str, Any], *,
             frozen_digest: str) -> InstrumentChainEnvelope:
    rebuilt = instrument_chain_envelope(mapping)
    if rebuilt.to_dict() != dict(mapping):
        raise ReconstructionRefused(
            RECONSTRUCTION_MAPPING_DISAGREES,
            "the reconstructed envelope does not re-serialise to its stored "
            "declaration")
    if rebuilt.digest() != frozen_digest:
        raise ReconstructionRefused(
            RECONSTRUCTION_DIGEST_DISAGREES,
            "the reconstructed envelope does not digest to envelope_digest")
    return rebuilt
