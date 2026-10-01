# Three Windows, One Tone: 100.8 MHz, Persistent Across Reconnect and Power Cycle

**Date:** September 30, 2026
**Author:** SCYTHE Core Engineering Team
**Category:** RF Capture, §5.21, Pre-Catalogue Evidence

Two things happened this week. The §5.21 catalogue runner merged to main as
PR #124 — 2472 tests green, governance check passed, squash-merged as
`5a425aca`. And the first live RF went through SCYTHE's measurement
machinery. Not as catalogue evidence. As the shakedown the machinery
deserved: three terminated-input windows, three escalating traumas to the USB
bus, and one tone that survived all of them.

## The runner lands, and refuses

PR #124 carried `rf_spur_catalogue_runner.py` and
`rf_spur_retention_estimator.py` onto main: the sequencer, the retention
estimator, the authorisation record, the protocol governance. The curated
suite ran 2472 tests at the branch tip with two host skips and zero
failures. The governance check passed on the merge commit.

The most important thing the runner does is refuse. Point it at a window
with no catalogue behind it and it answers `RUNNER_SPUR_CATALOGUE_ABSENT` —
no formal verdict, no manufactured declaration, no path from "I captured
something" to "the catalogue says." That refusal is correct behavior, and
everything below was produced under it. Nothing in this post is a catalogue
declaration. The three windows are classified `PRE_CATALOGUE_EVIDENCE`, and
the runner's refusal stands over all of them.

## Three windows, no antenna

The SMA-male 50 Ω termination load stayed fitted through all three
captures; the telescopic antenna stayed off. Each window is 120 seconds of
uint8 I/Q at 2.048 MS/s, 100 MHz LO, from the NESDR SMArt v5 (serial
14530058) on the SDR-side host:

- **E1 — INITIAL_SESSION**, site 12426. The dongle as found.
- **E2 — RECONNECT**, site 12502. Software USB detach/reattach between
  captures. Nothing else touched.
- **E3 — POWER_CYCLE**, site 12502. Physical unplug/replug of the dongle
  between captures.

The operator seed was verified independently before any of this:
BLAKE2s-8 of the declaration ASCII yields `68926329f9fff6c4`, i.e.
u64be `7535194158483371716` — matching the declaration exactly, with no
measurement entering the derivation. All three `.iq` files transferred
byte-exact (491,520,000 bytes each) with SHA-256 verified at both ends of
every hop, and they now rest on the analysis host alongside their sidecars
and checksum manifests.

## The tone

Every window shows the same empirical character: a thermal-like floor plus
one persistent unresolved narrow feature. The numbers, from averaged
periodograms over 2^18-sample blocks (7.8125 Hz per bin, 937 blocks per
window):

| epoch | floor | peak | excess | occupied width |
|-------|-------|------|--------|----------------|
| E1 | 68.62 dB | 100799968.8 Hz | +39.62 dB | 85.9 Hz |
| E2 | 68.76 dB | 100799968.8 Hz | +39.83 dB | 85.9 Hz |
| E3 | 68.99 dB | 100799968.8 Hz | +40.00 dB | 85.9 Hz |

The peak sits in the identical bin in all three epochs — no detected shift
at 7.8125 Hz resolution. The ±1 kHz cutout shapes cross-correlate at 0.998
or better between every pair, maximum at zero lag. Same bin, same shape.

Going in, the epoch-3 outcome had three branches: same bin and shape would
mean persistent across reconnect and power cycle; disappearance would mean
power-cycle-state dependent; movement would mean preserving both
frequencies and reading the direction. Branch one is what the data gave.
The feature is **persistent across reconnect and power cycle**.

Note the arithmetic, recorded without interpretation: 100.8 MHz is
3.5 × 28.8 MHz, and a reference comb is integer harmonics only — so this
tone can never be `CONSISTENT_WITH_INTERNAL_REFERENCE`. If the retune
experiment shows it receiver-relative, the investigation goes to
fractional/divider/PLL mixing products, not the comb.

## The temperature wrinkle

The feature's excess rises monotonically across the three epochs: +39.62,
+39.83, +40.00 dB. The floors creep too: 68.62, 68.76, 68.99 dB. Temperature
drift is the obvious suspect — the NESDR runs hot — and the numbers are
consistent with it, with one wrinkle worth naming: the floor rose +0.37 dB
while the feature rose +0.75 dB absolute. Pure common-mode gain drift would
leave the excess over the floor flat. It isn't flat, so temperature is
plausibly moving *both* the chain gain and whatever couples this spur in.

That is an observation, not an interpretation. The discriminators are
cheap: log USB flap timestamps against dongle uptime, put a thermometer
near the enclosure, and run the powered-hub test to separate port power
droop from the dongle's own thermals. Heat is one live hypothesis for the
flaps; the usbipd attach layer is another, and the 006→007 node hop during
re-attach is its signature. Both stories stay on the table until the data
picks one.

Operationally, the catalogue's bounded 120-second captures don't need
full-time streaming — rtl_tcp idling hot between captures is thermal cost
with no evidentiary return. Full-time operation (a public stream, an
ambient survey) will need heat management as a design input: airflow, a
powered hub, and a liveness watchdog for the silent-but-active wedge
state, so a 3 a.m. thermal flap becomes a logged event instead of a lost
night.

## What it doesn't prove

No origin is assigned to the tone. Internal product and ambient ingress
are both still permitted by the evidence, and the prose stays inside that
discipline deliberately: "persistent across reconnect" is an empirical
description, never the catalogue stability class. The retune experiment —
same terminated input, different LO — is the discriminator, and it hasn't
run yet.

Two gates stand ahead of the real catalogue run. The reference gate is
undeclared and iron-clad: nominal-reference authority needs unit-specific
evidence (a PCB/TCXO marking or a direct measurement of this exact unit),
and the ±0.5 ppm figure stays or goes on the strength of a unit
calibration, not a datasheet. Until that gate clears, the K=64/R=8 capture
schedule is not authorized to execute. The machine that learned to refuse
will keep refusing, and that's the whole point.

---

*The three windows, their sidecars, and the checksum manifests are archived
on the analysis host. The comparison analyzer is a streaming
memmap implementation — the load-whole-file version was OOM-killed at
three full-window FFTs, which is its own small lesson about this
hardware.*
