# SCYTHE: GAME — Design Document

**Status:** DRAFT v0.1 — vertical slice scope only
**Milestone:** SCYTHE: GAME — Vertical Slice
**Related:** `docs/niantic_scythe.md` (evidence/variant data contract inspiration, not an implementation commitment)

Items marked **[OPEN]** are undecided and need an owner before implementation.

---

## 1. Product definition

SCYTHE: GAME is an intelligence-analysis game. The player is an analyst weighing
conflicting evidence, not an operator tuning a radio.

**Core loop:**

> Observe → Collect → Hypothesize → Test → Reduce uncertainty → Commit findings

**Pillars**

1. **Evidence has strength.** Every item has provenance, reliability, and known limits.
2. **Uncertainty is a resource.** Players spend actions to reduce it and are scored on calibration.
3. **Correlation is not cause.** Each case contains plausible but wrong explanations.
4. **Consequential commitment.** Findings are committed once, and the debrief reveals the truth.
5. **Understandable without a manual.** No RF background required to play the opening mission.

**Non-goals (vertical slice):** live RF/SDR input, real-world surveillance data, multiplayer,
Workshop, Steamworks, procedural missions, scene reconstruction pipelines.

---

## 2. Vertical slice scope

One authored mission, **SIGNAL GHOST**, playable start to finish offline, with save/load
and deterministic replay.

| In scope (P0/P1) | Out of scope (post-slice) |
|---|---|
| Fixed synthetic evidence set | Live acquisition / hardware |
| 3 competing hypotheses | Multiplayer / co-op |
| 6–10 meaningful player decisions | Workshop / community content |
| Verdict + calibrated scoring + debrief | Steam achievements / cloud saves |
| Desktop prototype, offline | Gaussian-splat → RF converter, AR monocle |

**Exit criterion:** a new player completes SIGNAL GHOST unaided, can explain why they
reached their verdict, and the run replays identically from its save.

---

## 3. Core rules

### 3.1 Entities

| Entity | Meaning |
|---|---|
| **Evidence** | An observation: SDR capture summary, timestamp log, device record, witness report. Immutable once in the case file. |
| **Source** | Where evidence came from; carries a reliability prior and known failure modes. |
| **Hypothesis** | A falsifiable claim about what happened. Has a confidence (0–1) set by the player. |
| **Link** | Player-asserted relation between evidence and hypothesis: `supports`, `contradicts`, or `neutral`. |
| **Test** | An action that produces new evidence or narrows an existing item (see 3.3). |
| **Finding** | A committed conclusion: chosen hypothesis, stated confidence, and cited evidence. |

### 3.2 Evidence strength

Each evidence item exposes to the player (progressively revealed through play):

- **Provenance:** source, collection time, chain of custody.
- **Reliability band:** `weak | moderate | strong` (derived from source + corroboration).
- **Limits:** short text on what it cannot show (e.g. "single receiver, no direction finding").
- **Corroboration count:** independent items agreeing with it.

Two items are *independent* only if their sources share no upstream dependency; the engine
tracks this explicitly so double-counted evidence is detectable.

### 3.3 Player actions

Actions cost **budget** (time-on-case, a fixed integer; **[OPEN]** exact value after playtest).

| Action | Effect |
|---|---|
| Inspect | Reveals provenance and limits of one evidence item. |
| Compare | Reveals agreement/conflict between two items. |
| Corroborate | Requests independent confirmation; may return nothing. |
| Re-evaluate under conditions | Re-reads existing evidence under a different environment variant (see §5). |
| Link / Unlink | Records the player's reading of evidence against a hypothesis. |
| Reject hypothesis | Removes it; irreversible but revisable by restoring (costs budget). |
| Commit finding | Ends the investigation. |

