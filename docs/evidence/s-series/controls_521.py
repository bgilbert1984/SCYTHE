"""§5.21's catalogue-analysis controls: the slope, the gate, and the two rows.

Group `S`. `PENDING_AMENDMENTS` entries 15 and 10 drained at #118 on the
strength of an analysis and a check, and a drain is worth exactly the tests
that would fail if either were quietly undone. Each control here undoes one
thing -- the tolerance, the family bound, the three-retune minimum, the
declared-delta requirement, the folding guard, the residual record, one row of
the classification table, one half of the span check, the reconstruction
authority list, one term of the fit -- and each must be killed by a test that
already stands in the suite. A control no test catches is a clause with no
control, which is the gap the analysis was written to close.

Mirrors `controls_3d` in shape: `CONTROLS` is a list of
`(id-and-prose, [(file, before, after), ...])`, applied to the tree before the
suite runs. `WITNESS` names, per control, the test whose failure is the kill --
read by `verify_s_series.py`, which proves each control without the full sweep.
"""

import collections

E = "rf_promotion_envelope.py"
R = "rf_corpus_reconstruction.py"

ACCEPTED_IN_SCOPE = ()
ACCEPTED_DEFERRED = {}

EARNED_IDS = frozenset("S%d" % n for n in range(1, 22))

# S1 is declared broad BEFORE launch, with the witness that must keep firing.
# A tolerance that is never applied matches every slope to its nearest
# integer, so the fixture catalogue's unresolved products stop being
# unresolved, every `CataloguedSpur` built from them refuses, and every plan
# built on that catalogue refuses with it. That is the mutation's real reach,
# not an artefact; the direct witness is the boundary test.
BROAD_DECLARED = {
    "S1": {
        "reason": "the tolerance is applied nowhere, so every measured slope "
                  "matches its nearest integer; the fixture's unresolved "
                  "products become matched, their entries refuse, and every "
                  "plan built on the catalogue refuses with them",
        "witness": "test_a_neighbouring_integer_is_outside_the_tolerance",
    },
}
SUBSUMED = {}
INTRODUCES = {}

# Per control, the test whose failure proves the property holds. Method names
# are unique across the suite, which is what `verify_s_series.py` selects on.
WITNESS = {
    "S1": ("test_rf_promotion_envelope.py",
           "test_a_neighbouring_integer_is_outside_the_tolerance"),
    "S2": ("test_rf_promotion_envelope.py",
           "test_a_neighbouring_integer_is_outside_the_tolerance"),
    "S3": ("test_rf_promotion_envelope.py",
           "test_the_family_is_bounded_by_the_slope_bound"),
    "S4": ("test_rf_promotion_envelope.py",
           "test_two_retunes_do_not_estimate_a_slope"),
    "S5": ("test_rf_promotion_envelope.py",
           "test_the_retunes_are_the_declared_ones"),
    "S6": ("test_rf_promotion_envelope.py",
           "test_a_folded_offset_is_not_an_observation"),
    "S7": ("test_rf_promotion_envelope.py",
           "test_residuals_are_recorded_beside_the_slope"),
    "S8": ("test_rf_promotion_envelope.py",
           "test_minus_one_is_a_member_and_only_the_reference_class_reads_it"),
    "S9": ("test_rf_promotion_envelope.py",
           "test_a_matched_slope_other_than_minus_one_is_not_the_reference_class"),
    "S10": ("test_rf_promotion_envelope.py",
            "test_an_unresolved_label_cannot_hide_a_matched_slope"),
    "S11": ("test_rf_promotion_envelope.py",
            "test_the_fit_and_the_persistence_name_one_tuning"),
    "S12": ("test_rf_promotion_envelope.py",
            "test_a_slope_zero_product_forbids_a_captured_thermal_window_anywhere"),
    "S13": ("test_rf_promotion_envelope.py",
            "test_the_span_check_is_the_whole_span_not_the_usable_half"),
    "S14": ("test_rf_promotion_envelope.py",
            "test_an_unmatched_slope_carries_the_tolerance_over_the_excursion"),
    "S15": ("test_rf_promotion_envelope.py",
            "test_a_matched_slope_predicts_by_its_integer_not_its_estimate"),
    "S16": ("test_rf_promotion_envelope.py",
            "test_a_spur_window_is_captured_where_a_product_is_in_span"),
    "S17": ("test_rf_promotion_envelope.py",
            "test_a_captured_thermal_window_needs_a_catalogue"),
    "S18": ("test_rf_eligible_set.py",
            "test_the_plan_rebuilds_to_the_exact_nominal_object"),
    "S19": ("test_rf_promotion_envelope.py",
            "test_the_fit_uses_every_observation"),
    "S20": ("test_rf_promotion_envelope.py",
            "test_an_integer_slope_is_measured_and_matched"),
    "S21": ("test_rf_promotion_envelope.py",
            "test_a_product_catalogued_at_an_undeclared_tuning_refuses"),
}

