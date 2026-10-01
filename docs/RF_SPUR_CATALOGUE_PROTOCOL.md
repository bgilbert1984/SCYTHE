# The Bounded Live Spur-Catalogue Run

```text
Status:         ACCEPTED as the procedure. Authorisation of live
                contact is recorded separately in
                RF_SPUR_CATALOGUE_AUTHORISATION.md (operator decision
                2026-09-28, in chat, commit dd87b05e); this file
                authorises nothing by itself.
Authority:      NORMATIVE for the procedure it describes.
Method:         §5.21 (ACCEPTED 2026-09-14) -- how a candidate is told apart
                from a received emission.
Parameters:     §5.22 (ACCEPTED 2026-09-14) -- K, R, the deltas, the
                tolerances, the order, the confidences. None is set here.
Analysis:       rf_promotion_envelope.py -- SpurSlopeEstimate, CataloguedSpur,
                ReferenceComb, SpurAllocation -- run over no receiver.
Live runner:    EXISTS. rf_spur_catalogue_runner.py (post-capture auditor;
                its THERMAL_NO_INPUT path refuses with
                RUNNER_SPUR_CATALOGUE_ABSENT until a catalogue is declared)
                and rf_spur_catalogue_sequencer.py (live driver; refuses
                without operator declarations). Both on
                feat/5.21-catalogue-runner.
Device contact: AUTHORISED IN PART. Three THERMAL_NO_INPUT pre-catalogue
                windows were captured 2026-09-30 on the declared receiver
                (NESDR SMArt v5, SN 14530058) with the declared 50 Ω load
                fitted, under operator authorisation in chat; they are
                pre-catalogue evidence, not the catalogue. The K=64/R=8
                catalogue schedule has NOT been executed and is NOT
                authorised: bands, seed, gain, reference_hz/ppm and
                justification remain operator declarations to be frozen.
```

This document makes the run §5.21 describes concrete enough to review: what
would be declared, what would be done, what would come out, and what each
part of the software would check or could not. It exists so that the
authorisation decision, when it is taken, is taken over a procedure and not
over a paragraph.

**That decision has since been taken in part.** The bounded capture is
authorised by RF_SPUR_CATALOGUE_AUTHORISATION.md (2026-09-28), under which
the three pre-catalogue THERMAL_NO_INPUT windows were captured 2026-09-30.
The K=64/R=8 catalogue schedule itself is a further separate authorisation
point and has not been taken. Merging this document, merging the analysis
it describes, and certifying the controls over that analysis remain three
things that have happened; none of them is the catalogue run.

---

## 1. What "bounded" means

The run is bounded in five ways, and a run that exceeds any of them is a
different run that this document does not describe.

| bound | value | why it is a bound |
| --- | --- | --- |
| **one receiver** | one declared sensor, one `signal_chain_manifest` per epoch | a catalogue is a property of an instrument; two units are §5.21's step 7 and a second run |
| **one termination** | a declared 50 Ω load, fitted before the first window | terminated throughout, so every product is observed with no antenna to receive through |
| **one band set** | the declared bands the plan will sample | the catalogue covers the distribution the plan samples and nothing else |
| **one artefact** | the catalogue, as JSON, under `docs/evidence/` | no stratum window, no corpus member, no namespace, no lock, no promotion |
| **one schedule** | K = 64 tunings, R = 8 repeats, three signed deltas, ≥ 3 visits per tuning, ≥ 10 apart, from a declared seed | §5.22's numbers, materialised by `generate_tunings` and `generate_visit_schedule`, so the run performs the schedule the plan will regenerate and compare byte for byte |

What the run produces is a **catalogue**: identified products, each with the
eight repeats it earned its entry with, the retune observations its slope was
fitted from, the class the analysis supports, the harmonic it claims if it
claims one, and the stability it survived to. What it does not produce is any
window the corpus will contain. `RECEIVER_SPURS` and captured
`THERMAL_NO_INPUT` are planned *against* the catalogue afterwards, under the
capture plan, under a separate authorisation again.

---

## 2. Declared before the first window

Every one of these is an `OPERATOR_DECLARED` input. The software checks that
each is present and well-formed; it cannot check that any is true.

| declaration | carried as | what the analysis does with it |
| --- | --- | --- |
| the receiver | the envelope's `ChainMember` for the terminated chain, with its `sensor_id` | every entry is this receiver's; the envelope's chain hash is what the terminated chain must produce |
| the reference and its ppm | `ReferenceComb(reference_hz, reference_ppm)` | derives the harmonic cap; matches every reference-class entry; is declared again on the plan and must agree |
| the justification for the ppm | prose in the run's provenance note | none. An uncalibrated dongle declares ±100 ppm and earns a cap of 3; a disciplined one declares what its discipline supports. A ppm chosen to reach a match is the §5.15 mistake |
| the termination | part, connector, and the act of fitting it, in the provenance note | none. The terminated chain hash is recorded; that a load is behind it is a declaration |
| the site and enclosure | prose | decides which accepted confidence the reference class can reach (§5.22: second site or shielded enclosure) |
| the bands | `Band` declarations | tunings are generated inside them, with the whole span inside the usable band |
| the seed | an integer | reproduces the tunings and the schedule; the plan regenerates both |
| the gain | one declared `gain_db` for the whole run | gain is inside the chain identity; a gain change is a different chain and a different catalogue |

