# Sandbox-phase authorization envelope (ephemeral sandbox, passive sensor)

Recorded 2026-11-06 from the owner's statements in session ("I decided I like this idea and want to encourage such during the ephemeral
sandbox phase where we are using a passive sensor to sample and iterate in multiple turns"; "Yes, excellent envelope plan, and write it
down"). This is a **standing pre-authorization given once, in advance, by the owner for a defined envelope**. It is not the assistant
approving its own actions, and it does not change the Gate D protocol, the receipt design (PR #444) or any frozen artifact.
It lasts until the sandbox phase ends or the owner withdraws it; the owner may narrow or end it at any time by saying so.

## Inside the envelope: the assistant proceeds without asking each time
- Receive-only capture on the XTR (survey scans, windowed captures, repeat runs), under the existing receive-only clearance.
- Analysis, estimator runs, synthetic validation, iterating on thresholds or the plan skeleton.
- Reading and writing scratch and sandbox evidence directories; local branches and commits.
- Chaining several turns of sample, analyze, adjust, with a short log line for each action.

## Outside the envelope: the assistant stops and asks
- Transmit or driven source of any kind (Stage 1, generator output).
- SSH or execution on other hosts, anything under the Gate D replay protocol (replay execution is not passive sampling).
- Editing, re-freezing or unfreezing any frozen plan, authorization or declaration; freezing a new one (a freeze is a decision).
- Deleting evidence or overwriting anything outside the sandbox.
- Pushing, opening PRs and merging (the owner approves these one by one unless widened later).
- Physical acts and signatures: the termination-removal statement, Reader 1 signatures, anything stating the owner did or checked something.
  The assistant does not write those on the owner's behalf.

## Safeguards that apply inside the envelope
1. Every action is logged against the envelope (what, why, outcome) so it can be audited afterwards.
2. The assistant stops and checks in on anything unexpected: a signal that looks like transmission, an unexpected error, or a result that would
   change a frozen figure.
3. A result is never reported as stronger than it is; failures and skipped steps are stated.
4. The envelope's wording is the limit. Anything not clearly inside it is outside.
