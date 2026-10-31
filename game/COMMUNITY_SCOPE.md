# Workshop, challenges, and multiplayer: scope and split (#364)

Status: **proposal.** Nothing here is built. It scopes #364 so the work can be split before it starts, as the
issue's done criterion asks. Related: design §5 (case contract), §6 (architecture), §7 (saves), #270.

The playtests in design §8 have not run. Everything below depends on them supporting the core loop, and the
order below is written so that the early items are useful even if the later ones are dropped.

## 1. What already helps

- **Replay is verification.** A save is case + seed + actions, and loading re-executes it and checks any
  recorded result (`runtime/save.js`). A score submitted as a save can be proven legal without trusting the sender.
- **The engine is pure.** It has no I/O, so a server or a second process can run the same code and reach the
  same state. Invalid actions are rejected with a code and leave state untouched.
- **Cases are plain JSON** with a content hash (`case_hash`), and the Hub renders text with `textContent`,
  not HTML.
- **The platform layer** (`platform/`, #373) already separates "what happened" from "who stores it".

## 2. What does not hold up for untrusted content

Checked by running `validateCase` on edited copies of the shipped case:

| Input | Result today | Why it matters |
|---|---|---|
| `hypotheses` is a string | **throws** (`hyps.map is not a function`) | The validator is an authoring check. It assumes shape, so hostile input crashes it instead of being refused. |
| 10 MB `title` | accepted | No size limits anywhere. A downloaded case could exhaust memory or make saves huge. |
| `<img onerror=...>` in `reading` | accepted | Harmless today only because the Hub never uses `innerHTML`. That is a convention, not an enforced rule. |
| extra top-level keys | accepted | No closed schema, so a case can smuggle data or collide with future fields. |
| `derived_from` cycle | accepted (engine does not hang) | Semantically wrong; the validator should say so. |
| A briefing that names the answer | accepted | No check that the case is fair or even solvable. |
| `case_id` | free text | A downloaded case can claim `signal_ghost` and collide with the shipped one and its saves. |

The engine also trusts that the case is valid. For community content, validation must be a separate, total
gate in front of it, not a library the author runs on their own machine.

## 3. Proposed split

Each item is independently shippable and ends in a testable "done when". Dependencies in brackets.

### A. Hardened case-file contract `game_case_1` (prerequisite for B, C, D)
Strict, total validation of untrusted case JSON: closed key set, type and length caps, byte budget, no
duplicate ids, acyclic `derived_from`, plain-text-only fields, and a **solvability check** (all evidence is
reachable; the reference posterior favours the truth once everything is inspected; a budget-feasible path
exists). Never throws. Versioned, with a migration or refusal for `game_case_0.1.0`.
**Done when:** a fuzz and mutation suite (every field removed, retyped, oversized, nested) is either refused
with a code or accepted and playable end to end, and never throws or hangs.

### B. Case bundles and local import [A]
A bundle is one file: case + manifest (title, author text, contract version) + content hash. The Hub gains
"Play a case file". Case identity becomes `author-namespace/case_id@hash` so imported cases cannot shadow the
shipped one or each other, and saves already carry `case_hash`. Local only; no network, no Steam.
**Done when:** a second authored case can be dropped in, played, saved, resumed, and replayed, and a tampered
bundle is refused with a plain message.

### C. Challenge missions [A, B; leaderboard needs #363]
A challenge is case + fixed seed. A result is a finished save; submitting it means the receiver replays it.
Local first: share a challenge code or file, compare scores on the debrief. Online leaderboards go through the
platform provider (a new `submit(score, save)` in its contract) once a Steam test app exists.
**Known limit:** replay proves a result was *legal*, not that it was played *blind*. A fixed-seed puzzle can be
replayed with foreknowledge. Mitigations to decide: seed revealed only at start, daily seeds, and
"first attempt only" boards.
**Done when:** two machines can exchange a challenge and see the same verified score for the same actions.

### D. Workshop publishing and subscription [A, B, #363, a Steam test app]
Upload a bundle as Steam UGC, subscribe, and install into a content folder. Content is **data only**, never
code, and always passes A before it is listed in the Hub. Needs a content policy, reporting, and a takedown
path.
**Done when:** a bundle uploaded on one machine appears on another, plays, and an invalid one is shown as
refused with the reason.

### E. Co-op investigation: design spike first [playtests; informs the engine]
No code until a design is agreed. Open questions:
- **Model:** shared board with one authoritative action log, or two analysts with separate evidence and a joint
  finding? The first is a small change; the second changes the game.
- **Authority:** host-runs-engine, dedicated server, or Steam relay. The desktop shell blocks all network by
  construction, so any option means a deliberate, narrow exception in the main process, not the page.
- **Engine and save changes:** an actor id on actions, per-actor or shared budget, conflict rules for
  simultaneous moves, and a save format v2 (the current one is single-player).
- **Validation (cf. #270):** the authoritative side must re-run `dispatch` and reject by code. Caps on action
  size, rate, and log length; no client-supplied state, only actions.
**Done when:** a written design with a threat model and a decision on the model above, reviewed before any
implementation issue is opened.

## 4. Suggested order

1. **A**, then **B**: useful even if nothing else ships (more authored cases, safer loading).
2. **C** locally, then online when #363 has a test app.
3. **D** after C.
4. **E** spike in parallel once playtests say the loop is worth multiplying.

## 5. Decisions needed from the owner

1. Is Workshop worth doing before the playtests, or should A and B wait too?
2. Leaderboards: Steam only, or a self-hosted backend? (Steam only keeps the "no servers" promise.)
3. Co-op: shared board or separate analysts, or defer entirely?
4. Who moderates Workshop content, and what is the policy for text that is not plain-text-safe?

## 6. Out of scope here

Voice or text chat, matchmaking, anti-cheat beyond replay verification, monetisation, and non-text
(image, audio) evidence in cases.
