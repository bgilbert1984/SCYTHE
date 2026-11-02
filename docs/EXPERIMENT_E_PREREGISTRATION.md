# Experiment E: Preregistration of Stage 1, 2 and 4 decision rules (DRAFT FOR OWNER FREEZE)

Companion to `EXPERIMENT_E_BENCH_PLAN.md` and `EXPERIMENT_E_STAGE1_CHECKLIST.md`. Machine-readable rule: `scripts/experiment_e/prereg/prereg_v1.json`. Evaluator: `prereg.py`. Blinding tools: `blind.py`. Simulation: `simulate.py`. Option sets: `profiles.py`. Tests: `test_prereg.py` (36).

**Status: not frozen.** Every threshold below is a *proposal* for the owner to approve or change before the first DUT run. Freezing is the owner's act:

```
python3 scripts/experiment_e/prereg/prereg.py freeze --owner "<name>"     # writes PREREG_FREEZE.json (sha256 of the rule)
git add scripts/experiment_e/prereg/PREREG_FREEZE.json && git commit      # BEFORE the first DUT run
```

The evaluator refuses to produce a result unless the rule's SHA-256 matches the freeze record. After the freeze the rule is never edited: a change is a new `prereg_v2.json` with its own freeze, and anything run under v1 stays v1. Anything done differently from the rule is reported as a deviation.

Everything here concerns the *procedure*. No DUT data has been used to set any threshold (Stage 0A forbids that). The simulation measures the procedure's error rates; it is **simulated**, not evidence about the DUT.

## What is decided in advance

| Item | Frozen value (proposal) |
|---|---|
| Keying | f_m 7.000 Hz, 60 s dwells, probe sweep 0.2-14 MHz in 100 kHz steps (139 points) in a seeded random order, a probe-off baseline every 10 dwells |
| DDC centres | 3.0, 7.5, 12.0 MHz (passband 2.048 MHz, at least 4.5 MHz apart so the plateaus never overlap) |
| In-band set | probe within 0.8 MHz of fc. Out-of-band set: 1.6 MHz or more from fc. 0.8-1.6 MHz is a guard band in neither set |
| A-priori exclusion | probes whose `alias_table.py` verdict (for the *measured* filter) is CONTAMINATED or MARGINAL |
| Lock-in power | `p = |A|^2 - sigma2`, sigma2 from 20 off-frequency bins (not mains, not harmonics of f_m) |
| Equal power | every dwell within 1.0 dB of the sweep median at the DUT end, else invalid |
| Selectivity threshold | 10 dB |
| Error control | Holm at family alpha 0.01 over {C1, C2, C5}, family size fixed at 3 |

## Confirmatory tests

- **C1 selectivity.** Per fc, `T = mean_p(IN) - 10 * mean_p(OUT)`; one-sided percentile bootstrap (20,000 resamples of dwells within each set) that `T > 0`. Intersection-union over the three fc (p = the largest).
- **C2 movement with fc.** (a) For each fc, a permutation test (5,000 shuffles of corrected power across probe frequencies) on the best match of a *matched boxcar* of the DDC width; p = the largest over fc. (b) For every pair of fc, the estimated centre shift must equal the commanded shift within 0.25 MHz.
- **C5 blind estimation (Stage 4).** At least 20 trials with a secret fc in 2-12 MHz, coarse sweeps (250 kHz steps, 30 s dwells). Permutation test (100,000 shuffles) on mean absolute error, **and** MAE <= 0.5 MHz, **and** at least 80% of trials within 0.512 MHz (half a passband).

Why the centre estimator is a matched boxcar and not the C1 sets: the guard-banded sets give a statistic that is exactly flat for any centre within about 0.22 MHz of the truth, so the argmax is arbitrary there and two centres could disagree by more than the tolerance. This was found in simulation and fixed before freezing.

## Gates (must hold before any claim about the DUT)

- **Detection.** At least one fc has a bootstrap-significant in-band response (Bonferroni over the three fc).
- **C3 controls.** Each Stage 2 control is **PASS** if the upper 99% bound of its corrected power is below 1% (-20 dB) of the matching in-band signal, **FAIL** if the lower bound is at or above it, otherwise **UNRESOLVED**. Unresolved never counts as a pass.
- **C4 sham gate** (only where a response was detected). The sham f_m (11 Hz) response must be clearly present and the 7 Hz response clearly below 1% of it.

## Outcome table (evaluated in this order)

