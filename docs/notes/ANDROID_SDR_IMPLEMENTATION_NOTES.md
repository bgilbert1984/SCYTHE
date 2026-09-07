# Android RTL-SDR Implementation Notes

```text
Status:                NON-NORMATIVE ENGINEERING NOTES
Authority:             RELAYED UPSTREAM CONTRACT KNOWLEDGE
                       NOT VALIDATED IN THIS REPOSITORY — see §10
Governing contract:    docs/RF_WALK_SURVEY_CONTRACT.md  (ACCEPTED, NORMATIVE)
Architecture examples: SUPERSEDED — named in §11, not reproduced
Target device:         Pixel 7 Pro, attached over USB debugging
Android version:       UNDECLARED — not read; see §9
libusb applicability:  per claim, in §9
```

**These notes bind nothing.** They are failure modes worth knowing before writing
Android SDR code, and nothing here grants an implementation permission to do
anything. What a walking-survey implementation may and may not do is
`docs/RF_WALK_SURVEY_CONTRACT.md`, which is accepted and normative. Where a note
here and the contract appear to disagree, the contract wins and the note is
wrong.

The authority line is deliberately not "observed implementation experience."
No Android SDR code exists in this repository, and nothing below was executed on
this project's hardware. §10 separates what is verifiable from an upstream
contract, what is inference, and what is only a recommendation.

---

## 1. USB file-descriptor ownership

Android will not let a normal process open `/dev/bus/usb/*`. The framework hands
out a descriptor; libusb is given that descriptor rather than being allowed to
enumerate.

```kotlin
val conn = usbManager.openDevice(device) ?: return
val fd = conn.fileDescriptor        // owned by conn, NOT by libusb
```

```c
libusb_device_handle *h;
libusb_wrap_sys_device(ctx, (intptr_t)fd, &h);
```

**The failure mode.** `libusb_close()` does *not* close this descriptor — the
`UsbDeviceConnection` owns it. If that Kotlin object is garbage-collected or
`close()`d while a transfer is in flight, the next libusb call returns
`LIBUSB_ERROR_NO_DEVICE` with nothing in the log explaining why. The symptom is
a stream that dies for no visible reason, and it will be blamed on the dongle.

**The rule, and it is exclusive:**

> Hold a strong reference to the `UsbDeviceConnection` for the whole session,
> **or** `dup()` the descriptor in JNI and close the dup yourself after
> `libusb_close`. Never both.

Doing both closes the descriptor twice. Doing neither closes it once, early, at
a time chosen by the garbage collector.

---

## 2. `NO_DEVICE_DISCOVERY` initialization ordering

```c
libusb_set_option(NULL, LIBUSB_OPTION_NO_DEVICE_DISCOVERY);  // BEFORE init
libusb_init(&ctx);
```

**The failure mode.** This is a global option and it must be set *before*
`libusb_init`. Set it after and the call succeeds, returns no error, and the
context has already tried to enumerate — which on Android it may not do. The
ordering bug does not announce itself.

**The alias.** The option was named `LIBUSB_OPTION_WEAK_AUTHORITY` before it was
renamed. The rename landed in **1.0.25** (2022-01-31): *"New NO_DEVICE_DISCOVERY
option replaces WEAK_AUTHORITY option."* Below 1.0.25 the old name is the one
that compiles.

Pin the NDK build rather than relying on a distro copy. §9 has the version
boundaries, including one that source material about this project got wrong.

---

## 3. Double claim — Kotlin and libusb both taking the interface

**The failure mode.** Calling `conn.claimInterface()` on the Kotlin side and
then letting libusb claim it produces `LIBUSB_ERROR_BUSY`, and the error names
the interface rather than the fact that you are competing with yourself.

**The rule.** Let libusb own the claim. Do not claim from Kotlin.

Still call `libusb_set_auto_detach_kernel_driver(h, 1)`. It is harmless on a
stock Pixel, where `dvb_usb_rtl28xxu` is not loaded, and it is what saves you on
a rooted device where somebody has modprobed it.

---

## 4. Hotplug is dead once discovery is disabled

**The failure mode.** With `NO_DEVICE_DISCOVERY` set,
`libusb_hotplug_register_callback` never fires. It also does not fail — it
registers successfully and then stays silent forever, so the absence looks like
a device that never got unplugged rather than a callback that cannot arrive.

