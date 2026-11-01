# Vertical RF Tomography: field measurement plan (DRAFT)

Status: draft for review. Every threshold below is a proposal with its reasoning, not a
requirement. Nothing here has been run. Cost and time figures are order-of-magnitude.

## 1. Why this experiment, and what it must answer

All results so far are simulated, and they disagree with each other about how dirty the world is.
On the synthetic four-tower block the ray-traced observables were *cleaner* than my assumed error
model (angle floor 0.2-0.8 deg vs 2 deg assumed, outliers 1-3% vs 10-25%), and calibrating the
solver to that clean world made it sharp but **overconfident once I added clutter** (90%-set
coverage fell to 0.58-0.78 under strong extra dirt). On real Midtown footprints with windowed
facades the same statistics were close to the assumed values for angles (floor ~1.2 deg, 10-13%
outliers) and the floor accuracy of the best layout dropped to top-1 0.43-0.54 (0.92-0.96 on the
block). Scene geometry and facade detail move the answer by more than any noise parameter, so
the right amount of inflation cannot be chosen from simulation. Only real observed-vs-direct-path
residuals can set it.

Questions, in priority order:

1. **How dirty is the real world?** Angular floor, delay floor, outlier rates, RSS model
   (exponent, sigma), and how often a reflection beats the direct path, per receiver type and
   per facade type.
2. **Are the credible sets calibrated on blind data?** Does the "90% set" contain the true floor
   about 90% of the time when the analyst does not know the answer?
3. **How accurate is it, and where does it fail?** Top-1 / top-3 floor accuracy, floor bias
   (the simulations predicted a downward bias from ground bounces), width of the sets.
4. **Does the hardware match the simulation's assumptions?** The simulations assumed a 3 m
   vertical aperture and 10 ns sync. See section 4.

Not a goal: attributing an emitter to an owner, inferring intent, or working with signals we do
not control. All emitters in this experiment are cooperative.

## 2. Design principles (each one comes from something that went wrong in simulation)

- **The independent unit is the emitter placement, not the snapshot.** Multipath is persistent
  for a fixed placement, so 1,000 snapshots of one placement are roughly one sample of the
  error. Plan around the number of distinct (floor, position) placements.
- **Pre-register and blind.** The "MC placement beats analytic" result was retracted after a
  generator bug exposed that I had tuned on the design draws. In the field: freeze the solver
  commit and the calibration rule *before* the blind test; ground truth is sealed by someone who
  is not the analyst; predictions are committed (git hash) before unsealing.
- **Calibrate the sensors with a known source before trusting any floor estimate.** A 2 deg
  common elevation bias broke 90%-set coverage for both solvers (0.78-0.88). Array tilt and phase
  calibration is therefore a first-class phase, not setup.
- **Stratify placements.** Floors in thirds (low / mid / high), at least two facade types
  (the simulations showed layout ranking flips between concrete and glass), indoor positions
  near and far from windows.
- **Pilot first.** One building, 8 placements, 2 receivers, before committing to the full plan.
- **Receive-only for us; cooperative low-power beacons for the targets.** No decoding of third-party
  traffic: record power, timing and angle of *our own* beacons only.

## 3. Sites

Two or three buildings, each at least 20 floors (floor spacing about 3.7 m, so 20 floors give a
meaningful vertical range), with owner/tenant consent for placing beacons on chosen floors and
rooftop or high-floor access for receivers in a neighbouring lower building:

| Building | Facade | Purpose |
|---|---|---|
| A | masonry / concrete, punched windows | the "concrete" case (path-loss exponent ~4.5 in the ray-traced model) |
| B | glass curtain wall | the "glass" case (street receivers suffer ground bounce) |
| C (optional) | mixed / setbacks | a realistic building; tests the model's no-setback simplification |

For each building: surveyed positions for the receivers (RTK GNSS on the roof and street; total
station or laser-measured offsets where GNSS is poor), a floor plan, and floor heights measured
(not assumed to be 3.7 m).

## 4. Equipment, and the gap to the simulation

Receiver nodes (what the placement tiers in `placement/sites.py` stand for):

| Tier | What it measures | Hardware sketch | Note |
|---|---|---|---|
| rss_az | RSS, azimuth | 2-4 channel coherent SDR, horizontal array | ~1 m aperture |
| tdoa | RSS, azimuth, TOA | + GPS-disciplined clock, common time base | target <= 20 ns sync |
| array | RSS, az, el, TOA | 4-8 element vertical array on a mast, coherent multi-channel SDR + GPSDO | see gap below |

