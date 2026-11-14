# §5.21 XTR Go/No-Go Launch Package (E4000 freeze)

**Status: DRAFT, prepared 2026-11-14 for owner review. Not a launch authorization by itself.**
**Gate: a new §5.21 catalogue run under the E4000 freeze. No tuner operation under this freeze has occurred.**
Supersedes `docs/521_FIRST_WINDOW_LAUNCH_PACKAGE.md` (v5, SN 14530058, retired) for the XTR. That file is kept unchanged as the
2026-09-29 record.

This package certifies the hardware, declaration and site preconditions for a bounded §5.21 catalogue window on the Nooelec SMArt XTR.
Every item must read GO before the first window opens. One NO-GO holds the run. Do not start the tuner on a partial pass.

## Standing authorizations (owner's framework, carried from the v5 package)
- **Receiver:** "the actual attached NESDR unit, verified first." Applies to the designated XTR, SN 71874552 (the v5 is permanently dead, owner 2026-10-11).
- **Site:** "The actual physical run location and enclosure. I cannot infer this from the host name or your residence."
- **Termination:** "A specific 50 Ω load and connector, physically fitted before the first window; record the part and the act of fitting it."
- **Epochs:** only epoch 1. The reconnect and power-cycle epochs are no longer required (owner-stated 2026-11-11). This changes the procedural lifecycle requirement
  only; the termination fit, receiver serial check and frozen declaration below still apply. Results stay `SESSION_SCOPED`.

