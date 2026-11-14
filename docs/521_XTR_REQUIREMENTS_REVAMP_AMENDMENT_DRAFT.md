# §5.21 XTR catalogue-run requirements: revamp proposal (DRAFT; R2/R4 implemented, E4000 freeze registered 2026-11-14 - see 521_XTR_E4000_FREEZE_RECORD.md; R1/R3 open)

Date: 2026-11-14 (drafted in a Claude Code session at the owner's request)
Status: PROPOSAL. Nothing here is frozen or in force. No frozen declaration, no code and no test is changed by this file.
Supersedes: nothing yet. If adopted, it would amend the XTR sections of `521_XTR_AUTHORIZATION_AMENDMENT.md`
and the receiver-binding paragraphs of `521_REFERENCE_AUTHORITY_AMENDMENT.md`; it would not touch
`521_CATALOGUE_AUTHORIZATION_DECISION.md`'s prohibition on fabricated, inferred or result-selected references.

## 1. What the owner asked for

1. Keep the 50 ohm load fitted and the antenna off as the catalogue's physical precondition, but revamp how it is recorded.
2. Unbind the dead NESDR SMArt v5 (SN 14530058).
3. Stop requiring the XTR's reference oscillator to be declared from inspection.

## 2. What the code read established (`rf_spur_catalogue_sequencer.py`, `rf_promotion_envelope.py`, main at 2a514b99)

- **Two authorizations are already accepted.** `check_declaration_authorized` tries the v5 file and the XTR file and passes a run that
  matches either on every chain field (`_authorized_chain_subset`: sensor_id, gain, termination part and connector, bands, seed,
  reference_hz, reference_ppm). The XTR needs no inspection today: its file carries 28.8 MHz at 100 ppm as
  `UNCALIBRATED_RECEIVER_CONSERVATIVE_BOUND`. The 28.8 MHz figure is carried over from the v5 and was never identified on the XTR.
- **The reference is nearly inert in the authorized bands.** It is used in one place: `classify_track`, slope -1 branch, which
  searches `range(1, comb.harmonic_cap + 1)` for a harmonic of the reference. `harmonic_cap` = floor(0.005 x 2.048 MHz / (28.8 MHz x ppm)).
  Computed here: **cap = 3 at 100 ppm (harmonics 28.8, 57.6, 86.4 MHz), cap = 711 at 0.5 ppm.** The authorized bands are UHF_LOW 400-440
  and UHF_HIGH 440-470 MHz, so at 100 ppm every slope -1 track there can only become an "ingress finding"; none can be classified as
  `CONSISTENT_WITH_INTERNAL_REFERENCE`. Declaring a reference changes nothing for these bands until the ppm is known to ~0.5 ppm.
- **Termination is only a statement.** `check_declaration` requires `termination.part`, `.connector`, `.fitted` to be truthy; the chain
  digest hashes the part and connector strings. Nothing measures that the load is on. `VANISHES_ON_DECLARED_TERMINATION` is never
  assigned by the sequencer ("this run is fully terminated"). The XTR file records the part as "NOT verified" and the connector as
  unverified; the run-time declaration must repeat those strings exactly.
- **The v5 is bound in three places:** `_AUTHORIZED_DECLARATION_PATH` (the v5 file), the `paths` tuple in `check_declaration_authorized`,
  and tests that load it (`test_rf_521_xtr_authorization.py` asserts both files accept themselves and share schedule fields with the v5).
  Other test files mention the v5 serial or file name; they were not read for this draft.
- **FINDING, VERIFIED on the unit 2026-11-14: the gain table is R820T2, the XTR tuner is E4000.** The sequencer refuses any gain not in
  `_R820T2_GAINS_DB`, and the frozen XTR authorization uses 29.7 dB, an R820T2 step. `rtl_test -t` on SN 71874552 reports an Elonics
  E4000 with 14 supported gain values: **-1.0 1.5 4.0 6.5 9.0 11.5 14.0 16.5 19.0 21.5 24.0 29.0 34.0 42.0 dB**. 29.7 is not among them,
  so `rtl_tcp` clamps the request to a neighbouring step and the chain hash names a gain the receiver is not at, which is exactly the
  failure the table's own comment says it prevents. The same run showed E4K range 52-2205 MHz with an L-band gap at 1104-1250 MHz
  (PLL-not-locked lines at 51, 1104, 1250 and 2206 MHz are the range edges, not a fault). The 2026-11-13 Everett survey asked for 30 dB
  and the tuner reported 29 dB, consistent with this table. The sequencer's own check does not know the tuner, so any R820T2 step outside the E4000 list would be accepted and then clamped.

## 3. Proposed requirements

### R1. Termination: keep the requirement, add a measured gate
- Keep: load fitted and antenna removed before the first window (the catalogue separates receiver-intrinsic products from signals).
- Replace "owner states it is fitted" as the only evidence with a pre-run control artifact: a short capture with the antenna connected
  and one terminated, same gain and band, in which known strong ambient carriers (FM broadcast, 88-108 MHz, plus the strongest UHF
  plateau) must fall by at least X dB. X is TBD and should be set from the existing antenna-on surveys and the RERUN3 terminated spool,
  not chosen here. The artifact's hash goes in the provenance note; a failed control is a pre-contact refusal.
- Record the load part and connector as observed facts when known; unknown stays "NOT verified" and the control, not the string, carries the claim.
- Cost: a launcher or pre-flight script. The sequencer need not change, but it should record the control hash.

### R2. Retire the v5 authorization, keep the sensor binding
- Mark `521_AUTHORIZED_RUN_DECLARATION.json` RETIRED (receiver permanently dead, owner-stated 2026-10-11). Leave the file byte-identical.
- Remove it from `check_declaration_authorized`'s accepted set; keep the per-receiver `sensor_id` binding itself, because it is what
  refuses a run on the wrong receiver. A v5 declaration then fails `DECLARATION_NOT_AUTHORIZED`.
- The 259 v5 catalogue entries stay v5-labelled and cannot satisfy anything for the XTR. Deletion of the v5 file is a later, separate slice
  (deprecate before delete).
- Cost: small code change plus test edits (the XTR authorization tests compare against the v5 file today).

### R3. Reference: no inspection requirement; the value is a bound, not a claim
Option A (recommended, no code change): keep `reference_hz = 28.8 MHz`, `reference_ppm = 100` in the XTR file as the uncalibrated bound,
and state explicitly that (a) it is a bound, not an identification of the XTR oscillator, (b) it has no classification power in the
authorized bands (section 2), and (c) the authority is `UNCALIBRATED_RECEIVER_CONSERVATIVE_BOUND`, never inspection. Only prose fields
change, which are not in the chain subset, so no refusal and no reseed. This already removes the inspection requirement.
Option B (literal removal): make the reference optional (`REFERENCE_NOT_DECLARED`); slope -1 tracks get a new class
instead of "internal reference vs ingress finding". It touches `check_declaration`, `ReferenceComb` use at lines 1133 and 1191, the analysis dict
(line 947), `write_artefacts` (1339) and replay-parity tests, and buys nothing in UHF, so it is not recommended.
Either way: any later comparison of observed spur spacing with a 28.8 MHz comb is post-hoc commentary. It must never feed back into
`reference_hz`/`reference_ppm` (the decision doc forbids result-selected references).

### R4. Gain: make the table tuner-aware (blocker if the finding holds)
- Gain steps confirmed by `rtl_test -t` (section 2): 29.7 dB is not an E4000 step. Add a tuner-aware gain table, pick a valid step (29.0 dB would match the surveys), and **freeze a new XTR
  authorization**, since `gain_db` is a chain field. That changes the chain digest and requires a new epoch/run id.

## 4. Slices if adopted
1. (Done 2026-11-14: gain steps read with `rtl_test -t`.) 2. R4 gain table and new XTR freeze (implemented, uncommitted, on branch `fix/521-e4000-gain-table` in worktree `~/SCYTHE-r4-e4000-gain`: tuner-aware tables, family read back from the rtl_tcp header, draft `521_AUTHORIZED_RUN_DECLARATION_XTR_E4000.DRAFT.json`, `test_rf_521_e4000_gain.py`; the draft is NOT registered, so it authorizes nothing). Found on the way, not fixed: the sequencer's R820T2 table has 23.1/25.0/27.8 dB where the bridge driver table has 22.9/25.4/28.0. 3. R2 retirement (code + tests).
4. R1 control gate. 5. R3 Option A prose in the XTR file, included in the R4 freeze. Each is its own proposal, acceptance, implementation.

## 5. What does not change
Bands, seed, schedule, the 100 ppm bound, epoch policy, the rtl_tcp watchdog rules, the "no result-selected reference" rule, and the
separate Dedirock Gate D authorization. A catalogue run still needs its own separate authorization to make contact; this file authorizes nothing.

## 6. Open questions for the owner
- The termination-control threshold X and which carriers it uses.
- Whether to keep the v5 file in `docs/` after retirement or move it to an archive directory.
- Whether the E4000 gain finding is already known (the XTR file was frozen 2026-11-03).
