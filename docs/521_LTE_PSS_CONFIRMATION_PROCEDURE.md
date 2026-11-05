# §5.21 Tier A — confirming the LTE PSS definition (DRAFT procedure)

Status: DRAFT. Nothing in this procedure has been carried out. Companion to
`docs/521_XTR_PPM_CALIBRATION_PROCEDURE.md` and `rf_lte_carrier_estimator.py`.

## 1. Why this exists

The LTE estimator finds the integer-subcarrier part of a cell's frequency offset by matching received
symbols against the primary synchronisation signal (PSS). It uses a PSS definition that was **not read from the
normative text**. What is known, and how well:

| Item | Status |
|---|---|
| Zadoff-Chu root indices 25, 29, 34 | text on a public mirror of TS 36.211 clause 6.11.1.1, Table 6.11.1.1-1: rows read 25, 29, 34; the column pairing with N_ID^(2) = 0, 1, 2 was inferred from row order |
| PSS transmitted in the last OFDM symbol of slots 0 and 10 (frame structure type 1, FDD) | text on the same mirror |
| d_u(n) equation (two branches, n = 0..30 and 31..61) | **image on the mirror; not read.** Code uses exp(-j pi u n(n+1)/63) and exp(-j pi u (n+1)(n+2)/63), from a patent filing and recollection |
| Resource-element mapping (62 elements around DC, DC unused) | **image; not read.** Code maps n = 0..30 to subcarriers -31..-1 and n = 31..61 to +1..+31 |
| Normal-CP slot structure at 1.92 MS/s (symbols of 138/137 samples, CP 10/9) | derived by scaling TS 36.211 clause 5 values; not read from the text |

A wrong definition passes every test in the repository, because the test generator uses the same definition as the
estimator. Only a comparison against the normative text, and then a real eNodeB, can find it. The calibration plan carries
`estimator.pss_definition_confirmed`; while it is false any proposal is blocked.

## 2. Principle

Two independent checks, neither sufficient alone:

- **A. Text check.** Someone reads the normative equations and compares them, value by value, with what the code computes.
  Independence means the comparison values come from the spec, not from the code or from the author's memory.
- **B. Signal check.** The estimator is run on a real LTE downlink recorded off the air, and the cell identity it reports is compared
  with an identity obtained by a method that does not use this code.

A passes if the definition matches the text. B passes if the definition works on something a real network transmits. A without B
can miss a clause that was misread by both people; B without A can pass on a lucky cell.

## 3. Check A: text (do this first)

### 3.1 Source
Obtain TS 36.211 from the 3GPP specification archive or from ETSI (TS 136 211). Use the release in force for the networks to be measured
(any release from Rel-8 on has the same PSS; record which). Record in the confirmation record: document number and version, release, URL,
date downloaded, file name, and the file's SHA-256. Read the **document**, not a mirror of it that renders equations as images.

### 3.2 What to read, and what the code assumes
Transcribe each item from the document into the record **before** looking at the code column.

| # | Clause | Item | The code assumes (compare only after transcribing) |
|---|---|---|---|
| A1 | 6.11.1.1 | d_u(n), n = 0..61, both branches | exp(-j pi u n(n+1)/63) for n <= 30; exp(-j pi u (n+1)(n+2)/63) for n >= 31 |
| A2 | 6.11.1.1, Table 6.11.1.1-1 | N_ID^(2) -> u | 0 -> 25, 1 -> 29, 2 -> 34 |
| A3 | 6.11.1.2 | mapping of d(n) to resource elements: formula for k, the symbol index l, and any reserved elements | k = n - 31 + N_RB^DL N_sc^RB / 2; the 62 elements are centred on DC with DC unused; the last symbol (l = 6, normal CP) of slots 0 and 10 |
| A4 | 6.12 | how resource-grid index k maps to subcarrier frequency (negative half, positive half, DC) | negative frequencies -36..-1 and positive +1..+36 in units of 15 kHz for 6 RB; DC unused; so n = 0..30 -> -31..-1 and n = 31..61 -> +1..+31 |
| A5 | 6.12, Table 6.12-1 | normal-CP lengths N_CP,0 and N_CP,l in T_s, with N = 2048 and slot = 15360 T_s | 160 and 144 -> 10 and 9 samples at 1.92 MS/s; slot 960 samples |
| A6 | 4.1 | frame structure type 1: slot 0.5 ms, subframe 1 ms, PSS period | PSS every 5 ms |
| A7 | 6.11.1.2 | time-division duplex placement (type 2) | not supported by the estimator; confirm it differs so that a TDD cell is refused, not mis-measured |

