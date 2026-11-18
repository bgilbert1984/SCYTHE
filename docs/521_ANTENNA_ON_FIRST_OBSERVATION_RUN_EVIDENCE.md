# §5.21 antenna-on observation: first run under the frozen scope (2026-11-17)

Evidence record. It changes no frozen file, test or behaviour. Everything about the venue, the antenna and the 50 ohm load is the owner's statement and was not verified.

## What ran
- Run `521-UHF-XTR-E4K-ANTENNA-OBS-20261117-MUK1` by `rf_antenna_on_run.py` (code at main `e669b4dc`, after #492), 2026-11-17 17:20:51 to about 17:28:19 PST, at 12502 Mukilteo Speedway,
  Mukilteo, WA 98275, antenna connected, gain 29.0 dB, receiver SN 71874552 (E4000).
- Authorization chain: frozen declaration sha256 `94b86ebe...2d795c` and tier `c46f56a6...d699a` (`521_ANTENNA_ON_FREEZE_RECORD.md`); run declaration digest
  `2964d5a56d3e5702357dc71c1fe9992f7eee93d7ae69f775e0ab25a2e89979d6`; launch authorization file sha256 `a47026a5aad1c27bae4d7ac66914b65ca509f67763b3611596a486907fe654ed`, status AUTHORIZED on the operator's own
  confirmation naming that digest (recorded verbatim in the file); no time window (the owner waived it). The 192-visit schedule matched the frozen digest `d35d706f...3636`.
- Evidence class `ANTENNA_ON_OBSERVATION_ONLY`: no catalogue entries, classifications or promotions.

## Result
| measure | value |
|---|---|
| visits acquired / refused | 192 / 0 |
| visits passing the frozen visit gate | 113 / 192 |
| tunings scored (all 3 visits pass) | 10 / 64 (tunings 1, 2, 3, 4, 5, 11, 13, 27, 30, 34) |
| refusals | 45 `CANARY_NOT_DETECTED`, 34 `CANARY_BELOW_MIN_EXCESS` (no clipping refusal; clipping at most 0.000012%) |
| visits with both sides detected | 147; weaker-side excess 10.0 / 12.0 / 15.1 dB (min / median / max) |
| weakest passing visit | 11.0 dB (median of passing visits 12.2 dB) |
| tunings by visits passed | 3 passed: 10; 2: 30; 1: 23; 0: 1 |

## Structure of the failures: time, not band
The pass rate by block of 16 visits (schedule order, which mixes the bands at random) is 14, 15, 15, 15, 16, 16, then 3, 4, 3, 6, 3, 3 of 16. The break falls between visit 95 and visit 96 (about 17:24:37):
95% of the first 96 visits pass (91/96) and 23% of the last 96 (22/96). It is the same in both halves of the band: before the break lower-band (LO below 435 MHz) 48/51 and upper-band 43/45 pass; after it 15/49 and 7/47.
Weaker-side excess falls from a median of 12.4 dB (first 96 visits, 95 with both sides detected) to 10.8 dB (last 96, 52 with both sides detected); the sample std rises from a median of 1.27 to 1.57 LSB; candidates per visit
fall from a median of 524 to 419. So conditions got about 1.6 dB worse across the run, and the hard 11.0 dB floor, which sits inside the cluster of weaker-side excess values, turned that into a step in the pass rate.
The break landing exactly at the middle of the schedule is a coincidence I cannot rule out as an artefact of the run, but nothing in `rf_antenna_on_run.py` changes at visit 96.
Because every tuning is visited across the whole run, the every-visit tuning gate leaves 54 of 64 tunings unscored.
An earlier reading of this run by band (pass rates by 5 MHz block, with the lower blocks looking better) was a confound of visit order and is not used.

## Receiver check (load fitted, the same day)
After the run the 50 ohm load was fitted (owner statement) and the 8 tunings of the canary repeats (0, 8, 16, 24, 32, 40, 48, 56, four retune positions each, gain 29.0 dB, Device 030) were captured once:
32/32 pass the gate, weaker-side excess 15.6 / 16.8 / 17.5 dB, sample std 0.73-0.80 LSB, no clipping, median 2,008 candidates, median floor -10.2 to -11.2 dB at every tuning.
The first three visits of each of those tunings in the antenna-on run had a median floor of -2.3 to -7.6 dB, 3.5-8.5 dB above the load-fitted floor and varying by up to about 4 dB between visits to one tuning.
The receiver is therefore healthy now and the degradation was on the antenna side, as at Hwy 99 (11-15) and Lynnwood (11-16). The check ran about an hour after the observation run, so it shows the receiver's state afterwards,
not during the run. The source of the antenna-side change is not identified.

## What this means for the frozen scope
- The gate and the every-visit tuning gate refused a degrading venue as designed, and the run finished with an honest observation document. Nothing about the freeze needs to change on this evidence.
- It is the largest case so far of the accepted assumption on margin (`521_ANTENNA_ON_FREEZE_RECORD.md`, assumption 1): with a floor that cuts through the cluster, a 1.6 dB drift moved the scored fraction from about all to
  about none of the tunings. Yield from a run of this length is sensitive to conditions at the venue, not only to the receiver.
- The same venue gave 32/32 on 11-16 and 11-17 (8 tunings each, minutes long), and 91/96 here in the first half. The run's 3.7-minute halves are not independent samples of venue quality.

## Evidence
- Run directory (locked, 199-file manifest): archive `antenna_on_obs_20261117_mukilteo`, tar.zst sha256 `f9e9d3cba0cbfc2484c2451fba48eaa5f73b81b1748ed377cb6ab21e6f3305aa`, tar sha256 `de66f8706c2da96ad710819ad11ad9c08110bd83049cdffad52e908c2c1ca378`,
  1,610,890,930 bytes raw, 2.71x, verified on dedirock (zstd -t, decompressed tar hash). It holds the spooled windows, journal, `antenna_on_observation.json`, the run and launch declarations and the run log.
- Load-fitted set: archive `slope_mukilteo_antoff_20261117`, tar.zst sha256 `453fb0cfee52da704b98f2d37f074450f5b5cc57d86ee3e9775b361dd78764f1`, tar sha256 `283c1ff919b12cac72303ce6f33ab925bc626557133f602ee264c58f89472f95`, verified on dedirock.
- In this repo (`scripts/s521_antenna_on_run/`): `observation_run_20261117_visits.json` (per-visit gate results), `analyse_observation_run.py`, and the load-fitted `loadfitted_visits.json`, `loadfitted_gate_eval.py`, `loadfitted_spectrum.py`, `loadfitted_spectrum.json`.
