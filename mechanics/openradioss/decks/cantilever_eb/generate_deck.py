"""Generate the Euler-Bernoulli cantilever verification deck (3 meshes).

Uniform cantilever, DISTRIBUTED end-face traction (not a point load --
a concentrated nodal force creates a local singular compliance field that
pollutes mesh convergence). Closed-form references:
  tip deflection  d = P*L^3 / (3*E*I)
  reaction force  = P, reaction moment = -P*L (support reaction, -Y)
  1st nat. freq.  f1 = (b1*L)^2/(2*pi) * sqrt(E*I/(rho*A*L^4)) [NOT_YET_VERIFIED]

Writes decks + analytical_reference.json. The generator is the source of
truth: it must reproduce the committed .rad files.
"""

import json
import math
import os
import sys

# ---------------------------------------------------------------- inputs
L = 1.0          # m, beam length (along X)
B = 0.05         # m, section width  (Y)
H = 0.05         # m, section height (Z)
E = 200.0e9      # Pa
NU = 0.3
RHO = 7850.0     # kg/m^3
P = 1000.0       # N, total end-face traction in -Z
T_END = 0.5      # s, engine stop time
RAMP_T = 0.1     # s, load ramp time
# Frozen static estimator (pre-registered 2026-10-05):
#   u = mean(DZ_face_avg, 0.20 s <= t <= 0.50 s), plus tail std and p2p ripple.
EST_T0 = 0.20
EST_T1 = 0.50

A = B * H
I = B * H**3 / 12.0
BETA1L = 1.87510407

# ------------------------------------------------------- analytical refs
# Signed: load is -Z, so the Z-displacement is negative.
d_tip_z = -P * L**3 / (3 * E * I)  # ≈ -0.0032 m
d_tip_mag = P * L**3 / (3 * E * I)  # +0.0032 m magnitude
f1 = (BETA1L**2 / (2 * math.pi)) * math.sqrt(E * I / (RHO * A * L**4))
G = E / (2 * (1 + NU))
k_shear = 5.0 / 6.0
d_shear = P * L / (k_shear * A * G)
# Support reaction moment: beam along +X, load in -Z at x=L.
# External tip moment about wall = +Y*P*L; wall balancing reaction = -Y*P*L.
reaction_My = -P * L
# Wall Z-reaction balances the -Z applied load: +1000 N.
reaction_Fz = P

print(f"I = {I:.6e} m^4")
print(f"tip DZ (EB, signed) = {d_tip_z*1e3:.6f} mm")
print(f"shear correction    = {d_shear*1e3:.6f} mm ({d_shear/d_tip_mag*100:.3f}%)")
print(f"reaction Fz = {reaction_Fz:.1f} N, My = {reaction_My:.1f} N m (support)")
print(f"f1 = {f1:.4f} Hz [NOT_YET_VERIFIED - separate dynamic gate]")

MESHES = {
    "coarse": (10, 2, 2),
    "medium": (20, 4, 4),
    "fine":   (40, 8, 8),
}

OUT = os.path.dirname(os.path.abspath(__file__))
# Filenames match committed canonical decks: cantilever_{mesh}_0000.rad)


def node_id(ix, iy, iz, ny, nz):
    return ix * (ny + 1) * (nz + 1) + iy * (nz + 1) + iz + 1


def build_mesh(nx, ny, nz):
    nodes = []
    for ix in range(nx + 1):
        for iy in range(ny + 1):
            for iz in range(nz + 1):
                x = L * ix / nx
                y = B * (iy / ny - 0.5)
                z = H * (iz / nz - 0.5)
                nodes.append((node_id(ix, iy, iz, ny, nz), x, y, z))
    bricks = []
    eid = 0
    for ix in range(nx):
        for iy in range(ny):
            for iz in range(nz):
                eid += 1
                n = lambda a, b, c: node_id(ix + a, iy + b, iz + c, ny, nz)
                # in-face order matches law42 QA example: (0,1)->(0,0)->(1,0)->(1,1)
                bricks.append((eid, n(0, 1, 0), n(0, 0, 0), n(1, 0, 0),
                               n(1, 1, 0), n(0, 1, 1), n(0, 0, 1),
                               n(1, 0, 1), n(1, 1, 1)))
    wall = [node_id(0, iy, iz, ny, nz) for iy in range(ny + 1)
            for iz in range(nz + 1)]
    # End-face (x=L) node classes for tributary-weighted traction.
    # Uniform pressure p = P/A; tributary areas: corner 1/4, edge 1/2, interior 1.
    dx, dy = B / ny, H / nz
    cell = dx * dy
    corners, edges, interior = [], [], []
    for iy in range(ny + 1):
        for iz in range(nz + 1):
            nid = node_id(nx, iy, iz, ny, nz)
            is_cy = iy in (0, ny)
            is_cz = iz in (0, nz)
            if is_cy and is_cz:
                corners.append(nid)
            elif is_cy or is_cz:
                edges.append(nid)
            else:
                interior.append(nid)
    p = P / A
    f_corner = p * cell / 4.0
    f_edge = p * cell / 2.0
    f_interior = p * cell
    face_all = corners + edges + interior
    # sanity: total must equal P
    total = len(corners) * f_corner + len(edges) * f_edge + len(interior) * f_interior
    assert abs(total - P) / P < 1e-12, f"tributary total {total} != {P}"
    loads = [
        ("face corners", corners, f_corner),
        ("face edges", edges, f_edge),
        ("face interior", interior, f_interior),
    ]
    return nodes, bricks, wall, face_all, loads


