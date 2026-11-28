# §5.21 plan: spectral event extraction, with optional speech-clip production (DRAFT, revised after the PR #516 review)

**Status: DRAFT plan, revised.** Nothing in it is built except the persistence pass (slice 2) and its corrections (slices 2A-2C, section 10), which the review of PR #516 required and which this revision includes with their results. It changes no frozen file, test or behaviour, and it authorizes nothing: every capture
or publication step below needs its own owner confirmation, in the same form as the existing launch authorizations. Not legal advice; the legal section records the owner's working line and what counsel should confirm.

## 1. Why, and what the owner wants to find
The owner wants to find persistent, always-on, rotating transmissions that are "gumming up the air" (for example an unlicensed or misused amateur-style transmitter). Raw IQ cannot say whether a transmitter is licensed, who runs it,
or where it is. It can say which narrow lines are persistent, whether they are real RF or the receiver's own, and (with demodulation) whether a candidate carries voice. This plan is the route from one to the other, in the order
that spends the least privacy and legal risk first.

**Framing (after the review).** The driving purpose is spectral event extraction: raw IQ, spectral analysis, instrument rejection, spectral observations, then persistence, bandwidth and modulation estimates feeding a temporal event model and the SCYTHE observation set. That supports interference characterization, occupancy, receiver-health diagnostics and anomaly detection on its own. Speech detection and transcription are an optional, gated downstream stage, not the point of the engine. Observations, inferences and correlations stay separate, as in the ARR work.

## 2. What exists and what it can and cannot support
- The dataset holds 9 antenna-on observation runs (192 visits each, as of 2026-11-27) at five venues. Each visit is one 2.048 s window of 2.048 MS/s uint8 IQ at 29.0 dB gain, tuned at 400-470 MHz. There is no demodulated audio anywhere.
- Each tuning is visited three times a few minutes apart, at retune offsets of -100, +200 and -50 kHz. That lets an RF line (fixed in absolute frequency) be told from a receiver spur (fixed relative to the centre).
- **2.048 s is too short to transcribe.** A window gives at best a fragment of a transmission, usually a part of a sentence, and a window only contains a transmission if one happened to be on air. Voice-activity detection and
  modulation checks are possible on these windows; reliable transcription is not. Real transcripts need a longer dwell on a chosen frequency: a **new capture**, outside the frozen scope and its evidence class, with its own declaration
  and authorization (slice 4).
- The receiver was the same XTR throughout and no load-fitted check followed any of these runs, so the receiver's own contribution to every line is separated only by the cross-site and cross-offset tests below.

## 3. Legal and publication line (owner's working line; to be confirmed by counsel before any publication)
- **47 U.S.C. §605(a)** restricts divulging or publishing the existence, contents, substance, purport, effect or meaning of radio communications, with exceptions that include broadcast transmissions and amateur and citizens-band
  radio. Transcripts of private land-mobile traffic (business, public safety and similar, roughly 450-470 MHz, and GMRS/FRS) are therefore the content this plan will not publish.
- Interception rules (18 U.S.C. §2511) and state recording law also apply. Washington's all-party-consent rule for recording private conversations (RCW 9.73.030) is the one most likely to matter for transcripts of speech picked up
  at a fixed address; whether it reaches over-the-air radio traffic is for counsel.
- Transcribed speech can carry names, addresses, call signs and other personal data, and the dataset already publishes the venue addresses of the captures. Publication must not join the two.
- The dataset is public and CC0, and its README says that no emitters are identified and no locations are given or implied. Nothing here changes that.

Working line, by tier:

| tier | what | where it lives | publishable |
|---|---|---|---|
| 0 | persistence metadata: frequency, persistence, SNR, RF-fixed or instrument, band context | dataset | yes (already in slice 2) |
| 1 | per-candidate audio features: modulation class, speech-present fraction, language ID, durations | dataset, as a metadata table | yes, no audio and no text |
| 2 | transcripts, only for candidates that have an authorization record (below), human-reviewed and with call signs and personal data removed | dataset, if the owner confirms per set | only after review and counsel |
| 3 | audio of any candidate without an authorization record (land-mobile, unknown, digital) | processed in memory to tier-1 features and deleted; never transcribed | never |

Digital voice (DMR, P25, NXDN and similar) is detected as "digital voice, not decoded" and goes no further. Encrypted or scrambled traffic is never decoded.