### 3.3 Numerical comparison (independent of the code)
From the transcribed A1 and A2, compute by hand or with a script that does **not import** `rf_lte_carrier_estimator`, at least
d_u(n) for n = 0, 1, 30, 31, 61 and u = 25, 29, 34. Then compare with what the code computes:

```
python3 -c "
import rf_lte_carrier_estimator as L
for u in L.PSS_ROOTS:
    p = L.pss_sequence(u)
    for n in (0, 1, 30, 31, 61):
        k = n - 31 if n <= 30 else n - 30
        print(u, n, k, '%+.6f %+.6fj' % (p[k].real, p[k].imag))"
```

Values the code produced when this procedure was drafted (compare, do not derive from):

| u | n | k | d_u(n) |
|---|---|---|---|
| 25 | 0 | -31 | +1.000000 +0.000000j |
| 25 | 1 | -30 | -0.797133 -0.603804j |
| 25 | 30 | -1 | -0.988831 +0.149042j |
| 25 | 31 | +1 | -0.988831 +0.149042j |
| 25 | 61 | +31 | +1.000000 +0.000000j |
| 29 | 0 | -31 | +1.000000 +0.000000j |
| 29 | 1 | -30 | -0.969077 -0.246757j |
| 29 | 30 | -1 | +0.955573 -0.294755j |
| 29 | 31 | +1 | +0.955573 -0.294755j |
| 29 | 61 | +31 | +1.000000 +0.000000j |
| 34 | 0 | -31 | +1.000000 +0.000000j |
| 34 | 1 | -30 | -0.969077 +0.246757j |
| 34 | 30 | -1 | +0.955573 +0.294755j |
| 34 | 31 | +1 | +0.955573 +0.294755j |
| 34 | 61 | +31 | +1.000000 +0.000000j |

Two properties follow from the code's equation and should also hold for the spec's: d_u(30) = d_u(31) (the punctured centre) and
d_34(n) = conj(d_29(n)). If the spec's equation does not have them, the equation is different.

### 3.4 Who
Not the author of the code's definition. The code's author (this assistant) cannot count as the independent reader. The owner or another
person reads the document. If the owner supplies the PDF, the assistant may read it and transcribe A1-A7 as a *second* reader; that
adds a reader and is not independence from the code's author, so it does not replace the owner's reading.

### 3.5 Pass
Every A1-A6 row matches, every compared value agrees to the printed precision, A7 confirmed as different. One mismatch fails the check
and the definition is corrected in code before anything else (section 6).

## 4. Check B: a real signal

### 4.1 Recording
Receive-only, off the air, with an antenna on a receiver that is **not** the XTR (the NESDR or any other SDR on hand). This does not
involve the XTR's termination and is not the calibration; it checks the definition, not the XTR. Tune the LO to the centre of a known LTE
downlink channel, 2.048 MS/s, gain pinned, and capture at least 8 byte-exact 256 ms windows (the sequencer's window code, or any
tool that writes unsigned 8-bit I/Q). Keep the raw file and its SHA-256. Record the band and channel number, the nominal centre
frequency and its source, the operator if known, the time and the place.

### 4.2 Independent identity
Obtain the cell's physical cell identity (PCI) without this code: a phone's field-test / engineering mode while camped on the same
operator and channel, a published cell database for the site, or an open-source LTE cell searcher run on the same recording. PCI = 3 x
N_ID^(1) + N_ID^(2), so **PCI mod 3 = N_ID^(2)**, which A2 maps to the root: 0 -> 25, 1 -> 29, 2 -> 34.

