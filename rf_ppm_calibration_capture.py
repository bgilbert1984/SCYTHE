#!/usr/bin/env python3
"""Capture and site survey for the XTR ppm calibration (DRAFT; never run on hardware).

Receive-only. Reuses the sequencer's rtl_tcp tuner, byte-exact 256 ms windows, settle discard and
durable spooling, so a calibration window is the same kind of object as a catalogue window.

Gates, in order, before the receiver is touched:
  1. the plan is frozen and its hash matches (rf_ppm_calibration.verify_frozen_plan);
  2. the owner's statement for this session is supplied and is recorded verbatim (the procedure
     requires the owner to state that the termination may be removed and will be refitted);
  3. the output directory does not exist or is empty.
This tool never decides that a termination is on or off; that is physical and is the owner's.

Visit order is a seeded shuffle of the plan hash and session id, so it cannot be chosen after the
data are seen. Each visit tunes to the carrier's nominal channel frequency, discards the settle
interval and takes the plan's eight windows.

The tuner is a parameter, so tests serve synthetic bytes. Not for use while a catalogue run holds
the rtl_tcp port; a second client will simply fail to connect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rf_ppm_calibration as cal
import rf_spur_catalogue_sequencer as seq


class CaptureRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def visit_order(plan: Dict[str, Any], session_id: str) -> List[str]:
    names = sorted(c["name"] for c in plan["carriers"])
    seed = int(hashlib.sha256(f"{plan['plan_sha256']}|{session_id}".encode()).hexdigest()[:16], 16)
    random.Random(seed).shuffle(names)
    return names


def _check_output_dir(out_dir: str) -> None:
    if os.path.exists(out_dir) and os.listdir(out_dir):
        raise CaptureRefused("OUTPUT_DIR_NOT_EMPTY", out_dir)
    os.makedirs(out_dir, exist_ok=True)


def capture_session(plan: Dict[str, Any], tuner, session_id: str, conditions: Sequence[str],
                    out_dir: str, owner_statement: str) -> Dict[str, Any]:
    cal.verify_frozen_plan(plan)
    if not owner_statement or not owner_statement.strip():
        raise CaptureRefused("OWNER_STATEMENT_MISSING", "the owner's statement for this session is required")
    if not session_id or not conditions:
        raise CaptureRefused("SESSION_UNDESCRIBED", "session id and conditions must be stated")
    if plan["sample_rate_hz"] != seq.SAMPLE_RATE_HZ:
        raise CaptureRefused("SAMPLE_RATE_MISMATCH", "the plan is not at the sequencer's sample rate")
    _check_output_dir(out_dir)
    by_name = {c["name"]: c for c in plan["carriers"]}
    order = visit_order(plan, session_id)
    tuner.set_manual_gain_db(plan["gain_db"])
    visits = []
    started = time.time()
    for position, name in enumerate(order):
        c = by_name[name]
        lo_hz = float(round(c["nominal_hz"]))
        acquired = seq.acquire_visit(tuner, position, name, position, lo_hz, 0.0)
        raw = b"".join(acquired.raw_windows)
        rel = f"visit_{position:02d}_{name}.iq"
        seq._durably_write(os.path.join(out_dir, rel), raw)
        visits.append({"position": position, "carrier": name, "lo_hz": lo_hz, "spool": rel,
                       "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                       "attempt": acquired.attempt})
    session = {"session_id": session_id, "plan_sha256": plan["plan_sha256"],
               "conditions": list(conditions), "owner_statement": owner_statement,
               "gain_db": plan["gain_db"], "visit_order": order, "visits": visits,
               "started_unix": started, "finished_unix": time.time(),
               "temperature": "not measured"}
    seq._durably_write(os.path.join(out_dir, "session.json"),
                       json.dumps(session, indent=2, sort_keys=True).encode())
    return session


def survey(candidates: Sequence[Dict[str, Any]], tuner, gain_db: float) -> List[Dict[str, Any]]:
    """Feasibility step: one window per candidate channel; in-channel power over the out-of-channel
    median. Candidate: {name, nominal_hz, bandwidth_hz}. Says nothing about WHO transmits; the cell
    must still be identified from its channel raster before it enters a plan."""
    tuner.set_manual_gain_db(gain_db)
    out = []
    for cand in candidates:
        tuner.set_lo_hz(float(round(cand["nominal_hz"])))
        tuner.discard(seq.SETTLE_S)
        iq = tuner.read_window()
        spec = np.abs(np.fft.fftshift(np.fft.fft(iq * np.hanning(len(iq))))) ** 2
        f = np.fft.fftshift(np.fft.fftfreq(len(iq), 1.0 / seq.SAMPLE_RATE_HZ))
        inband = np.abs(f) <= cand["bandwidth_hz"] / 2
        floor = float(np.median(spec[~inband]) / math.log(2.0)) if (~inband).any() else float("nan")
        p_in = float(spec[inband].mean()) if inband.any() else float("nan")
        out.append({"name": cand["name"], "nominal_hz": cand["nominal_hz"],
                    "inband_over_floor_db": 10 * math.log10(max(p_in, 1e-30) / max(floor, 1e-30))})
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("capture", "survey"):
        p = sub.add_parser(name)
        p.add_argument("--host", default="127.0.0.1"); p.add_argument("--port", type=int, default=1234)
    c = sub.choices["capture"]
    c.add_argument("--plan", required=True); c.add_argument("--session-id", required=True)
    c.add_argument("--conditions", required=True, help="comma-separated, e.g. warm,after_power_cycle")
    c.add_argument("--owner-statement", required=True); c.add_argument("--out", required=True)
    s = sub.choices["survey"]
    s.add_argument("--candidates", required=True, help="JSON list of {name, nominal_hz, bandwidth_hz}")
    s.add_argument("--gain-db", type=float, required=True); s.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    with seq.RtlTcpTuner(args.host, args.port) as tuner:
        if args.cmd == "capture":
            with open(args.plan) as h:
                plan = json.load(h)
            res = capture_session(plan, tuner, args.session_id, args.conditions.split(","),
                                  args.out, args.owner_statement)
            print("captured", len(res["visits"]), "visits to", args.out)
        else:
            with open(args.candidates) as h:
                cands = json.load(h)
            res = survey(cands, tuner, args.gain_db)
            with open(args.out, "w") as h:
                json.dump(res, h, indent=2)
            print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