| Verdict | Condition | What it supports |
|---|---|---|
| INCONCLUSIVE_PROTOCOL | recorded order does not match the preregistered schedule | nothing |
| INCONCLUSIVE_INCOMPLETE | more than 10% of alias-eligible dwells excluded at any fc, fewer than 8 in-band or 30 out-of-band valid dwells, or fewer than 3 fc | nothing |
| CONTROLS_FAIL | any control FAIL | H0c: coupling or contamination. No inference about the DUT |
| INCONCLUSIVE_CONTROLS | no failure, but a control is UNRESOLVED | lengthen the controls |
| H0a_NO_RESPONSE | no detection at any fc | "proxy not demonstrated at the reported detection limit". Not impossibility |
| H0b_NOT_SELECTIVE | detected, controls clean, C1 or C2 failed, **and** affirmative evidence that selectivity is below 10 dB at every detected fc | the channel exists but is not DDC-selective |
| H1_SUPPORTED_RUNG4 | C1 and C2 pass under Holm (C5 absent counts as p = 1) | physical emission depends on the digitally selected frequency |
| H1_SUPPORTED_RUNG5 | as above and C5 passes | tuned frequency inferred under blinded conditions |
| INCONCLUSIVE | anything else | in particular: a failed C1/C2 **without** affirmative non-selectivity. An underpowered selective channel is never reported as non-selective |

## Exclusions

Dwells are invalid for: `ring_ovf > 0`, clip fraction >= 1e-4, power differing from the sweep median by more than 1.0 dB, or a bounding probe-off baseline whose DUT stream RMS drifted by more than 1.5 dB from the first baseline (drift is judged from the stream RMS, never from a single lock-in amplitude, which is too noisy). Excluded dwells are reported and never replaced selectively. Alias-table exclusions are decided before data and do not count toward the 10% limit.

## Blinding and randomisation (procedural)

- The dwell order is generated by `blind.py schedule` from a seed derived from public text (use the freeze commit hash), and the evaluator checks the recording matches it exactly.
- Stage 4: a second operator runs `blind.py commit`, publishes only the SHA-256 commitment, and keeps the secret. The analyst writes `estimates.json` and records its SHA-256 before `blind.py reveal` checks both. The commitment and the estimate hash make later changes detectable; they cannot stop someone opening a file they should not.
- **Stage 1/2 cannot be blind to control roles**, because the C3 and C4 rules differ by role. The protection there is the frozen rule, the automated evaluator and the randomised order.
- The Stage 4 driver that tunes the DUT from the secret schedule is hardware-dependent and not written yet.

## The second operator in Stage 4

**What it protects.** Stage 4 is the only part of the plan that claims the tuned frequency can be *recovered* from the lock-in sweeps. That claim means something only if whoever (or whatever) estimates fc did not already know it. Knowledge leaks into an analysis in quiet ways even when the estimator is frozen: which dwells look "obviously bad", whether to re-run a trial, when to stop looking, which exclusion to apply. The second operator holds the true fc so that the analyst cannot be influenced by it.

**What the role consists of.**
1. Before any trial: draw the secret list (`blind.py commit`), keep the secret file, and publish only the commitment hash somewhere the analyst cannot alter.
2. For each trial: tune the DUT's centre frequency from the secret list, start the preregistered coarse sweep, and make sure nothing the analyst receives reveals fc. In particular the run metadata given to the analyst omits fc, and no DUT stream statistics (RMS, toggle rates) reach the estimator, because Stage 0B showed those vary with tuning.
3. After the analyst has written `estimates.json` and recorded its SHA-256: run `blind.py reveal`, which checks the commitment, the estimates hash and the mode's attestation, and writes the record the evaluator checks.

**What it takes.** No RF knowledge: a one-page checklist (section 7b of the Stage 1 checklist). Time: about 15 minutes to commit and about 15 to reveal. The 20-30 trials are sequential, each a coarse sweep of about 30 minutes (56 points of 30 s), so about 10-15 hours of elapsed run time. Unless the tuning is automated, the operator must act at each trial boundary (seconds each); the driver that automates this from the secret list is hardware-dependent and **not written yet**. Do Stage 4 last, so scheduling the person is the only constraint.