**Gap: aperture.** The simulations used a 3 m vertical aperture. At 915 MHz (lambda 0.33 m) a
3 m aperture needs ~10-19 elements; a practical 4-8 element array at half-wavelength spacing is
0.5-1.2 m, i.e. a beamwidth of ~15-40 deg. At 2.4 GHz (lambda 0.125 m) 8 elements are only
0.4 m. Resolution against a ground bounce 2-6 deg below the direct path will be worse than
simulated. **Before buying hardware, re-run the simulations with the achievable apertures**
(this is a code task, see section 9). It may change the conclusion about which tier is worth
deploying.

Cooperative targets: battery-powered beacons, vertically polarised, certified low-power
devices in unlicensed bands (e.g. 915 MHz LoRa-class, 2.4 GHz BLE/Wi-Fi-class), known payload so
the receivers can matched-filter, antenna orientation recorded for every placement. (Not legal
advice: confirm local rules on ISM operation and on building/roof access.)

Ground truth tools: laser distance meter, barometric altimeter logged per placement (floor
cross-check, not used by the solver), photos, floor-plan coordinates, RTK for outdoor points.

Rough budget: two coherent array nodes + three timing nodes + GPSDOs + RTK + beacons +
mast/rigging is on the order of USD 10-20k; three field days per building plus two weeks of
analysis. Treat as a first estimate for planning, not a quote.

## 5. Phases