def f10(v):
    """10-char field."""
    if isinstance(v, int):
        return f"{v:10d}"
    return f"{v:10.4E}" if abs(v) >= 1e5 or (abs(v) < 1e-3 and v != 0) else f"{v:10.4f}"


def f20(v):
    """20-char field for node coordinates (matches law42 QA format)."""
    if isinstance(v, int) or v == int(v):
        return f"{int(v):>20d}"
    s = f"{v:.14f}"
    if s.startswith("-"):
        s = "-" + s[1:].lstrip("0")
    else:
        s = s.lstrip("0")
    return f"{s:>20s}"


RULER = "#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|"


def write_starter(path, nodes, bricks, wall, face_all, loads):
    L_ = []
    A_ = L_.append
    A_("#RADIOSS STARTER")
    A_("# SCYTHE solver-verification deck: Euler-Bernoulli cantilever")
    A_("# Distributed end-face traction (tributary-weighted), NOT a point load.")
    A_(RULER)
    A_("/BEGIN")
    A_("CANTILEVER_EB" + " " * 56)
    A_("      2026         0")
    A_("                  kg                   m                   s")
    A_("                  kg                   m                   s")
    A_(RULER)
    A_("/TITLE")
    A_("Euler-Bernoulli cantilever verification")
    A_(RULER)
    # material (must precede /ANALY per starter card-order requirement)
    A_("/MAT/ELAST/1")
    A_("steel")
    A_("".join(f10(x) for x in [RHO, 0]))
    A_("".join(f10(x) for x in [E, NU]))
    A_(RULER)
    A_("/ANALY")
    A_("         0                   0         0")
    A_(RULER)
    A_("/DEF_SOLID")
    A_("         0         0                   0                                       0")
    A_(RULER)
    # nodes: 10-char ID + three 20-char coordinate fields (law42 format)
    A_("/NODE")
    for nid, x, y, z in nodes:
        A_(f"{nid:>10d}" + f20(x) + f20(y) + f20(z))
    A_(RULER)
    # bricks (group id 1 -> part 1)
    A_("/BRICK/1")
    for b in bricks:
        A_("".join(f10(v) for v in b))
    A_(RULER)
    # property: Isolid=1 one-point brick, viscous hourglass (byte-exact q_a/q_b)
    A_("/PROP/SOLID/1")
    A_("cantilever solid")
    A_("         1         0                   0         0         0         0         2                   0")
    A_("               1E-30               1E-30                   0                   0                   0")
    A_("                   0")
    A_(RULER)
    A_("/PART/1")
    A_("cantilever")
    A_("".join(f10(v) for v in [1, 1, 0]))
    A_(RULER)
    # wall BC: Trarot single field, six Boolean DOFs (byte-exact from TWISBEAM QA)
    A_("/BCS/1")
    A_("fixed wall x=0")
    A_("   111 111         0         1")
    A_("/GRNOD/NODE/1")
    A_("wall nodes")
    for i in range(0, len(wall), 8):
        A_("".join(f10(v) for v in wall[i:i + 8]))
    A_(RULER)
    # distributed end-face traction: one /CLOAD per node class, tributary-weighted.
    # funct 1 ramps 0 -> -1; Fscaley > 0 per node; product = -Z traction.
    grnod_id = 2
    for cload_id, (title, nids, fnode) in enumerate(loads, start=1):
        A_(f"/CLOAD/{cload_id}")
        A_(title)
        A_(f"{1:>10}{'Z':>10}{0:>10}{0:>10}{grnod_id:>10}{0:>20.1f}{fnode:>20.4f}")
        A_(f"/GRNOD/NODE/{grnod_id}")
        A_(title)
        for i in range(0, len(nids), 8):
            A_("".join(f10(v) for v in nids[i:i + 8]))
        A_(RULER)
        grnod_id += 1
    A_("/FUNCT/1")
    A_("load ramp 0->-1")
    A_("".join(f10(v) for v in [0.0, 0.0]))
    A_("".join(f10(v) for v in [RAMP_T, -1.0]))
    A_("".join(f10(v) for v in [10.0, -1.0]))
    A_(RULER)
    # TH: end-face DZ for all face nodes (averaged by post-processor),
    # plus wall reactions.
    A_("/TH/NODE/1")
    A_("end-face DZ (averaged)")
    A_("DX        DY        DZ")
    for nid in face_all:
        A_("".join(f10(v) for v in [nid, 0]))
    A_(RULER)
    A_("/TH/NODE/2")
    A_("wall reaction")
    A_("REACX     REACY     REACZ")
    for nid in wall:
        A_("".join(f10(v) for v in [nid, 0]))
    A_(RULER)
    A_("/END")
    open(path, "w").write("\n".join(L_) + "\n")


