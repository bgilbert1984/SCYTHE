# §5.21 antenna-on observation run: launch authorization (DRAFT, slice 3c)

Date: 2026-11-17. Status: PROPOSAL. Nothing here authorizes a run. No operator confirmation has been given or is supplied by this file.
Builds on the frozen record `521_ANTENNA_ON_FREEZE_RECORD.md` (declaration sha256 `94b86ebe99f88c4963d62553516d02af92703f2c4e33af36a56d9dfb032d795c`,
tier file sha256 `c46f56a6a29026c4c8baf7ce55e195d0e67b5018d95404e88f6ac1e183dd699a`).

## What slice 3c adds
- `rf_antenna_on_run.py`: a SEPARATE antenna-on observation run. It reuses the sequencer's acquisition, schedule and spooling primitives and changes nothing in
  `rf_spur_catalogue_sequencer.py`. The terminated catalogue run is untouched: it still requires a fitted termination and its authorization still binds the terminated chain
  (pinned by `test_rf_antenna_on_run.py::CatalogueRunUntouched`).
- The run produces `antenna_on_observation.json` with `evidence_class: ANTENNA_ON_OBSERVATION_ONLY` and `not_catalogue_evidence: true`. It makes no catalogue entries,
  classifications or promotions. Per visit it records the frozen visit gate result; per tuning it records the tuning gate (scored only if every visit passed). Every visit's
  byte-exact windows are spooled with their SHA-256, as in the terminated run.
- Templates: `521_ANTENNA_ON_RUN_DECLARATION.TEMPLATE.json` (the operator's run declaration) and `521_ANTENNA_ON_LAUNCH_AUTHORIZATION.TEMPLATE.json` (status DRAFT, confirmation blank).

## Pre-contact checks (no tuner command until all pass)
1. The frozen declaration and tier file match their pinned hashes, are consistent (`rf_antenna_on_declaration.check_declaration`) and authorize.
2. The run declaration matches the frozen scope: receiver SN 71874552 / E4000, gain 29.0 dB, the two UHF bands, `OBSERVATION_ONLY`; it states the antenna is connected
   (the operator's statement; nothing measures it) and a non-empty site; it names the frozen declaration's hash.
3. The declared seed (7535194158483371716) regenerates the frozen schedule digest `d35d706f...` (64 tunings, 3 visits each).
4. The launch authorization named by path and SHA-256 verifies: status `AUTHORIZED`; window current at the moment of the run; bound to the frozen declaration hash AND to the
   canonical SHA-256 of this exact run declaration (excluding its `launch_authorization` field); the operator's own confirmation text contains that digest; permitted steps and
   stop conditions enumerated. Editing the run declaration after authorizing breaks the binding.
5. The output directory is new (the sequencer's `OUTPUT_DIR_NOT_EMPTY` rule).
Then the sequencer's first-contact rule applies: the family read back from rtl_tcp must be E4000 and the gain must be in its table.

## What the software cannot check
- That the antenna is connected, where it is, or that the venue statement is true.
- That the operator's confirmation is genuine. It checks that the text exists and names the digest. Per the standing rule on authorizations (`scythe-gate-authorization-strictness`),
  the assistant never writes or implies that confirmation; only the operator's own present-tense statement naming the digest counts. The template leaves it blank.
- That the clipping guard and canary describe a usable visit (proxies; see the threshold proposal).

## Operator go/no-go before the first window (to be recorded, not assumed)
- [ ] `lsusb` shows `0bda:2838`; `scythe-rtl-tcp.service` is the only rtl_tcp and nothing else holds the device; no re-enumeration flaps in the last hour.
- [ ] Serial readback is SN 71874552 (E4000).
- [ ] The antenna is connected; the venue and antenna are stated in the run declaration. The 50 ohm load is NOT fitted.
- [ ] 750 MHz, 881 MHz and FM are out of scope; the run does not touch them.
- [ ] The run declaration's digest is computed, the operator states their confirmation naming it, the window is set, and the authorization file's SHA-256 is written into the run declaration.
- [ ] Expect refused tunings: 401-435 MHz was refused in part at 2 of 5 venues tried and the margin over 11.0 dB can be near zero at a degraded venue (accepted assumptions in the freeze record).

## Stop conditions (also enumerated in the authorization)
`STREAM_NOT_LIVE`; the device drops off the host or rtl_tcp exits (re-attach and start a new run, do not retry blindly); a visit refused three times in a row; the declared site or antenna state changes during the run.

## Not decided here
- A launch authorization is the operator's act, per run. This file is the format and the checks, not an authorization.
- What a scored tuning is used for downstream. The run is observation-only; no consumer of `antenna_on_observation.json` exists yet.
- Tier revalidation (12 months and the two triggers) is recorded in the frozen declaration and is not enforced by this run.
