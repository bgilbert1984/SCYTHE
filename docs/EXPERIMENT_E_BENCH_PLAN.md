# Experiment E: Active Lock-in Bench Plan (DRAFT)

Status: draft, nothing run. Companion to `PASSIVE_TUNING_SENSOR.md` §8.1.

## Question

Does a keyed probe tone inside a direct-sampling receiver's DDC passband produce a keying-rate component in the device's external electrical emissions that an out-of-band tone of equal power does not? If so, passband location is recoverable, and so is the tuned center frequency.

## Hypothesis and nulls

- **H1:** The lock-in response at the keying frequency f_m is significantly larger for in-passband probes than for out-of-passband probes at equal power. The response curve is a plateau about one sample-rate wide, centered on the DDC center.
- **H0a:** No response at all (no leak at this sensitivity).
- **H0b:** Response is flat across frequency. The ADC's front-end activity leaks regardless of the DDC, so the channel exists but is not selective. This is still a useful finding because it characterizes the sensor, as in Candidate 3 of the doc.
- **H0c:** Response comes from direct coupling of the generator into the measurement chain, not from the DUT.

## Scope and safety

- Owned SCYTHE hardware only.
- Conducted injection through cable, attenuator and 50 ohm path. Nothing radiates; the DUT input is not connected to an antenna.
- Radiated (near-field) stages only inside a shielded enclosure or at levels below the unlicensed limits.

## Setup

| Element | Choice |
|---|---|
| DUT | RTL-SDR with HF direct-sampling mode (v3/v5 class). The Nooelec XTR v5 on scythe-1 (E4000, confirmed by rtl_test) is not expected to support this mode. |
| DUT config | Direct sampling Q-branch, fixed sample rate (2.4 MS/s), fixed gain, fixed center frequency f_c. Host runs `rtl_sdr` to `/dev/null` so no host-side processing varies with the data. |
| Stimulus | Signal generator, CW, gated on/off at f_m by an external square wave. Then step attenuator, then DUT input through shielded coax. Use f_m ~ 7 Hz to avoid 50/60 Hz and harmonics. |
| Probe A (conducted) | Low-noise shunt or current probe on USB VBUS, amplified, into a DAQ or scope. |
| Probe B (near-field) | H-field probe on the USB cable and board, into a spectrum analyzer or reference SDR logging band-power envelopes around the 28.8 MHz harmonics and the USB harmonics. |
| Lock-in | Software. Mix the probe envelope with a reference at f_m, low-pass, record amplitude and phase. Reference comes from the same gating square wave. |
| Reference clock | Lock generator, DAQ and gating source to one 10 MHz reference if available. |

## Procedure

**Stage 0A: offline software validation (no DUT).**
Run `scripts/experiment_e/stage0_stats.py` on external recordings (e.g. labeled HF noise captures, test vectors only) with synthetic CW injection, and on independently generated null vectors. Use these to validate the code and estimate null variability and false-positive rate. Freeze the decision rule (block length, confidence level, effect-size floor) before any DUT capture. Do not calibrate the final threshold on external data and assume it transfers to the DUT.

**Stage 0B: DUT sample response (owned receiver, fixed config).**
Capture the DUT's own sample stream with the probe off, in-band, and out-of-band. The script reports hw, I-to-Q, Q-to-I and I-to-I toggle rates and clipping, with block-based confidence intervals (windows are not independent).
- *Detected response:* proceed to Stage 1 to test whether an external emission correlates with it.
- *Not demonstrated:* this output-byte proxy showed no response at the tested sensitivity. Close this proxy branch only. Other leakage hypotheses (internal NCO/ADC activity, power-rail coupling, clock sidebands) were not measured and stay open.
- Ambient signals (e.g. FT8 near 7.074 MHz) are a receiver sanity check, not a controlled stimulus. Establish the real input path and frequency mapping for the DUT before interpreting in-band vs out-of-band.
- Check saturation (clip fraction) before attributing a change to the stimulus.

**Evidence ladder (each claim needs its own evidence):** RF detected in DUT samples; byte statistics depend on input; physical emission depends on receiver activity; physical emission depends specifically on the digitally selected frequency; tuned frequency inferred under blinded conditions.

**Stage 1: conducted, VBUS.**
1. Fix f_c. Pick a probe power sweep (e.g. -60 to -10 dBFS equivalent) at one in-band frequency to find the response vs amplitude and the clipping onset.
2. At a mid-level power, sweep the probe across 0.2-14 MHz in 100 kHz steps, 60 s dwell each, in randomized order. Record lock-in amplitude and phase.
3. Repeat the sweep with f_c moved to two other values.
- *Gate:* the response curve must move with f_c. Otherwise the response is not DDC-selective (H0b or H0c).

**Stage 2: controls (run with Stage 1 data).**
- Generator gating on but DUT input terminated and generator into a dummy load (tests H0c).
- DUT unplugged from USB, generator on (tests coupling into the measurement chain).
- Keying disabled (constant tone): response at f_m must vanish.
- Direct-sampling mode off (tuner path) for contrast.
- Gate source swapped for a sham square wave at a different f_m: response must follow the sham, not the generator.
- Repeat on a second DUT unit to check device specificity.

**Stage 3: near-field, then isolation.**
Repeat the Stage 1 sweep with Probe B in free air, then inside a shielded enclosure, then at increasing distance from the USB cable. Record the distance or shielding at which the response falls below the detection limit.

**Stage 4: blind estimation.**
A second operator or a script chooses a secret f_c. The analyst sees only lock-in sweeps. Estimate f_c from the plateau center. Run at least 20 trials with randomized f_c. Report mean absolute error and the fraction within half a passband.

## Analysis and pass criteria

- Detection limit: noise floor of the lock-in output with the probe off, and the SNR gain from integration (improves roughly with the square root of dwell time).
- Selectivity: ratio of in-band to out-of-band response at equal power. A useful channel needs a clear ratio (suggest >= 10 dB) that survives the controls.
- Report per stage: effect size with confidence intervals, not only a visible peak.
- Success for the research question: Stage 4 estimation error clearly better than guessing, at a stated distance/shielding.

## Risks and confounds

- ADC input activity leaks regardless of the DDC, which would make the response flat across frequency (H0b). This is the main thing the experiment discriminates.
- Direct coupling of the keyed generator into probes or ground loops. Controls in Stage 2 are essential.
- Thermal drift: a few degrees of warm-up can mimic slow responses. Warm up the DUT, randomize sweep order, interleave a reference point.
- Host-side USB DMA activity depends on throughput, so keep the stream rate constant.
- 8-bit saturation at high probe levels changes the statistics and can create harmonics. Stay below clipping for the main sweep.

## Deliverables

- Raw lock-in sweeps (CSV), controls, config and script versions.
- A short results note appended to `PASSIVE_TUNING_SENSOR.md` §8 with the claims table updated.
- Estimated effort: Stage 0 half a day, Stage 1-2 two to three days, Stage 3-4 a week.
