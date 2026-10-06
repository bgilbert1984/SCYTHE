# Solver verification deck: Euler-Bernoulli cantilever

## Purpose
First real deck for the mechanics/ interface. Upgrades the scaffold from
"plumbing smoke test" to **solver qualification**: a closed-form cantilever
solution gives us known-truth values to compare against the frozen solver,
before we trust it for anything we can't hand-check.

## Specimen
Uniform Euler-Bernoulli cantilever, distributed end-face traction.

| Quantity | Value |
|---|---|
| Length L | 1.0 m (along X) |
| Section | 0.05 m × 0.05 m square (Y × Z, centered) |
| Material | Steel: E = 200 GPa, ν = 0.3, ρ = 7850 kg/m³ |
| Load | 1000 N total, -Z, distributed over end face (x=L) |
| Load type | Tributary-area-weighted nodal traction (NOT a point load) |
| Wall | x = 0, all translations/rotations fixed |

Units: SI (kg, m, s) throughout.

**Why distributed, not point:** A concentrated nodal force creates a local
singular/distorted compliance field. Refining the mesh refines the
singularity as well as the beam, which can break monotonic convergence even
when the solver is correct. The distributed traction tests beam deformation,
not local indentation.

## Closed-form references
- **Tip deflection:** δ = PL³/(3EI) = **3.200000 mm**
  - I = 5.208333e-7 m⁴
  - Tests: static stiffness / element formulation / mesh convergence
- **Reaction:** Fz = 1000 N, My = **-1000 N·m** (support/wall balancing reaction)
  - With beam along +X and load in -Z, external tip moment is +Y·P·L;
    the wall's balancing reaction is -Y·P·L.
  - Tests: equilibrium / load-path correctness
- **First natural frequency:** f₁ = 40.7690 Hz (β₁L = 1.87510407)
  - **Status: NOT_YET_VERIFIED** — dynamic verification is a separate gate
    requiring impulse/release or broadband excitation, ringdown, and a
    documented spectral estimator. Not established by the quasi-static run.
- **Shear correction** (Timoshenko): 0.00624 mm (0.195% of bending)
  - Confirms Euler-Bernoulli is an adequate reference at L/h = 20.

See `analytical_reference.json` for machine-readable values.

## Meshes
Three densities for convergence study (refinement ratio r = 2):

| Mesh | Divisions (x,y,z) | Bricks | Nodes | Face nodes |
|---|---|---|---|---|
| coarse | 10×2×2 | 40 | 99 | 9 |
| medium | 20×4×4 | 320 | 525 | 25 |
| fine | 40×8×8 | 2560 | 3321 | 81 |

8-node hexahedra (`/BRICK`), Isolid=1 (one-point, viscous hourglass).

## Loading
Quasi-static: three `/CLOAD` cards (corners / edges / interior), each ramped
0 → -1 over 0.1 s via `/FUNCT/1`, hold to T=0.5 s. Tributary-weighted so the
nodal forces sum to exactly 1000 N with uniform-pressure distribution.

## Frozen static estimator (pre-registered 2026-10-05)
- **Definition:** u = mean(DZ_face_avg(t), 0.20 s ≤ t ≤ 0.50 s)
  where DZ_face_avg(t) is the mean Z-displacement over all end-face nodes.
- **Recorded:** tail standard deviation, peak-to-peak ripple, kinetic-energy
  history (to verify quasi-static conditions per Radioss guidance).
- `/TFILE` 0.0005 s → 2 kHz sampling; ~49 samples per 24.5 ms first-mode period.

## Acceptance bounds (FROZEN — pre-registered 2026-10-05)
Direct errors against analytical (r = 2):
- e_coarse = |u_coarse − u_exact| / |u_exact|
- e_medium, e_fine similarly
- p_cm = ln(e_coarse/e_medium)/ln(2), p_mf = ln(e_medium/e_fine)/ln(2)
  (recorded, not gated)

**Frozen gate:**
- e_fine ≤ 0.02
- e_coarse > e_medium > e_fine (strict monotonic)
- Reaction Fz within ±1% of 1000 N

Verdict: `VERIFIED_WITHIN_DECLARED_ENVELOPE` or investigate.

## Element formulation scope
`/PROP/SOLID` Isolid=1: one-point 8-node brick with viscous hourglass
stabilization. This qualification is **scoped to Isolid=1**; it does not
imply other formulations (e.g. HEPH Isolid=24). The verification record
captures the resolved Starter hourglass parameters.

## Provenance
- Solver: OpenCourant release `33e685176cccf0c539a3ce07aa2096985a284e2a`
- Generator: `generate_deck.py` — **the source of truth**; must reproduce
  the committed .rad files.
- Analytical reference: `analytical_reference.json`
- Regression test: `test_scythe_solver_verification_deck.py` (in CI glob)

## Status
- [x] Generator reproduces committed decks
- [x] Decks pass starter (0 volume errors, all meshes)
- [x] Field-width regression test in CI
- [ ] Engine runs + TH extraction (pending re-review)
- [ ] Residuals and SOLVER_VERIFICATION record

## Format notes (hard-won)
- `/NODE` coordinates: **20-char fields** (not 10-char). 10-char silently
  misparses → zero-volume errors. Enforced by regression test.
- `/PROP/SOLID` q_a/q_b: `1E-30` in 20-char fields (byte-exact).
- `/BCS`: Trarot is ONE field with six Boolean DOFs: `   111 111`.
  Writing 111 and 111 as separate 10-char fields shifts subsequent fields.
- `/MAT/ELAST` must precede `/ANALY` (starter card-order requirement).
- Deck must start with `#RADIOSS STARTER` header.
