"""§5.21 slice 3c: the antenna-on OBSERVATION-ONLY run (separate from the terminated catalogue run).

This module does not change `rf_spur_catalogue_sequencer.py`. The catalogue run (`run_epoch`) stays terminated-only:
its declaration check requires a fitted termination, and its authorization binds the terminated chain. This run
reuses the sequencer's acquisition, schedule and spooling primitives, applies the frozen visit gate (rf_canary_gate)
to every visit and the tuning gate to every tuning, and writes an observation document. It produces NO catalogue
entries, classifications or promotions: the evidence class is ANTENNA_ON_OBSERVATION_ONLY.

Pre-contact (no tuner command is sent until every check passes):
  1. the frozen antenna-on declaration and the frozen tier artifact match their pinned file hashes, are
     consistent (`rf_antenna_on_declaration.check_declaration`) and authorize;
  2. the run declaration matches the frozen scope (receiver, tuner, gain, bands, observation-only), states the
     antenna is connected (the operator's statement, not a measurement) and names the frozen declaration's hash;
  3. the declared seed regenerates exactly the frozen schedule digest and 3 visits per tuning;
  4. a launch authorization verifies: status AUTHORIZED, time window current if it names one (optional), bound to the frozen declaration and to
     this exact run declaration's digest, with the operator's own confirmation naming that digest.
The software cannot check that the operator's confirmation is genuine; it checks that it exists and names the digest.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

import rf_antenna_on_declaration as aod
import rf_canary_gate as gate
import rf_spur_catalogue_sequencer as seq
from rf_promotion_envelope import Band, admit_schedule, generate_tunings, generate_visit_schedule

REVISION = "antenna-on-run-1"
EVIDENCE_CLASS = "ANTENNA_ON_OBSERVATION_ONLY"
DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
FROZEN_DECLARATION_FILE = "521_ANTENNA_ON_OBSERVATION_DECLARATION_XTR_E4000.json"
FROZEN_TIER_FILE = "521_INTRINSIC_TIER_XTR_E4000.json"
# Pinned file hashes of the frozen record (docs/521_ANTENNA_ON_FREEZE_RECORD.md). A change to either file is a
# new frozen record, so these constants change only with a new freeze.
FROZEN_DECLARATION_SHA256 = "94b86ebe99f88c4963d62553516d02af92703f2c4e33af36a56d9dfb032d795c"
FROZEN_TIER_SHA256 = "c46f56a6a29026c4c8baf7ce55e195d0e67b5018d95404e88f6ac1e183dd699a"
OBSERVATION_NAME = "antenna_on_observation.json"

_REQUIRED_RUN_KEYS = ("run_id", "site", "output_dir", "rtl_tcp_host", "rtl_tcp_port", "receiver", "gain_db",
                      "bands", "seed", "mode", "antenna", "frozen_declaration_sha256", "launch_authorization")


class RunRefused(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def canonical_sha256(doc: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def load_frozen(docs_dir: str = DOCS) -> Dict[str, Any]:
    """Load the frozen declaration and tier; refuse unless pinned, consistent and authorizing."""
    decl_path = os.path.join(docs_dir, FROZEN_DECLARATION_FILE)
    tier_path = os.path.join(docs_dir, FROZEN_TIER_FILE)
    if _file_sha256(decl_path) != FROZEN_DECLARATION_SHA256:
        raise RunRefused("AOR_FROZEN_DECLARATION_HASH_MISMATCH", decl_path)
    if _file_sha256(tier_path) != FROZEN_TIER_SHA256:
        raise RunRefused("AOR_FROZEN_TIER_HASH_MISMATCH", tier_path)
    with open(decl_path) as handle:
        decl = json.load(handle)
    with open(tier_path) as handle:
        tier = json.load(handle)
    result = aod.check_declaration(decl, tier)
    if not result["ok"]:
        raise RunRefused("AOR_FROZEN_DECLARATION_INCONSISTENT", ", ".join(result["codes"]))
    if not result["authorizes"]:
        raise RunRefused("AOR_FROZEN_DECLARATION_NOT_AUTHORIZING")
    return {"declaration": decl, "tier": tier, "offsets_hz": gate.tier_offsets(tier)}


def _parse_time(text: Any) -> _dt.datetime:
    try:
        value = _dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_WINDOW_UNPARSEABLE", str(text)) from exc
    return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)


def verify_launch_authorization(run_decl: Dict[str, Any], *, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Refuse unless the named launch authorization is AUTHORIZED, current, and bound to this exact run."""
    named = run_decl["launch_authorization"]
    path, claimed = named.get("path"), named.get("sha256")
    if not path or not claimed:
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_UNNAMED")
    if not os.path.exists(path):
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_MISSING", path)
    if _file_sha256(path) != claimed:
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_HASH_MISMATCH", path)
    with open(path) as handle:
        auth = json.load(handle)
    if auth.get("status") != "AUTHORIZED":
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_NOT_AUTHORIZED", str(auth.get("status")))
    if auth.get("frozen_declaration_sha256") != FROZEN_DECLARATION_SHA256:
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_WRONG_FROZEN_DECLARATION")
    body = {k: v for k, v in run_decl.items() if k != "launch_authorization"}
    run_digest = canonical_sha256(body)
    if auth.get("run_declaration_sha256") != run_digest:
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_WRONG_RUN_DECLARATION", f"this run declaration's digest is {run_digest}")
    if run_digest not in str(auth.get("operator_confirmation", "")):
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_CONFIRMATION_MISSING",
                         "the operator's own confirmation must name the run declaration digest")
    window = auth.get("window")
    if window:    # optional (owner, 2026-11-17: no time window is required); a window that is given is enforced
        current = now or _dt.datetime.now(_dt.timezone.utc)
        if not (_parse_time(window.get("not_before")) <= current <= _parse_time(window.get("not_after"))):
            raise RunRefused("AOR_LAUNCH_AUTHORIZATION_WINDOW_NOT_CURRENT")
    if not auth.get("permitted_steps") or not auth.get("stop_conditions"):
        raise RunRefused("AOR_LAUNCH_AUTHORIZATION_STEPS_UNENUMERATED")
    return {"run_declaration_sha256": run_digest, "authorization_sha256": claimed, "window": window}


