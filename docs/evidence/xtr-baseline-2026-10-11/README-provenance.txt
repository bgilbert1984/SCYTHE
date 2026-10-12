XTR baseline -- bounded receiver characterization, not a catalogue run.

SCOPE

Authorized 2026-10-11 by operator chat decision: "This is a bounded
characterization measurement, not a catalogue run. It doesn't touch the
v5's 259 entries, doesn't claim catalogue status, and follows the same
terminated-input procedure as the v5 pre-catalogue windows. Authorised."
It supplies no catalogue evidence and does not advance any step of the
device-contact order gating first corpus-path RTL-SDR contact.

DEVICE

Nooelec SMArt XTR, Elonics E4000 tuner, SN 71874552, confirmed by the
rtl_sdr banner at tune time (not just requested):
  "Nooelec, SMArt XTR v5, SN: 71874552" / "Found Elonics E4000 tuner"
Site: 13601 Highway 99, Everett. Aluminum enclosure fitted (unlike the
other receiver's no-enclosure state).

NAMING COLLISION -- READ BEFORE GREPPING FOR "v5": the rtl_sdr banner
above calls this unit "SMArt XTR v5" -- that is Nooelec's own product
revision label, stamped by the vendor firmware string. It is unrelated to
"the v5" used elsewhere in this project's records (the other receiver,
reference oscillator 28.8 MHz, the one with the ~100.8 MHz feature and
the 259-entry catalogue). The two have nothing in common but the digit.

