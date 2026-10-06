"""Importer: OpenCourant/OpenRadioss time-history -> mechanical_state.v1.

Reads the CSV produced by the solver's `th_to_csv` converter and emits
schema-conformant SIMULATED_MECHANICAL_STATE records, one per timestep.

EVIDENCE DISCIPLINE (load-bearing):
- Absent solver evidence is a REFUSAL, never a zero. A missing channel
  column aborts the import naming the absent channels (MissingChannelError).
  A blank cell refuses that timestep: it is not emitted, and the refusal
  count is reported. Nothing is ever zero-filled.
- Antenna orientation is never assumed. It must be derived from deck
  geometry (boresight node minus phase-center node) and passed in, or
  derived from boresight channels in the export. There is no default.
- phase_center_m is nominal_position + displacement (simulation frame),
  never displacement alone. nominal_position comes from the deck.

Hard boundary: this module reads solver *exports*. It never imports,
links, shells to, or vendors the solver. AGPL-3.0 stays on its side.
"""

import argparse
import csv
import json
import math
import sys

SCHEMA_ID = "scythe.mechanical-state.v1"
STATE_CLASS = "SIMULATED_MECHANICAL_STATE"

TIME_HEADERS = {"time", "t", "time_s"}

# Channels required to build a complete receiver block. All nine must be
# present as columns; any absence is a refusal, not a zero.
REQUIRED_SUFFIXES = ("dx", "dy", "dz", "vx", "vy", "vz", "ax", "ay", "az")


class MissingChannelError(ValueError):
    """Absent solver evidence. Raised, never converted to 0.0."""


def _find_time_column(fieldnames):
    for i, name in enumerate(fieldnames):
        if name.strip().lower() in TIME_HEADERS:
            return i
    raise MissingChannelError(f"no time column in {fieldnames!r}")


def _channel_index(fieldnames, node, suffix):
    want = f"{node}_{suffix}".lower()
    for i, name in enumerate(fieldnames):
        if name.strip().lower() == want:
            return i
    return None


def parse_th_csv(path, node, boresight_node=None):
    """Parse th_to_csv output.

    Returns (rows, refused_count). rows is a list of (t, channels) where
    every REQUIRED_SUFFIXES channel was observed. refused_count is the
    number of timesteps refused for blank cells — reported, never filled.

    Raises MissingChannelError if any required channel column (or any
    requested boresight channel column) is absent from the file.
    """
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        fieldnames = next(reader)
        ti = _find_time_column(fieldnames)

        idx = {}
        missing = []
        for s in REQUIRED_SUFFIXES:
            i = _channel_index(fieldnames, node, s)
            if i is None:
                missing.append(f"{node}_{s}")
            idx[s] = i

        bidx = {}
        if boresight_node is not None:
            for s in ("x", "y", "z"):
                i = _channel_index(fieldnames, boresight_node, s)
                if i is None:
                    missing.append(f"{boresight_node}_{s}")
                bidx[s] = i

        if missing:
            raise MissingChannelError(
                "absent solver channels — refusing, not zero-filling: "
                + ", ".join(missing)
            )

        rows, refused = [], 0
        for row in reader:
            if not row or ti >= len(row) or not row[ti].strip():
                refused += 1
                continue
            ch, bad = {}, False
            for s, i in idx.items():
                v = row[i].strip() if i < len(row) else ""
                if not v:
                    bad = True
                    break
                ch[s] = float(v)
            bch = {}
            if not bad:
                for s, i in bidx.items():
                    v = row[i].strip() if i < len(row) else ""
                    if not v:
                        bad = True
                        break
                    bch[s] = float(v)
            if bad:
                refused += 1
                continue
            if bch:
                ch["boresight"] = bch
            rows.append((float(row[ti]), ch))
    return rows, refused