def write_engine(path):
    L_ = []
    A_ = L_.append
    A_("# OpenCourant engine input: cantilever verification")
    A_(RULER)
    A_("/ANIM/DT")
    A_("".join(f10(v) for v in [0.0, 0.05]))
    A_("/DT")
    A_("".join(f10(v) for v in [0.9, 0.0]))
    A_("/TFILE")
    A_("".join(f10(v) for v in [0.0005]))
    A_("/RUN/CANTILEVER/1")
    A_("".join(f10(v) for v in [T_END]))
    A_("/VERS/2026")
    open(path, "w").write("\n".join(L_) + "\n")


def main():
    # Track serialized load totals (after formatting) for the record.
    serialized_totals = {}
    for name, (nx, ny, nz) in MESHES.items():
        nodes, bricks, wall, face_all, loads = build_mesh(nx, ny, nz)
        s_path = os.path.join(OUT, f"cantilever_{name}_0000.rad")
        e_path = os.path.join(OUT, f"cantilever_{name}_0001.rad")
        write_starter(s_path, nodes, bricks, wall, face_all, loads)
        write_engine(e_path)
        # Compute serialized total: re-parse the formatted Fscaley values.
        total = 0.0
        for _, nids, fnode in loads:
            # fnode formatted as >20.4f in the deck; replicate rounding
            f_ser = float(f"{fnode:>20.4f}")
            total += len(nids) * f_ser
        serialized_totals[name] = total
        n_load = sum(len(nids) for _, nids, _ in loads)
        print(f"{name}: {len(nodes)} nodes, {len(bricks)} bricks, "
              f"{n_load} loaded face nodes, serialized total {total:.4f} N")
    ref = {
        "geometry": {"L_m": L, "B_m": B, "H_m": H},
        "material": {"E_Pa": E, "nu": NU, "rho_kg_m3": RHO},
        "load": {
            "P_N": P,
            "type": "distributed_end_face_traction",
            "weighting": "tributary_area",
            "direction": "-Z",
            "ramp_s": RAMP_T,
            "serialized_total_N": serialized_totals,
            "serialized_note": "Sum of formatted Fscaley values in committed decks; "
                               "differs from 1000 N by rounding only.",
        },
        "element_formulation": {
            "Isolid": 1,
            "description": "one-point 8-node brick, viscous hourglass stabilization",
            "note": "verdict scoped to this formulation; see NOTES.md",
        },
        "analytical": {
            "tip_displacement_z_m": d_tip_z,
            "tip_displacement_z_note": "Signed: load is -Z, DZ is negative. "
                                       "Error formula uses signed values.",
            "tip_deflection_magnitude_m": d_tip_mag,
            "reaction_Fz_N": reaction_Fz,
            "reaction_My_Nm": reaction_My,
            "reaction_My_note": "support (wall balancing) reaction about Y; "
                                "external tip moment is +Y*P*L",
            "f1_Hz": f1,
            "f1_status": "NOT_YET_VERIFIED",
            "shear_correction_m": d_shear,
            "shear_fraction": d_shear / d_tip_mag,
        },
        "static_estimator": {
            "displacement": "u = mean(DZ_face_avg(t), t in [0.20, 0.50] s)",
            "displacement_note": "DZ_face_avg(t) = mean over end-face nodes. "
                                 "Compare signed u against tip_displacement_z_m.",
            "reaction": "Rz = mean(sum(REACZ_wall(t)), t in [0.20, 0.50] s)",
            "reaction_note": "Sum over wall nodes, then time-mean. "
                             "Compare against reaction_Fz_N (+1000 N).",
            "record": ["tail_std", "tail_peak_to_peak"],
            "kinetic_energy": "diagnostic-only for this qualification; "
                              "no frozen pass/fail criterion. Recorded for "
                              "engineering judgment, not gating.",
            "frozen": "2026-10-05",
        },
        "acceptance": {
            "e_fine_le": 0.02,
            "monotonic": "e_coarse > e_medium > e_fine (strict)",
            "error_formula": "|u - u_exact| / |u_exact| with signed values",
            "observed_orders": "recorded, not gated",
            "reaction_Fz_tol": 0.01,
            "frozen": "2026-10-05",
        },
        "meshes": {k: {"nx": v[0], "ny": v[1], "nz": v[2]} for k, v in MESHES.items()},
    }
    json.dump(ref, open(os.path.join(OUT, "analytical_reference.json"), "w"), indent=1)
    print("wrote analytical_reference.json")


if __name__ == "__main__":
    main()
