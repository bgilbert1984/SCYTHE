# RF Spur Catalogue — Authorisation Record

The bounded live catalogue run described in `docs/RF_SPUR_CATALOGUE_PROTOCOL.md`
(§5.21) is authorised. Protocol §7.4 requires the authorisation to be an
explicit decision, recorded with the commit it was taken at, naming the
receiver, the site, the termination and the epochs. This file is that record.

- **Decision taken:** 2026-09-28, at this commit, by the operator (Spectrcyde),
  granted in chat 2026-09-28 ~14:16 PDT with epoch design delegated to the
  agent. The decision authorises the bounded capture only.
- **Receiver:** Nooelec NESDR SMArt v5, SN 14530058, attached via USBIP to
  `scythe-1` (WSL2 AlmaLinux 10.2, tailnet 100.107.199.57), served by `rtl_tcp`
  on 0.0.0.0:1234 as the systemd user unit `scythe-rtl-tcp.service`.
  Verified visible and streaming 2026-09-28.
- **Site:** OPERATOR TO DECLARE — the physical run location and enclosure.
  Required before the first window. (Determines the accepted confidence the
  reference class can reach, per §5.22: second site or shielded enclosure.)
- **Termination:** OPERATOR TO DECLARE — the specific 50 ohm load part and
  connector. To be physically fitted before the first window; the act of
  fitting is recorded here when it happens.
- **Epochs** (agent's choice, per protocol §3 step 7 and §5.22 blocking):
  - **Epoch 1 — initial session.** 192 visits (64 tunings x 3 visits, >= 10
    apart, from a declared seed), each visit a refill and 8 non-overlapping
    256 ms windows at 2.048 MS/s, three signed retunes per tuning per epoch
    (deltas 50/100/200 kHz). Budgeted ~1-2 h.
  - **Blocking.** >= 24 h between the end of one epoch and the start of the
    next. Temporal blocking per §5.22; the epochs are not run back to back.
  - **Epoch 2 — after disconnect/reconnect.** USB detach/reattach of the
    NESDR via usbipd on the Windows host (operator act). Identical schedule,
    same declared seed. Budgeted ~1-2 h.
  - **Epoch 3 — after a power cycle.** Full power cycle of the receiver
    (unplug/replug, operator act). Identical schedule, same declared seed.
    Budgeted ~1-2 h.
  - **Overall: 3-5 days**, stated in hours per epoch and days overall.
  - Stability classes per the protocol: `POWER_CYCLE_STABLE` (all three),
    `RECONNECT_STABLE` (first two), `SESSION_SCOPED` (one). Stability
    classifies and never excludes.
- **Acquisition:** capture-local on `scythe-1`. Measured tailnet throughput
  dedirock<->scythe-1 is 10.5 Mbps on a direct path (124 ms RTT); the run
  needs 32.8 Mbps sustained, and `rtl_tcp` drops samples silently under
  backpressure, which would void windows. The §7.1 sequencer runs where the
  dongle is; the artefact ships to dedirock after each epoch (~1.6 GB/epoch,
  ~20 min at measured speed). Analysis on dedirock.
- **Chain manifest:** one `signal_chain_manifest` per epoch, including the
  termination, hashed, per the protocol §2.
- **Grants:** bounded capture per the protocol only. No production namespace,
  no corpus capture. Protocol §9 stands: the protocol document itself
  authorises nothing; this record is the decision.

## Operator declarations (before the first window)

- [ ] Site named:
- [ ] Termination part and connector:
- [ ] Termination fitted (act recorded, date/time):
