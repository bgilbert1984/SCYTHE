# Experiment E: Stage 1 Bench Checklist (DRAFT)

Status: draft, nothing run. Companion to `EXPERIMENT_E_BENCH_PLAN.md` (Stage 1, Stage 2 controls, "Stage 1 keyed source") and `EXPERIMENT_E_STAGE0B_RESULTS.md`. Tick boxes as you go; write measured values in the margin or the run log.

## 0. Before touching hardware

- [ ] Owner authorization recorded for enabling the generator output (conducted only, no antenna on the DUT input). Date and scope: ______
- [ ] XTR contact clearance confirmed (host env file says it is not cleared for catalogue/corpus contact; see the `scythe-rf-device-contact-order` note). Date: ______
- [ ] Decision rule frozen in writing before any DUT run: block length, confidence level, effect-size floor, in-band/out-of-band selectivity threshold (plan suggests >= 10 dB, a guess). Frozen on: ______
- [ ] Run log file created (see section 8). Run ID: ______

## 1. Equipment

- [ ] DUT: XTR on scythe-1, Q-branch direct sampling (`rtl_tcp` command 0x09 = 2). I-branch carries nothing.
- [ ] Si5351 breakout + Arduino running `scripts/experiment_e/keyed_source/keyed_source.ino`.
- [ ] Low-pass filter(s) for the probe (cutoff above the highest probe frequency, sharp by 3x the lowest fundamental). Part and cutoff: ______
- [ ] Step attenuator, 10+ steps, range covering about 60 dB; plus a fixed pad at the Si5351 output. Calibrated steps: ______
- [ ] DC block, shielded coax, 50 ohm terminators (2).
- [ ] VBUS current probe or low-noise shunt + amplifier, DAQ or scope (>= 1 kS/s per channel, 3 channels: probe, REF, MON). Record the settings.
- [ ] Reference receiver or spectrum analyzer for characterizing the probe chain (not the DUT).
- [ ] Separate supply or isolated USB for the Arduino. Generator physically away from the DUT and emission probes.
- [ ] Optional: 10 MHz reference to lock generator, DAQ and gate source.

## 2. Firmware bring-up (no DUT connected)

- [ ] Board boots: `READY unarmed, outputs off`; `?` shows `armed=0 out=0`.
- [ ] With a scope on CLK0 into 50 ohm: no output while unarmed, after `F`, after `D`, after `E 1` (must reply `ERR not armed`).
- [ ] `ARM 1`, `E 1`: output appears. `X`: output stops, `armed=0`.
- [ ] Dead-man: arm, enable output, send nothing for 30 s: `DEADMAN off` printed and output off.
- [ ] Independent hardware kill (power relay or output-enable line) that works with the Arduino hung or the I2C bus stuck; tested with the RF detector, not the serial reply. The firmware's own `X`/dead-man cannot help if the Si5351 write fails: serial only shows what the firmware believes. Cut time measured: ______
- [ ] Unattended worst case measured: host process killed mid-keying, time until RF detector shows off (simulation predicts about the 30 s dead-man). Value: ______ . If that is too long, lower `DEADMAN_MS` and the compiler's ping interval together.
- [ ] The firmware has no notion of approval; anyone with the serial port can `ARM`. Port access restricted to the supervisor (`replay/lifecycle.py`) during runs; note how: ______
- [ ] Supervisor kill latencies measured against the RF detector (operator kill, revoke, expiry, tamper); simulation bounds are about 5 ms and 130 ms. Values: ______
- [ ] Frequency clamp: `F 50000` and `F 40000000` both return `ERR`.
- [ ] Keying: `K 7000 50`. Scope REF (D8) vs MON (D9) vs CLK0: measure the REF-to-MON delay, period error and edge jitter over 60 s. Record: ______
- [ ] Sweep: `S 1000000 1100000 100000 500` steps and finishes with `SWEEP DONE`.
- [ ] `set_correction` unit confirmed against the installed library version; XO error measured against the reference receiver. Value applied: ______
- [ ] After any retune (sweep step) CLK0 stays off in the keyed off-phase (check on the scope).

## 3. Probe-chain characterization (generator -> filter -> attenuator -> reference receiver, DUT not connected)

- [ ] Output level at each attenuator step measured at the DUT end. Table saved.
- [ ] Residual harmonics measured for each probe frequency to be used (in-band and out-of-band). Level below fundamental: ______
- [ ] Alias table re-derived for the actual fc and probe frequencies (sample clock 28.8 MHz assumed; confirm). No residual harmonic inside the DDC passband that would contaminate the out-of-band probe.
- [ ] In-band and out-of-band probes set to equal power at the DUT end (within ______ dB).
- [ ] Noise floor of the reference receiver with the generator disarmed vs armed-but-unkeyed vs keyed: no spurs at the keying frequency or its harmonics in the chain.

