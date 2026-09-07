#!/usr/bin/env python3
"""Summarise a margin sweep against the promotion requirement stated before it ran.

Reports every margin against all of the criteria, and reports the cost as well
as the benefit.  It deliberately does not print a winner: the tradeoff between
preserved structure and admitted neighbours is a decision, and compressing it
into one ranked column is how a decision gets made by a script.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from typing import Any, Dict, List, Optional

import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/home/spectrcyde/SCYTHE")

from rf_structure_channel_sweep import PROMOTION_REQUIREMENT as CURRENT_REQUIREMENT  # noqa: E402


def _finite(values: List[Optional[float]]) -> List[float]:
    return [v for v in values if isinstance(v, (int, float))]


def _pct(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def reference_detected(cell: Dict[str, Any]) -> bool:
    """Did the wide reference actually find the true symbol clock?

    Retention is only meaningful where there was something to retain. Two cells
    make the unconditioned median meaningless and both are in the grid on
    purpose: a beta = 0 sinc has no excess bandwidth and therefore no timing
    line at all, and a -10 dB cell has a reference that is measuring noise. In
    both the ratio of two noise statistics sits near 1.0 and pulls every median
    toward "no effect", which is how the first pass of this report concluded
    that the margin does not matter.
    """
    reference = cell.get("reference_statistic")
    found = cell.get("reference_symbol_rate_hz")
    true = cell.get("symbol_rate_true_hz")
    return (reference is not None and reference >= 2.5 and found is not None
            and true and abs(found - true) / true <= 0.05)


def summarise(data: Dict[str, Any]) -> Dict[str, Any]:
    cells = data["cells"]
    # A cell whose reference could not clear the widest margin under test is not
    # a baseline any margin can be scored against, so it is excluded from
    # retention and counted separately rather than quietly averaged in.
    scorable = [c for c in cells
                if c.get("retention") is not None and reference_detected(c)]
    per_margin: Dict[str, Any] = {}
    for margin in data["margins"]:
        rows = [c for c in cells if c["margin"] == margin]
        detected_rows = [c for c in scorable if c["margin"] == margin]
        scored = [c for c in scorable if c["margin"] == margin]
        retention = _finite([c["retention"] for c in scored])
        errors = _finite([c["symbol_rate_error"] for c in scored])
        channelized = [c for c in rows if c["outcome"] == "CHANNELIZED"]
        refusals: Dict[str, int] = {}
        for cell in rows:
            if cell["outcome"] != "CHANNELIZED":
                refusals[cell["outcome"]] = refusals.get(cell["outcome"], 0) + 1
        # Contamination: output power with a neighbour over the matched cell
        # without one. Matched on every other axis, so the neighbour is the only
        # thing that changed.
        keyed = {(c["symbol_rate_requested_hz"], c["roll_off"], c["snr_db_requested"],
                  c["offset"], c["neighbour"]): c for c in rows}
        contamination = []
        for key, cell in keyed.items():
            if key[4] == "none" or not cell.get("output_power"):
                continue
            base = keyed.get((*key[:4], "none"))
            if base and base.get("output_power"):
                contamination.append(
                    10.0 * math.log10(cell["output_power"] / base["output_power"]))
        per_margin[str(margin)] = {
            "cells": len(rows),
            "channelized": len(channelized),
            "refusals": refusals,
            "scorable_cells": len(scored),
            "median_retention": (round(statistics.median(retention), 4)
                                 if retention else None),
            "p5_retention": (round(_pct(retention, 0.05), 4) if retention else None),
            "mean_retention": (round(statistics.fmean(retention), 4)
                               if retention else None),
            "retention_ge_0_9": (round(sum(1 for r in retention if r >= 0.9)
                                       / len(retention), 4) if retention else None),
            "median_symbol_rate_error": (round(statistics.median(errors), 5)
                                         if errors else None),
            "symbol_rate_within_5pct": (round(sum(1 for e in errors if e <= 0.05)
                                              / len(errors), 4) if errors else None),
            "median_contamination_db": (round(statistics.median(contamination), 3)
                                        if contamination else None),
            "max_contamination_db": (round(max(contamination), 3)
                                     if contamination else None),
            "median_usable_samples": (
                int(statistics.median([c["usable_samples"] for c in channelized]))
                if channelized else None),
            "median_sps_achieved": (
                round(statistics.median(_finite([c["sps_achieved"] for c in channelized])), 3)
                if _finite([c["sps_achieved"] for c in channelized]) else None),
            "median_runtime_s": round(statistics.median(
                [c["runtime_s"] for c in rows]), 4),
            "median_flat_coverage": (round(statistics.median(_finite(
                [c.get("flat_coverage_ratio") for c in detected_rows])), 4)
                if _finite([c.get("flat_coverage_ratio") for c in detected_rows])
                else None),
            "p5_flat_coverage": (round(_pct(_finite(
                [c.get("flat_coverage_ratio") for c in detected_rows]), 0.05), 4)
                if _finite([c.get("flat_coverage_ratio") for c in detected_rows])
                else None),
            "detected_by_channel": (round(sum(
                1 for c in detected_rows
                if c.get("statistic") and c["statistic"] >= 2.5) / len(detected_rows), 4)
                if detected_rows else None),
        }

    # A requirement absent from the data file is not a requirement waived. A
    # sweep run before a criterion existed still carries the per-cell data the
    # criterion needs, so the current declared requirement is applied and the
    # substitution is reported rather than silently passing the check.
    requirement = dict(data["promotion_requirement"])
    supplemented = [key for key in CURRENT_REQUIREMENT if key not in requirement]
    for key in supplemented:
        requirement[key] = CURRENT_REQUIREMENT[key]
    for margin, row in per_margin.items():
        coverage_min = requirement.get("p5_flat_coverage_min")
        # The lower tail, not the median. Selecting on the median would pick a
        # margin under which a quarter of cells still have their shoulders in
        # the FIR skirt.
        coverage_ok = (coverage_min is None
                       or (row["p5_flat_coverage"] is not None
                           and row["p5_flat_coverage"] >= coverage_min))
        meets = (coverage_ok
                 and row["median_retention"] is not None
                 and row["median_retention"] >= requirement["median_retention_min"]
                 and row["p5_retention"] is not None
                 and row["p5_retention"] >= requirement["p5_retention_min"])
        row["meets_p5_flat_coverage"] = coverage_ok
        row["meets_retention_requirement"] = meets
    return {
        "grid": data["grid"],
        "promotion_requirement": requirement,
        "requirement_keys_supplemented_from_current": supplemented,
        "scoring_condition": ("REFERENCE_DETECTED_THE_TRUE_SYMBOL_CLOCK. "
                              "RETENTION IS UNDEFINED WHERE THERE WAS NOTHING TO RETAIN"),
        "unscorable_cells": len(cells) - len(scorable),
        "per_margin": per_margin,
        "alias_rejection": data["alias_rejection"],
        "false_clock": data["false_clock"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    with open(args.path, encoding="utf-8") as handle:
        data = json.load(handle)
    summary = summarise(data)
    if args.json:
        print(json.dumps(summary, indent=1))
        return 0
    total = len(data["cells"])
    print(f"cells where the reference found the clock (the only ones retention "
          f"means anything on): {total - summary['unscorable_cells']} of {total}")
    print(f"selection basis: "
          f"{summary['promotion_requirement'].get('selection_basis', 'UNDECLARED')}")
    if summary["requirement_keys_supplemented_from_current"]:
        print(f"NOTE: this sweep predates "
              f"{summary['requirement_keys_supplemented_from_current']}; "
              f"applied from the current declared requirement")
    header = (f"{'margin':>7} {'n':>5} {'cover':>6} {'med ret':>8} {'p5 ret':>8} "
              f"{'p5 cov':>7} {'>=0.9':>7} {'found':>7} {'rate ok':>8} "
              f"{'contam dB':>10} {'meets':>6}")
    print(header)
    print("-" * len(header))
    for margin, row in summary["per_margin"].items():
        def fmt(key, spec=">8.3f"):
            value = row[key]
            return f"{value:{spec}}" if isinstance(value, (int, float)) else f"{'--':>8}"
        print(f"{margin:>7} {row['scorable_cells']:>5} "
              f"{fmt('median_flat_coverage', '>6.2f')} {fmt('median_retention')} "
              f"{fmt('p5_retention')} {fmt('p5_flat_coverage', '>7.2f')} "
              f"{fmt('retention_ge_0_9', '>7.3f')} "
              f"{fmt('detected_by_channel', '>7.3f')} "
              f"{fmt('symbol_rate_within_5pct')} {fmt('median_contamination_db', '>10.2f')} "
              f"{str(row['meets_retention_requirement']):>6}")
    print("\nrefusals by margin:")
    for margin, row in summary["per_margin"].items():
        if row["refusals"]:
            print(f"  {margin}: {row['refusals']}")
    print("\nalias rejection:")
    for row in summary["alias_rejection"]:
        floor = " (at measurement floor)" if row["at_measurement_floor"] else ""
        print(f"  margin {row['margin']}: {row['alias_rejection_db']} dB{floor}")
    print("\nthreshold crossings on noise (NOT a validated false-alarm rate):")
    for row in summary["false_clock"]:
        print(f"  margin {row['margin']}: {row['threshold_crossings']}/{row['trials']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