The reference is **declared, never assumed**. 28.8 MHz is the common R820T2
crystal and 24 MHz exists; a run that read the crystal off a datasheet for the
model rather than off the unit would be inventing the instrument.

---

## 3. The procedure, as acts the software can see

§5.21's seven steps, with what each does to the ring and what is recorded.

1. **Terminate and record.** Fit the load. The chain rebuilds and the ring is
   invalidated with `SIGNAL_CHAIN_CHANGE`. Record the terminated chain hash;
   it is the chain every observation below is made on.
2. **Generate the schedule.** From the seed and the bands:
   `generate_tunings` for the K = 64 centres,
   `generate_visit_schedule` for the ordered visits, each with its signed
   delta. Record both. The plan later regenerates and compares them.
3. **Visit.** For each scheduled visit in order: set the LO to the tuning's
   centre plus the visit's delta (the ring invalidates with `RETUNE`), wait
   for a full refill, acquire R = 8 complete non-overlapping windows.
4. **Retain features.** Per tuning, a feature is retained if it exceeds the
   local median by the persistence margin in at least 7 of the 8 repeats at
   that visit. The eight excesses are the entry's
   `SpurPersistenceObservation`; the verdict is derived from them, not stored
   beside them.
5. **Fit slopes.** For each retained feature at each tuning, its signed
   baseband offset at every visit to that tuning is one observation:
   `(retune_delta_hz, signed_baseband_hz)`, with the tuning's centre as the
   anchor. At least three distinct deltas, every offset inside the folding
   guard, every delta one of the declared three. `SpurSlopeEstimate` fits the
   line and records the slope, the intercept, and every residual.
6. **Classify.** `CataloguedSpur` refuses any class the slope does not
   support (§4 below). A reference-class entry declares its harmonic and the
   catalogue matches it against the declared comb.
7. **Repeat across epochs.** Once more after `DISCONNECT` / `RECONNECT`, once
   more after a power cycle. A feature present in all three is
   `POWER_CYCLE_STABLE`; in the first two, `RECONNECT_STABLE`; in one,
   `SESSION_SCOPED`. Stability classifies and never excludes: the
   session-scoped products are the periodic, narrow population the stratum
   exists to test.

Step 7's second unit is **out of this run's bound**. It is a second run with
a second receiver, and its purpose is to say which products are the model's
and which are the unit's.

**Wall-clock.** The floor is arithmetic: 192 visits per epoch (64 tunings,
three visits each), each a refill and 8 windows of 256 ms, so about two
seconds of window time per visit and under ten minutes of window time per
epoch. The real cost is above it -- retune
settling, refill after every invalidation, the reconnect, the power cycle,
and the blocking §5.22 requires between epochs -- and §5.22 says the plan
states wall-clock in **days**. This run is three epochs of one receiver and
should be stated in **hours per epoch and days overall**, with the epochs
separated as the plan will separate them, not run back to back.

---

## 4. What the analysis decides, and what it refuses

Every row is enforced at construction. A catalogue that reaches disk has
already passed all of it; a reader who rebuilds it through
`rf_corpus_reconstruction` passes it again, because every derived value is
recomputed and compared.

| the entry claims | the analysis requires | refused with |
| --- | --- | --- |
| it persisted | 8 repeats, ≥ 7 at or above the margin | `PLAN_REPEATS_NOT_OBSERVED`, `PLAN_SPUR_NOT_PERSISTENT` |
| a slope | ≥ 3 distinct declared deltas, signed offsets inside the guard, a positive finite anchor centre | `PLAN_SLOPE_NOT_ESTIMATED`, `PLAN_RETUNE_NOT_DECLARED`, `PLAN_TRIAL_NOT_ELIGIBLE`, `PLAN_QUANTITY_NOT_FINITE` |
| `CONSISTENT_WITH_INTERNAL_MIXING` | the slope matches an integer in `[−3, 3]` other than −1, within 0.01 | `PLAN_CLASSIFICATION_NOT_SUPPORTED` |
| `CONSISTENT_WITH_INTERNAL_REFERENCE` | the slope matches −1; a harmonic `n ≥ 1` is declared; `n` is at or below the cap the ppm forces; the RF position (anchor centre + intercept) is within `n · f_ref · ppm` of `n · f_ref` | `PLAN_CLASSIFICATION_NOT_SUPPORTED`, `PLAN_REFERENCE_HARMONIC_UNDECLARED`, `PLAN_REFERENCE_ABOVE_HARMONIC_CAP`, `PLAN_REFERENCE_COMB_MISMATCH` |
| `SPUR_CANDIDATE_UNRESOLVED` | the slope matches no member of the family | `PLAN_CLASSIFICATION_NOT_SUPPORTED` |
| `VANISHES_ON_DECLARED_TERMINATION` | any slope; the load decided it | -- |
| a harmonic, on any other class | never | `PLAN_CLASSIFICATION_NOT_SUPPORTED` |
| an anchor | a tuning the plan declares, at the centre the plan declares | `PLAN_TRIAL_NOT_ELIGIBLE`, `PLAN_SPUR_ANCHOR_DISAGREES` |
| a comb | the same reference and ppm the plan declares | `PLAN_REFERENCE_DISAGREES` |

