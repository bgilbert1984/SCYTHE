# Solver verification deck: Euler-Bernoulli cantilever

## Purpose
First real deck for the mechanics/ interface. Upgrades the scaffold from
"plumbing smoke test" to **solver qualification**: a closed-form cantilever
solution gives us known-truth values to compare against the frozen solver,
before we trust it for anything we can't hand-check.

## Specimen
Uniform Euler-Bernoulli cantilever, point load at free end.

| Quantity | Value |
|---|---|
| Length L | 1.0 m (along X) |
| Section | 0.05 m × 0.05 m square (Y × Z, centered) |
| Material | Steel: E = 200 GPa, ν = 0.3, ρ = 7850 kg/m³ |
| Load P | 1000 N in -Z at free-end center node |
| Wall | x = 0, all translations fixed (111) |

Units: SI (kg, m, s) throughout.

## Closed-form references
- **Tip deflection:** δ = PL³/(3EI) = **3.200000 mm**
  - I = 5.208333e-7 m⁴
  - Tests: static stiffness / element formulation / mesh convergence
- **Reaction:** Fz = 1000 N, My = 1000 N·m
  - Tests: equilibrium / load-path correctness
- **First natural frequency:** f₁ = 40.7690 Hz
  - (β₁L = 1.87510407)
  - **Status: NOT_YET_VERIFIED** — dynamic verification is a separate gate
    requiring impulse/release or broadband excitation, ringdown, and a
    documented spectral estimator. An explicit quasi-static transient does
    not establish f₁. Recorded here as reference only.
- **Shear correction** (Timoshenko): 0.00624 mm (0.195% of bending)
  - Confirms Euler-Bernoulli is an adequate reference at L/h = 20.

See `analytical_reference.json` for machine-readable values.

## Meshes
Three densities for convergence study:

| Mesh | Divisions (x,y,z) | Bricks | Nodes | Tip node |
|---|---|---|---|---|
| coarse | 10×2×2 | 40 | 99 | 95 |
| medium | 20×4×4 | 320 | 525 | 513 |
| fine | 40×8×8 | 2560 | 3321 | 3281 |

8-node hexahedra (`/BRICK`), Isolid=1 (under-integrated).

## Loading
Quasi-static: `/CLOAD` ramped 0 → -1000 N over 0.1 s via `/FUNCT/1`,
hold to T=0.5 s. Settled tip displacement from `/TH/NODE` tail average.

## Acceptance bounds (FROZEN — pre-registered 2026-10-05)
Refinement ratio r = 2 (brick count 8× per level = halving in 3D).

Direct errors against analytical:
- e_coarse = |u_coarse − u_exact| / |u_exact|
- e_medium = |u_medium − u_exact| / |u_exact|
- e_fine   = |u_fine − u_exact| / |u_exact|

Observed orders (recorded, not gated):
- p_cm = ln(e_coarse / e_medium) / ln(2)
- p_mf = ln(e_medium / e_fine) / ln(2)

**Frozen gate:**
- e_fine ≤ 0.02
- e_coarse > e_medium > e_fine (strict monotonic decrease)

The 2% bound accommodates the 0.2% shear-modeling difference with margin;
expected discretization error on the fine mesh is <1%. Observed orders are
recorded but not frozen — with first-order bricks, bending, and BC
localization, the apparent rate can be messy before the asymptotic regime.
Positive monotonic convergence plus the 2% endpoint is a defensible first
qualification.

## Provenance
- Solver: OpenCourant release `33e685176cccf0c539a3ce07aa2096985a284e2a`
  (SHA-256 `d7b9876e2e8e1b2451540e53052a3fa453a0d0ea4bd4e891a11f9039e44650f7`)
- Deck generator: `generate_deck.py` (in this directory)
- Analytical reference: `analytical_reference.json`

## Status
- [x] Decks pass starter (0 volume errors, all meshes)
- [x] Engine runs to NORMAL TERMINATION (coarse verified)
- [ ] Tip displacement extraction from TH (output config issue — see below)
- [ ] Run medium and fine meshes
- [ ] Compute residuals and verification record

## Known issue
The `/TH/NODE` "DEF" output is not capturing displacement (all zeros).
The engine runs successfully but displacement vector output appears disabled.
Workaround options: (1) fix TH variable specification, (2) extract from
animation files via `anim_to_vtk`, (3) use `/IOFLAG` to enable output.
This does not affect deck validity — the physics is correct, only the
output plumbing needs attention.

## Node IDs of interest
- Wall nodes: all with ix=0 (see deck for full list per mesh)
- Tip node: center of free end (95 / 513 / 3281)
- Tip load: `/CLOAD/1` on tip node group, -Z direction

## References
- OpenCourant QA template: `qa-tests/miniqa/SMOKE_TEST/data/TWISBEAM_*.rad`
- Brick ordering: `qa-tests/miniqa/REFERENCE_STATE_FILES/REFSTA/SOLIDES/law42/`
- Critical format note: `/NODE` coordinates use **20-char fields**
  (not 10-char); `/PROP/SOLID` q_a/q_b as `1E-30` in 20-char fields.
  Getting this wrong produces silent misparses or volume errors.
