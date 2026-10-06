# decks/

Solver input decks. **Decks are data**: versioned, hashed, reviewed —
never executed by SCYTHE. The solver runs out-of-tree by hand (see
`run.sh`); only the exported CSV/VTK/JSON crosses the boundary.

## Convention

```
decks/<name>/
  <name>.rad     # native Radioss deck (or .k LS-DYNA, converted via inp2rad)
  NOTES.md       # what this deck models: geometry, material cards,
                 # boundary conditions, loading, node ids of interest
                 # (phase-center node, mount nodes, enclosure nodes)
```

`NOTES.md` is the human-readable source for the `material_model_digest`
and `boundary_condition_digest` provenance fields — hash the file.

## Node ids of interest

Every deck's NOTES.md MUST name:

- the **phase-center node** (antenna phase center; the importer reads this
  node's displacement/velocity history),
- **mount nodes** (for mount_flex_m),
- **enclosure nodes** (for enclosure_vibration_mps2, the quantity an
  accelerometer corroborates).

## Regime note

Keep explicit-dynamics decks (OpenCourant) to problems that need them:
impacts, large deformation, contacts, violent transients — drone arms,
rotor blades, landing events. Small-amplitude receiver vibration is a
modal problem; those decks belong to a modal solver and are welcome here
under `solver.name: CalculiX` (or Code_Aster), same schema.