CONTROLS = [
 # --- the tolerance: applied, and applied at its declared width -------------
 ("S1 the tolerance is applied nowhere", [(E,
   "    if distance > PLAN_SLOPE_TOLERANCE and not math.isclose(\n",
   "    if False and not math.isclose(\n")]),
 ("S2 the tolerance is twice its declared width", [(E,
   "    if distance > PLAN_SLOPE_TOLERANCE and not math.isclose(\n",
   "    if distance > 2 * PLAN_SLOPE_TOLERANCE and not math.isclose(\n")]),
 ("S3 the family has no bound", [(E,
   "    if abs(nearest) > PLAN_MAX_MIXING_SLOPE:\n",
   "    if False:\n")]),

 # --- the observations: three declared retunes, signed, inside the guard ----
 ("S4 two retunes estimate a slope", [(E,
   "        if len(set(deltas)) < 3:\n",
   "        if len(set(deltas)) < 2:\n")]),
 ("S5 an undeclared retune is an observation", [(E,
   "        if undeclared:\n",
   "        if False:\n")]),
 ("S6 a folded offset is an observation", [(E,
   "            if abs(value) > usable_half_span_hz():\n",
   "            if False:\n")]),

 # --- the record: residuals beside the slope --------------------------------
 ("S7 the residuals are not recorded", [(E,
   '            "residuals_hz": [float(r) for r in self.residuals_hz],\n',
   '            "residuals_hz": [],\n')]),

 # --- the gate: one row of §5.21's table at a time --------------------------
 ("S8 mixing accepts slope minus one", [(E,
   "            CONSISTENT_WITH_INTERNAL_MIXING: matched is not None and matched != -1,\n",
   "            CONSISTENT_WITH_INTERNAL_MIXING: matched is not None,\n")]),
 ("S9 the reference class accepts any matched slope", [(E,
   "            CONSISTENT_WITH_INTERNAL_REFERENCE: matched == -1,\n",
   "            CONSISTENT_WITH_INTERNAL_REFERENCE: matched is not None,\n")]),
 ("S10 an unresolved label hides a matched slope", [(E,
   "            SPUR_CANDIDATE_UNRESOLVED: matched is None,\n",
   "            SPUR_CANDIDATE_UNRESOLVED: True,\n")]),
 ("S11 the fit and the persistence may name two tunings", [(E,
   "        if self.slope.tuning_id != self.persistence.tuning_id:\n",
   "        if False:\n")]),

 # --- entry 10: the thermal row, the spur row, and the span they share ------
 ("S12 a captured thermal window is not checked against the catalogue", [(E,
   "                if present:\n",
   "                if False:\n")]),
 ("S13 the span check uses the usable half-span", [(E,
   "        return abs(self.predicted_baseband_hz(lo_offset_hz)) - margin <= span_hz / 2.0\n",
   "        return abs(self.predicted_baseband_hz(lo_offset_hz)) - margin <= usable_half_span_hz()\n")]),
 ("S14 an unmatched slope carries no uncertainty over the excursion", [(E,
   "        margin = (0.0 if self.matched_slope is not None\n"
   "                  else PLAN_SLOPE_TOLERANCE * abs(lo_offset_hz))\n",
   "        margin = 0.0\n")]),
 ("S15 a matched slope predicts by its estimate", [(E,
   "        model = self.measured_slope if slope is None else float(slope)\n",
   "        model = self.measured_slope\n")]),
 ("S16 a spur window is not checked against the eligible units", [(E,
   "                if tuning.tuning_id not in observable:\n",
   "                if False:\n")]),
 ("S17 a captured thermal window needs no catalogue", [(E,
   "            if self.spur_allocation is None:\n"
   "                raise EnvelopeRefused(\n"
   "                    PLAN_SPUR_CATALOGUE_ABSENT,\n"
   '                    "THERMAL_NO_INPUT is captured and no catalogue says where "\n',
   "            if False:\n"
   "                raise EnvelopeRefused(\n"
   "                    PLAN_SPUR_CATALOGUE_ABSENT,\n"
   '                    "THERMAL_NO_INPUT is captured and no catalogue says where "\n')]),

 # --- reconstruction: a derived key is refused unless an authority lists it -
 ("S18 the residual record is a field no authority declares", [(R,
   '    "SpurSlopeEstimate": ("measured_slope", "intercept_hz", "residuals_hz",\n'
   '                          "matched_slope", "slope_tolerance"),\n',
   '    "SpurSlopeEstimate": ("measured_slope", "intercept_hz",\n'
   '                          "matched_slope", "slope_tolerance"),\n')]),

 # --- the fit itself --------------------------------------------------------
 ("S19 the fit drops an observation", [(E,
   "        deltas, offsets = self.retune_delta_hz, self.signed_baseband_hz\n",
   "        deltas, offsets = self.retune_delta_hz[:-1], self.signed_baseband_hz[:-1]\n")]),
 ("S20 the intercept is the mean offset", [(E,
   "        return slope, mean_offset - slope * mean_delta\n",
   "        return slope, mean_offset\n")]),

 # --- the catalogue's anchor: a tuning the plan does not declare ------------
 ("S21 a product at an undeclared tuning is placed anyway", [(E,
   "        if anchor is None:\n",
   "        if False:\n")]),
]


def check_inventory(accepted_ids=()):
    problems = []
    ids = [c[0].split()[0] for c in CONTROLS]
    counts = collections.Counter(ids)
    for cid, n in sorted(counts.items()):
        if n > 1:
            problems.append(f"{cid} appears {n} times")
    missing = sorted(EARNED_IDS - set(ids), key=lambda s: int(s[1:]))
    extra = sorted(set(ids) - EARNED_IDS, key=lambda s: int(s[1:]))
    if missing:
        problems.append(f"declared but absent: {missing}")
    if extra:
        problems.append(f"present but undeclared: {extra}")
    for cid in ids:
        if cid not in WITNESS:
            problems.append(f"{cid} names no witness test")
    for cid, entry in BROAD_DECLARED.items():
        if cid not in ids:
            problems.append(f"{cid} is declared broad and has no control")
        elif WITNESS.get(cid, (None, None))[1] != entry["witness"]:
            problems.append(f"{cid}'s declared broad witness is not its WITNESS")
    return problems


try:                                                # pragma: no cover
    from controls527 import check_mutations         # noqa: E402  (shared checker)
except ImportError:                                 # standalone (verify script)
    def check_mutations(*_args, **_kwargs):
        return []
