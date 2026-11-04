# §5.21 XTR reference calibration — non-invasive procedure (DRAFT)

Status: DRAFT procedure. No measurement has been taken. No tooling exists yet (see "What must be built").
Receiver: Nooelec SMArt XTR, SN 71874552. Unit not opened and not to be opened.

## 1. Purpose and claim

`521_AUTHORIZED_RUN_DECLARATION_XTR.json` declares `reference_hz = 28,800,000` and `reference_ppm = 100`, the
accepted bound for an uncalibrated receiver. `521_CATALOGUE_AUTHORIZATION_DECISION.md` allows "a direct calibrated
frequency measurement" to supersede oscillator inspection and support "a different justified ppm figure". This
procedure produces that measurement without opening the case.

What it can establish: the XTR's reference frequency error in ppm, with an uncertainty, at the stated times and
temperatures. What it cannot establish: which oscillator is fitted, or the manufacturer's tolerance. The
declared authority would be "direct calibrated frequency measurement against an external reference", not
inspection and not the datasheet.

Not transferable: nothing measured on the NESDR SMArt v5 (SN 14530058) applies to this unit.

## 2. Principle

The R820T2 LO and the RTL2832U ADC clock both derive from the one reference oscillator, so a reference error
`e` (fractional) shifts every received carrier by `e` x its frequency. Tune to carriers whose true frequencies
are known to better than the error being measured, estimate where each appears, and fit

    (f_measured - f_nominal) = a + b * f_nominal        b = reference error (ppm = b x 1e6),  a = fixed offset

A pure oscillator error gives `a ~ 0`. A large `a`, or residuals that do not fit a line, means the model is wrong
(PLL rounding, DC/IQ artefacts, a mis-identified carrier) and the result is not accepted (section 7).

## 3. Why not the obvious shortcuts

- `rtl_test -p` compares the dongle sample clock with the HOST clock. scythe-1 reaches the dongle through
  USB-over-IP (`vhci_hcd`), which adds jitter and gaps; the figure is not meaningful there.
- A datasheet TCXO figure is corroboration only, never the measurement.
- The Si5351 keyed source cannot be the reference: it runs from its own crystal.
- Anything inferred from the 100.800 MHz-type "persistent features" or from catalogue spurs: forbidden by the
  decision doc (result-selected, not a reference).

## 4. Reference sources (choose, record the choice BEFORE data)

Tier A, no new hardware, receive-only, needs an antenna:
- Cellular base-station downlink carriers (LTE and, where still present, GSM). Wide-area base-station carrier
  accuracy is specified very tightly (on the order of 0.05 ppm; CONFIRM the clause and figure in 3GPP
  TS 36.104 / 45.005 before citing it in the amendment). Nominal carrier frequency comes from the channel
  raster (EARFCN / ARFCN) of the cell identified at the site; use only cells whose channel and operator are
  identified, and drop any cell with a repeater or frequency-shifting suspicion. Use >= 3 carriers across the
  tuner span (e.g. one near 700-800 MHz, one near 1.8-1.9 GHz, one in between) so that `a` and `b` separate.
- Feasibility step first: survey the site with the antenna connected, list candidate carriers and their SNR,
  and stop if fewer than 3 usable carriers exist.

Tier B, needs hardware not yet owned: a GPS-disciplined 10 MHz/CW source (GPSDO) into the XTR (through proper
attenuation). Lower uncertainty; not required to proceed.

Do not use: FM or ATSC pilot tones (station tolerance is too loose to beat a few ppm), NOAA weather radio, or
HF time signals (direct sampling on this unit is unproven).

## 5. Setup and contact

This is a different contact type from the catalogue: the termination comes off and an antenna goes on.
- Run only when no catalogue run is in progress. Receive-only; nothing is transmitted.
- Owner states in the session: "the termination may be removed from the XTR input for the calibration, and will be
  refitted afterwards". Record the refit and the load identity afterwards in a note beside the evidence. The
  connector is the only physical contact; the case stays shut.
- Normal tuner mode (direct sampling OFF), gain pinned (record it), 2.048 MS/s u8, same byte-exact 256 ms /
  1,048,576-byte windows as the sequencer, capture-local on the dongle host, spooled with SHA-256 per file.
- Warm up >= 30 min powered on, then log the host/enclosure temperature if any sensor exists (otherwise record
  "not measured").
- No other client on rtl_tcp port 1234. Do not retune mid-window.

## 6. Measurement plan (write down, hash, then collect)

