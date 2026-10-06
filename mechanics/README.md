# mechanics/

Mechanical truth for SCYTHE's RF inference stack.

## What this is

SCYTHE's measurement model has always distinguished *receiver identity*
from *receiver state* (position, heading). This directory adds the missing
branch: **mechanical state** — what the receiver's structure was doing at
the instant of each RF observation.

```
receiver identity
  ├── RF configuration
  ├── position / GPS
  ├── orientation / heading
  └── mechanical-state estimate      <-- this directory
       ├── antenna phase-center displacement
       ├── antenna normal vector
       ├── velocity
       ├── acceleration
       ├── mount flex
       ├── enclosure vibration
       └── uncertainty
```

The solver (OpenCourant, the community fork of OpenRadioss) lives **behind
a hard process boundary**. It is AGPL-3.0. No solver code is vendored,
linked, or imported here — not the engine, not the starter, not the
converters. The solver runs out-of-tree; SCYTHE consumes only its exported
artifacts (CSV, VTK, JSON metadata).

## The one distinction that matters

Every record emitted here carries `state_class`:

- `SIMULATED_MECHANICAL_STATE` — a physics-derived hypothesis from a solver.
- `MEASURED_RECEIVER_STATE` — from instruments (GPS, IMU, accelerometer).

A simulation never becomes a measurement. The schema enforces the label;
the importer refuses to emit anything else.

## Evidence discipline (merge-gated)

Three rules the importer and provenance helpers enforce, with negative
controls in `test_scythe_mechanics.py`:

1. **Absence never means zero.** A missing solver channel column aborts the
   import naming the absent channels (`MissingChannelError`). A blank cell
   refuses that timestep — it is not emitted, and the refusal count is
   reported. Nothing is ever zero-filled.
2. **Orientation is never assumed.** `antenna_normal` has no default; it
   must be derived from deck geometry (boresight node minus phase-center
   node) and passed in, or derived from boresight channels in the export.
3. **Provenance must be internally satisfiable.** Every record carries the
   full chain — deck, mesh, materials, boundary conditions, solver commit,
   export. An embedded mesh binds `mesh_sha256` to `deck_sha256` (one
   artifact, one digest); material and boundary-condition digests are
   required, never empty. `provenance_complete()` is the gate.

## Layout

- `schema/` — the interchange contract (`mechanical_state.v1.json`) plus a
  worked example. Versioned; v1 freezes once first used in anger.
- `importers/` — converts solver exports into schema instances.
  `openradioss_history.py` reads `th_to_csv` output and refuses absent
  evidence (see above). Stub: the exact `th_to_csv` column contract gets
  pinned on the first real deck.
- `openradioss/` — decks and the documented solver invocation. Decks are
  *data*: versioned, hashed, never executed by SCYTHE.
- `provenance.py` — hashing helpers. Every record carries the full chain:
  deck, mesh, materials, boundary conditions, solver build, export.

## Pinned solver

See `/home/bgilbert1984/OpenCourant/ARCHIVE_MANIFEST.md` on dedirock.
Commit `33e685176cccf0c539a3ce07aa2096985a284e2a`, SHA-256-archived
release zip (`d7b9876e…44650f7`). The solver is a frozen instrument:
if upstream vanishes tomorrow, the tarball and the hash don't care.

## Solver-regime note

OpenCourant/OpenRadioss is an *explicit-dynamics* code: crashes, impacts,
large deformation, violent transients. It is the right tool for drone
structural dynamics (rotor flex, landing impacts, damaged blades). It is
the *wrong* tool for small-amplitude receiver vibration (the §5.21
mechanical-susceptibility experiment) — that is a linear modal problem
and belongs to an implicit modal solver (CalculiX, Code_Aster). This
directory's schema is solver-agnostic; the `solver.name` field says which
instrument produced each record.
