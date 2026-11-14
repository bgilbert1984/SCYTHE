#!/usr/bin/env python3
"""Two-tier reading of §5.21 catalogues: INTRINSIC_REPRODUCIBLE vs SESSION_SCOPED.

Offline analysis only. It reads finished ``catalogue.json`` files and writes a
hashed tier artifact. It never contacts a device, changes no frozen
declaration and does not touch the sequencer.

Tier rule (all must hold for a fixed baseband offset):
  * slope-0 entries (fixed baseband offset, i.e. internal products);
  * occupancy >= MIN_OCCUPANCY of the tunings in EVERY session;
  * occupancy exceeds the local background (median occupancy of the
    neighbouring offset bins) by >= MIN_EXCESS in every session, so that the
    dense near-DC cloud cannot qualify by density alone;
  * at least MIN_SESSIONS sessions, with distinct operator-supplied labels,
    distinct run ids, one tuner family and one schedule digest.
Everything else stays SESSION_SCOPED and is not reused across sessions.

Inputs must be valid-run catalogues. A stream-liveness failure is not visible
in catalogue.json; ``MAX_ENTRIES_PER_TUNING`` is a heuristic backstop (the
2026-11-13 periodic-stream run had ~1,690 per tuning against ~180).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Sequence

REVISION = "tiers-1"
BIN_HZ = 24.0
MIN_OCCUPANCY = 0.90
MIN_EXCESS = 0.50
MIN_SESSIONS = 3
BACKGROUND_HALF_WIDTH_BINS = 10
MAX_ENTRIES_PER_TUNING = 500

INTRINSIC = "INTRINSIC_REPRODUCIBLE"
SESSION = "SESSION_SCOPED"


class TierRefused(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def _bin(offset_hz: float) -> int:
    return int(offset_hz // BIN_HZ)


def load_session(label: str, path: Path) -> Dict:
    raw = path.read_bytes()
    doc = json.loads(raw)
    n_t = doc.get("n_tunings") or 0
    if not n_t or doc.get("n_visits_refused") or not doc.get("reconciliation", {}).get("reconciled"):
        raise TierRefused("SESSION_NOT_COMPLETE", label)
    if len(doc["entries"]) / n_t > MAX_ENTRIES_PER_TUNING:
        raise TierRefused("SESSION_IMPLAUSIBLE_DENSITY", label)
    per: Dict[str, set] = collections.defaultdict(set)
    for e in doc["entries"]:
        s = e["slope"]
        if s.get("matched_slope") != 0:
            continue
        offs = s["signed_baseband_hz"]
        per[e["persistence"]["tuning_id"]].add(_bin(sum(offs) / len(offs)))
    return {
        "label": label, "run_id": doc["run_id"], "n_tunings": n_t,
        "tuner_type": doc.get("tuner_type"), "schedule_digest": doc["schedule_digest"],
        "chain_digest": doc["chain_digest"], "sha256": hashlib.sha256(raw).hexdigest(),
        "per_tuning_bins": per, "n_entries": len(doc["entries"]),
    }


def occupancy(session: Dict) -> Dict[int, float]:
    cnt: Dict[int, int] = collections.Counter()
    for bins in session["per_tuning_bins"].values():
        cnt.update(bins)
    return {b: c / session["n_tunings"] for b, c in cnt.items()}


def _background(occ: Dict[int, float], b: int) -> float:
    vals = [occ.get(b + k, 0.0) for k in range(-BACKGROUND_HALF_WIDTH_BINS,
                                               BACKGROUND_HALF_WIDTH_BINS + 1) if k]
    return statistics.median(vals)


def classify(sessions: Sequence[Dict]) -> Dict:
    if len(sessions) < MIN_SESSIONS:
        raise TierRefused("TOO_FEW_SESSIONS", f"{len(sessions)} < {MIN_SESSIONS}")
    if len({s["label"] for s in sessions}) != len(sessions):
        raise TierRefused("SESSION_LABEL_REUSED")
    if len({s["run_id"] for s in sessions}) != len(sessions):
        raise TierRefused("SESSION_RUN_ID_REUSED")
    if len({s["schedule_digest"] for s in sessions}) != 1:
        raise TierRefused("SCHEDULE_MISMATCH")
    if len({s["tuner_type"] for s in sessions}) != 1:
        raise TierRefused("TUNER_MISMATCH")
    occs = [occupancy(s) for s in sessions]
    candidates = set(occs[0])
    for o in occs[1:]:
        candidates &= set(o)
    rows: List[Dict] = []
    for b in sorted(candidates):
        occ_v = [o[b] for o in occs]
        exc_v = [o[b] - _background(o, b) for o in occs]
        if min(occ_v) >= MIN_OCCUPANCY and min(exc_v) >= MIN_EXCESS:
            rows.append({
                "offset_hz": b * BIN_HZ + BIN_HZ / 2, "bin_hz": BIN_HZ,
                "occupancy": [round(v, 4) for v in occ_v],
                "excess_over_background": [round(v, 4) for v in exc_v],
                "tier": INTRINSIC,
            })
    return {
        "revision": REVISION,
        "rule": {"min_occupancy": MIN_OCCUPANCY, "min_excess": MIN_EXCESS,
                 "min_sessions": MIN_SESSIONS, "bin_hz": BIN_HZ,
                 "background_half_width_bins": BACKGROUND_HALF_WIDTH_BINS},
        "sessions": [{k: v for k, v in s.items() if k != "per_tuning_bins"} for s in sessions],
        "intrinsic": rows,
        "everything_else": SESSION,
        "caveat": ("low power inside the dense near-DC cloud: a small reproducible "
                   "subset could hide there; three sessions only"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", action="append", required=True, metavar="LABEL=catalogue.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        sessions = []
        for item in a.session:
            label, _, p = item.partition("=")
            sessions.append(load_session(label, Path(p)))
        doc = classify(sessions)
    except TierRefused as exc:
        print(f"REFUSED {exc}", file=sys.stderr)
        return 2
    body = json.dumps(doc, indent=1, sort_keys=True)
    doc["content_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    Path(a.out).write_text(json.dumps(doc, indent=1, sort_keys=True))
    print(f"INTRINSIC {len(doc['intrinsic'])}: "
          + ", ".join(f"{r['offset_hz']:+.0f}" for r in doc["intrinsic"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
