# §5.21 bounded catalogue run — authorization decision

Decision date: 2026-10-02
Repository baseline: main at or after 7e38abf0
Receiver: NESDR SMArt v5, operator-designated unit SN 14530058

## Current finding

The three pre-catalogue terminated-input observations are complete.

A narrow feature near 100.800 MHz persisted across the initial session, USB detach/reattach, and receiver power cycle, with no detected displacement at 7.8125 Hz bin resolution. These observations remain PRE_CATALOGUE_EVIDENCE and confer no catalogue stability class.

The K=64/R=8 catalogue machinery is present on main.

Receiver, termination, gain, bands, schedule seed, chain policy, epoch definitions, epoch blocking, and sites are frozen.

## Reference declaration

reference_hz: UNDECLARED

reference_ppm: UNDECLARED

No declaration is made from the NESDR SMArt v5 model specification alone.

The manufacturer's schematic identifying a nominal 28.8 MHz local oscillator and the manufacturer's 0.5 ppm TCXO specification are accepted as corroborating evidence, but they are not substituted for evidence identifying the reference of physical receiver SN 14530058.

The persistent 100.800 MHz observation is not evidence for the reference declaration and shall not participate in choosing either value.

## Catalogue schedule authorization

NO-GO.

The K=64/R=8 live catalogue schedule is not authorized to execute while reference_hz or reference_ppm remains undeclared.

No tuner schedule shall be generated or executed under a fabricated, inferred, or result-selected reference value.

## Smallest sufficient path to GO

The gate may be reconsidered when physical receiver SN 14530058 supplies unit-specific evidence for its nominal reference.

A sufficient low-complexity package is:

- direct inspection of the physical unit establishing the fitted oscillator/reference marking, preferably with a photograph retained in evidence;
- identification of that marking as the oscillator serving the RTL2832/R820T2 reference path;
- manufacturer documentation supplying the applicable tolerance bound.

If the physical unit establishes a 28.800 MHz reference and the installed oscillator is the manufacturer's specified 0.5 ppm TCXO, the intended declaration is:

```
reference_hz = 28_800_000 Hz
reference_ppm = 0.5 ppm
```

with authority explicitly recorded as:

physical-unit reference identification + manufacturer tolerance specification

—not model inference and not spectral back-fitting.

A direct calibrated frequency measurement may supersede that package and support a different justified ppm figure.

## rtl_tcp reliability hardening

AUTHORIZED.

Implement the shelved rtl_tcp functional-health watchdog before the catalogue run.

The watchdog may:

- verify the expected listening socket;
- perform a bounded functional connection/probe;
- distinguish a healthy stream endpoint from an active-but-wedged process;
- restart scythe-rtl-tcp.service on demonstrated functional failure;
- rate-limit restart attempts;
- record every detection and recovery action.

The watchdog may not:

- retune a healthy receiver;
- alter catalogue declarations;
- silently continue an interrupted catalogue visit;
- convert a failed or interrupted visit into accepted evidence.

Any watchdog recovery during a live catalogue visit invalidates that visit; normal catalogue reacquisition/refusal rules govern.

## Other experiments

The dongle-versus-laptop 100.8 MHz discriminator and antenna-connected ambient survey remain optional and do not block §5.21.

The ADS-B timer remains disabled.

## Final state

Watchdog hardening GO; catalogue acquisition NO-GO pending unit-specific reference authority.