Reference oscillator: NOT DECLARED. Spec sheet claims 0.5 ppm TCXO; no
manufacturer-confirmed crystal frequency found. The unit was not opened,
so no crystal marking was read and 28.8 MHz (the other receiver's value)
is not assumed here. Unverified-spec status only.

Gain: 29.00 dB, confirmed by rtl_sdr banner at every tune ("Tuner gain
set to 29.00 dB"). Chosen as the nearest E4000 step to the other
receiver's 29.7 dB; the E4000's non-uniform step table (2.5 dB up to 24,
then 5, then 8) is why that table isn't carried over directly.

INPUT STATE

50 ohm load fitted before capture and held through the run -- terminated
input throughout, same procedure as the other receiver's pre-catalogue
windows. This measures the receiver's own noise floor, not an
over-the-air signal.

Load fitting was explicitly confirmed once, at 2026-10-12T02:24:15Z
(host UTC clock at logging time, ~6 min after final.json was written).
That confirmation covers the state between the three baseline captures
and the retune test below -- it does not by itself cover anything after
it. The retune capture at 02:46:00Z (see RETUNE TEST) declares the same
held 50 ohm termination, since no device-contact event occurred between
02:24 and the retune capture other than the retune capture itself, but
that continuation was not independently re-logged at its own timestamp.
No load-fit confirmation was logged after the retune capture completed;
treat the receiver's state past 02:46:00Z as undetermined in this
record until a separate check is logged, rather than assuming it is
idle or safe to unplug.

CAPTURE

rtl_sdr, async mode, 2,048,000 S/s, single epoch, 60 s target per
frequency, three centers -- 100, 400, 1500 MHz. All three completed to
the full 245,760,000 bytes (60.00 s at 2 B/sample) with 0 samples at
either rail (clip0, clip255 both zero in every r*.json).

Capture timeline (local PDT / UTC):
  f100.iq    captured 18:54:47 PDT (2026-10-12 01:54:47Z)
  f400.iq    captured 18:55:52 PDT (2026-10-12 01:55:52Z)
  f1500.iq   captured 18:57:00 PDT (2026-10-12 01:57:00Z)
  final.json written 19:18:47 PDT (2026-10-12 02:18:47Z)
  load fitting confirmed (baseline only, see INPUT STATE)
                             2026-10-12 02:24:15Z
  f1500r.iq  captured 19:46:00 PDT (2026-10-12 02:46:00Z) -- retune
             test, see RETUNE TEST; no separate load-fit confirmation
             logged for this capture or afterward

ANALYSIS

red.c: reads raw IQ, computes clip/mean/std over the full capture, and
an 8-segment (per-segment = capture/8) 4096-point Hann-windowed FFT power
spectrum -> r<freq>.json. post.py: per-bin dB, 129-bin running local
median, and a narrow-feature rule fixed before results were read -- a
bin needs >=6 dB over its local median in >=7 of 8 segments to count,
merging adjacent hits within one FFT bin spacing. Both run unmodified
against r100.json / r400.json / r1500.json; final.json is their
concatenated output, one object per center frequency.

RESULTS (from final.json, c = center MHz, fl = median floor dBFS/bin,
dc/dcx = DC-bin level / excess over its local median, nb = immediate
neighbor bins' dB, F = merged narrow features [offset_Hz, excess_dB,
hit_count/8, in_clean_span]):

  100 MHz:  floor -41.87 dBFS/bin, DC +12.85 dB over local median,
            mean/std I=127.389/0.875 Q=127.357/0.828.
            Features: +-1000 Hz at ~8.5 dB (DC-spike skirt, not treated
            as separate -- not independently retested).
            No feature anywhere else in-span, specifically none at the
            other receiver's ~100.8 MHz offset -- but that offset falls
            in the anti-alias roll-off region (in_clean_span=0 there),
            so this absence is a weaker statement than elsewhere.

  400 MHz:  floor -42.07 dBFS/bin, DC +13.19 dB over local median,
            mean/std I=127.389/0.863 Q=127.358/0.816.
            Features: +-1000 Hz at ~8.8 dB (DC-spike skirt only).

  1500 MHz: floor -42.31 dBFS/bin, DC +13.59 dB over local median,
            mean/std I=127.391/0.867 Q=127.359/0.816.
            Features: +-1000 Hz at ~9.8 dB (DC-spike skirt), and
            +2500 Hz at 6.5 dB, hit 8/8 segments, in_clean_span=1.
            CLASSIFIED by retune test below: receiver-internal,
            tuning-dependent (follows the LO).

RETUNE TEST (1500 MHz +2.5 kHz feature)

Same gain (29.00 dB), same 50 ohm terminated input, same sample rate and
sample count, retuned +50 kHz to 1500.05 MHz. If the feature is tied to
the synthesizer/mixer (receiver-internal), it stays at the same offset
from whatever center is tuned. If it is a fixed-absolute-frequency
source (external, or an internal spur unrelated to the LO), it should
instead appear near -47.5 kHz from the new center (-95 bins at 500
Hz/bin), since 1500.0025 MHz is now off-center by that much.

  f1500r.iq  captured 19:46:00 PDT (2026-10-12 02:46:00Z), 245,760,000
             bytes, 0 clipped, via rtl_sdr -f 1500050000 -s 2048000
             -g 29 -n 122880000.
  r1500r.json  produced by the unmodified red.c binary against f1500r.iq.
  post_retune.py  post.py's run() logic, parameterized to take any
             r*.json path directly (post.py itself hardcodes the
             r<int-MHz>.json naming, which 1500.05 doesn't fit).

RESULT: feature reappeared at +2500 Hz offset, 6.6 dB over local
median, 8/8 segments -- unchanged within measurement noise. It did not
move to the -95-bin location the fixed-frequency hypothesis predicts.

  SHA256(f1500r.iq)      = d4ae31f356d8da812b1a26678fc492aab1c04ba52f1013f584261facf30022f5
  SHA256(r1500r.json)    = a41cea11d93c892df6814678ec7a2a82caeeac9ac1ba51b1ae132086af539e1a
  SHA256(post_retune.py) = 69e591efe33fc1c2b95020adef6fe6996bc0ddf6c8dc9a9715e7060f13e684ff

VERDICT: the +2.5 kHz feature at 1500 MHz is a receiver-internal,
tuning-dependent artifact of the E4000's own synthesis at this center --
not an external signal, and not a fixed clock/EMI spur. Nothing reached
it to be external anyway: the input was 50 ohm terminated throughout
both captures. Raw f1500r.iq (245,760,000 bytes) is retained, with the
other three baseline captures, at
~/scythe-local-provenance/raw-iq/xtr-baseline-2026-10-11/ (outside
git, owner-only permissions) -- see FILES AND HASHES below for its
custody record.

DC offset at every center: about -0.11 LSB on I, -0.14 LSB on Q
(mean - 127.5).

FILES AND HASHES

  SHA256(final.json) = 12d67e8c8c11cc90e3e6432aaf148b29e6ab5737344d529ebd8c2378fcd7f02c
  SHA256(post.py)     = 89506e83259ffc331a0e31a851c8ff0d99fc610f803894c5ec39349208d15e2a
  SHA256(red.c)        = 200b6a26012d7743f40d535cfc6004206328e12d669e7ebffbd481ebc113d130

Not carried into this repo (raw captures are large and nothing here
needed re-derivation from them -- see "No raw IQ unless something looks
wrong" in the evidence pattern this follows).

RAW IQ RETENTION (post-migration)

All four raw captures (three baseline, plus the retune capture above)
are retained outside git at
~/scythe-local-provenance/raw-iq/xtr-baseline-2026-10-11/, owner-only
permissions (chmod 700 dir / 600 files). Each was verified against the
digest below both before the move (against the /tmp/xtr_base
originals) and after (against the retained copy, independently, before
the /tmp/xtr_base originals were deleted). The retained copy's own
MANIFEST.md and per-file SHA256 sidecars in that directory are the
custody reference; the digests here are the ones both verification
passes were checked against:

  SHA256(f100.iq)   = 4bb5a340417d2406f5c88e354d5c3bec2b54b9e3bb4ca6a5b5cfb53d15a4bd18
  SHA256(f400.iq)   = bb346a376eee9d12d55b1f62119fa520d0a8b41fb89322a295008765035a0c5b
  SHA256(f1500.iq)  = a2043691d2d7ea6dba099ea863b854c0e29b08e7105eea8a02445b28b6851d51
  SHA256(f1500r.iq) = d4ae31f356d8da812b1a26678fc492aab1c04ba52f1013f584261facf30022f5

Disposition: RETAINED_PENDING_MANIFEST_CONTROLLED_RETIREMENT. Not
scheduled for deletion; retiring this evidence set requires a future
explicit decision, not a timer or disk-space pressure.

The r100.json / r400.json / r1500.json intermediate FFT outputs (one
per baseline frequency, pre-dating final.json's merge) are not
separately retained -- they are reproducible from the retained raw
bytes plus red.c unmodified, and are not authoritative raw evidence.
They were left in /tmp/xtr_base as ordinary scratch; their eventual
removal is not a retirement of evidence and needs no manifest entry.
Hashes, for completeness only:

  SHA256(r100.json) = d02dc29b3474674277271ad0042d75e9a39971144a67d4d5e3d7f19fdc205f31
  SHA256(r400.json) = aec7bc351cbb66d594f39774214b1e8954143964a9aaca27cbbe39fc2669213f
  SHA256(r1500.json)= f7a7ab4bcfbebfbab7a763d4a32ca8a8fdec8e4e668c4e21734f4f213b9a384e

WHAT THIS RECORD IS NOT

Not a catalogue, not three epochs, not a frequency-accuracy claim (the
oscillator is undeclared), and not evidence toward entry 10 or the
corpus capture plan. It is a single-epoch, terminated-input
characterization of one receiver at one gain and three centers, kept so
a later catalogue-track decision about this receiver has a real baseline
to compare against instead of none.