**Fail-closed authorization.** Content-level processing (keeping demodulated audio beyond the in-memory feature step, any speech output beyond a speech-present fraction, language ID, transcription) runs only for a candidate that has an authorization record naming the frequency, the capture and time window, the claimed class (broadcast, amateur or other) and the evidence for that class, confirmed by the owner in the same form as the run authorizations. Band membership alone is never enough: amateur allocations are shared with federal radiolocation, and nothing in the IQ says which user is on a channel. With no record, processing stops at tier-1 features. This resolves the earlier wording in which tier 3 held transcripts while step 6 restricted transcription to tier 2: nothing is transcribed without a record, and what is not transcribed is not retained.

## 4. Pipeline
Each stage writes a hash-pinned intermediate and a small report, and nothing runs ahead of its authorization.

1. **Candidate selection.** From the persistence pass (slice 2): RF-fixed lines with their persistence and cross-site behaviour; reference-harmonic candidates and lines consistent with internal mixing are set aside, not discarded, and unobservable frequencies are reported as such. Output: a frequency list the owner can edit.
2. **Channelization.** Shift each candidate to baseband, filter to the channel (12.5 or 25 kHz for land-mobile style; 200 kHz for broadcast FM), decimate to a low rate. Deterministic and tested on synthetic carriers.
3. **Demodulation.** Narrowband FM by default (the usual UHF voice mode), AM as a second try. Report deviation, SNR and whether the signal is analog voice-like, a carrier with no modulation, or digital. Nothing else is demodulated.
4. **Audio conditioning.** De-emphasis and a band-pass for voice, resample to 16 kHz, segment by squelch.
5. **Speech detection.** A voice-activity detector (for example Silero VAD or webrtcvad, pinned by version and hash) gives speech segments and a speech-present fraction per candidate. Segments under about 0.5 s do not count.
6. **Language ID and transcription (candidates with an authorization record only).** A local Whisper-class model (for example faster-whisper or whisper.cpp, pinned model hash), run offline. Keep a segment only if language ID is English above a threshold and the model's own
   confidence (average log-probability, no-speech probability) clears a threshold; drop everything else. These models hallucinate fluent text on noise, so VAD and SNR gates come first, repeated or boilerplate output is rejected, and a
   transcript is never published without a human reading it against the audio.
7. **Review and redaction.** Human review; remove call signs, names, addresses, phone numbers and plate numbers; record the reviewer and the version hashes.
8. **Publication artifacts.** `clips.parquet` (tier 1 rows) and, where tier 2 is confirmed, a `transcripts.parquet` with set, visit, frequency, start offset, duration, language, review state and text. Audio files are not published.