def precontact(run_decl: Dict[str, Any], *, docs_dir: str = DOCS, now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Every check that must pass before any tuner command. Returns the frozen record, tunings and schedule."""
    missing = [k for k in _REQUIRED_RUN_KEYS if k not in run_decl]
    if missing:
        raise RunRefused("AOR_RUN_DECLARATION_ABSENT", f"missing: {missing}")
    frozen = load_frozen(docs_dir)
    fdecl = frozen["declaration"]
    if run_decl["frozen_declaration_sha256"] != FROZEN_DECLARATION_SHA256:
        raise RunRefused("AOR_RUN_NAMES_WRONG_FROZEN_DECLARATION")
    if run_decl["mode"] != aod.SCOPE_MODE:
        raise RunRefused("AOR_MODE_NOT_OBSERVATION_ONLY")
    if not isinstance(run_decl["antenna"], dict) or run_decl["antenna"].get("connected") is not True:
        raise RunRefused("AOR_ANTENNA_NOT_STATED_CONNECTED", "the run states the antenna is connected (the operator's statement)")
    if not str(run_decl["site"]).strip():
        raise RunRefused("AOR_SITE_UNDECLARED")
    for key in ("sensor_id", "tuner"):
        if run_decl["receiver"].get(key) != fdecl["receiver"][key]:
            raise RunRefused("AOR_RECEIVER_OUT_OF_SCOPE", key)
    if run_decl["gain_db"] != fdecl["gain_db"]:
        raise RunRefused("AOR_GAIN_OUT_OF_SCOPE")
    if json.dumps(run_decl["bands"], sort_keys=True) != json.dumps(fdecl["bands"], sort_keys=True):
        raise RunRefused("AOR_BANDS_OUT_OF_SCOPE")
    if type(run_decl["seed"]) is not int:
        raise RunRefused("AOR_SEED_NOT_INTEGER")
    bands = tuple(Band(band_id=b["band_id"], low_hz=b["low_hz"], high_hz=b["high_hz"]) for b in run_decl["bands"])
    tunings = generate_tunings(seed=run_decl["seed"], bands=bands)
    schedule = generate_visit_schedule(seed=run_decl["seed"], tunings=tuple(tunings))
    digest = admit_schedule(schedule, tuple(tunings), run_decl["seed"])
    if digest != fdecl["schedule_digest"]:
        raise RunRefused("AOR_SCHEDULE_DIGEST_MISMATCH", f"seed regenerates {digest}")
    per_tuning: Dict[int, int] = {}
    for visit in schedule:
        per_tuning[visit.tuning_index] = per_tuning.get(visit.tuning_index, 0) + 1
    if len(tunings) != fdecl["tunings"] or set(per_tuning.values()) != {fdecl["visits_per_tuning"]}:
        raise RunRefused("AOR_VISITS_PER_TUNING_MISMATCH")
    authorization = verify_launch_authorization(run_decl, now=now)
    return {"frozen": frozen, "tunings": tunings, "schedule": schedule, "schedule_digest": digest,
            "authorization": authorization, "visits_per_tuning": fdecl["visits_per_tuning"]}


def run_observation(run_decl: Dict[str, Any], tuner, progress=None, *, docs_dir: str = DOCS,
                    now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """Pre-contact checks, then acquire every scheduled visit and gate it. Returns the observation document."""
    pre = precontact(run_decl, docs_dir=docs_dir, now=now)
    tunings, schedule, offsets = pre["tunings"], pre["schedule"], pre["frozen"]["offsets_hz"]
    output_dir = run_decl["output_dir"]
    seq._materialize_precontact(run_decl, tunings, schedule, pre["schedule_digest"], output_dir)

    tuner.set_manual_gain_db(run_decl["gain_db"], declared_family=seq.declared_tuner_family(run_decl))

    visit_rows: List[Dict[str, Any]] = []
    refused: List[Dict[str, Any]] = []
    by_tuning: Dict[int, List[Dict[str, Any]]] = {}
    for number, visit in enumerate(schedule, start=1):
        tuning = tunings[visit.tuning_index]
        lo_hz = tuning.center_frequency_hz + visit.retune_delta_hz
        if progress:
            progress(number, len(schedule), visit, tuning.tuning_id, lo_hz)
        try:
            acquired = seq.acquire_visit(tuner, visit.position, tuning.tuning_id, visit.tuning_index, lo_hz,
                                         visit.retune_delta_hz)
        except seq.VisitRefused as exc:
            rec = {"type": "visit_refused", "position": visit.position, "tuning_index": visit.tuning_index,
                   "lo_hz": lo_hz, "reason": str(exc)}
            refused.append(rec)
            seq._append_journal(output_dir, rec)
            continue
        spool_rel, window_sha256, window_bytes = seq._spool_visit_windows(output_dir, visit, acquired.raw_windows)
        candidates = seq.detect_candidates(acquired.windows)
        result = gate.visit_gate(candidates, acquired.raw_windows, offsets)
        row = {"type": "visit_gated", "position": visit.position, "tuning_index": visit.tuning_index,
               "tuning_id": tuning.tuning_id, "lo_hz": lo_hz, "retune_delta_hz": visit.retune_delta_hz,
               "attempt": acquired.attempt, "spool_path": spool_rel, "window_sha256": window_sha256,
               "window_bytes": window_bytes, "n_candidates": len(candidates), "gate": result}
        acquired.windows.clear()
        acquired.raw_windows.clear()
        seq._append_journal(output_dir, row)
        visit_rows.append(row)
        by_tuning.setdefault(visit.tuning_index, []).append(result)

    tuning_rows = []
    for index, tuning in enumerate(tunings):
        outcome = gate.tuning_gate(by_tuning.get(index, []), pre["visits_per_tuning"])
        tuning_rows.append({"tuning_index": index, "tuning_id": tuning.tuning_id,
                            "center_frequency_hz": tuning.center_frequency_hz, **outcome})
    scored = [t["tuning_index"] for t in tuning_rows if t["scored"]]
    document = {
        "evidence_class": EVIDENCE_CLASS,
        "not_catalogue_evidence": True,
        "revision": REVISION,
        "gate_revision": gate.REVISION,
        "run_id": run_decl["run_id"],
        "site_statement": run_decl["site"],
        "antenna_statement": run_decl["antenna"],
        "frozen_declaration_sha256": FROZEN_DECLARATION_SHA256,
        "frozen_tier_sha256": FROZEN_TIER_SHA256,
        "run_declaration_sha256": pre["authorization"]["run_declaration_sha256"],
        "launch_authorization_sha256": pre["authorization"]["authorization_sha256"],
        "schedule_digest": pre["schedule_digest"],
        "summary": {"visits_scheduled": len(schedule), "visits_acquired": len(visit_rows),
                    "visits_refused": len(refused),
                    "visits_passed_gate": sum(1 for r in visit_rows if r["gate"]["passed"]),
                    "tunings": len(tunings), "tunings_scored": len(scored)},
        "scored_tunings": scored,
        "tunings": tuning_rows,
        "visits": visit_rows,
        "refused_visits": refused,
    }
    seq._durably_write(os.path.join(output_dir, OBSERVATION_NAME),
                       (json.dumps(document, indent=1, sort_keys=True) + "\n").encode())
    return document


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="§5.21 antenna-on OBSERVATION-ONLY run (slice 3c)")
    parser.add_argument("--declaration", required=True, help="run declaration JSON (see docs/521_ANTENNA_ON_RUN_DECLARATION.TEMPLATE.json)")
    args = parser.parse_args(argv)
    with open(args.declaration) as handle:
        run_decl = json.load(handle)

    def progress(number, total, visit, tuning_id, lo_hz):
        print(f"[{number:3d}/{total}] visit {visit.position}: {tuning_id} LO {lo_hz:,.0f} Hz", flush=True)

    precontact(run_decl)   # refuse before opening the socket
    with seq.RtlTcpTuner(run_decl["rtl_tcp_host"], run_decl["rtl_tcp_port"]) as tuner:
        document = run_observation(run_decl, tuner, progress=progress)
    s = document["summary"]
    print(f"{EVIDENCE_CLASS}: {s['visits_passed_gate']}/{s['visits_acquired']} visits passed the gate, "
          f"{s['tunings_scored']}/{s['tunings']} tunings scored, {s['visits_refused']} visits refused")
    print(f"wrote {os.path.join(run_decl['output_dir'], OBSERVATION_NAME)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