1. Freeze the plan before taking data: carrier table (name, operator, channel, nominal Hz and its source),
   window count N per visit, number and spacing of sessions, the estimator, the acceptance thresholds in
   section 7. Hash it and record the hash in the evidence manifest, the way `prereg` does for Experiment E.
2. Sessions: at least 3, separated in time, including one after a full USB re-attach / power cycle of the dongle
   and, if practical, one cold-start session (shortly after power-up) and one warm. Randomise carrier order
   within each session (reuse the seeded schedule approach in `scripts/experiment_e/prereg/blind.py`).
3. At each visit: tune to the nominal carrier, discard the settle interval (`SETTLE_S`), capture N windows.
4. Estimate the carrier's apparent frequency offset per window with an estimator suited to the signal (e.g.
   cyclic-prefix or synchronisation-signal correlation for LTE, FCCH tone for GSM). The estimator is chosen
   and tested on synthetic data (known injected offset, noise, frequency-selective fading) BEFORE use; its bias
   on synthetic data must be < 0.05 ppm over the test range.
5. Store raw windows and per-window estimates; derive nothing from unlogged data.

## 7. Analysis and acceptance

Per session, fit `offset = a + b * f_nominal` (weighted by per-carrier standard error). Report `b` (ppm), `a` (Hz),
residuals, and the per-carrier offsets in ppm.

Accept a session only if ALL hold (thresholds fixed in the plan, defaults shown):
- >= 3 carriers, each with SNR sufficient for the estimator's stated standard error <= 0.1 ppm;
- residuals consistent with the standard errors (reduced chi-square within [0.3, 3]);
- |a| <= 5 x u(a) AND <= the pre-set absolute limit (default 500 Hz): a pure-ppm model;
- carriers are mutually consistent in ppm within their combined standard errors.

Combine accepted sessions:
- `m` = mean of the session `b` values (ppm); `u_stat` from the within-session fit; `u_session` = standard deviation
  across sessions (power cycle, warm/cold, time of day); `u_ref` = the reference accuracy cited for the source
  (0.05 ppm for wide-area base stations IF confirmed in the cited spec).
- Expanded uncertainty `U = 2 * sqrt(u_stat^2 + u_session^2 + u_ref^2)`.
- Drift allowance `d` = the observed range of session means over the conditions the catalogue runs span (cold vs
  warm; the first minutes after power-up). If the catalogue is run under conditions not sampled, `d` is not
  established and the bound is not tightened for those runs.

Decision rule (fixed in advance):
- If any section-7 acceptance test fails, or `U > 5 ppm`, or session means disagree by more than `U`: no
  amendment; `reference_ppm` stays 100.
- Otherwise the proposed declaration is `reference_ppm = ceil(|m| + U + d)` (rounded up to a stated precision,
  never below the measurement floor), `reference_hz` unchanged at 28,800,000. Do NOT "correct" the reference by `m`
  inside the catalogue; the tolerance covers the error as it is.

## 8. Evidence and amendment

Evidence directory (read-only after the last session): plan + plan hash, carrier table with nominal-frequency
sources, raw spool with SHA-256 manifest, estimator source with hash, synthetic-validation results, per-session
fits, the combined result, and the note on connector handling (termination removed/refitted, load identity).

A tighter `reference_ppm` is chain-bound, so it requires a new frozen authorization file for the XTR (the same
mechanism as `521_AUTHORIZED_RUN_DECLARATION_XTR.json`, a new freeze, never an edit of a frozen file) plus an
amendment note stating the authority ("direct calibrated frequency measurement against an external reference") and the
numbers above. Catalogues already produced at 100 ppm (including 521-UHF-XTR-EPOCH1-RERUN2) are not changed
retroactively; a catalogue under the tighter bound is a new run under the new authorization.

## 9. What must be built before this can run

- A capture script for the calibration visits (spool, hash, seeded randomised order) reusing the sequencer's
  byte-exact window code.
- The per-carrier frequency-offset estimator and its synthetic validation.
- The fit/combination script implementing sections 7-8 and emitting the evidence manifest.
- A site survey step (find and identify usable cells).
None of these exist yet.

## 10. Risks and limits

- Cell identification mistakes (wrong nominal frequency) produce a confident wrong answer; hence the multi-carrier
  consistency test and the identified-cell requirement.
- A repeater or small cell with a poorer oscillator can masquerade as a base station: exclude suspect cells.
- The XTR's PLL has finite frequency resolution; this shows up in `a` and the residuals, not in `b`, if the
  carriers are spread across the span.
- Temperature: a bound measured warm may not hold cold; section 7's `d` exists for this.
- Receiving only; no transmission, no regulatory issue beyond lawful reception of public downlink signals.