def derive_antenna_normal(phase_pos, boresight_pos):
    """Antenna boresight unit vector from deck geometry.

    normal = normalize(boresight_position - phase_center_position),
    both in the same frame. Refuses on degenerate input.
    """
    v = [b - p for b, p in zip(boresight_pos, phase_pos)]
    n = math.sqrt(sum(x * x for x in v))
    if n <= 0:
        raise ValueError(
            "cannot derive antenna normal: boresight coincides with phase center"
        )
    return [x / n for x in v]


def to_mechanical_state(t, ch, solver, provenance, nominal_position,
                        antenna_normal):
    """Build one schema instance from a single timestep. Refuses on absence.

    nominal_position: deck-geometry nominal phase-center position (m);
        phase_center_m = nominal_position + displacement.
    antenna_normal: unit vector derived from deck geometry. There is no
        default: orientation evidence the solver did not provide must not
        be fabricated.
    """
    if nominal_position is None:
        raise MissingChannelError(
            "nominal phase-center position is required: "
            "phase_center_m = nominal_position + displacement"
        )
    if antenna_normal is None:
        raise MissingChannelError(
            "antenna_normal is required: derive it from deck geometry "
            "(boresight node minus phase-center node); never assume it"
        )
    disp = [ch["dx"], ch["dy"], ch["dz"]]
    pos = [n + d for n, d in zip(nominal_position, disp)]
    return {
        "schema": SCHEMA_ID,
        # The label is a constant. This importer MUST NOT emit anything else,
        # and no caller parameter can override it.
        "state_class": STATE_CLASS,
        "solver": solver,
        "simulation_time_s": t,
        "receiver": {
            "phase_center_displacement_m": disp,
            "phase_center_m": pos,
            "velocity_mps": [ch["vx"], ch["vy"], ch["vz"]],
            "acceleration_mps2": [ch["ax"], ch["ay"], ch["az"]],
            "antenna_normal": list(antenna_normal),
        },
        "provenance": provenance,
    }


def _parse_vec(text, name):
    try:
        v = [float(x) for x in text.split(",")]
    except ValueError:
        raise ValueError(f"--{name} must be three comma-separated numbers")
    if len(v) != 3:
        raise ValueError(f"--{name} must be three comma-separated numbers")
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="th_to_csv -> scythe.mechanical-state.v1 records. "
                    "Absent evidence is refused, never zero-filled.")
    ap.add_argument("csv", help="th_to_csv output")
    ap.add_argument("node", help="phase-center node id in the deck")
    ap.add_argument("provenance", help="provenance.json from provenance.py")
    ap.add_argument("--nominal", required=True,
                    help="nominal phase-center position x,y,z (m), from deck geometry")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--antenna-normal",
                     help="boresight unit vector x,y,z, derived from deck geometry")
    src.add_argument("--boresight-node",
                     help="boresight node id; normal derived from its x/y/z channels")
    args = ap.parse_args(argv)

    provenance = json.load(open(args.provenance))
    nominal = _parse_vec(args.nominal, "nominal")

    if args.antenna_normal:
        antenna_normal = _parse_vec(args.antenna_normal, "antenna-normal")
    else:
        antenna_normal = None  # derived per-timestep below

    rows, refused = parse_th_csv(args.csv, args.node,
                                 boresight_node=args.boresight_node)
    solver = {
        "name": "OpenCourant",
        "commit": provenance.get("solver_commit", ""),
        "build": provenance.get("solver_build", ""),
    }
    for t, ch in rows:
        if antenna_normal is None:
            b = ch["boresight"]
            n = derive_antenna_normal(
                [nominal[i] + ch[d] for i, d in enumerate(("dx", "dy", "dz"))],
                [b["x"], b["y"], b["z"]])
        else:
            n = antenna_normal
        print(json.dumps(to_mechanical_state(t, ch, solver, provenance,
                                             nominal, n)))
    if refused:
        print(f"refused {refused} timestep(s) with blank cells "
              f"(not emitted, not zero-filled)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