### 4.3 Run
Analyse each window with `estimate_lte_carrier` (search range wide enough for the uncalibrated receiver). Record per window: detected or
refused (and the code), root, integer shift, vote share, CP coefficient, offset.

### 4.4 Pass (fixed before the recording is analysed)
- the cell is detected (not refused) in at least 6 of 8 windows;
- the reported root is the same in every detected window and equals the root for PCI mod 3 from 4.2;
- the integer shift is the same in every detected window and the offsets agree within the estimator's stated error;
- the offset is plausible for the receiver (within the 100 ppm accepted bound of its LO, and, if the receiver has been calibrated,
  consistent with that calibration).

Repeat on at least two cells on different channels, one with PCI mod 3 different from the other, so a definition that happens to detect
one root is not mistaken for correct. Failing to detect is **not** evidence the definition is wrong (weak signal, wrong channel, a TDD
cell), and a detection with the wrong root is. Record both.

### 4.5 What B does not show
It does not establish that offsets are accurate in ppm. That needs the calibration itself. It shows only that the code finds a real eNodeB's
PSS and identifies its N_ID^(2) the way the network does.

## 5. The nominal frequency (same discipline, separate record)
The calibration also relies on the channel-raster relation from an EARFCN to the carrier centre frequency (TS 36.101, clause 5.7.3 and the
operating-band table: F_DL = F_DL_low + 0.1 (N_DL - N_Offs-DL) MHz) and on the centre being the downlink carrier frequency. Read these in
the document, record band, F_DL_low and N_Offs-DL for each channel used, and recompute the centre. The same rules (document, not mirror;
not the code's author) apply. A wrong nominal frequency gives a confident wrong ppm, which is the main risk the calibration procedure names.
The eNodeB frequency-error requirement (TS 36.104; used as `reference_accuracy`) is a third figure of the same kind and has its own row in
the plan; confirm it by the same method. This procedure does not state its value.

## 6. Recording the result and setting the flag
1. Write `docs/521_LTE_PSS_CONFIRMATION_RECORD.md` from the template below, with the document hash, the transcription, the comparison
   table, the recording hash and the check B results, and each reader's name and date.
2. If anything failed: stop. Correct `pss_sequence` / the slot constants in `rf_lte_carrier_estimator.py`, rerun the tests, and repeat
   the failed checks. The plan's frozen hash includes the estimator source, so a plan frozen against the old code is refused and
   must be re-frozen.
3. If everything passed: a plan may be frozen with `estimator.pss_definition_confirmed = true`. The flag is a boolean and carries no
   evidence; to make it auditable, a follow-up change should also require the confirmation record's SHA-256 in the plan. Until then
   the plan note must cite the record by hash.
4. The flag covers this definition, this code version and the cells checked. It does not cover TDD, extended CP, or a different
   estimator.

### Record template
```
Document: TS 36.211 V__.__.__ (Rel-__)   URL:        downloaded:        SHA-256:
Reader 1 (name, date):                    Reader 2 (name, date):
A1 d_u(n) transcribed:
A2 table:           A3 mapping:          A4 subcarrier frequencies:
A5 CP lengths:      A6 timing:           A7 TDD placement:
Comparison (u, n, spec value, code value, agree?):
Check A result:  PASS / FAIL
Recording: band/channel:   centre Hz and source:   time/place:   file SHA-256:
Independent PCI and how obtained:      PCI mod 3 -> root:
Per-window results (detected/refused, root, shift, share, CP coeff, offset):
Second cell (different PCI mod 3): same fields
Check B result:  PASS / FAIL
Nominal-frequency check (section 5) result:
Flag set to true in plan (hash):        Limits of the confirmation:
```

## 7. What this procedure cannot do
It cannot be completed by the assistant alone: the normative text has to be read by a human (or supplied to the assistant as a document),
the recording needs an antenna and a receiver, and the independent cell identity needs a phone or another tool. It is a plan for those
steps and a record to fill in.
