# §5.21 candidate-count gap: margin-shift test (2026-11-17)

> **Correction (2026-11-18).** The conclusion below that floor compression "accounts for the gap" holds for the *count* dominated by the near-DC cloud only. The candidate-level
> membership check (`521_MEMBERSHIP_CHECK_MUKILTEO.md`) shows it fails outside the cloud: antenna-on has about 64 candidates per visit there against 499 load-fitted at 10 dB and about 40 at 12 dB, and the
> weak (10-14 dB) candidates do not survive better than a cross-tuning control. The count match at 12 dB is therefore not evidence that a margin shift reproduces antenna-on membership. Kept as a record, not rescued.

Evidence record. It changes no frozen file, test or behaviour. Script `scripts/s521_canary_threshold/margin_shift_test.py`, results
`scripts/s521_canary_threshold/margin_shift_mukilteo.json`. The test was proposed in a staged analysis note of 2026-11-16 (not committed) and run here.

## Question
Antenna-on captures yield about half the per-visit candidates of terminated ones (about 600-1,400 against about 2,000-2,200; see the tables in
`521_CANARY_THRESHOLD_PROPOSAL_DRAFT.md`). Sample std does not explain it: quiet-venue antenna-on and terminated captures have nearly the same std (about 0.72-0.73 LSB) and a 2.1x
difference in count. The hypothesis tested: a higher noise floor compresses every spur's excess over the floor, so fewer cross the detector's margin.

## Test
`detect_candidates` counts local maxima more than `PLAN_PERSISTENCE_MARGIN_DB` (10.0) above the median of the median spectrum. A flat floor rise of x dB lowers a fixed-power product's excess by about x dB.
So a terminated capture counted at a margin of 10 + x dB should give about the count of an antenna-on capture of the same visit counted at 10 dB.
Paired by visit: the 32 visits of the Mukilteo load-fitted set (`slope_mukilteo_antoff_20261117`, 2026-11-17, owner-stated 50 ohm load) and the Mukilteo antenna-on set (`slope_mukilteo_20261116`, 2026-11-16),
same 8 tunings and four retune positions, gain 29.0 dB. The detector margin is restored after each count.

## Result
| load-fitted margin (dB) | median candidates per visit (q25-q75) |
|---|---|
| 10 (as run) | 2,008 (1,952-2,048) |
| 11 | 1,412 (1,365-1,459) |
| 12 | 1,003 (973-1,054) |
| 13 | 730 (704-745) |
| 14 | 521 (497-542) |

Antenna-on at the real 10 dB margin: median 978 (977.5). Load-fitted at 12 dB: 1,003. Per visit, the antenna-on count divided by the load-fitted count at 12 dB has a median of 0.98 (range 0.82-1.14).
The count falls smoothly, about 30% per dB between 10 and 12 dB. The antenna-on floor of that set was 1.2-2.0 dB above the load-fitted floor at the same tunings (median floor -8.9 to -9.9 dB against -10.2 to -11.2 dB),
and its weaker-side canary excess was about 1.4 dB lower (median 15.4 against 16.8 dB): the same compression seen from the canary's side.

## What it shows and what it does not
- The count gap is reproduced by a margin shift of about 2 dB, so floor compression accounts for the gap in count at this venue and tunings. Total noise power (std) does not.
- Only counts were compared. It is not shown that the same candidates survive in both cases, so "the whole story" is not claimed. (Tested afterwards: they largely do not outside the near-DC cloud; see the correction above.)
- One venue, one pair of sets, taken on different days (11-16 antenna-on, 11-17 load-fitted); the floor of an antenna-on venue changes from day to day (see the Hwy 99 runs). The paired visits share tunings and retunes, not time.
- The mechanism says nothing about what the surviving candidates are. The ~2,000 candidates of a terminated visit are not shown here to be noise excursions or spurs.
- An earlier analysis (2026-11-11, not committed) found that lowering RERUN2's threshold by 2.4 dB reproduced RERUN3's entry counts at 15 and 20 dB margins but over-counted at 10 dB. That was a different comparison
  (entries after persistence, lowering a threshold). Here the margin is raised from 10 dB and the count falls smoothly; the two are not inconsistent but have not been reconciled.

## Consequence
The candidate-count gap is not a separate problem: it is the same floor compression that costs antenna-on visits 1-3 dB of canary excess, and the same mechanism that lets a floor rise of about 6 dB (Hwy 99, 11-15) remove the
canary entirely. It does not change the frozen scope or the accepted assumptions in `521_ANTENNA_ON_FREEZE_RECORD.md`; it explains the margin assumption (assumption 1) mechanically.