## 5. Slices
| slice | content | needs owner confirmation |
|---|---|---|
| 1 | this plan (merged in #516) | no |
| 2 | persistence pass (merged in #516; its first results are superseded) | no |
| 2A | coverage and classification corrections: one eligibility mask, one RF-fixed rule across runs and sites, cautious instrument vocabulary | no (done in this revision) |
| 2B | integrity and reproducibility: size and sha256 checks, pinned dataset revision, isolated cache, provenance on every result, completeness gate | no (done in this revision) |
| 2C | reprocess the nine runs with the corrected script (run on dedirock) | no (done in this revision) |
| 3A | channelizer, demodulator and VAD, tested on synthetic IQ | no |
| 3B | validation against known public or controlled sources (below) | no |
| 4 | a **new narrowband dwell-capture declaration** (frequencies, dwell, gain, site, bands, tool versions, hash). **Held**: only if 3B passes and a candidate justifies it | yes, naming the digest, as for every run |
| 5 | local transcription of slice 4 captures, for candidates with an authorization record only | yes, per candidate |
| 6 | publish tier 0 and 1 tables; tier 2 only after review and counsel | yes |
| 7 | optional: a terminated-input capture near 403.2 MHz to test the instrument-origin hypothesis (section 8) | no new authorization class: a load-fitted canary-style set |

## 6. Validation before anything unknown is run
- Prove the chain end to end on sources whose content is public: a broadcast FM station (the bridge already sees two near 99.9 and 100.7 MHz) and a NOAA weather radio channel (162.4-162.55 MHz, clean English). The pipeline must recover
  the speech there first.
- A negative control: windows with no signal must produce no speech, and the language gate must reject non-English test audio.
- Use the owner's own transmissions (a licensed amateur or FRS handset used by the owner) for any test that needs a UHF narrowband voice signal.

## 7. What this plan does not do
- It does not decide that a transmitter is unlicensed. That needs the FCC license database by frequency and location and a bearing or a second site, none of which the IQ supplies.
- It does not locate anything. One fixed antenna gives no direction. Venue-to-venue differences in which lines appear are a hint about locality, not a position.
- It does not publish content from private communications, and it does not decode digital or encrypted voice.

## 8. Slices 2A-2C result: the persistence pass, corrected (observation only)
`scripts/s521_persistence/persistence_pass.py` (detect, aggregate, classify; 33 unit tests) processed all 1,728 windows of the 9 runs at dataset revision `6426d013b1282882a87d860cc1a438e61619ddb5` with 0 errors, on dedirock. Every window was checked
to be exactly 8,388,608 bytes and to match the sha256 in `metadata/captures.parquet`; remote reads used an isolated temporary cache. The line detector is unchanged: the eight original runs reproduce the first pass's per-run line-detection counts exactly.
Full tables with their provenance block: `scripts/s521_persistence/results_corrected_20261128_all9_runs.json` (all 9 runs) and `..._original8_runs.json` (the original 8, for comparison). The first results file (`results_20261126.json`) is marked superseded.

**Rules.** A line is a narrow peak at least 10 dB over a running-median floor, at most 6 kHz wide, inside +/-920 kHz of the centre and outside the DC guard and the canary lines at plus and minus 255,996 Hz; visits above the frozen clipping limit
are excluded. That one eligibility mask is used both to find lines and to count the visits that can observe a 1 kHz bin, so a bin a visit is blind to is not a non-detection, and a bin observable from fewer than three visits is UNOBSERVABLE. A run **detects**
a frequency only if it is observed, persistent (at least 60% of observing visits) and RF-fixed (seen from visits whose centres differ by at least 50 kHz); the same rule is applied in every run and at every site. 83% of 400-470 MHz is observable in a full run
(81% on 2026-11-26 at Highway 99 and 67% at 1400 6th Ave, where 39 clipped visits were excluded).

**Effect of the corrections on the original 8 runs.** Non-single-run classifications fall from 26 to 23 and distinct RF-fixed frequencies rise from 160 to 167. Site-local persistent lines go from 20 to 18 and multi-site partial from 4 to 3. Three entries
dropped out and none was added: 425.0095 MHz, 425.0250 MHz and 448.0830 MHz each rested on two eligible visits (a third visit of that tuning was blind there; the old denominator counted it), below the three needed. 403.200 MHz is relabelled (next point).

**Results for all 9 runs.** 169 distinct RF-fixed frequencies; 143 appear in one run only and are not treated as persistent. The other 26: 19 site-local persistent (18 at hwy99, 1 at muk12502; Highway 99 has four runs, so counts partly reflect the number of runs), 5 multi-site partial, 1 consistent with internal mixing and 1 multi-run single-site with the others unclear.

- **403.200 MHz: consistent with internal mixing, not confirmed.** It is RF-fixed and present in all 9 runs at all five venues, up to 33 dB, and equals 14 x 28.8 MHz, the receiver's reference-clock frequency. That is a strong instrument-origin hypothesis. It is not confirmed: no terminated-input measurement covers it
  (the 2026-11-27 load-fitted set's lowest tuning reaches only about 402.5 MHz and its next starts near 405.9 MHz). The classifier cannot output a confirmed origin; INSTRUMENT_ORIGIN_CONFIRMED and EXTERNAL_RF_CONFIRMED are reserved for a controlled discrimination test or independent evidence of external origin.
- **A clock-like comb, as a hypothesis.** Of the 26 persistent lines, three sit within 2 kHz of a multiple of 4.8 MHz (28.8 MHz / 6): 403.200, 417.600 and 422.400 MHz; three of a multiple of 3.2 MHz (28.8 MHz / 9): 403.200, 422.400 and 448.000 MHz; and four of a whole MHz: 405.002, 426.002, 448.000 and 450.002 MHz.
  Chance would give well under one in each case. A clock-derived comb from the receiver, the host or something nearby fits, and the pass cannot separate those.
- **452.050 and 464.906 MHz** appear at three venues but not all (452.050: 6 of 8 observing runs, up to 38 dB; 464.906: 4 of 8). Neither is on the clock combs above. A wide-area source, a harmonic product or a repeater pair would all do this; the pass cannot say which.
- **Against the load-fitted baseline of 2026-11-27** (`hwy99_load_20261127`): nine lines from the first pass fell inside that set's coverage and all nine were absent with the load fitted (0.1-0.4 dB against 11-17 dB antenna-on); seven of them remain classified here (401.238, 426.002, 445.050, 445.055, 449.997, 450.002 and 450.061 MHz). So they enter through the antenna side and are not the receiver's own,
  which still does not say whether the source is a transmitter or local electronics.
- **Not assessed:** rotating or hopping transmissions, modulation class, bandwidth and duty cycle.

Band context, US allocations in broad terms and to be verified against 47 CFR 2.106 (context only, not identification): about 400.15-406 MHz meteorological aids and satellite; 406.0-406.1 MHz distress beacons; 406.1-420 MHz federal government; 420-450 MHz amateur 70 cm (shared with federal radiolocation); 450-470 MHz private land mobile (business, public safety), with GMRS/FRS around 462-467 MHz.

Persistent lines, all 9 runs (single-run lines are in the results file):

| MHz | class | runs detecting / observing | venues detecting | max mean SNR (dB) |
|---|---|---|---|---|
| 401.2380 | site local persistent | 2/9 | hwy99 | 12.7 |
| 403.2000 | consistent with internal mixing | 9/9 | harbourpointe, hwy99, muk12426, muk12502, sea6thave | 33.1 |
| 404.9920 | site local persistent | 2/8 | hwy99 | 15.0 |
| 405.0020 | site local persistent | 2/8 | hwy99 | 11.7 |
| 408.0050 | site local persistent | 2/8 | hwy99 | 18.3 |
| 410.6560 | site local persistent | 2/9 | hwy99 | 13.3 |
| 410.6720 | site local persistent | 2/9 | hwy99 | 18.7 |
| 417.6000 | multi site partial | 2/8 | hwy99, muk12502 | 12.7 |
| 422.4000 | multi site partial | 2/9 | hwy99, muk12502 | 11.2 |
| 426.0020 | site local persistent | 2/9 | hwy99 | 11.4 |
| 431.5855 | site local persistent | 2/8 | hwy99 | 11.3 |
| 432.0660 | site local persistent | 2/9 | hwy99 | 18.3 |
| 438.7550 | site local persistent | 2/8 | hwy99 | 21.7 |
| 442.4860 | site local persistent | 2/9 | hwy99 | 13.5 |
| 445.0495 | site local persistent | 2/9 | hwy99 | 17.2 |
| 445.0550 | site local persistent | 3/9 | hwy99 | 15.9 |
| 447.9170 | site local persistent | 2/9 | hwy99 | 12.2 |
| 448.0003 | multi site partial | 3/9 | hwy99, muk12426 | 23.7 |
| 449.9970 | multi run single site others unclear | 2/9 | hwy99 | 13.7 |
| 450.0020 | site local persistent | 3/9 | hwy99 | 14.2 |
| 450.0610 | site local persistent | 2/9 | muk12502 | 14.4 |
| 452.0500 | multi site partial | 6/8 | hwy99, muk12426, muk12502 | 37.8 |
| 452.4340 | site local persistent | 2/9 | hwy99 | 20.2 |
| 454.0560 | site local persistent | 2/9 | hwy99 | 20.8 |
| 456.0050 | site local persistent | 2/9 | hwy99 | 16.2 |
| 464.9060 | multi site partial | 4/8 | hwy99, muk12426, muk12502 | 20.8 |

## 9. Open questions for the owner
1. Is a new narrowband dwell capture wanted at all, given that the existing windows cannot support transcripts? If so, at which candidate frequencies and for how long?
2. Tier 2 (transcripts of broadcast and amateur-band content): wanted in the public dataset, or kept local?
3. Counsel review before any tier 2 publication: who, and by when?
4. Should rotating/hopping detection come before demodulation work (recommended)?
5. Is a terminated-input capture near 403.2 MHz (a few minutes with the load fitted) wanted to test the instrument-origin hypothesis?

## 10. Corrections after the review of PR #516
| ID | finding | status | what changed |
|---|---|---|---|
| F-01 | persistence denominator counted frequencies the detector cannot observe | fixed | one `eligible_offsets` mask for detection and coverage; blind bins are not non-detections; fewer than three observing visits is UNOBSERVABLE |
| F-02 | cross-site step bypassed the RF-fixed rule and used 0.5 instead of 0.6 | fixed | one per-run rule (observed, persistence >= 0.6, RF-fixed) used in every run and site; the full classification function is now tested |
| F-03 | the 403.200 MHz attribution was too definitive | fixed | REFERENCE_HARMONIC_CANDIDATE, CONSISTENT_WITH_INTERNAL_MIXING; the two confirmed classes are never emitted |
| F-04 | no input validation, hash check, revision pin or provenance; partial results | fixed | exact window size, sha256 against the metadata, pinned revision, provenance block on every file, skip only on matching provenance, completeness gate, full table committed |
| F-05 | deleting shared cache blobs | fixed | isolated temporary cache per read; the shared Hugging Face cache is never touched (no dangling links were found from the first pass) |
| F-06 | three multi-site lines named where the results had four; tier 3 versus step 6 | fixed | section 8 is generated from the results file; tier 3 and step 6 reconciled; fail-closed authorization added |

