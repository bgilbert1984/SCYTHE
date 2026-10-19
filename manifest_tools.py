"""Consumers of ingest manifests: equivalence comparator and benchmark aggregator.

    python manifest_tools.py compare BASELINE.json CANDIDATE.json
    python manifest_tools.py bench   MANIFEST_DIR [--json]

``compare`` exits 0 on EQUIVALENT, 1 on DIFFERENT.  Fields that must match
describe the input and the output; fields that describe the environment
(git commit, runtime versions, pipeline version) are reported as context
differences without failing the comparison, since a restore drill may
legitimately run on a newer build.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List

MUST_MATCH = (
    "pcap_sha256", "config_hash", "session_hash",
    "session_count", "entity_count", "edge_count",
)
CONTEXT = ("pipeline_version", "git_commit", "runtime")


def load(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text())


def compare(baseline: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
    mismatches = {
        k: {"baseline": baseline.get(k), "candidate": candidate.get(k)}
        for k in MUST_MATCH if baseline.get(k) != candidate.get(k)
    }
    context = {
        k: {"baseline": baseline.get(k), "candidate": candidate.get(k)}
        for k in CONTEXT if baseline.get(k) != candidate.get(k)
    }
    return {
        "status": "EQUIVALENT" if not mismatches else "DIFFERENT",
        "mismatches": mismatches,
        "context_differences": context,
    }


def _pct(values: List[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]


def _stats(values: List[float]) -> Dict[str, float]:
    return {
        "min": min(values), "median": statistics.median(values),
        "p95": _pct(values, 0.95), "max": max(values), "n": len(values),
    }


def aggregate(manifests: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate stage timings and derived rates over successful manifests."""
    ok = [m for m in manifests if m.get("ok")]
    series: Dict[str, List[float]] = {}

    def add(name: str, v: float) -> None:
        series.setdefault(name, []).append(v)

    for m in ok:
        t = m.get("stage_timings_sec", {})
        dur = m.get("duration_sec") or 0.0
        mb = (m.get("pcap_size") or 0) / 1e6
        for stage, secs in t.items():
            add(f"{stage}_ms", secs * 1000)
        add("total_ms", dur * 1000)
        if dur > 0:
            add("packets_per_sec", m.get("packets_decoded", 0) / dur)
            add("sessions_per_sec", m.get("session_count", 0) / dur)
            add("entities_per_sec", m.get("entity_count", 0) / dur)
            add("edges_per_sec", m.get("edge_count", 0) / dur)
        if m.get("session_count"):
            add("ms_per_session", dur * 1000 / m["session_count"])
        if mb > 0:
            add("ms_per_mb", dur * 1000 / mb)
    return {
        "manifests": len(manifests), "successful": len(ok),
        "metrics": {k: _stats(v) for k, v in sorted(series.items())},
    }


def _render(agg: Dict[str, Any]) -> str:
    lines = [f"{agg['successful']}/{agg['manifests']} manifests ok",
             f"{'metric':<20}{'min':>12}{'median':>12}{'p95':>12}{'max':>12}"]
    for name, s in agg["metrics"].items():
        lines.append(f"{name:<20}{s['min']:>12.2f}{s['median']:>12.2f}{s['p95']:>12.2f}{s['max']:>12.2f}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare"); c.add_argument("baseline"); c.add_argument("candidate")
    b = sub.add_parser("bench"); b.add_argument("dir"); b.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "compare":
        report = compare(load(a.baseline), load(a.candidate))
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "EQUIVALENT" else 1

    paths = sorted(Path(a.dir).glob("*.manifest.json"))
    if not paths:
        print(f"no manifests in {a.dir}", file=sys.stderr)
        return 2
    agg = aggregate([load(p) for p in paths])
    print(json.dumps(agg, indent=2, sort_keys=True) if a.json else _render(agg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