**The consequence.** Detach must be handled from the Android framework:
`USB_DEVICE_DETACHED` broadcast → `rtlsdr_cancel_async` → `rtlsdr_close`.

The framework path is worth preferring for attach as well. A `device_filter.xml`
intent filter on vendor `0x0bda`, products `0x2838` / `0x2832`, grants
permission without a dialog and relaunches the service on plug-in. It also
sidesteps the `PendingIntent` requirement below.

**Related, API 31+.** A permission `PendingIntent` must be `FLAG_MUTABLE` or the
grant broadcast never arrives. The intent-filter path avoids needing one.

---

## 5. Cancellation and the threading deadlock boundary

**The failure mode.** Calling `rtlsdr_cancel_async` from the same thread that is
blocked inside `read_async` deadlocks. The app hangs with no exception and no
log line.

**The rule.** Cancel from a *different* thread than the one blocked in the read
loop. This is a hard boundary, and it interacts with §4: the detach broadcast
arrives on the main thread, the read loop is on its own thread, and the cancel
must cross that gap rather than being dispatched into the blocked thread.

---

## 6. Per-buffer JNI callback overhead

**The failure mode.** A JNI upcall per async buffer drops samples. At 2.4 MS/s
the buffers arrive fast enough that the transition cost dominates, and the loss
appears as gaps that look like USB problems.

**The rule.** Keep the sweep loop, the FFT and the dB conversion in a native
thread. Return only the quantized bin vector, through a preallocated direct
`ByteBuffer`. One upcall per published product, not one per buffer.

`setpriority(-19)` on the native thread is the usual recommendation; treat it as
a recommendation (§10), not a measured requirement.

**Transfer sizing.** librtlsdr's default 15 × 256 KB async buffers are a
starting point. Android USB stack latency spikes are common enough that more
buffers is the usual mitigation. Set a fixed gain —
`rtlsdr_set_tuner_gain_mode(dev, 1)` plus an explicit gain — so AGC does not
smear a survey across its own dynamic range. **The contract requires gain to
travel with every comparable product** (`RF_WALK_SURVEY_CONTRACT.md` §6), which
a fixed gain makes possible and an AGC-driven one does not.

---

## 7. Retune settling and the mandatory stale-buffer flush

**The failure mode.** `rtlsdr_set_center_freq` costs settling time. Buffers
already in flight were captured at the previous centre frequency, and consuming
them bakes tuner transients into the spectrum at the *new* frequency. The
artefact is stable, reproducible, and looks exactly like a signal.

**The rule.** Discard at least the first buffer after every retune. A hop plan
retunes constantly, so this is per-hop, not per-sweep.

**Contract interaction.** `RF_WALK_SURVEY_CONTRACT.md` §2 requires monotonic
acquisition bounds per frame and §6 requires a `processing_revision`. The flush
policy is part of processing: changing how many buffers are discarded changes
what the power numbers mean, and two surveys with different flush policies are
not comparable products.

---

## 8. Android 16 KB page compatibility

**The failure mode.** A shared library built for 4 KB pages fails to load on a
16 KB-page device. The failure is at load time, on the device, not at build
time.

**The rule.** NDK r27+, and link **both** librtlsdr and libusb with:

```text
-Wl,-z,max-page-size=16384
useLegacyPackaging=false
```

Both libraries. Getting one right and missing the other fails the same way.

---

## 9. Version and device applicability

Every claim below is scoped. A claim without a scope is a claim that will be
wrong somewhere.

| Claim | Applies to | Source |
|---|---|---|
| `libusb_wrap_sys_device` exists | libusb **≥ 1.0.23** (2019-08-28) | libusb ChangeLog, verified |
| Option is `LIBUSB_OPTION_NO_DEVICE_DISCOVERY` | libusb **≥ 1.0.25** (2022-01-31) | libusb ChangeLog, verified: *"New NO_DEVICE_DISCOVERY option replaces WEAK_AUTHORITY option"* |
| Option is `LIBUSB_OPTION_WEAK_AUTHORITY` | libusb **< 1.0.25** | Implied by the rename entry. The release that *introduced* `WEAK_AUTHORITY` is **not stated in the ChangeLog** — check the header of the exact build you pin |
| `FLAG_MUTABLE` required on permission `PendingIntent` | Android **API 31+** | Android platform behaviour, not verified here |
| Foreground service types `location` + `connectedDevice` | Android **14+** | Android platform behaviour, not verified here |
| 16 KB page linker flags | NDK **r27+**, 16 KB-page devices | Not verified here |
| `dvb_usb_rtl28xxu` not loaded | stock Pixel; **not** guaranteed on rooted or non-Pixel | Not verified here |
| Sample-rate and buffer-count guidance | RTL-SDR at ~2.4 MS/s on Tensor-class hardware | Recommendation only |

