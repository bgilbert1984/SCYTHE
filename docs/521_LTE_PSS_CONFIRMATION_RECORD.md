# §5.21 Tier A — LTE PSS confirmation record (Check A: text only)

Status: **PARTIAL. Check A transcribed and compared by one reader (the assistant, acting as second reader). Reader 1 (human) has not
yet verified the quoted clauses. Check B (real recording), the nominal-frequency check and the plan flag are NOT done.**
`estimator.pss_definition_confirmed` must stay false until Reader 1 signs and Check B passes. Procedure:
`docs/521_LTE_PSS_CONFIRMATION_PROCEDURE.md`.

```
Document: TS 36.211 V19.3.0 (Rel-19), 3GPP file 36211-j30.zip (4 .docx: cover, s00-s05, s06-s08, s09-sxx)
URL:        https://www.3gpp.org/ftp/Specs/archive/36_series/36.211/36211-j30.zip
downloaded: 2026-11-05 15:15 UTC
SHA-256:    c1b132375361596e713dc51bfa20afbe4bf4c92bf1992c829a775f59a2ece5a1   (36211-j30.zip, 7,629,041 bytes)
Reader 1 (name, date):  PENDING            Reader 2 (name, date): Claude (assistant), 2026-11-05
```

## How the document was read (limits)
The .docx stores every equation as an embedded MathType object with a WMF picture. Prose and table text were read directly. Each
equation was recovered by extracting the glyphs and their coordinates from the WMF and reconstructing the layout, not by viewing a
rendering. Reader 1 should open the clauses below and compare with the transcription. The zip was not added to the repository
(7.6 MB); it is in the assistant's scratchpad only and should be archived by the owner with the hash above.

## Transcription (made before comparing with the code)
| # | Clause | Transcribed from the document |
|---|---|---|
| A1 | 6.11.1.1 | d_u(n) = exp(-j pi u n(n+1)/63) for n = 0,1,...,30; exp(-j pi u (n+1)(n+2)/63) for n = 31,32,...,61 |
| A2 | 6.11.1.1, Table 6.11.1.1-1 | header "N_ID^(2)"; rows 0 -> 25, 1 -> 29, 2 -> 34 (the pairing is read, no longer inferred from row order) |
| A3 | 6.11.1.2 | a_{k,l} = d(n), n = 0..61, k = n - 31 + floor(N_RB^DL N_sc^RB / 2); frame structure type 1: last OFDM symbol of slots 0 and 10; elements with n = -5,-4,...,-1,62,63,...,66 are reserved and not used for the PSS |
| A4 | 6.12 | s_l(t) = sum over k = -floor(N_RB N_sc/2)..-1 of a^(-)_{k,l} exp(j2 pi k df (t - N_CP,l T_s)) + sum over k = 1..ceil(N_RB N_sc/2) of a^(+)_{k,l} exp(j2 pi k df (t - N_CP,l T_s)); k^(-) = k + floor(N_RB N_sc/2), k^(+) = k + floor(N_RB N_sc/2) - 1. DC (k = 0) carries nothing |
| A5 | 6.12, Table 6.12-1 | N = 2048 for 15 kHz; normal CP N_CP,l = 160 for l = 0, 144 for l = 1..6 (T_s units). **The slot length 15360 T_s was not read** (equation in 4.1 is an image; not extracted) |
| A6 | 4.1 | **Not read.** Only the slots-0-and-10 placement (A3) was read; 5 ms period follows from 10 slots x 0.5 ms but 4.1's slot/subframe equations were not extracted |
| A7 | 6.11.1.2 | frame structure type 2: PSS in the **third** OFDM symbol of subframes 1 and 6 (differs from type 1); type 3 follows type 1 with exceptions. A TDD cell therefore has a different placement |

Consequence of A3 + A4: n = 0 -> physical subcarrier -31, n = 30 -> -1, n = 31 -> +1, n = 61 -> +31, DC unused.

## Comparison (independent of the code)
Spec values were computed from the A1 formula in a standalone script (no repo import) and compared with the 15 reference values in
section 3.3 of the procedure (u = 25, 29, 34; n = 0, 1, 30, 31, 61): **15 of 15 agree to 1e-5**. d_34(n) = conj(d_29(n)) holds for
all n = 0..61. The code column (A1 split at 30/31; A2 roots; A3 mapping; A4 -31..-1 / +1..+31; A5 160/144 -> 10/9 samples at 1.92
MS/s) agrees with the transcription. The procedure's table values were not compared against `rf_lte_carrier_estimator.py` output
in this record; run section 3.3's snippet to cover that.

## Result
```
A1 agree   A2 agree   A3 agree   A4 agree   A5 CP lengths agree (slot length not read)   A6 NOT READ   A7 differs from type 1 as expected
Check A result:  PASS for A1-A4, A5 CP lengths, A7 by Reader 2; A5 slot length and A6 outstanding; Reader 1 verification PENDING
Recording: NOT DONE     Independent PCI: NOT DONE     Check B result: NOT DONE
Nominal-frequency check (section 5) result: NOT DONE (TS 36.101 channel raster and 3GPP ppm figure still unconfirmed)
Flag set to true in plan (hash): NO
Limits of the confirmation: definition text only; does not show the code detects real cells, nor that offsets are accurate in ppm;
FDD normal CP only; one release (Rel-19 V19.3.0) read, no cross-check against an earlier release.
```
