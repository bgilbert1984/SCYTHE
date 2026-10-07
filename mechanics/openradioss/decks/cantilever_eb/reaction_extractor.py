"""Committed REACZ reaction extractor for the cantilever solver-verification deck.

Implements the documented estimator:
    Rz = mean(sum(REACZ_wall(t)), t in [0.20, 0.50] s)

Design decisions (frozen 2026-10-06):
- SIGNED sum: REACZ values are summed with their signs. The wall reaction
  balances the applied -Z load, so the sum should be +1000 N. Using absolute
  values would mask sign errors and double-count oscillations.
- SAMPLE-BASED mean: the mean is over TH samples within the window, not
  time-weighted. This is valid because Radioss /TFILE writes at uniform
  intervals (0.0005 s per the deck). The extractor verifies uniform sampling
  and refuses if timesteps are non-uniform beyond tolerance.
- REFUSE incomplete coverage: if any wall node is missing from the TH data,
  or any REACZ value is absent/NaN, the extractor raises rather than
  substituting zero. Absent data becoming 0.0 is the "semantically poisonous"
  bug class — it produces a plausible number that means nothing.
- Time window [0.20, 0.50] s is inclusive. Samples outside are ignored.

Provenance: this is analysis code for SIMULATED_MECHANICAL_STATE.
It does not touch measured receiver data.
"""

import csv
import math


# Frozen estimator parameters
T_START = 0.20  # s, inclusive
T_END = 0.50    # s, inclusive
DT_TOLERANCE = 1e-9  # s, max deviation from uniform sampling


class IncompleteCoverageError(Exception):
    """Raised when TH data lacks required wall nodes or values."""
    pass


class NonUniformSamplingError(Exception):
    """Raised when TH timesteps are not uniform within tolerance."""
    pass


def extract_reaction(th_csv_path, wall_node_ids, t_start=T_START, t_end=T_END):
    """Extract Rz from TH CSV output.

    Args:
        th_csv_path: Path to th_to_csv output. Expected columns: "time"
            plus one REACZ column per wall node. Column headers must
            contain the node ID and "REACZ" (case-insensitive).
        wall_node_ids: List of wall node IDs that must all be present.
        t_start, t_end: Time window [s], inclusive.

    Returns:
        (Rz, n_samples, n_nodes): Rz is the signed sum averaged over
        samples in the window. n_samples is the count of TH samples
        used. n_nodes is the count of wall nodes summed.

    Raises:
        IncompleteCoverageError: if any wall node is missing from the
            TH data, or any REACZ value in the window is absent/NaN.
        NonUniformSamplingError: if timesteps in the window are not
            uniform within DT_TOLERANCE.
    """
    wall_set = set(wall_node_ids)
    if len(wall_set) != len(wall_node_ids):
        raise IncompleteCoverageError(
            "Duplicate node IDs in wall_node_ids: %d unique of %d" % (
                len(wall_set), len(wall_node_ids)))

    with open(th_csv_path, newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        if not headers or headers[0].strip().strip('"').lower() != "time":
            raise IncompleteCoverageError(
                "First column must be time, got: %r" % (headers[0] if headers else None))

        # Map wall node IDs to their REACZ column headers
        col_map = {}  # node_id -> header
        for h in headers[1:]:
            hl = h.lower()
            if "reacz" not in hl:
                continue
            # Extract node ID from header (assumes ID appears as integer)
            for nid in wall_set:
                if str(nid) in h and nid not in col_map:
                    col_map[nid] = h
                    break

        missing = wall_set - set(col_map.keys())
        if missing:
            raise IncompleteCoverageError(
                "Wall nodes missing from TH data: %s "
                "(refusing to substitute zeros)" % sorted(missing))

        # Read rows in the time window
        times = []
        sums = []  # signed sum over wall nodes per sample
        for row in reader:
            t = float(row[headers[0]])
            if t < t_start or t > t_end:
                continue
            s = 0.0
            for nid in wall_node_ids:
                val = row[col_map[nid]].strip()
                if val == "" or val.lower() in ("nan", "inf", "-inf"):
                    raise IncompleteCoverageError(
                        "Absent REACZ for node %d at t=%.6f s "
                        "(refusing to substitute zero)" % (nid, t))
                s += float(val)  # SIGNED sum
            times.append(t)
            sums.append(s)

    if not times:
        raise IncompleteCoverageError(
            "No TH samples in window [%.2f, %.2f] s" % (t_start, t_end))

    # Verify uniform sampling (required for sample-based mean)
    if len(times) > 1:
        dts = [times[i+1] - times[i] for i in range(len(times)-1)]
        dt_mean = sum(dts) / len(dts)
        if any(abs(dt - dt_mean) > DT_TOLERANCE for dt in dts):
            raise NonUniformSamplingError(
                "TH timesteps not uniform: max deviation %.2e s "
                "exceeds tolerance %.2e s" % (
                    max(abs(dt - dt_mean) for dt in dts), DT_TOLERANCE))

    # Sample-based mean of signed sums
    rz = sum(sums) / len(sums)
    return rz, len(sums), len(wall_node_ids)