**One correction to prior source material.** Notes preceding this file stated
that the `WEAK_AUTHORITY` alias applied at 1.0.23 and advised pinning ≥ 1.0.26.
The rename boundary is **1.0.25**, per the ChangeLog. The ≥ 1.0.26 advice was
conservative rather than wrong, but the stated boundary was off by one release
and would have misled anyone reasoning about which name to compile against.

**Android version is `UNDECLARED`.** The device is attached over USB debugging,
but it is bound to the Windows host rather than to this WSL instance, `adb` is
not on this PATH, and nothing here read a version property. Fill it in from the
device rather than from an assumption:

```bash
adb shell getprop ro.build.version.release
adb shell getprop ro.build.version.sdk
adb shell getprop ro.product.model
```

`UNDECLARED`, not `UNKNOWN`: nobody looked, and that is different from having
looked and been unable to tell.

---

## 10. Observed, inferred, recommended

The distinction this repository applies to RF evidence applies to its own
engineering notes.

**Verified against an upstream contract.** The two libusb version boundaries in
§9, checked against the libusb ChangeLog while writing this file.

**Follows from a published API contract, not verified by execution.** That
`libusb_close` does not close a wrapped descriptor; that `libusb_set_option` is
global and order-sensitive relative to `libusb_init`; that a second claim of a
claimed interface returns `BUSY`; that hotplug callbacks cannot fire when
discovery is disabled. These are consequences of documented behaviour. They are
not observations.

**Inference from how the pieces fit.** That cancelling from the blocked thread
deadlocks; that per-buffer JNI upcalls dominate at 2.4 MS/s; that in-flight
buffers after a retune carry transients. Each is a sound inference about a
mechanism and none was measured here.

**Recommendation only.** Buffer counts, thread priority, which NDK release to
pin, and preferring the intent-filter path over `PendingIntent`. Reasonable
defaults. Not requirements, and not derived from measurement.

**Observed on this project's hardware: nothing in this file.** No Android SDR
code exists in this repository. When an implementation does execute, the entries
it confirms or contradicts should be moved out of these categories and dated —
that is what would make this file "observed implementation experience", and it
is not that yet.

---

## 11. Superseded architecture — named, not reproduced

Source material preceding the walking-survey contract contained schemas and
integration examples that the contract **prohibits**. They are named here so a
reader who remembers them knows they were rejected, and deliberately **not
reproduced**: a schema written out in a document is a schema that stays
searchable and copyable, and an obsolete example beside a normative contract is
read as guidance.

| Rejected concept | Why | Contract |
|---|---|---|
| `SurveyFrame` with `t_utc_ns` as the only time base | UTC is display metadata; monotonic bounds are the join axis, and this host takes wall-clock steps | §2 |
| `power_dbm_q` on the wire from an uncalibrated dongle | Power is `DBFS` unless a named calibration identity supports `DBM` | §3 |
| Per-observer scalar offset producing "dBm" | A moved dBFS number is not a calibrated one; generic offsets are prohibited | §3 |
| Sweep block carrying only gain / ppm / dwell | Antenna, feedline, extension, sample rate, sweep-plan and processing revision must travel too | §6 |
| Auto-promotion of `sensor:` nodes and observation edges through WriteBus on frame arrival | Frame acceptance authorizes nothing to be written; four separate authorities | §7 |
| `power_dbm` on projected entities to "label emitters" | A surface is receiver-path-conditioned received power, never an emitter claim | §8 |
| CoT export of survey hotspots as point features | A point estimate before the geometry, repeatability and refusal gates | §8 |
| "IQ snippet on demand" on the grounding lane | No transported snippet at any size or duty cycle; there is no grounding-lane exception | §9 |

The full contract is `docs/RF_WALK_SURVEY_CONTRACT.md`. The next implementation
begins from its admission verdict model — one disposition plus bounded reason
codes — and nothing else.
