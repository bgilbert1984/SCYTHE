# Game cases

Authored, read-only case files for SCYTHE: GAME. Design: `../GAME_DESIGN.md`.

- `signal_ghost/case.json`: the vertical-slice mission (contract `game_case_0.1.0`).
- `lib/validate.js`: contract and referential checks.
- `lib/posterior.js`: reference posterior p* (design §3.4), deterministic.

Run from the repo root:

    node --test game/cases/

The case file is ground truth and is never mutated at runtime. Player state (confidence, links,
rejections) lives in the save, not here.
