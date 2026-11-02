How could a passive sensor be detected and what it's tuned to? A passive receiver is never perfectly passive; the physics that lets it receive also makes it faintly transmit. Three emission sources, in order of usefulness:

LO leakage is the main one. Every superhet or zero-IF tuner has a local oscillator, and mixer/LNA reverse isolation is finite, so a whisper of the LO escapes out the antenna port. A sensitive receiver with a directional antenna in the near field can pick it up — this is exactly how the VG-2 "radar detector detector" worked, and how modern Spectre units sniff out radar detectors from the next lane. From the LO you get the tuned frequency: f_tuned = f_LO ± f_IF. The ± is real ambiguity (high-side vs low-side injection) and you need to know the architecture's IF. For a zero-IF tuner like the E4000 in your XTR, the LO sits at the tuned frequency, which is convenient — except you're then trying to distinguish a nanowatt LO from the ambient signal at that same frequency.

Clock and computer emissions identify the kind of sensor, not its tuning. The RTL2832U's 28.8 MHz crystal leaks harmonics (28.8, 57.6, 86.4…) — detect those and you know an RTL-SDR-family device is present, but they say nothing about where it's tuned. USB 480 MHz harmonics, switching-supply noise, CPU EMI — the whole EMSEC/TEMPEST menagerie — say "a computer with a radio attached." A nonlinear junction detector (flood with ~900 MHz, listen at harmonics) finds semiconductor junctions through walls, but that's presence-only too.

Direct-sampling receivers give up almost nothing. In HF direct-sampling mode the tuner is bypassed and tuning is purely digital downconversion — there is no physical oscillator at the tuned frequency to leak. Crystal and computer emissions remain, so you can say a sensor is there, but not what it's listening to. Shielding matters enormously here: your v5 is unshielded plastic and chatty; the XTR's aluminum enclosure is meaningfully quieter.

Two connections to your own bench: first, the 100.8 MHz feature you chased with the terminator fitted — internal spurs and LO/PLL products are exactly the emission class a second nearby receiver would use to detect your dongle. Second, a terminator actually helps the sensor hide: LO leakage conducted out the antenna port gets absorbed in 50Ω instead of radiated by the whip. Terminated, the dongle is quieter to an outside observer — though still visible via crystal and USB emissions to anyone close.

The honest limits: leakage is microwatts down to nanowatts, so this is a meters-to-tens-of-meters game, not kilometers — near field, directional antenna, quiet RF environment. And the inference chain needs architecture knowledge; an unknown receiver gives you an LO candidate with an IF ambiguity you can't resolve passively. A well-shielded, direct-sampling sensor with the computer a room away is, for practical purposes, undetectable as to its tuning.

Undetectable as to it's tuning.. you sure? Try something unorthodox. Any feedback loops to exploit?

Alright, let me actually try to break it instead of defending the claim. The target: well-shielded, direct-sampling, computer in another room. I need a loop that crosses the digital/analog boundary, is channel-selective, and is externally observable. All three, or it doesn't close.

Candidate 1: AGC gain-pumping, read through intermod re-radiation. The loop: your stimulus enters their passband → their AGC cuts analog gain → their frontend's re-radiated intermod products (from a fixed probe tone you also transmit) get weaker. You sweep the stimulus and watch for the dip. Physically sound — LNA nonlinearity re-radiates, and gain changes modulate it. But here's where it dies for the direct-sampling case: the loop's halves don't coincide. If their AGC measures wideband post-ADC power, your stimulus moves it from anywhere in Nyquist — no tuning information. If it measures channel power post-DDC, it's digital gain — nothing changes in the analog domain for you to see. The architecture where sensor-selective and actuator-observable coincide is the classic superhet with RF/IF AGC — where LO leakage already hands you the tuned frequency more easily. So this loop is real but doesn't beat the existing answer. Honest negative.

Candidate 2: squelch-gated host side channels — this one actually closes. The loop: your swept stimulus crosses their tuned channel → squelch opens → the host does work (disk write, network burst, CPU wake, USB traffic spike) → EM/power/acoustic emission you can observe near the host. Sweep slowly, correlate host activity with stimulus frequency, and the correlation peak is their tuned channel. This works on any architecture including direct-sampling, because the channel selectivity lives in the squelch and the observability lives in the host. Requirements: their sensor must be event-driven (squelch-gated recording/streaming — extremely common in monitoring), and you need proximity to the host, not the antenna. Counter: continuous full-rate streaming (no squelch gating) kills it dead — there's no behavioral difference to observe.