## 4. DUT and host setup (scythe-1)

- [ ] Nothing else is consuming the `rtl_tcp` stream (`/proc/net/tcp` on port 04D2 shows only LISTEN). If the service and watchdog must be stopped, stop the watchdog timer first, then the service, and note the time: ______
- [ ] Sample rate 2.048 MS/s, fixed gain, fc chosen (first fc: ______ MHz), direct sampling Q-branch.
- [ ] Antenna disconnected; DUT input terminated (50 ohm) until the probe chain is connected.
- [ ] DUT warm-up of at least 30 minutes with the stream running. Interleave a reference point in every sweep to catch drift.
- [ ] Host only writes the stream to `/dev/null` or a fixed sink, so no host processing varies with the data.
- [ ] Clipping check: with the probe at the top level, clip fraction < 1e-4 (use `stage0_stats.py`).

## 5. Measurement chain

- [ ] Probe A (VBUS) noise floor recorded with the DUT streaming and the generator disarmed.
- [ ] Mains pickup check: spectrum of the probe signal shows no line at the keying frequency; f_m ~ 7 Hz chosen to avoid 50/60 Hz and harmonics (confirm 7.000 Hz is clean here).
- [ ] DAQ clock and Arduino clock relation known; lock-in reference taken from REF (D8) and the I2C-complete time from MON (D9). Neither is the RF gate: record an RF detector or reference-receiver envelope as `rf_gate_observed`.
- [ ] Gate timing measured on the scope/DAQ: PIN_REF to PIN_MON latency (compare with the firmware `lat=` log), PIN_MON to RF envelope edge, idle and during a sweep. Values: ______
- [ ] Status line after each run shows `ring_ovf=0` and `log_drop=0`; `max_lat_us` recorded: ______
- [ ] Optional near-field probe B placed and fixed in position; photo taken.

## 6. Controls first (Stage 2), each with a full lock-in run

- [ ] Generator keyed, disconnected from the DUT, DUT input terminated: no response at f_m.
- [ ] Generator connected to the DUT but disarmed: no response.
- [ ] DUT unplugged from USB, generator on: no response in the measurement chain.
- [ ] Constant tone (no keying): the response at f_m vanishes.
- [ ] Sham gate at a different f_m: the response follows the sham, not the generator.
- [ ] Direct sampling off (tuner mode), same stimulus: contrast run recorded.

Stop here if any control shows a response of comparable size to the expected signal; fix the coupling before continuing.

## 7. Stage 1 runs

- [ ] Amplitude sweep at one in-band frequency (about -60 to -10 dBFS equivalent): response vs level, clipping onset marked. Mid-level chosen for the frequency sweep: ______
- [ ] Frequency sweep 0.2 to 14 MHz in 100 kHz steps, 60 s dwell, randomized order (about 2.3 h per sweep), one reference point after every 10 steps.
- [ ] Repeat the sweep with fc moved to two other values; the response curve must move with fc.
- [ ] Same-power in-band vs out-of-band comparison repeated at least 3 times, interleaved.
- [ ] Second DUT unit if available.
- [ ] Optional Stage 3 preview: same sweep with probe B in free air, then shielded.

## 8. Logging

- [ ] Every run saves: raw DAQ files, the Arduino serial log (with `V 1`), run metadata JSON (run ID, UTC start/end, fc, f_m, duty, drive, attenuator step, filter, gain, antenna state, firmware commit, operator).
- [ ] The decision rule file is committed before the first DUT run and not edited after.
- [ ] Raw captures are stored off the host `/tmp` (size: roughly 4 MB/s per capture stream).

## 9. Stop criteria

- Any unintended output while unarmed, or an unexpected dead-man trip: stop, fix, rerun section 2.
- Response in the controls comparable to the signal: stop at section 6.
- Clip fraction >= 1e-4 at the chosen level: reduce the level and redo that point.
- DUT or probe-chain drift beyond the decision rule's tolerance between reference points.

## 10. Shutdown and restore

- [ ] `X` sent, generator disarmed, attenuator set to maximum, probe chain disconnected from the DUT.
- [ ] DUT input terminated again.
- [ ] If stopped: `scythe-rtl-tcp.service` and `scythe-rtl-watchdog.timer` restarted, active, and `rtl_tcp` serving on port 1234.
- [ ] Direct sampling off, center frequency 100000000 Hz, confirmed with a short test.
- [ ] Temporary files removed from the host.

## 11. After the run

- [ ] Results note written (what was demonstrated at each rung of the evidence ladder, what was not), updating the claims table in `PASSIVE_TUNING_SENSOR.md`.
- [ ] Negative results recorded as "proxy not demonstrated at the tested sensitivity", not as impossibility.
