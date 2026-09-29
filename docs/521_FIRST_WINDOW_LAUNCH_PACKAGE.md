# §5.21 First-Window Go/No-Go Launch Package

**Status: DRAFT — prepared 2026-09-29, awaiting the 50 Ω load delivery**
**Gate: Epoch 1 (initial session). No tuner operation for the catalogue run has occurred.**

This package certifies the hardware and site preconditions for the first bounded
§5.21 catalogue window. Every item below must read GO before the first window
opens. One NO-GO holds the run.

## Standing authorizations (your framework, restated verbatim)

- **Receiver:** "the actual attached NESDR unit, verified first."
- **Site:** "The actual physical run location and enclosure. I cannot infer this
  from the host name or your residence."
- **Termination:** "A specific 50 Ω load and connector, physically fitted before
  the first window; record the part and the act of fitting it."
- **Epochs:** "Three on that one receiver: initial session, after
  disconnect/reconnect, and after a power cycle."

Known unit: NESDR SMArt v5, SN 14530058, USB `0bda:2838`.
Declared site: 12426 Mukilteo Speedway, Mukilteo, WA 98275, no enclosure.

---

## 1. Part receipt & identity (delivery day)

- [ ] Received load is **SMA-male, 50 Ω**, rated ≥ 1 GHz (DC–1 GHz minimum;
      DC–6/12/18 GHz variants all acceptable).
- [ ] Record: manufacturer, part number, Ω rating, connector sex, frequency
      range, power rating. This is the declared part; it goes in the run record.
- [ ] Visual inspection: clean connector faces, center pin straight, no debris.
- [ ] Confirm this is the user-ordered unit (Amazon, 2026-09-29; the parked
      $13.37 Superbat checkout died on card trouble — the unit in hand is the
      one you ordered yourself).

## 2. Fit-and-verify checklist (record the act of fitting)

- [ ] Telescopic antenna **removed** from the NESDR. It is explicitly NOT a
      50 Ω load and must not be attached during any terminated window.
- [ ] Fit with the dongle **unpowered** (or rtl_tcp stopped) — no live SMA
      mating with the receiver energized.
- [ ] Thread by hand until finger-tight; snug to ~5 in·lbf (0.56 N·m). No
      over-torque, no cross-threading.
- [ ] Visual: load fully seated, no gap between connector bodies.
- [ ] Record the act: part identity from §1, date/time of fitting, who fitted it.
- [ ] Wiggle check: no looseness, no intermittent contact.

## 3. Receiver-visibility check (the actual unit, verified first)

- [ ] `lsusb` on scythe-1 shows `0bda:2838`; note bus/device node.
- [ ] Serial readback identifies **SN 14530058** (NESDR SMArt v5). Any other
      serial → wrong receiver → NO-GO.
- [ ] `systemctl --user is-active scythe-rtl-tcp.service` → **active**
      (managed by systemd only; no manual rtl_tcp instances).
- [ ] Port 1234 listening on scythe-1; **exactly one** consumer — dedirock's
      rf_bridge. A second client means a port/dongle fight → stop and clear it.
- [ ] Stream sanity: non-zero IQ bytes flowing to dedirock; spot-check sample
      variance sits in the expected terminated-noise band for the set gain.
- [ ] No USB re-enumeration flaps in the preceding hour (see TOOLS.md flap
      heuristic; a flap mid-window invalidates that window).

Measured 2026-09-29 11:05 PDT (SSH verified live after admin re-auth): scythe-1
(hostname SCYTHE) — dongle on the bus as Bus 001 Device 002, USB `0bda:2838`,
serial **14530058**, product **NESDR SMArt v5** — the authorized receiver,
verified first. `scythe-rtl-tcp.service` **active** (clean stop/start cycled
for the serial readback; Restart=always, no strays). dedirock online,
rf_bridge currently inactive — no consumer connected, which is correct
pre-run. One consumer only (dedirock's rf_bridge) at window time.

## 4. Site confirmation (declared at run time, never inferred)

- [ ] Physical run location stated: ________________________________
- [ ] Enclosure stated (or "none"): ________________________________
- [ ] Expected: 12426 Mukilteo Speedway, Mukilteo, WA 98275, no enclosure.
      Any deviation is recorded, not assumed away.

## 5. Go / No-Go

| Check | GO | NO-GO |
|---|---|---|
| Load part (§1) | Identity recorded, spec verified | Wrong spec / unrecorded |
| Fitting (§2) | Antenna off, load seated, act recorded | Antenna still attached / loose fit |
| Receiver (§3) | SN 14530058, stream clean, single consumer | Wrong unit / no stream / flapping |
| Site (§4) | Location + enclosure declared | Assumed from hostname |

**All GO → Epoch 1 authorized.** Any NO-GO → hold, note the blocker, re-run
this package. Do not start the tuner on a partial pass.

## 6. Epoch 1 window

First window: **THERMAL_NO_INPUT** with the 50 Ω load fitted — the baseline
against which the catalogue's in-span/out-of-span claims are judged.

Epochs 2 and 3 on the same receiver:
- **Epoch 2:** after disconnect/reconnect (usbipd detach/reattach). Load stays
  fitted; re-run §2 wiggle check + §3 receiver-visibility before the window.
- **Epoch 3:** after a power cycle (physical unplug/replug). Same re-checks.

The load remains fitted for all three epochs; it is not re-fitted between them,
only re-verified.