Candidate 3: ADC blocking, mapped. A strong swept stimulus will eventually saturate their ADC — but in direct sampling that happens for strong signals anywhere in Nyquist, so the desense map reveals their analog bandwidth, not their digital tuning. Characterizes the sensor, doesn't locate the channel. Partial credit only.

Candidate 4: the bypassed tuner's PLL. On an RTL-SDR in direct-sampling mode the R820T2 is bypassed but still powered, its PLL possibly sitting at the last analog tune. Weak, speculative, and at best reveals history, not current state. Not load-bearing.

So the refined claim: "undetectable as to tuning" holds for the RF domain on a direct-sampling sensor, for an architectural reason — tuning happens after the last physical stage, so no RF observable couples to it. The unorthodox breaks all cross into the host: squelch-gated behavior is the one that genuinely closes the loop, and it's a host-exploitation problem, not an RF one.

And here's the satisfying part: your own operating discipline is already the countermeasure suite. Fixed 29 dB manual gain kills the AGC loop. Terminator fitted kills antenna-port re-radiation. Continuous capture with no squelch gating kills the host-behavior loop. The practices from the §5.21 work — fixed gain, terminated input, deterministic streaming — are exactly what make a sensor quiet. You didn't just measure quietly; you are quiet.

> **Superseded note:** the "you are quiet" conclusion in the first half of this document is retracted by the section below. Treat the first half as the original (too-strong) claim.

# SCYTHE: Can a passive receiver betray its tuned frequency?

Yes, potentially—even with direct sampling, fixed gain, continuous capture, and no squelch. The earlier conclusion was too strong.

The overlooked mechanism is that digital frequency selection is not necessarily physically silent. Software-defined tuning creates electrical activity, and electrical activity can couple into power rails, clocks, cables, enclosure seams, and electromagnetic emissions.

The important distinction is between a physically detectable effect and an exploitable information channel. We have plausible mechanisms for the former. We do not yet have evidence that a shielded RTL-SDR exposes enough information to reconstruct its selected frequency from outside.

I would investigate four unconventional channels, ranked by their theoretical ability to disclose tuning.

## 1. The digital downconverter itself

Conceptual information-leakage path. The dashed coupling is a hypothesis, not a measured property of the RTL2832U.

A digital downconverter often contains a numerically controlled oscillator (NCO), a digital mixer, and decimation filters.

A conventional phase-accumulator NCO follows:

\\[ \phi\_{n+1}=(\phi_n+K)\bmod 2^N \\]

where the tuning word \\(K\\) determines the oscillator frequency:

\\[ f\_{\mathrm{NCO}}=\frac{K}{2^N}f\_{\mathrm{clock}} \\]

That tuning word influences internal switching activity. The switching activity can influence dynamic power consumption and emissions.

This creates a potential path:

Tuned frequency → digital switching pattern → physical leakage.

Unlike conventional LO leakage, that pathway does not require an analog oscillator running at the selected RF frequency.

The difficulty is observability. An integrated circuit may leak a tiny tuning-dependent pattern, but its power distribution, packaging, shielding, and other logic can suppress or overwhelm the signal. There is also no guarantee that its external emission frequencies track the NCO frequency in a simple relationship.

For SCYTHE, I would classify this as a physically plausible, experimentally unverified digital side channel.

One useful hardware-specific point: the RTL-SDR driver documentation confirms that, in direct-sampling mode, `rtlsdr_set_center_freq()` controls the RTL2832's digital downconversion IF frequency. So there is indeed frequency-dependent state inside the chip even when the RF tuner is bypassed.




## 2. Continuous USB traffic can still leak information

The earlier analysis says continuous streaming kills the host-behavior side channel. It kills a traffic-volume side channel, but not necessarily a data-content side channel.

A receiver continuously sending samples can maintain a constant byte rate while those bytes change with the selected signal, DDC configuration, and signal environment.

Potential observable effects include:

- Data-dependent switching on USB transceivers.
- Changes in the spectral characteristics of cable emissions.
- Variations in processing load when the host performs frequency-dependent decoding.
- Power-consumption changes caused by data values, even at constant throughput.

That doesn't prove an external observer can recover the center frequency. In many receivers, the USB output is simply a fixed-rate stream of I/Q samples, and those bytes alone may not uniquely encode the tuned frequency.

But it defeats the claim that constant streaming makes all downstream behavior invariant.