Two consequences worth stating before the run rather than discovering in it:

- **A slope-−1 feature that persists terminated and matches no harmonic is
  not catalogued.** It fits a modelled slope, so it is not
  `SPUR_CANDIDATE_UNRESOLVED`; it matches no harmonic, so it is not the
  reference class. It is, by §5.21's own table, a received emission that the
  termination did not remove -- ingress -- and the run records it in the
  provenance note as a finding about the site, not in the catalogue as a
  product.
- **At ±100 ppm there is no reference class above 86.4 MHz.** The cap is 3.
  A UHF catalogue from an uncalibrated dongle contains mixing products,
  unresolved candidates and vanishing features, and no reference match. That
  is the correct outcome and the run should expect it.

---

## 5. The artefact

One directory under `docs/evidence/spur-catalogue/<sensor_id>-<date>/`, in
the layout the sweep evidence uses:

| file | contents |
| --- | --- |
| `catalogue.json` | the `ReferenceComb` and the list of `CataloguedSpur.to_dict()` entries, one per product per epoch it was observed in, with its stability class |
| `schedule.json` | seed, bands, the generated tunings and visits, so the plan can regenerate them |
| `chain.json` | the terminated `ChainMember` and its hash, per epoch |
| `README-provenance.txt` | every declaration in §2 with its justification; the epochs' start and end times; the ingress findings; what was not done |
| `SHA256SUMS` | over all of the above |

The catalogue is read back only through `rf_corpus_reconstruction`, which
refuses any field no authority declares and recomputes every derived value --
the slope, the intercept, the residuals, the match, the RF position, the cap.
A stored value that disagrees with what its own observations produce is a
refusal, not a warning.

---

## 6. What the run cannot establish

Unchanged from §5.21, restated so the artefact is not read as more than it is.

- **That a load was fitted.** `OPERATOR_DECLARED`, the same authority class as
  a position fix.
- **That nothing got in.** Without a screened enclosure, case pickup, a poor
  termination and USB-borne coupling put a strong local emission into a
  "terminated" capture. At the pinned host's venues that is broadcast FM.
- **That a model match is a second discriminator.** It is not. The reference
  class attests only at `TERMINATION_DECLARED_AND_SECOND_SITE_OR_SHIELDED_ENCLOSURE`,
  and matching the comb does not move it.
- **That the stratum is achievable.** A perfect catalogue of S products does
  not make 5 561 trials; the plan's feasibility check does, or refuses.

---

## 7. What would have to exist before the run could be performed

Stated so that authorisation is not mistaken for readiness.

1. **A catalogue runner.** No module drives the tuner through a visit
   schedule, acquires windows, retains features or writes `catalogue.json`.
   `rf_bridge` reconnects and invalidates; `rf_iq_ring` holds the ring;
   nothing sequences a schedule. Writing it is work that follows the
   authorisation and precedes the run.
2. **Feature retention.** The persistence margin is declared; the estimator
   that measures a feature's excess over the tuning-local median is not
   written.
3. **The acceptance of this document**, with whatever review changes it.
4. **The authorisation itself**: an explicit decision, recorded with the
   commit it was taken at, naming the receiver, the site, the termination and
   the epochs. It is not implied by any of the above.

---

## 8. Review checklist

For the reviewer of this document, not for the operator of the run.

- [ ] Is every §5.22 parameter consumed by the procedure, and none set here?
- [ ] Is every declaration in §2 something the software either checks or
      records as declared, with no third state?
- [ ] Does §4 match `CataloguedSpur`, `ReferenceComb` and
      `CapturePlanDeclaration` as they are, refusal by refusal?
- [ ] Is the ingress case (slope −1, no harmonic) handled as a finding and
      not as a product?
- [ ] Are the epochs separated as §5.22's blocking requires, and is the
      wall-clock stated in days?
- [ ] Does anything here read as authorising device contact? If so, it is
      wrong and must be reworded.

---

## 9. What this document does not authorise

No capture. No tuner operation, no `rtl_tcp`, no termination, no catalogue,
no persistence, no byte, no directory. It describes a run so that a decision
about the run can be reviewed; it is not the decision, and it will not
become one by being merged.