**Choosing a mode** (recorded in the commitment; the evaluator awards the blinded verdict, rung 5, only when the mode's checks pass):

| Mode | Who | What the software checks at reveal | Protects against | Does not protect against |
|---|---|---|---|---|
| `human_second_operator` (default) | another person | operator and analyst named and different | the analyst's knowledge, deliberate or not; accidental metadata leaks if the operator redacts | the operator and analyst colluding or being the same person under two names |
| `os_account_separation` | one person, two OS accounts on the DUT host | the secret file and the estimates file have different owners | accidental or convenient peeking; most unconscious bias | anyone with root, or the same person who knows both passwords and chooses to look. Needs sudo on the host to create the account and the driver |
| `self_with_external_commitment` | one person | a record of where the hash was published before trial 1 | after-the-fact changes to the secret | peeking: it makes changes detectable, not knowledge impossible. Weakest |
| none | | | | Stage 4 may still be run and reported, but cannot support rung 5; the verdict is capped at rung 4 |

For a solo operator the practical choice is `os_account_separation` if sudo on scythe-1 is acceptable, otherwise `self_with_external_commitment`, reporting it as the weaker mode. If a second person is available at all, even for an hour at the start and an hour at the end with an automated driver, `human_second_operator` is stronger than either.

## Sensitivity: what the simulation says the bench must deliver

`s2` below is mean corrected in-band power divided by the noise power, per 60 s dwell: the one hardware-dependent number. All results are from `simulate.py` (100 simulations per row; 50 for tables B-D), assuming independent Gaussian noise, a known plateau width and clean controls.

**Error rates (the procedure itself).** Over 1,300 null simulations (H0a, H0b and H0c at every s2, 100 each) there were **no** H1 verdicts. A genuine selective channel was never labelled H0b. In 100 H0a simulations, 98 gave H0a, 1 INCONCLUSIVE and 1 CONTROLS_FAIL (a false detection tripping the controls).

**Power, with a 25 dB-selective channel and ample controls (Stage 4 included):**

| s2 | H1_SUPPORTED_RUNG5 | RUNG4 only | everything else |
|---|---|---|---|
| 8 | 95 | 0 | 5 INCONCLUSIVE |
| 4 | 21 | 6 | 73 (50 INCONCLUSIVE, 23 controls unresolved) |
| 2 | 0 | 1 | 99 |
| 1 | 0 | 0 | 100 |

**Conclusion: the experiment needs about s2 >= 8 (9 dB of in-band power over the noise per 60 s dwell).** Below about 4 it cannot tell a selective channel from nothing. The frozen rule makes this a precondition: the Stage 1 amplitude sweep (a calibration run, excluded from every confirmatory test) must find a level below clipping with s2 >= 8 at the first fc, otherwise the experiment is not informative and stops. Each result reports its own s2 and detection limit.

**The 10 dB threshold is a margin test, not a reading.** At s2 = 8, an H1 verdict (C1 and C2 both passing) is reached in 0/50 runs when the true selectivity is 12 dB or lower, 23/50 at 15 dB and 48/50 at 25 dB. To certify "at least 10 dB" the channel must be considerably more selective than 10 dB or the SNR much higher.

**Controls are the expensive part.** Showing a control is below -20 dB needs `k*sqrt(n) >= z / (0.01 * s2)` (n control dwells, each k times the sweep dwell) with a safety margin. Fraction of runs where all controls resolve, controls truly at -35 dB:

| s2 | n=8, k=20 (2.7 h each) | n=12, k=40 (8 h) | n=12, k=80 (16 h) |
|---|---|---|---|
| 4 | 10/50 | 41/50 | 50/50 |
| 8 | 32/50 | 50/50 | 50/50 |

Three controls at s2 = 8 cost about 24 h of control time at the 8 h setting. By the formula (not simulated), relaxing the control threshold to -15 dB would cut that roughly threefold; that is an owner decision about how small a coupling is acceptable, not a statistical one.

**Stage 4 (20 trials).** C5 passes in 50/50 at s2 = 8, 33/50 at 4, and about 0 at 2 or below.

## Exploratory (no confirmatory claim)

Per-frequency response curves, phase, the amplitude-sweep shape, Stage 3 distance and shielding curves, the second DUT, the tuner-mode contrast, and any per-frequency flags (Benjamini-Hochberg q = 0.05 at most, labelled exploratory). They are reported with effect sizes and intervals and are not used to change a verdict.

## Owner decision sheet (generic options, with a default for each)

Pick a profile, optionally override single decisions, build the rule, then freeze it:

```
python3 scripts/experiment_e/prereg/profiles.py list                         # profiles, centre-frequency sets, overridable decisions
python3 scripts/experiment_e/prereg/profiles.py budget standard              # sweep and control time for that profile
python3 scripts/experiment_e/prereg/profiles.py build standard --out prereg_v1.json      # --fc-set B, --set tests.selectivity_db=15 ...
python3 scripts/experiment_e/prereg/prereg.py freeze --owner "<name>"        # the owner's act; commit PREREG_FREEZE.json before the first DUT run
```

Every build is validated and refused if inconsistent (plateau outside the sweep, overlapping centres, p-value resolution too coarse for the alpha, a C2 tolerance below 2.5x the sweep step, sets that cannot be filled). The validator caught two real problems while this was written: the stringent alpha could not be resolved with 5,000 permutations, and a 150 kHz C2 tolerance cannot be met with a 100 kHz sweep step even with no noise.

| | `pragmatic` | `standard` (default) | `stringent` |
|---|---|---|---|
| Use | pilot-grade first look. Preregistered, but do not cite as confirmation | confirmatory | a claim to be relied on or published |
| Family alpha | 0.05 | 0.01 | 0.001 |
| Selectivity certified | 6 dB | 10 dB | 10 dB |
| Control must be below (CI level) | -15 dB (95%) | -20 dB (99%) | -25 dB (99.5%) |
| C2 centre-shift tolerance | 500 kHz | 250 kHz | 250 kHz |
| Stage 4 (min trials) | MAE <= 0.75 MHz, >= 70% within half a passband (20) | MAE <= 0.5 MHz, >= 80% (20) | MAE <= 0.25 MHz, >= 90% (30) |
| Equal-power / baseline-drift tolerance | 2 dB / 3 dB | 1 dB / 1.5 dB | 0.5 dB / 1 dB |
| Required in-band power / noise (s2) | 4 | 8 | 16 |
| Control time each (3 controls) | 5.4 h (16 h) | 8 h (24 h) | 13.4 h (40 h) |
| Sweep time | 2.5 h per fc, 7.6 h for the three | same | same |

Measured by `simulate.py characterise_profile` (simulated; 40 simulations per cell; ample controls; a 25 dB-selective channel unless stated):

| | `pragmatic` | `standard` | `stringent` |
|---|---|---|---|
| False H1 verdicts under the three nulls | 0/120 | 0/120 | 0/120 |
| H1 verdict at s2 = 4 / 8 / 16 / 32 | 40 / 40 / 40 / 40 | 14 / 40 / 40 / 40 | 3 / 38 / 40 / 40 |
| True selectivity first certified (s2 = 16) | 10 dB: 40/40 (6 dB: 0/40) | 15 dB: 40/40 (10 dB: 0/40) | 15 dB: 33/40 (10 dB: 0/40) |

Zero false verdicts in 120 bounds the rate below about 2.5% (95%); only the standard profile has been run at larger scale (0 in 1,300). The pragmatic profile reaches full power at s2 = 4 because its bar is lower, which is exactly why its results are labelled pilot-grade.

**Single decisions** (override with `--set path=value`). Default in bold:

| Decision | Options | Consequence |
|---|---|---|
| DDC centres | **A: 3.0 / 7.5 / 12.0 MHz**; B: 4 / 8 / 12; C: 2.5 / 7 / 11.5 | at least 3 MHz apart with each plateau inside the 0.2-14 MHz sweep. B puts the top plateau edge at 13.02 MHz. Re-derive the alias table for whichever set is chosen |
| Selectivity to certify | 6 / **10** / 15 dB | a margin test: the true value must exceed the number clearly (10 dB certifies at about 15 dB true) |
| Family alpha | 0.05 / **0.01** / 0.001 | needs enough bootstrap and permutation resamples to resolve it; the validator checks |
| Control level | -15 dB / **-20 dB** / -25 dB | control time scales as 1 over the fraction: 3x less at -15 dB, 3x more at -25 dB |
| C2 tolerance | 500 / **250** kHz | tighter than 2.5x the sweep step needs a finer step (50 kHz doubles the sweep time) |
| Stage 4 floors | 0.75 MHz and 70% / **0.5 MHz and 80%** / 0.25 MHz and 90% | stricter floors need more SNR and 30 trials |
| Baseline drift tolerance | 3 / **1.5** / 1 dB | tighter invalidates more dwells |
| Required s2 | 4 / **8** / 16 | 4 gives about 27% power under the standard rules; 16 needs a stronger probe or longer dwells |

Still the owner's to decide, not statistical: **the control time budget** (8 h per control at the default, 24 h for the three), **who the second operator is** and which mode to use (see the section above), and whether the pilot-grade profile is acceptable for a first look.

## Limits of this document

The simulation assumes independent Gaussian dwell noise, a rectangular plateau of exactly the stated width, and a lock-in whose noise floor estimate is unbiased. Real lock-in noise may be coloured or drift, the plateau may be wider or softer, and the H0b/H0c split rests entirely on the real controls (the simulated controls here are clean by construction). The error rates are therefore properties of the *procedure under these assumptions*, to be re-checked on the bench noise before relying on them.