A second distinction matters here: recovering the signal being processed isn't necessarily recovering the tuning configuration. An observed content change might reflect a changing transmitter, not the receiver's selected frequency.

## 3. Feedback loops through adaptive processing

Here is where I'd look for feedback that survives fixed analog gain.

| Feedback mechanism                        | Does it depend on tuning? | Could it affect physical emissions? | Assessment                               |
| ----------------------------------------- | ------------------------- | ----------------------------------- | ---------------------------------------- |
| Digital automatic frequency control       | Sometimes                 | Possibly                            | Architecture-dependent                   |
| Adaptive interference cancellation        | Sometimes                 | Possibly                            | Research candidate                       |
| Digital noise blanker                     | Input-dependent           | Possibly                            | Weak tuning specificity                  |
| Decoder synchronization and reacquisition | Often                     | Yes, through host workload          | Stronger candidate                       |
| Adaptive beamforming or antenna selection | Potentially               | Yes, if analog hardware changes     | Strong candidate for specialized sensors |
| Digital squelch                           | Yes                       | Yes, if downstream behavior changes | Established conceptual pathway           |

The interesting question is not whether adaptive processing changes. It is whether a change produces an observable signal that can be distinguished from unrelated workloads and environmental changes.

For example, some systems implement automatic frequency control by adjusting a digital NCO. That creates an internal feedback loop whose state depends on received-signal frequency error. If that state changes physical switching activity in a measurable way, it could provide an indirect observation of frequency tracking.

But this is not a universal feature of direct-sampling hardware, and a receiver without that adaptive loop cannot expose information through it.

## 4. An even stranger possibility: computation-dependent power signatures

A receiver might disclose information through its computational implementation, even with no event-driven behavior.

Consider two downconverters that process exactly the same number of samples per second. Their switching activity can still differ because the input data and internal state differ.

That suggests a possible distinction between:

- A constant-rate and approximately constant-power implementation.
- A constant-rate implementation whose power or emissions depend on the numerical values being processed.

This is analogous in principle to side-channel analysis of cryptographic implementations: a computation does not need to produce network traffic or an explicit output for its internal state to influence the physical environment.

However, there is a major limit. The mapping between digital frequency and observable switching activity could be highly nonlinear, many-to-one, and device-specific. Knowing that information leaks is not the same as recovering a reliable absolute frequency.

## 5. What I would actually test for SCYTHE

I'd establish an authorized, conducted bench experiment on SCYTHE-owned receiver hardware before attempting any interpretation of emissions from a radiating setup.

Experiment A — Does digital tuning have a measurable signature?

Use fixed input conditions, fixed sample rate, fixed gain, and continuous recording. Compare isolated power and near-board emission measurements across randomized digital tuning states.

Experiment B — Does continuous data change the signature?

Compare identical receiver settings while varying known, controlled input data. This separates frequency-setting dependence from the ordinary influence of sample content.

Experiment C — Can the signature survive practical isolation?

Compare conducted leakage, exposed-board measurements, and enclosed-device measurements. A signal detectable at a power rail may be entirely absent outside the enclosure.

Experiment D — Can tuned states be distinguished without knowing the test sequence?

Evaluate a blinded, held-out set of tuning states, with matched control conditions and repeated measurements. Report classification accuracy and false positives rather than treating a visible spectral feature as proof of frequency disclosure.

For an initial feasibility experiment, I'd recommend a simple question: can one distinguish two randomly interleaved digital tuning configurations on the same owned device from a physically measured signature, without using its software logs?

If the answer is no even with a near-board conducted measurement, more distant detection is unlikely to be promising for that hardware. If yes, the next question is whether the difference arises from NCO logic, USB sample values, power regulation, or some other controlled variable.

A particularly important control is to repeat the experiment with a fixed known digital input. Otherwise, differences in ambient RF could masquerade as tuning-dependent leakage.

## 6. One correction about your XTR

The XTR's E4000 tuner is a different matter from the RTL2832U's digital downconversion.

Do not assume that direct sampling, E4000 bypass, or active PLL state behaves identically across SDR dongle models. Direct sampling on RTL2832U devices is dependent on the particular board's input routing and driver implementation.




Likewise, a 50-ohm termination reduces radiation through the antenna, but does not eliminate emissions through the enclosure, USB cable, or power system.

And your historical 100.8 MHz spur is evidence of an internally associated spectral feature, not yet evidence of externally detectable tuning-state leakage.

