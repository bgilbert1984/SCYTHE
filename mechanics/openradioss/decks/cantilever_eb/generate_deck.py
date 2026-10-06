"""Generate the Euler-Bernoulli cantilever verification deck (3 meshes).

Uniform cantilever, point load at free end. Closed-form references:
  tip deflection  d = P*L^3 / (3*E*I)
  reaction force  = P, reaction moment = P*L
  1st nat. freq.  f1 = (b1*L)^2/(2*pi) * sqrt(E*I/(rho*A*L^4)), b1*L=1.87510407

Writes: decks/cantilever_eb/<density>/{cantilever_0000.rad, cantilever_0001.rad}
plus analytical_reference.json (frozen inputs for the verification record).
"""

import json
import math
import os

# ---------------------------------------------------------------- inputs
L = 1.0          # m, beam length (along X)
B = 0.05         # m, section width  (Y)
H = 0.05         # m, section height (Z)
E = 200.0e9      # Pa
NU = 0.3
RHO = 7850.0     # kg/m^3
P = 1000.0       # N, tip load in -Z
T_END = 0.5      # s, engine stop time
RAMP_T = 0.1     # s, load ramp time

A = B * H
I = B * H**3 / 12.0
BETA1L = 1.87510407

# ------------------------------------------------------- analytical refs
d_tip = P * L**3 / (3 * E * I)
f1 = (BETA1L**2 / (2 * math.pi)) * math.sqrt(E * I / (RHO * A * L**4))
# Timoshenko shear correction (for the record; expected << bound)
G = E / (2 * (1 + NU))
k_shear = 5.0 / 6.0
d_shear = P * L / (k_shear * A * G)

print(f"I = {I:.6e} m^4")
print(f"tip deflection (EB) = {d_tip*1e3:.6f} mm")
print(f"shear correction    = {d_shear*1e3:.6f} mm ({d_shear/d_tip*100:.3f}%)")
print(f"reaction Fz = {P:.1f} N, My = {P*L:.1f} N m")
print(f"f1 = {f1:.4f} Hz")

MESHES = {
    "coarse": (10, 2, 2),
    "medium": (20, 4, 4),
    "fine":   (40, 8, 8),
}

OUT = "/tmp/cantilever_eb"


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
                bricks.append((eid, n(0, 0, 0), n(1, 0, 0), n(1, 1, 0),
                               n(0, 1, 0), n(0, 0, 1), n(1, 0, 1),
                               n(1, 1, 1), n(0, 1, 1)))
    wall = [node_id(0, iy, iz, ny, nz) for iy in range(ny + 1)
            for iz in range(nz + 1)]
    tip = node_id(nx, ny // 2, nz // 2, ny, nz)
    return nodes, bricks, wall, tip


def f10(v):
    """10-char field."""
    if isinstance(v, int):
        return f"{v:10d}"
    return f"{v:10.4E}" if abs(v) >= 1e5 or (abs(v) < 1e-3 and v != 0) else f"{v:10.4f}"


def write_starter(path, nodes, bricks, wall, tip):
    L_ = []
    A_ = L_.append
    A_("#RADIOSS STARTER")
    A_("# SCYTHE solver-verification deck: Euler-Bernoulli cantilever")
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/BEGIN")
    A_("CANTILEVER_EB" + " " * 56)
    A_("      2026         0")
    A_("                  kg                   m                   s")
    A_("                  kg                   m                   s")
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/TITLE")
    A_("Euler-Bernoulli cantilever verification")
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/ANALY")
    A_("         0                   0         0")
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/DEF_SOLID")
    A_("         0         0                   0                                       0")
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    # material
    A_("/MAT/ELAST/1")
    A_("steel")
    A_("".join(f10(x) for x in [RHO, 0]))
    A_("".join(f10(x) for x in [E, NU]))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    # nodes
    A_("/NODE")
    for nid, x, y, z in nodes:
        A_("".join(f10(v) for v in [nid, x, y, z]))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    # bricks
    A_("/BRICK")
    for b in bricks:
        A_("".join(f10(v) for v in b))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    # property + part
    A_("/PROP/SOLID/1")
    A_("cantilever solid")
    A_("".join(f10(v) for v in [1, 0, 0, 0, 0, 0, 2, 0]))
    A_("".join(f10(v) for v in [1e-30, 1e-30, 0, 0, 0]))
    A_("".join(f10(v) for v in [0]))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/PART/1")
    A_("cantilever")
    A_("".join(f10(v) for v in [1, 1, 0]))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    # wall BC
    A_("/BCS/1")
    A_("fixed wall x=0")
    A_("".join(f10(v) for v in [111, 111, 0, 1]))
    A_("/GRNOD/NODE/1")
    A_("wall nodes")
    for i in range(0, len(wall), 8):
        A_("".join(f10(v) for v in wall[i:i + 8]))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    # tip load, ramped by /FUNCT/1 (0 -> -1): Fscaley * funct = -P in Z
    A_("/CLOAD/1")
    A_(f"{1:>10}{'Z':>10}{0:>10}{0:>10}{2:>10}{0:>20.1f}{P:>20.4f}")
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/GRNOD/NODE/2")
    A_("tip node")
    A_("".join(f10(v) for v in [tip]))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/FUNCT/1")
    A_("load ramp 0->-1")
    A_("".join(f10(v) for v in [0.0, 0.0]))
    A_("".join(f10(v) for v in [RAMP_T, -1.0]))
    A_("".join(f10(v) for v in [10.0, -1.0]))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/TH/NODE/1")
    A_("tip displacement")
    A_("DEF       ")
    A_("".join(f10(v) for v in [tip, 0]))
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
    A_("/END")
    open(path, "w").write("\n".join(L_) + "\n")


def write_engine(path):
    L_ = []
    A_ = L_.append
    A_("# OpenCourant engine input: cantilever verification")
    A_("#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|")
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


os.makedirs(OUT, exist_ok=True)
for name, (nx, ny, nz) in MESHES.items():
    d = os.path.join(OUT, name)
    os.makedirs(d, exist_ok=True)
    nodes, bricks, wall, tip = build_mesh(nx, ny, nz)
    write_starter(os.path.join(d, "cantilever_0000.rad"), nodes, bricks, wall, tip)
    write_engine(os.path.join(d, "cantilever_0001.rad"))
    print(f"{name}: {len(nodes)} nodes, {len(bricks)} bricks, tip node {tip}")

ref = {
    "geometry": {"L_m": L, "B_m": B, "H_m": H},
    "material": {"E_Pa": E, "nu": NU, "rho_kg_m3": RHO},
    "load": {"P_N": P, "direction": "-Z", "ramp_s": RAMP_T},
    "analytical": {
        "tip_deflection_m": d_tip,
        "reaction_Fz_N": P,
        "reaction_My_Nm": P * L,
        "f1_Hz": f1,
        "shear_correction_m": d_shear,
        "shear_fraction": d_shear / d_tip,
    },
    "meshes": {k: {"nx": v[0], "ny": v[1], "nz": v[2]} for k, v in MESHES.items()},
}
json.dump(ref, open(os.path.join(OUT, "analytical_reference.json"), "w"), indent=1)
print("wrote analytical_reference.json")
