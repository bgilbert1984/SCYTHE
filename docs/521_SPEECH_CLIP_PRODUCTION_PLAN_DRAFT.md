# §5.21 plan: producing speech clips from the antenna-on observation IQ (DRAFT)

**Status: DRAFT plan.** Nothing in it is built except the persistence pass in slice 2, which is included here with its results. It changes no frozen file, test or behaviour, and it authorizes nothing: every capture
or publication step below needs its own owner confirmation, in the same form as the existing launch authorizations. Not legal advice; the legal section records the owner's working line and what counsel should confirm.

## 1. Why, and what the owner wants to find
The owner wants to find persistent, always-on, rotating transmissions that are "gumming up the air" (for example an unlicensed or misused amateur-style transmitter). Raw IQ cannot say whether a transmitter is licensed, who runs it,
or where it is. It can say which narrow lines are persistent, whether they are real RF or the receiver's own, and (with demodulation) whether a candidate carries voice. This plan is the route from one to the other, in the order
that spends the least privacy and legal risk first.

## 2. What exists and what it can and cannot support
- The dataset holds 8 antenna-on observation runs (192 visits each) at five venues. Each visit is one 2.048 s window of 2.048 MS/s uint8 IQ at 29.0 dB gain, tuned at 400-470 MHz. There is no demodulated audio anywhere.
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
| 2 | transcripts, only for broadcast or amateur-band candidates, human-reviewed and with call signs and personal data removed | dataset, if the owner confirms per set | only after review and counsel |
| 3 | audio and transcripts of everything else (land-mobile, unknown, digital) | local only, deleted after triage | never |

Digital voice (DMR, P25, NXDN and similar) is detected as "digital voice, not decoded" and goes no further. Encrypted or scrambled traffic is never decoded.

## 4. Pipeline
Each stage writes a hash-pinned intermediate and a small report, and nothing runs ahead of its authorization.

1. **Candidate selection.** From the persistence pass (slice 2): RF-fixed lines, persistence and cross-site behaviour, with receiver-reference harmonics removed. Output: a frequency list the owner can edit.
2. **Channelization.** Shift each candidate to baseband, filter to the channel (12.5 or 25 kHz for land-mobile style; 200 kHz for broadcast FM), decimate to a low rate. Deterministic and tested on synthetic carriers.
3. **Demodulation.** Narrowband FM by default (the usual UHF voice mode), AM as a second try. Report deviation, SNR and whether the signal is analog voice-like, a carrier with no modulation, or digital. Nothing else is demodulated.
4. **Audio conditioning.** De-emphasis and a band-pass for voice, resample to 16 kHz, segment by squelch.
5. **Speech detection.** A voice-activity detector (for example Silero VAD or webrtcvad, pinned by version and hash) gives speech segments and a speech-present fraction per candidate. Segments under about 0.5 s do not count.
6. **Language ID and transcription (tiers 2 only).** A local Whisper-class model (for example faster-whisper or whisper.cpp, pinned model hash), run offline. Keep a segment only if language ID is English above a threshold and the model's own
   confidence (average log-probability, no-speech probability) clears a threshold; drop everything else. These models hallucinate fluent text on noise, so VAD and SNR gates come first, repeated or boilerplate output is rejected, and a
   transcript is never published without a human reading it against the audio.
7. **Review and redaction.** Human review; remove call signs, names, addresses, phone numbers and plate numbers; record the reviewer and the version hashes.
8. **Publication artifacts.** `clips.parquet` (tier 1 rows) and, where tier 2 is confirmed, a `transcripts.parquet` with set, visit, frequency, start offset, duration, language, review state and text. Audio files are not published.

## 5. Slices
| slice | content | needs owner confirmation |
|---|---|---|
| 1 | this plan | no (a PR) |
| 2 | persistence pass over the 8 runs: `scripts/s521_persistence/` and the results below | no (local analysis of already-published data) |
| 3 | channelizer, demodulator and VAD, tested on synthetic IQ and on a known source (below) | no |
| 4 | a **new narrowband dwell-capture declaration**: chosen frequencies, dwell, gain, site, bands, tool versions, hash | yes, naming the digest, as for every run |
| 5 | local transcription of slice 4 captures, tier 3 handling | yes, per set |
| 6 | publish tier 0 and 1 tables; tier 2 only after review and counsel | yes |

## 6. Validation before anything unknown is run
- Prove the chain end to end on sources whose content is public: a broadcast FM station (the bridge already sees two near 99.9 and 100.7 MHz) and a NOAA weather radio channel (162.4-162.55 MHz, clean English). The pipeline must recover
  the speech there first.
- A negative control: windows with no signal must produce no speech, and the language gate must reject non-English test audio.
- Use the owner's own transmissions (a licensed amateur or FRS handset used by the owner) for any test that needs a UHF narrowband voice signal.

## 7. What this plan does not do
- It does not decide that a transmitter is unlicensed. That needs the FCC license database by frequency and location and a bearing or a second site, none of which the IQ supplies.
- It does not locate anything. One fixed antenna gives no direction. Venue-to-venue differences in which lines appear are a hint about locality, not a position.
- It does not publish content from private communications, and it does not decode digital or encrypted voice.

## 8. Slice 2 result: the persistence pass (observation only)
`scripts/s521_persistence/persistence_pass.py` (detect, aggregate, classify; seven unit tests) processed all 1,536 windows of the 8 runs with 0 read errors. A line is a narrow peak at least 10 dB over a running-median floor, at most
6 kHz wide, outside the DC guard and the canary lines at plus and minus 255,996 Hz and inside +/-920 kHz of the centre; visits above the frozen clipping limit are excluded. A line is **RF-fixed** only if the visits that saw it had centres
at least 50 kHz apart, and **persistent** if it appears in at least 60% of the visits that cover its 1 kHz bin (at least three). Full results: `scripts/s521_persistence/results_20261126.json`.