## What is frozen (read it, do not restate it from memory)
`docs/521_AUTHORIZED_RUN_DECLARATION_XTR_E4000.json` (frozen 2026-11-14; registered by PR #471). Chain-defining fields the run-time declaration must match exactly:

| Field | Frozen value |
|---|---|
| `receiver.sensor_id` / `receiver.tuner` | `71874552` / `E4000` |
| `gain_db` | **29.0** (an E4000 step; 29.7 is not) |
| bands | UHF_LOW 400-440 MHz, UHF_HIGH 440-470 MHz |
| `seed` | 7535194158483371716 |
| `reference_hz` / `reference_ppm` | 28,800,000 / 100, a conservative bound for an uncalibrated receiver, not an identification of the oscillator |
| `termination.part` | `generic 50 ohm SMA termination load (part number NOT verified)` |
| `termination.connector` | `SMA-male` (carried over, unverified) |

Run id for this freeze: `521-UHF-XTR-E4K-EPOCH1`. Operational fields (run id, epoch, site prose, rtl_tcp endpoint, output dir) are the operator's launch choices and are not bound.
If the real load part differs from the frozen string, the run-time declaration cannot repeat it: that needs a new freeze, never an edit of the frozen file.

---

## 1. Load identity (record what is known)
- [ ] The termination is **SMA-male, 50 Ω**, rated ≥ 1 GHz.
- [ ] Record manufacturer, part number, Ω rating, connector sex, frequency range, power rating, **if known**. The owner stated on 2026-11-03 that a termination is fitted;
      the part number has never been verified. If it stays unknown, the declaration keeps the frozen "NOT verified" string and the provenance says so. It must not claim verification.
- [ ] Visual inspection: clean connector faces, centre pin straight, no debris.

## 2. Fit-and-verify checklist (record the act of fitting)
- [ ] Telescopic antenna **removed**. It is not a 50 Ω load and must not be attached during any terminated window.
- [ ] Fit with the dongle unpowered or rtl_tcp stopped: no live SMA mating with the receiver energized.
- [ ] Thread by hand, finger-tight, snug to ~5 in·lbf (0.56 N·m). No over-torque or cross-threading.
- [ ] Visual: load fully seated, no gap. Wiggle check: no looseness or intermittent contact.
- [ ] Record the act: part identity from §1 (or "not verified"), date/time, who fitted it.

## 3. Receiver check (the actual unit, verified first)
- [ ] Nothing holds the dongle: no `rtl_tcp`, `rtl_power` or SDR process, and the scythe-rtl-tcp units are not running. Starting rtl_tcp for the run is an explicit operator action.
- [ ] `lsusb` shows `0bda:2838`; note bus/device node.
- [ ] `rtl_eeprom` readback (unit not held by another process) shows serial **71874552**, product "SMArt XTR v5", and the tuner reported is an **Elonics E4000**. Any other serial: wrong receiver, NO-GO.
- [ ] Optional, no catalogue contact: `rtl_test -t` lists 14 gain values, including 29.0 and not 29.7 (read 2026-11-14).
- [ ] rtl_tcp started by the operator for the run reports in its dongle_info header tuner type 1 and 14 gains; the sequencer refuses a mismatch before sending any command.
- [ ] Port (default 1234) has **exactly one** client: the run's own sequencer. A second client means a port or dongle fight: stop and clear it.
- [ ] No USB re-enumeration flaps in the preceding hour (a flap mid-window invalidates that window).

## 4. Declaration check (no tuner contact)
- [ ] Build the run-time declaration from the frozen file plus the operational fields, then run, from the repository root:
      `python3 -c "import json,sys,rf_spur_catalogue_sequencer as s; d=json.load(open(sys.argv[1])); s.check_declaration(d); s.check_declaration_authorized(d); print('declaration OK (no tuner contact)')" DECLARATION.json`
      It must print `declaration OK`. A refusal (`DECLARATION_NOT_AUTHORIZED`, `GAIN_NOT_SUPPORTED`, `RECEIVER_RETIRED`) is a NO-GO and the message names the field.
- [ ] `receiver.sensor_id` is 71874552 and `receiver.tuner` is E4000 in the declaration (the old 2026-11-03 file is history-only and fails this check for a new run).

## 5. Site confirmation (declared at run time, never inferred)
- [ ] Physical run location stated by the owner: ________________________________
- [ ] Enclosure stated (or "none"): ________________________________
- [ ] The venue is a per-capture label. Take the owner's stated venue for this run; earlier venues are not a conflict. Any deviation from what the declaration prose says is recorded, not assumed away.

## 6. Go / No-Go

| Check | GO | NO-GO |
|---|---|---|
| Load (§1) | Identity recorded, or "not verified" stated truthfully | A verification claim without evidence |
| Fitting (§2) | Antenna off, load seated, act recorded | Antenna attached / loose fit |
| Receiver (§3) | SN 71874552, E4000, single client, no flaps | Wrong unit / second client / flapping |
| Declaration (§4) | `declaration OK` against the E4000 freeze | Any refusal |
| Site (§5) | Location + enclosure declared by the owner | Assumed from hostname |

**All GO: the run may be separately launched.** Any NO-GO: hold, note the blocker, re-run this package.

## 7. The window
`THERMAL_NO_INPUT` with the 50 Ω load fitted, antenna off: the baseline against which the catalogue's in-span and out-of-span claims are judged. 192 visits in the frozen schedule.
Results are `SESSION_SCOPED`. The load stays fitted for the run and is verified, not refitted.

## Known limits (not gates)
- **R1:** a measured termination control (antenna-on versus terminated capture) is proposed in `521_XTR_REQUIREMENTS_REVAMP_AMENDMENT_DRAFT.md` but is not implemented, so it is not a gate here.
  Until it is, "the load is fitted" rests on the owner's statement and the recorded act.
- **Reference:** the 28.8 MHz / 100 ppm is a bound. At 100 ppm the comb's harmonic cap is 3 (up to 86.4 MHz), so it has no classification power in 400-470 MHz.
- **Earlier XTR runs (RERUN2, RERUN3):** they declared **29.7 dB** (the 2026-11-03 file) on the E4000, which has no such step. The gain `rtl_tcp` actually applied for those runs is not established by their
  records, so their "gain 29.7" chain hash should not be read as the applied gain. The sequencer could not catch this until the tuner-family check (#469). This does not change their evidence; it
  limits what their provenance can claim about gain. They stay valid for historical replay and reanalysis.
