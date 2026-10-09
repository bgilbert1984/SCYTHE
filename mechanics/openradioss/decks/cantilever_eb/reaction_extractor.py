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
# ---------------------------------------------------------------------------
# Versioned method for irregular timestamps: trapezoidal_v1
#
# Added 2026-10-06: the th_to_csv converter labels channels as
#   "<title>  <node_id>  var <N>" (e.g. "wall reaction ... 1 ... var 53"),
# not with the Radioss variable names (REACX/REACY/REACZ). The uniform-cadence
# extractor above (extract_reaction) requires REACZ in the header and uniform
# sampling; it is PRESERVED unchanged. This v1 method handles the converter
# output format explicitly:
#
# - Channel resolution: parse the "var N" suffix and node ID from the label.
#   For /TH/NODE/2 requesting REACX, REACY, REACZ for nodes 1..9, the converter
#   assigns var 51,52,53 to node 1; 54,55,56 to node 2; etc. REACZ for node k
#   (1-indexed) is var (50 + 3*k). The caller supplies the expected var numbers;
#   the extractor verifies them against the parsed labels as a cross-check.
# - Time-weighting: trapezoidal integration over the actual timestamps, divided
#   by the observed span. No uniformity assumption. The observed endpoints are
#   reported explicitly; integrating to a nominal endpoint (e.g. 0.50 s) requires
#   an explicit endpoint rule, which this method does NOT apply.
# - Channel representation (2026-10-08 correction): th_to_csv may emit REAC
#   channels as cumulative IMPULSE (N s) rather than force (N). OpenRadioss
#   guidance states T01 stores reaction impulses; the converter differentiates
#   them into forces only when the required /TH/TITLE information is present.
#   Evidence for impulse in a given CSV: (a) project guidance, (b) monotonically
#   rising channel values under steady load, (c) generic "var N" labels without
#   force-unit metadata. The caller selects via channel_kind; the choice must
#   rest on such evidence, NOT on which interpretation closes a balance.
# - Refusal: missing nodes, absent/NaN values, or var-number mismatch all raise
#   IncompleteCoverageError. Non-uniform sampling does NOT raise here (that is
#   the point of this method); the uniform-cadence method remains available.
# ---------------------------------------------------------------------------

def extract_reaction_trapezoidal_v1(th_csv_path, wall_node_ids,
                                    reacz_var_numbers,
                                    t_start=T_START, t_end=T_END,
                                    channel_kind="force"):
    """Extract Rz via trapezoidal time-weighting (v1, for converter output).

    Args:
        th_csv_path: Path to th_to_csv output. Headers are expected in the
            converter format: '"<title>" ... <node_id> ... var <N>'.
        wall_node_ids: List of wall node IDs (1-indexed) that must all be present.
        reacz_var_numbers: List of var numbers for the REACZ channel of each
            wall node, in the same order as wall_node_ids. E.g. for nodes
            1..9 with /TH/NODE/2 requesting REACX,REACY,REACZ: [53,56,...,77].
            These are cross-checked against the parsed header labels.
        t_start, t_end: Time window [s], inclusive.
        channel_kind: "force" (default) -- channels carry force; the mean is
            the trapezoidal time-average. "impulse" -- channels carry
            cumulative impulse; the mean force is
            (sum(t1) - sum(t0)) / (t1 - t0) from the observed endpoints.
            Choose on evidence (converter docs/version, monotonic-rise
            signature, TH metadata), NOT on which value closes a balance.

    Returns:
        (Rz, t_observed_start, t_observed_end, n_samples, n_nodes):
        Rz is the mean wall-reaction force in N under the channel_kind
        interpretation. t_observed_* are the actual first/last timestamps.
        The caller MUST record which channel_kind was used and its basis.

    Raises:
        IncompleteCoverageError: on missing nodes, absent/NaN values,
            var-number/label mismatch, or unknown channel_kind.
    """
    import re

    wall_list = list(wall_node_ids)
    if len(set(wall_list)) != len(wall_list):
        raise IncompleteCoverageError("Duplicate node IDs in wall_node_ids")
    if len(reacz_var_numbers) != len(wall_list):
        raise IncompleteCoverageError(
            "reacz_var_numbers length %d != wall_node_ids length %d" %
            (len(reacz_var_numbers), len(wall_list)))

    # Expected: node_id -> var number
    expected_var = dict(zip(wall_list, reacz_var_numbers))

    with open(th_csv_path, newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames
        if not headers:
            raise IncompleteCoverageError("Empty CSV header")

        # Parse converter labels: '"wall reaction ... 1 ... var 53"'
        # Extract node ID and var number from each header.
        col_map = {}  # node_id -> header, for REACZ channels only
        for h in headers[1:]:
            # Find var number
            m_var = re.search(r"var\s+(\d+)", h)
            if not m_var:
                continue
            var_num = int(m_var.group(1))
            # Find which wall node this var belongs to (reverse lookup)
            for nid, expected in expected_var.items():
                if var_num == expected:
                    # Cross-check: header should also mention the node ID
                    # (as a standalone integer token)
                    tokens = re.findall(r"\b\d+\b", h)
                    if str(nid) in tokens and nid not in col_map:
                        col_map[nid] = h
                    break

        missing = set(wall_list) - set(col_map.keys())
        if missing:
            raise IncompleteCoverageError(
                "Wall nodes missing from TH data (var cross-check failed): %s" %
                sorted(missing))

        times = []
        sums = []
        for row in reader:
            t = float(row[headers[0]])
            if t < t_start or t > t_end:
                continue
            s = 0.0
            for nid in wall_list:
                val = row[col_map[nid]].strip()
                if val == "" or val.lower() in ("nan", "inf", "-inf"):
                    raise IncompleteCoverageError(
                        "Absent REACZ for node %d at t=%.7f s" % (nid, t))
                s += float(val)
            times.append(t)
            sums.append(s)

    if not times:
        raise IncompleteCoverageError(
            "No TH samples in window [%.2f, %.2f] s" % (t_start, t_end))

    # Trapezoidal integration over actual timestamps
    integral = 0.0
    for i in range(len(times) - 1):
        dt = times[i+1] - times[i]
        if dt <= 0:
            raise IncompleteCoverageError(
                "Non-monotonic timestamps at index %d: t=%.7f -> %.7f" %
                (i, times[i], times[i+1]))
        integral += 0.5 * (sums[i] + sums[i+1]) * dt

    t_obs0, t_obs1 = times[0], times[-1]
    span = t_obs1 - t_obs0
    if span <= 0:
        raise IncompleteCoverageError("Zero time span in window")

    if channel_kind == "force":
        rz = integral / span
    elif channel_kind == "impulse":
        # Cumulative impulse: mean force is the endpoint difference rate.
        # A trapezoidal mean of J(t) would return N s mislabeled as N.
        rz = (sums[-1] - sums[0]) / span
    else:
        raise IncompleteCoverageError(
            "Unknown channel_kind %r (expected 'force' or 'impulse')"
            % (channel_kind,))
    return rz, t_obs0, t_obs1, len(times), len(wall_list)