**Phase 0: bring-up and calibration (before any blind data)**
- Timing: feed all nodes one common beacon, verify TOA agreement; target <= 20 ns (simulations
  assumed 10 ns; set the solver's sync assumption from what is measured).
- Array calibration with a known source at surveyed positions at several angles, including
  elevations above and below the horizon: estimate per-element phase/gain and the *common
  elevation bias*. Target <= 0.5 deg residual; 2 deg is known to break coverage.
- Receiver noise, saturation and dynamic range with the beacon at 5-150 m.
- Record everything into the pre-registered data schema (section 8).

**Phase 1: direct-path residual collection (sets the solver's beliefs)**
- Beacons at *known* positions on known floors with a clear line of sight to a receiver (the
  solver's own geometric test decides which links count as "solver-LOS").
- At least 40 solver-LOS links per receiver type per building, at several floors and ranges.
- Output: the same table the ray-traced calibration produced (`rt_calibration`): angle error
  quantiles and outlier rate (|error| > 5 deg), delay-excess quantiles, path-loss exponent and
  residual sigma (LOS and NLOS), how often the strongest path is not the direct one, split by
  receiver type (street / rooftop) and facade.
- Output: beliefs for the solver via the *frozen rule* `rt_solver_calibration.calibrate`
  (fit, double the outlier/NLOS beliefs, multiply floors by 3). The rule is fixed here, before
  Phase 2; if you want a different inflation, decide it now and write it down.

**Phase 2: blind floor test (the main result)**
- A randomised schedule of placements is drawn in advance by the field lead: at least 20 per
  building, stratified by floor third and window distance, plus at least 4 deliberately hard
  placements (core/stairwell, elevator lobby, interior room) that are *expected* to degrade.
- The field lead keeps the placement log sealed. The analyst receives only the capture IDs and
  receiver data.
- Per placement: at least 3 captures of 60 s at each of two frequencies, at different times;
  treated as repeats of one unit, never as independent samples.
- The analyst runs the frozen pipeline and commits floor posteriors (probabilities per floor,
  90% set, modal floor) for every placement before the log is unsealed.

**Phase 3: targeted physics checks (optional, after Phase 2)**
- Window ripple: same floor, beacon at glass height vs spandrel height vs interior, to check
  the ~8 dB window effect seen in the ray-traced model and its effect on RSS as a floor cue.
- Persistence: repeat a placement on another day / with vehicles and people present, to see how
  much of the error is stable (bias) versus fluctuating.
- Ground-bounce check: street receivers on the glass building, where the model predicted 8% of
  links with a reflection stronger than the direct path.

## 6. Sample size and what it can resolve

With n independent placements the 95% half-width on a proportion near 0.9 is about
1.96 * sqrt(0.9 * 0.1 / n):

| placements | half-width on coverage / top-3 |
|---|---|
| 8 (pilot) | +-0.21 (a shake-down, not a result) |
| 40 | +-0.09 |
| 60 | +-0.08 |
| 100 | +-0.06 |

So 60 placements can tell "roughly calibrated" from "clearly overconfident" (coverage
0.6-0.7), but cannot distinguish 0.88 from 0.92. If that finer distinction matters, plan 100+.
Per-building and per-facade conclusions will have 20 placements each (+-0.13): report them as
indicative.

## 7. Pre-registered metrics and decision rules (proposals)

Primary, computed per layout and overall, with exact binomial intervals:

| ID | Metric | Pass if | Reasoning |
|---|---|---|---|
| P1 | 90% credible-set coverage | lower 95% bound >= 0.75 | not overconfident; simulations showed under-coverage is the dangerous failure |
| P2 | top-3 floor accuracy (best layout) | point estimate >= 0.80 | below the 0.90 design target, which assumed a cleaner world |
| P3 | median 90%-set size | <= 6 floors | useful triage (40 floors -> 6) |
| P4 | median signed floor error (point estimate = posterior median floor), reported per floor third | absolute value <= 1 floor in each third | ground bounces predicted a downward bias; the Midtown ray-traced bias study found a one-sided heavy tail (median +0.4 vs mean +4.9 at floors 2-4), so the mean is dominated by a few emitters and is reported only as secondary |

Secondary: mean signed floor error, top-1 accuracy, floor MAE, coverage by facade / by floor third / by window distance,
by receiver tier, and how Phase-1 beliefs compare with the simulation's assumptions.

What the outcomes mean:

| Outcome | Action |
|---|---|
| P1 and P2 pass | the method works at triage level in this environment; plan a larger, more varied study |
| P1 fails, others fine | solver too sharp: raise the inflation, re-fit on Phase 1 data only, re-test on a *new* blind set (never on this one) |
| P1 passes, P2/P3 fail | honest but not useful: the geometry (apertures, receiver heights) is the limit; revisit hardware, not the solver |
| P4 fails | systematic bias in the median: check array elevation calibration and ground-bounce geometry first; if only the mean is off, look for a few badly wrong emitters (few LOS receivers, low floors) |
| hard placements fail badly | expected; document the failure region (shielded cores, elevator shafts) |

Tuning on the blind set is not allowed. Any change after unsealing is a new hypothesis that
needs new data.

## 8. Data schema and handling

Per capture: UTC timestamps (GPS), receiver ID and surveyed position, array calibration
version, centre frequency and bandwidth, estimator outputs (RSS, azimuth, elevation, TOA, each
with a quality figure), and a short raw IQ snippet of the beacon only for QA. Per placement
(sealed until unsealing): building, floor, position in the floor plan, height, antenna
orientation, notes.

Privacy and governance: receive only our own beacons; no demodulation or retention of other
signals; discard IQ outside the beacon windows; keep placement logs access-controlled; apply the
same mission scoping and approval lifecycle as other SCYTHE work. Building owners and tenants
consent in writing to beacon placement and roof access.

## 9. Code work this plan implies

1. **Realistic apertures:** parameterise element count and spacing; re-run `identifiability`,
   `placement` and the ray-traced evaluation with 0.5-1.5 m apertures; decide on tiers before
   buying hardware.
2. **Field ingestion:** a loader from the Phase 0-2 schema to the solver's measurement vectors
   (surveyed receiver positions, calibration applied, per-measurement quality to set sigmas).
3. **Frozen runner:** one command that takes a config hash, runs the pre-registered pipeline on
   unlabelled captures, writes posteriors, and refuses to read the sealed log.
4. **Analysis scripts** for P1-P4 and the Phase-1 calibration table, reusing
   `rt_calibration` and `rt_solver_calibration`.
5. **Pilot dry run on simulated "field" data** (ray-traced Midtown observations pushed through
   the same ingestion and frozen runner) so the pipeline is debugged before the first field day.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Array calibration drifts (temperature, mast flex) | recalibrate at start/end of each day; log residuals |
| Beacon antenna pattern / orientation dominates RSS | fixed orientation, recorded, vertical polarisation; RSS treated cautiously |
| Sync fails in urban canyons | GPSDO holdover; measure drift; fall back to RSS+AoA tiers |
| Multipath is far dirtier than simulated | that is the finding; Phase 1 quantifies it; P1 is designed to catch overconfidence |
| Too few distinct placements | pilot first; stratify; report intervals, not points |
| Analyst bias | sealed log, frozen commit, committed predictions |
| Regulatory / consent | low-power certified beacons; written permissions; confirm local rules |
| Roof/height safety | trained personnel, access procedures, no lone working |

## 11. Timeline sketch

| Weeks | Work |
|---|---|
| 1-3 | aperture re-simulation, schema, ingestion, frozen runner, dry run on ray-traced data; procure hardware |
| 4 | bench calibration; pilot (1 building, 8 placements, 2 receivers) |
| 5 | review pilot; lock Phase-1 rule and Phase-2 schedule |
| 6-8 | Phase 0-1 per building; Phase 2 blind collection (about 3 field days per building) |
| 9-10 | unseal, analyse, write up (including failures) |

Open questions for the reviewer: which buildings are realistically accessible; whether a
licensed experimental allocation is wanted for wider bandwidths (TDOA benefits from bandwidth,
the simulations barely did); and who plays the field-lead / sealed-log role.
