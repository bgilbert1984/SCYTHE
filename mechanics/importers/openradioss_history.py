"""Importer stub: OpenCourant/OpenRadioss time-history -> mechanical_state.v1.

Reads the CSV produced by the solver's `th_to_csv` converter and emits
schema-conformant SIMULATED_MECHANICAL_STATE records, one per timestep.

STATUS: stub. The exact `th_to_csv` column contract (node/element channel
naming, units, time column header) gets pinned against the first real deck
run. Until then this defines the interface: the contract the importer
expects, the mapping it performs, and the label it refuses to violate.

Hard boundary: this module reads solver *exports*. It never imports,
links, shells to, or vendors the solver. AGPL-3.0 stays on its side.
"""

import csv
import json
import math
import sys

SCHEMA_ID = "scythe.mechanical-state.v1"
STATE_CLASS = "SIMULATED_MECHANICAL_STATE"

# Expected CSV contract (to be pinned against th_to_csv output):
#   time,<node>_dx,<node>_dy,<node>_dz,<node>_vx,...
# where <node> is the phase-center / antenna-mount node id from the deck.
# Units: metres, m/s, seconds. Column names are matched case-insensitively.
TIME_HEADERS = {"time", "t", "time_s"}


def _find_time_column(fieldnames):
    for i, name in enumerate(fieldnames):
        if name.strip().lower() in TIME_HEADERS:
            return i
    raise ValueError(f"no time column in {fieldnames!r}")


def _channel_index(fieldnames, node, suffix):
    """Locate e.g. node 1042's 'dx' channel. Returns None if absent."""
    want = f"{node}_{suffix}".lower()
    for i, name in enumerate(fieldnames):
        if name.strip().lower() == want:
            return i
    return None


def parse_th_csv(path, node):
    """Yield (t, channels) per row. channels maps suffix -> float.

    Suffixes understood: dx, dy, dz (displacement, m),
    vx, vy, vz (velocity, m/s), ax, ay, az (acceleration, m/s^2).
    Missing suffixes yield None (record degrades gracefully; the
    schema marks which receiver fields are required).
    """
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        fieldnames = next(reader)
        ti = _find_time_column(fieldnames)
        idx = {s: _channel_index(fieldnames, node, s) for s in
               ("dx", "dy", "dz", "vx", "vy", "vz", "ax", "ay", "az")}
        for row in reader:
            if not row or not row[ti].strip():
                continue
            ch = {}
            for s, i in idx.items():
                ch[s] = float(row[i]) if i is not None and row[i].strip() else None
            yield float(row[ti]), ch


def _vec(ch, *suffixes):
    return [ch[s] if ch[s] is not None else 0.0 for s in suffixes]


def _normalize(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else v


def to_mechanical_state(t, ch, solver, provenance, antenna_normal=(0.0, 0.0, 1.0)):
    """Build one schema instance from a single timestep's channels."""
    disp = _vec(ch, "dx", "dy", "dz")
    vel = _vec(ch, "vx", "vy", "vz")
    acc = _vec(ch, "ax", "ay", "az")
    return {
        "schema": SCHEMA_ID,
        # The label is a constant. This importer MUST NOT emit anything else.
        "state_class": STATE_CLASS,
        "solver": solver,
        "simulation_time_s": t,
        "receiver": {
            "phase_center_displacement_m": disp,
            "phase_center_m": disp,  # sim frame; nominal offset applied by consumer
            "velocity_mps": vel,
            "acceleration_mps2": acc,
            "antenna_normal": _normalize(list(antenna_normal)),
        },
        "provenance": provenance,
    }


def main(argv):
    if len(argv) != 4:
        print("usage: openradioss_history.py <th.csv> <node-id> <provenance.json>",
              file=sys.stderr)
        print("emits one JSON record per timestep to stdout", file=sys.stderr)
        return 2
    csv_path, node, prov_path = argv[1], argv[2], argv[3]
    provenance = json.load(open(prov_path))
    solver = {
        "name": "OpenCourant",
        "commit": provenance.get("solver_commit", ""),
        "build": provenance.get("solver_build", ""),
    }
    for t, ch in parse_th_csv(csv_path, node):
        print(json.dumps(to_mechanical_state(t, ch, solver, provenance)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
