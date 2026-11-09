# scythe-1: antenna-fitted FM band measurements (2026-11-09)

Measurements taken on scythe-1 (Nooelec SMArt XTR v5, Elonics E4000) through its `rtl_tcp`
service, centre 100.0 MHz, 2.048 MS/s, uint8 IQ, with a magnetic-base antenna fitted
(site: Everett, WA area; street location deliberately not recorded here). These are
single-session observations, not a calibration. Nothing here applies to other bands,
sites or antennas.

## 1. Tuner automatic gain saturates the ADC with an antenna fitted

Fraction of raw samples at the rails (value <= 2 or >= 253), 0.6 s per setting, two strong
local FM stations in band. IQ rms in raw counts (full scale about 127).

| Gain | At the rails | IQ rms |
|---|---|---|
| auto | 27.5% | 87 |
| 42 dB | 49.6% | 103 |
| 34 dB | 29.0% | 88 |
| 29 dB | 30.2% | 89 |
| 24 dB | 3.0% | 63 |
| 19 dB | 0.0% | 37 |
| 14 dB | 0.0% | 25 |
| 9 / 4 / -1 dB | 0.0% | 13 / 7 / 4 |

19 dB is the highest step with no saturation. A later 2.8 s capture at 19 dB showed
0.0000 of samples at the rails (a few single samples still touch 0 or 255).

Effect on the observation store (median over the retained detections): noise floor
-44 dBFS (auto) to about -58..-60 dBFS (19 dB); SNR 26-44 dB to 31-49 dB; peaks no
longer smeared across the saturated band. Demodulated deviation at 99.9 MHz read
30 kHz rms while clipped and 16.8 kHz at 19 dB, so earlier clipped figures were
inflated.

`SDRPP_TUNER_GAIN_DB=19` (#460) makes the bridge re-apply this on every connection.
Verified by restarting `rtl_tcp` without touching the gain: noise floor returned to
-59.9 dBFS. Limitation: the gain is sent after the first chunk is fed, so the first few
frames of a new connection are at whatever gain the dongle had. Whether those frames can
reach the observation store has not been checked.

## 2. The two dominant detections are broadcast FM

With the 14 dB gate (#459/#461) and a 50 kHz centre guard, the store held only two
clusters: 99.84-99.95 MHz (about 83%) and 100.64-100.76 MHz (about 17%). Both
demodulate as wideband FM broadcast: 19 kHz pilot +37 dB and +34 dB over adjacent MPX,
a sharp 15 kHz audio cutoff, pre-emphasised audio. A control channel at 100.3 MHz
showed none of this.

The 99.9 MHz cluster also shows shoulders about 18-20 dB below the carrier at roughly
125-200 kHz either side and a 99%-power width of 372 kHz. This is the spectral shape of
HD Radio digital sidebands; it was inferred from the spectrum, not decoded.

## 3. RDS decode (offline, 9.5 s of IQ at 19 dB)

Pilot-locked 57 kHz demodulation, biphase and differential decoding, CRC-10 block sync.
107 complete groups per station, one programme ID each.

| Carrier | PI | Call letters (US PI formula) | PTY | RadioText (assembled, 16/16 segments) |
|---|---|---|---|---|
| 99.9 MHz | 0x270A | KISW | 5 (Rock) | "The Men's Room Replay -99.9 KISW ..." |
| 100.7 MHz | 0x2CA9 | KKWF | 10 (Country) | "Now playing She Got the Best Of Me by Luke Combs on The Wolf" |

For a K-call, PI = 4096 + 676*c2 + 26*c3 + c4 with letters counted from A = 0:
K-I-S-W gives 9994 = 0x270A and K-K-W-F gives 11433 = 0x2CA9, so the call letters come
from the PI code, not the text. The PS field is not the station name on either station:
KISW cycles programme-title words and KKWF scrolls the song title. The RadioText content is
a one-moment snapshot; the PI and call letters are the stable part. That these are the
Seattle-market stations of those names is background knowledge, not shown by the capture.

The offline decoder is not included in this change.