Headline numbers:
- 160 distinct RF-fixed persistent frequencies across the 8 runs; 134 of them appear in only one run and are not treated as persistent.
- **403.200 MHz is the receiver, not the air.** It is present in all 8 runs at all five venues, persistence 0.78-1.00 and up to 32 dB SNR, and it is exactly 14 x 28.8 MHz, the receiver's reference-clock frequency (the only reference harmonic in the
  list; 0 are expected by chance). It is classed as an instrument line and excluded from candidates.
- A second instrument-like pattern, counted as per-run line detections: 13 fall within 3 kHz of a whole MHz (about 1 expected by chance) and 13 within 2 kHz of a 3.2 MHz multiple (0.2 expected; 3.2 MHz is 28.8 MHz / 9; 8 of those 13 are the 403.200 line), the others including 416.000 and 448.000 MHz. Lines at exact whole-MHz
  frequencies from a TCXO-locked receiver look more like digital clock harmonics (receiver, host or something nearby) than land-mobile channels. Not resolved, and reported as unresolved.
- Beyond those, 20 lines are persistent in at least two runs at one venue and quiet at the other covered venues (19 at 13601 Highway 99, which has three runs; one at the Mukilteo 12502 address, which has two). Venues with more runs
  have more chances to show persistence, so this count is partly an artefact of the number of runs.
- Three lines appear at several venues but not all: **452.050 MHz** (5 of 7 covering runs, three venues, up to 31 dB), **464.906 MHz** (4 of 7, three venues) and **448.000 MHz** (3 of 8). A wide-area source, a harmonic product or a
  repeater pair would all do this; the pass cannot tell which.
- 1400 6th Ave contributes almost nothing (2 lines, 153 usable visits): the site overloaded the receiver, which is not evidence that the air there is quiet.
- **Not assessed:** rotating or hopping transmissions (a line that moves between visits), modulation class, bandwidth and duty cycle. They are the next analysis, before any demodulation.

Band context, US allocations in broad terms and to be verified against 47 CFR 2.106 (context only, not identification): about 400.15-406 MHz meteorological aids and satellite; 406.0-406.1 MHz distress beacons; 406.1-420 MHz federal
government; 420-450 MHz amateur 70 cm (shared with federal radiolocation); 450-470 MHz private land mobile (business, public safety), with GMRS/FRS around 462-467 MHz.

Classified frequencies (excluding single-run lines):

| MHz | class | runs detecting / covering | venues detecting | max mean SNR (dB) |
|---|---|---|---|---|
| 401.2380 | site local persistent | 2/8 | hwy99 | 12.7 |
| 403.2000 | instrument reference harmonic | 8/8 | harbourpointe, hwy99, muk12426, muk12502, sea6thave | 32.0 |
| 404.9920 | site local persistent | 2/7 | hwy99 | 15.0 |
| 405.0020 | site local persistent | 2/7 | hwy99 | 11.7 |
| 408.0050 | site local persistent | 2/7 | hwy99 | 18.3 |
| 410.6560 | site local persistent | 2/8 | hwy99 | 13.3 |
| 410.6720 | site local persistent | 2/8 | hwy99 | 18.9 |
| 425.0095 | multi site partial | 2/7 | harbourpointe, muk12426 | 12.6 |
| 425.0250 | site local persistent | 2/7 | hwy99 | 14.8 |
| 426.0020 | site local persistent | 2/8 | hwy99 | 11.4 |
| 431.5855 | site local persistent | 2/7 | hwy99 | 11.3 |
| 432.0660 | site local persistent | 2/8 | hwy99 | 18.3 |
| 438.7550 | site local persistent | 2/7 | hwy99 | 21.7 |
| 442.4860 | site local persistent | 2/8 | hwy99 | 13.5 |
| 445.0495 | site local persistent | 2/8 | hwy99 | 17.2 |
| 445.0550 | site local persistent | 3/8 | hwy99 | 15.9 |
| 447.9170 | site local persistent | 2/8 | hwy99 | 12.2 |
| 448.0003 | multi site partial | 3/8 | hwy99, muk12426 | 23.7 |
| 448.0830 | site local persistent | 2/8 | hwy99 | 12.7 |
| 449.9970 | multi run single site others unclear | 2/8 | hwy99 | 13.7 |
| 450.0020 | site local persistent | 3/8 | hwy99 | 14.2 |
| 450.0610 | site local persistent | 2/8 | muk12502 | 14.4 |
| 452.0500 | multi site partial | 5/7 | hwy99, muk12426, muk12502 | 31.1 |
| 452.4340 | site local persistent | 2/8 | hwy99 | 20.2 |
| 456.0050 | site local persistent | 2/8 | hwy99 | 16.2 |
| 464.9060 | multi site partial | 4/7 | hwy99, muk12426, muk12502 | 20.8 |

## 9. Open questions for the owner
1. Is a new narrowband dwell capture wanted at all, given that the existing windows cannot support transcripts? If so, at which candidate frequencies and for how long?
2. Tier 2 (transcripts of broadcast and amateur-band content): wanted in the public dataset, or kept local?
3. Counsel review before any tier 2 publication: who, and by when?
4. Should rotating/hopping detection come before demodulation work (recommended)?