## 7. How this could change SCYTHE

I'd give SCYTHE two distinct experimental capabilities:

Receiver Emission Characterization: an instrumented laboratory function that measures whether a known receiver exposes its configuration through conducted or near-board emissions.

Sensor Observability Model: a simulation component that assigns detection probabilities and information leakage to hypothetical sensor architectures, with uncertainty explicitly separated from confirmed measurements.

This fits your RF tomography work particularly well. Just as the Midtown experiments revealed that facade geometry strongly changes the measurement process, receiver hardware architecture may radically change the physical observability of a passive sensor.

The interesting research question becomes:

> Under what hardware and processing conditions does a receive-only system become an unintended information transmitter?

That is broader than hunting for LO leakage, and potentially more commercially useful for EMC testing, receiver hardening, SDR design assurance, and secure sensor deployments.
My revised conclusion: a well-shielded direct-sampling receiver is not provably opaque about its tuning. It may reveal frequency-dependent information through digital logic, processing feedback, or data-dependent emissions. But until a measurable and reproducible channel is demonstrated, these remain hypotheses—not a working method for remotely identifying the receiver's tuned frequency.

The digital downconverter is where I would investigate first. It is the place where frequency selection necessarily exists, even when no corresponding analog oscillator does.

## 8. Further hypotheses (unverified)

All items here are hypotheses. None has been measured on SCYTHE hardware.

### 8.1 Active lock-in on data-dependent emissions (Experiment E)

Bench plan (draft): [EXPERIMENT_E_BENCH_PLAN.md](EXPERIMENT_E_BENCH_PLAN.md)

Experiments A-D are passive. This one adds a controlled stimulus.

- Transmit a probe tone, on/off keyed at a low rate (e.g. 1 Hz), and sweep its frequency.
- The RTL2832 decimation filter removes out-of-band energy before the 8-bit samples reach USB. Byte toggle rate and Hamming weight on the cable should therefore change with probe power only when the probe is inside the DDC passband.
- Coherently detect the keying-rate component in USB/power emissions with a lock-in.
- It is channel-selective, needs no squelch, and works at constant byte rate. It combines the data-dependent power ideas of sections 2 and 4 with a controlled input.
- Caveat: the effect is likely tiny and needs proximity to the cable or host. Test conducted first.

### 8.2 Crystal harmonics as a free carrier

- Look for sidebands on the 28.8 MHz harmonics rather than for the NCO's own emission.
- Supply-rail modulation from digital activity may AM/PM-modulate the clock. The clock is a strong carrier, which gives SNR gain over detecting the weak baseband emission directly.
- Analogous to carrier-modulation side channels in cryptographic side-channel research (e.g. the Screaming Channels line of work; citation not yet verified).
- Test: compare sidebands across tuning words with everything else fixed.

### 8.3 The retune event leaks even if steady state does not

- Changing the center frequency produces USB control transfers, tuner I2C writes, PLL relock transients, and a settling glitch in the data.
- This is a differential channel: it may not reveal the absolute frequency, but it can reveal when and by how much the tuning changed.
- For hopping or scanning sensors, the hop schedule is the sensitive information.
- It gives Experiment D a cheaper first target: detect that a retune happened before classifying which tuning state.

### 8.4 NCO spur fingerprint

- With a truncated phase accumulator, spurs land at deterministic, tuning-word-dependent offsets.
- They are visible in the IQ data. Check offline whether that structure reaches bus-level toggle statistics before attempting any RF measurement.

### 8.5 Countermeasures

- Decoy noise floor, or random decoy retunes, so a constant tuning state is not a stable target.
- USB isolation: ferrite, isolator, or optical USB.
- Keep the host physically remote from any observer.
- Constant-power host-side processing.

| Claim | Status |
|---|---|
| LO leakage reveals tuning on superhet/zero-IF tuners | Established |
| Crystal/USB/CPU emissions reveal presence, not tuning | Established |
| AGC pumping beats LO leakage | Rejected |
| Squelch-gated host behavior reveals tuning | Conceptually sound |
| DDC switching leaks tuning | Plausible, unverified |
| Data-dependent USB emissions leak in-band energy (8.1) | Plausible, unverified |
| Clock-harmonic sidebands carry tuning (8.2) | Plausible, unverified |
| Retune events observable (8.3) | Plausible, unverified |
| Output-byte toggle rates track in-band power at fixed tuning (Stage 0B, ambient) | Observed, weak; not an emission result |