Tests may return **no match**, and the engine distinguishes *no-match* (inference found
nothing) from *resolution failure* (couldn't evaluate). This mirrors open issue #214 and is
a gameplay-visible distinction: "nothing found" is itself weak evidence; "couldn't tell" is not.

### 3.4 Uncertainty model

- Each hypothesis holds a player-set confidence `c ∈ [0,1]`.
- The engine maintains a hidden reference posterior `p*` computed from the authored
  evidence and the actions the player has taken (authored per case, not learned).
- The player never sees `p*` until the debrief.

### 3.5 Scoring

Score rewards being right *and* appropriately sure.

- **Correctness:** did the committed hypothesis match ground truth.
- **Calibration:** proper scoring rule (Brier) between stated confidence and outcome,
  compared against `p*`. Unsupported certainty is penalised more than honest uncertainty.
- **Citation quality:** committed evidence must actually bear on the claim; irrelevant or
  double-counted citations lose points.
- **Efficiency:** minor weight; must never reward guessing early.

**[OPEN]** Weighting across the four terms; to be tuned in playtests.

### 3.6 Win and end states

No binary win. Outcomes: *Sound & correct*, *Correct but overconfident*, *Sound but wrong
(case was misleading)*, *Unsupported*. The debrief explains which and why.

---

## 4. Mission: SIGNAL GHOST

**Premise.** A fictional transmitter appears intermittently in a coastal city (fictional,
no real places or transmissions). The player receives contradictory observations and must
establish what actually happened.

**Evidence set (synthetic, fixed).** ~12–16 items across four types:
SDR observation summaries, timestamp logs, device records, witness reports.

**Competing hypotheses** (final wording **[OPEN]**; structure fixed):

| ID | Claim | Intended trap |
|---|---|---|
| H1 | One mobile transmitter moving along the waterfront | Matches timestamps; fits the loudest clue |
| H2 | Two fixed transmitters alternating | Fits the device records; contradicted by one witness |
| H3 | One fixed transmitter whose apparent strength and presence change with atmospheric conditions | Truth; only visible after re-evaluating under variants |

**Designed misleading correlations**

- A strong correlation between ferry schedule and signal appearance (coincidence).
- A witness report that is confident but from a source with a known timing bias.
- Two "independent" captures that share an upstream receiver (double-counted evidence).

**Decision points** (6–10; draft list **[OPEN]**): which items to inspect first, whether to
spend budget corroborating the ferry correlation, whether to re-evaluate under fog/rain,
which hypothesis to reject and when, and what confidence to commit at.

**Debrief.** Shows ground truth, which clues were misleading and why, the player's
calibration against `p*`, and the single decision that mattered most.

---

## 5. Evidence and environment data contract

Adopted from `docs/niantic_scythe.md`, reduced to what the slice needs. The scene is
**hand-authored and small**; no reconstruction pipeline is involved.

### 5.1 Identity

Every case file carries:

```json
{
  "case_id": "signal_ghost",
  "env_id": "coastal_city_01",
  "variant_id": "clear_day",
  "contract_version": "game_case_0.1.0"
}
```

Variants for the slice: `clear_day`, `fog_morning`, `post_rain_evening`. Geometry and
entity IDs are **stable across variants**; only conditions-dependent values change.

### 5.2 Nodes and edges (game-level hypergraph)

| Node | Notes |
|---|---|
| `EnvNode` | `env_id` + `variant_id` |
| `EvidenceNode` | One per evidence item; immutable payload |
| `SourceNode` | Reliability prior and limits |
| `HypothesisNode` | Authored claim; player confidence stored in save state, not the node |
| `DeviceNode` | Fictional receivers/transmitters |

| Edge | Meaning |
|---|---|
| `ObservedUnder` | Evidence → EnvNode variant it was captured under |
| `DerivedFrom` | Evidence → Source (and upstream evidence, to detect shared dependency) |
| `Supports` / `Contradicts` | Authored ground-truth relations (hidden from player) |
| `PlayerLink` | Player-asserted relation (stored in save state) |

Authored ground truth and player state are kept in **separate stores**. The case file is
read-only at runtime.

### 5.3 Determinism

- All case data is plain JSON, sorted by ID on serialisation.
- Any randomness (e.g. whether a corroboration request returns a result) is seeded from
  `(case_id, save_seed, action_index)`.
- Rule: same case file + same seed + same action log ⇒ identical state.

---

## 6. Architecture constraints

- **Isolated game runtime.** Game state and simulated evidence never share a process,
  credentials, or storage with live RF acquisition, production services, or hardware controls.
- **No heavyweight dependencies for players.** No DuckDB, FusionAuth, SDR hardware, or
  orchestrator needed to play. **[OPEN]** runtime choice (embedded web view vs. native engine).
- **Reuse where cheap:** Mission, Evidence, Graph, and Timeline concepts. Cesium only if it
  can be bundled offline; otherwise a 2D map is acceptable for the slice.
- **Player-facing Mission Hub** is a separate entrypoint (New Investigation, Continue, Career)
  from the operator dashboard. The operator Hub is unchanged.
- **Actions go through explicit, validated mutation paths** (cf. #217, #218, #270). The
  game rejects unsupported or malformed actions explicitly rather than ignoring them.

---

## 7. Save and replay

A save is: `case_id`, `contract_version`, `seed`, ordered **action log**, and derived
player state (confidence, links, rejected set). State is always reconstructible from
`case + seed + action log`; the derived copy is a cache.

Replay re-executes the action log and must reproduce the final score exactly.
Saves are versioned and rejected with a clear message on contract mismatch.

---

## 8. Playtest plan

Questions to answer before expanding scope:

1. Do players understand evidence strength without reading documentation?
2. Do players use "re-evaluate under conditions", or never discover it?
3. Does confidence calibration feel fair, or punishing?
4. Is the case solvable by guessing the ferry correlation? (It must not be.)
5. Do players explain their verdict in their own words afterward?

Target: 5–8 observed sessions with players unfamiliar with SCYTHE before the slice is
declared done.

---

## 9. Backlog mapping

| Priority | Item | Section |
|---|---|---|
| P0 | GAME_DESIGN.md and vertical slice definition | this doc |
| P0 | One complete investigation (SIGNAL GHOST) | §4, §5 |
| P0 | Isolated game runtime | §6 |
| P0 | Player-facing Mission Hub | §6 |
| P1 | Evidence/hypothesis workbench | §3 |
| P1 | Uncertainty and scoring | §3.4–3.5 |
| P1 | Save games and deterministic replay | §5.3, §7 |
| P1 | Desktop prototype | §6 |
| P2 | Steamworks, Workshop, challenges, multiplayer | out of slice |

Engine issues that affect the game (not blocked on, but relevant): #214, #216, #217,
#218, #270.

---

## 10. Open questions

1. Runtime choice: embedded web view or native engine?
2. Budget model: fixed action count or in-fiction time?
3. Is the Brier-style scoring legible to non-technical players, or does it need a simpler presentation?
4. Cesium offline bundling feasibility versus a 2D map.
5. Does the game track live in this repo under `game/` or in a separate one? (Drafted here under `game/`.)
6. Licensing and attribution for any adopted third-party scene tooling, once it matters (post-slice).
