#!/usr/bin/env python3
"""Replay-only incremental processor for a preserved §5.21 evidence directory (ADR 0010).

This is the vertical slice ADR 0010 proposes: feed an existing run's journal
records to an incremental processor one at a time, emit provisional results as
they arrive, and compare the final result with the batch catalogue.

What it is not:
  * It never touches a receiver, and it never writes into the evidence
    directory. It reads declaration.json, schedule.json, the journal and the
    spool, and writes only a report to a path outside the evidence directory.
  * It is not a catalogue producer. Nothing it emits is a §5.21 classification
    of record; the batch path (`reanalyze_from_spool`) remains that.
  * It does not change the §5.21 stability classes or the classification
    contract. It reuses `associate_tracks` and `classify_track` unchanged.

Incremental semantics, stated so they can be wrong in a reviewable way:
  * A visit is analysed when its journal record arrives (candidate detection and
    persistence, from the byte-exact spool or from the journal's derived data).
  * A tuning's tracks are associated and classified only once every scheduled
    visit of that tuning has arrived. The protocol needs several distinct
    retunes for a slope, so a tuning with fewer is reported PROVISIONAL and is
    never classified early.
  * Ingest is idempotent per position: the same record again is a no-op, a
    different record at the same position is refused.
  * State is a pure function of the ingested records, ordered by position, so a
    restart that re-ingests the committed records reaches the same state.

Parity is defined as in ADR 0010: discrete quantities exact, floating-point
quantities within the tolerance frozen below. The tolerance and the comparison
method were fixed before the first measurement run and are not tuned afterwards;
changing them requires a revision of the ADR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rf_spur_catalogue_sequencer as seq
from rf_promotion_envelope import EnvelopeRefused, PLAN_SLOPE_NOT_ESTIMATED, ReferenceComb

# --- frozen before any measurement run (ADR 0010, "Parity definition") --------
# Same value the existing reanalysis-equivalence test declares for this estimator.
FLOAT_REL_TOL = 1e-9
FLOAT_ABS_FLOOR = 1e-12
# Hybrid comparison: |a - b| <= max(FLOAT_ABS_FLOOR, FLOAT_REL_TOL * max(|a|, |b|)).

# Top-level keys that legitimately differ between the live run and any later
# analysis of its spool, with the reason; every other key is compared.
NOT_COMPARED = {
    "tuner_type": "recorded from the live receiver; a replay has no receiver",
    "events": "live acquisition telemetry (attempt, elapsed time); catalogue.json does not carry it",
}

PROVISIONAL = "REPLAY_TUNING_PARTIAL"
TUNING_COMPLETE = "REPLAY_TUNING_COMPLETE"
ACCEPTED = "REPLAY_RECORD_TAKEN"
DUPLICATE = "REPLAY_RECORD_REPEATED"


class ReplayRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class Update:
    """What one ingested record changed."""
    position: int
    status: str                      # ACCEPTED | DUPLICATE
    tuning_id: Optional[str] = None
    tuning_state: Optional[str] = None   # PROVISIONAL | TUNING_COMPLETE
    visits_in: int = 0
    visits_scheduled: int = 0
    retained_candidates: int = 0
    entries: List[Dict[str, Any]] = field(default_factory=list)
    ingress_findings: List[Dict[str, Any]] = field(default_factory=list)
    classification_refusals: List[Dict[str, Any]] = field(default_factory=list)
    ingest_seconds: float = 0.0


def _record_digest(rec: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(rec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ReplayProcessor:
    """Incremental processor over one preserved evidence directory.

    `recompute=True` re-derives each visit's candidates and persistence from the
    spool (verifying its SHA-256 first), as the batch reanalysis does.
    `recompute=False` takes them from the journal record, as the live run
    computed them; the spool is not read.
    """

    def __init__(self, evidence_dir: str, *, recompute: bool = True) -> None:
        self.evidence_dir = evidence_dir
        self.recompute = recompute
        with open(os.path.join(evidence_dir, "declaration.json")) as handle:
            self.decl = json.load(handle)
        seq.check_declaration_authorized(self.decl)
        with open(os.path.join(evidence_dir, "schedule.json")) as handle:
            sched_doc = json.load(handle)
        self.tunings, self.schedule, self.schedule_digest = (
            seq._verify_schedule_against_declaration(self.decl, sched_doc))
        self.comb = ReferenceComb(reference_hz=self.decl["reference_hz"],
                                  reference_ppm=self.decl["reference_ppm"])
        self._scheduled = {v.position: v for v in self.schedule}
        self._per_tuning: Dict[int, int] = {}
        for v in self.schedule:
            self._per_tuning[v.tuning_index] = self._per_tuning.get(v.tuning_index, 0) + 1
        self._digests: Dict[int, str] = {}
        self._visit_data: Dict[int, Any] = {}          # position -> TuningVisitData
        self._refused: Dict[int, Dict[str, Any]] = {}  # position -> refused record
        self._emitted_tunings: set = set()

    # -- ingest ---------------------------------------------------------------
    def ingest(self, rec: Dict[str, Any]) -> Update:
        t0 = time.perf_counter()
        rtype = rec.get("type")
        if rtype not in ("visit_acquired", "visit_refused"):
            raise ReplayRefused("JOURNAL_RECORD_UNKNOWN_TYPE",
                                f"position {rec.get('position')}: type {rtype!r}")
        pos = rec["position"]
        visit = self._scheduled.get(pos)
        if visit is None:
            raise ReplayRefused("POSITION_NOT_SCHEDULED", f"position {pos}")
        digest = _record_digest(rec)
        if pos in self._digests:
            if self._digests[pos] == digest:
                return Update(position=pos, status=DUPLICATE,
                              ingest_seconds=time.perf_counter() - t0)
            raise ReplayRefused("POSITION_CONFLICT",
                                f"position {pos} already committed with different content")
        tuning = self.tunings[visit.tuning_index]
        if (rec.get("tuning_index") != visit.tuning_index
                or rec.get("retune_delta_hz") != visit.retune_delta_hz
                or rec.get("tuning_id") != tuning.tuning_id
                or rec.get("lo_hz") != tuning.center_frequency_hz + visit.retune_delta_hz):
            raise ReplayRefused("RECORD_NOT_BOUND_TO_SCHEDULE",
                                f"position {pos}: record disagrees with the scheduled visit")
        if rtype == "visit_refused":
            self._refused[pos] = rec
            self._digests[pos] = digest
            return self._update(pos, visit, tuning, 0, t0)
        data = (seq._recompute_visit_data(self.evidence_dir, rec) if self.recompute
                else seq._visit_data_from_record(rec))
        self._visit_data[pos] = data
        self._digests[pos] = digest
        return self._update(pos, visit, tuning, len(data.retained), t0)

    def _tuning_visits(self, tuning_index: int) -> List[Any]:
        return [d for p, d in sorted(self._visit_data.items())
                if d.visit.tuning_index == tuning_index]

    def _tuning_arrived(self, tuning_index: int) -> int:
        return sum(1 for p in self._digests if self._scheduled[p].tuning_index == tuning_index)

    def _update(self, pos, visit, tuning, retained, t0) -> Update:
        arrived = self._tuning_arrived(visit.tuning_index)
        total = self._per_tuning[visit.tuning_index]
        up = Update(position=pos, status=ACCEPTED, tuning_id=tuning.tuning_id,
                    tuning_state=PROVISIONAL, visits_in=arrived, visits_scheduled=total,
                    retained_candidates=retained)
        if arrived == total and visit.tuning_index not in self._emitted_tunings:
            up.tuning_state = TUNING_COMPLETE
            self._emitted_tunings.add(visit.tuning_index)
            entries, findings, refusals = self._classify_tuning(visit.tuning_index)
            up.entries, up.ingress_findings, up.classification_refusals = (
                entries, findings, refusals)
        up.ingest_seconds = time.perf_counter() - t0
        return up

    def _classify_tuning(self, tuning_index: int):
        """The per-tuning body of `_analyze_epoch`, on this tuning's visits only.

        Duplicated deliberately and small: the parity comparison against the
        batch document is what shows whether the two ever diverge."""
        tuning = self.tunings[tuning_index]
        tracks, _una, _amb = seq.associate_tracks(
            tuning.tuning_id, tuning.center_frequency_hz, self._tuning_visits(tuning_index))
        entries: List[Dict] = []
        findings: List[Dict] = []
        refusals: List[Dict] = []
        for track_no, track in enumerate(tracks):
            try:
                spur, finding = seq.classify_track(track, self.comb, track_no)
            except EnvelopeRefused as exc:
                if exc.code != PLAN_SLOPE_NOT_ESTIMATED:
                    raise
                refusals.append({
                    "tuning_id": track.tuning_id, "track_no": track_no,
                    "visit_positions": list(track.visit_positions),
                    "retune_deltas_hz": list(track.deltas),
                    "refusal_code": exc.code, "refusal_detail": exc.detail})
                continue
            if finding is not None:
                findings.append(finding)
            else:
                entries.append(spur.to_dict())
        return entries, findings, refusals

    # -- state ----------------------------------------------------------------
    def committed_digests(self) -> Dict[int, str]:
        """position -> record digest. A restart that re-ingests these records
        reaches the same state; nothing else is state."""
        return dict(sorted(self._digests.items()))

    def final_document(self) -> Dict[str, Any]:
        """The batch document over everything ingested so far, built by the
        batch `_analyze_epoch` itself (so only the incremental plumbing is under
        test, not a second copy of the analysis)."""
        by_tuning: Dict[int, List] = {}
        for pos, data in sorted(self._visit_data.items()):
            by_tuning.setdefault(data.visit.tuning_index, []).append(data)
        refused = [self._refused[p] for p in sorted(self._refused)]
        events: List[Dict] = []
        for pos in sorted(self._digests):
            if pos in self._refused:
                events.append({"type": "visit_refused", "position": pos,
                               "reason": self._refused[pos]["reason"]})
            else:
                events.append({"type": "visit_acquired", "position": pos,
                               "tuning_id": self._visit_data[pos].visit.tuning_id,
                               "recomputed_from_spool": self.recompute})
        return seq._analyze_epoch(
            self.decl, self.tunings, self.comb, by_tuning, refused, events,
            self.schedule_digest, self.decl["seed"], len(self.schedule),
            tuner_type=None, output_dir=self.evidence_dir, schedule=self.schedule)


# -- journal source -------------------------------------------------------------
def read_journal(evidence_dir: str) -> Iterator[Dict[str, Any]]:
    with open(os.path.join(evidence_dir, seq.ACQUISITION_JOURNAL_NAME)) as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


# -- parity ---------------------------------------------------------------------
@dataclass
class ParityReport:
    compared_floats: int = 0
    compared_discrete: int = 0
    max_abs_float_diff: float = 0.0
    max_rel_float_diff: float = 0.0
    mismatches: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches


def _float_ok(a: float, b: float) -> Tuple[bool, float, float]:
    if math.isnan(a) or math.isnan(b):
        return (math.isnan(a) and math.isnan(b)), 0.0, 0.0
    if math.isinf(a) or math.isinf(b):
        return a == b, 0.0, 0.0
    diff = abs(a - b)
    scale = max(abs(a), abs(b))
    rel = diff / scale if scale else 0.0
    return diff <= max(FLOAT_ABS_FLOOR, FLOAT_REL_TOL * scale), diff, rel


def compare(expected: Any, actual: Any, report: Optional[ParityReport] = None,
            path: str = "$", skip: Iterable[str] = ()) -> ParityReport:
    """Discrete quantities exact (including key sets, list lengths and order);
    floats within the frozen tolerance. bool and int are discrete, never floats."""
    report = report or ParityReport()
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            report.mismatches.append(f"{path}: expected object, got {type(actual).__name__}")
            return report
        skip = set(skip) if path == "$" else set()
        ek = sorted(k for k in expected if k not in skip)
        ak = sorted(k for k in actual if k not in skip)
        if ek != ak:
            report.mismatches.append(f"{path}: keys differ: {sorted(set(ek) ^ set(ak))}")
        for k in ek:
            if k in actual:
                compare(expected[k], actual[k], report, f"{path}.{k}")
    elif isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)) or len(expected) != len(actual):
            report.mismatches.append(f"{path}: list shape differs")
            return report
        for i, (a, b) in enumerate(zip(expected, actual)):
            compare(a, b, report, f"{path}[{i}]")
    elif isinstance(expected, float) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            report.mismatches.append(f"{path}: expected float, got {type(actual).__name__}")
            return report
        ok, diff, rel = _float_ok(float(expected), float(actual))
        report.compared_floats += 1
        report.max_abs_float_diff = max(report.max_abs_float_diff, diff)
        report.max_rel_float_diff = max(report.max_rel_float_diff, rel)
        if not ok:
            report.mismatches.append(f"{path}: {expected!r} vs {actual!r} outside tolerance")
    else:
        report.compared_discrete += 1
        if expected != actual or type(expected) is not type(actual):
            report.mismatches.append(f"{path}: {expected!r} != {actual!r}")
    return report


def _percentile(values: Sequence[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))] if s else float("nan")


def replay(evidence_dir: str, *, recompute: bool = True,
           records: Optional[Sequence[Dict[str, Any]]] = None
           ) -> Tuple[ReplayProcessor, List[Update]]:
    proc = ReplayProcessor(evidence_dir, recompute=recompute)
    updates = [proc.ingest(rec) for rec in (records if records is not None
                                            else list(read_journal(evidence_dir)))]
    return proc, updates


def run_parity(evidence_dir: str, *, recompute: bool = True) -> Dict[str, Any]:
    """Replay the journal and compare with the frozen catalogue.json. Read-only."""
    with open(os.path.join(evidence_dir, "catalogue.json")) as handle:
        frozen = json.load(handle)
    proc, updates = replay(evidence_dir, recompute=recompute)
    final = proc.final_document()
    doc_parity = compare(frozen, final, skip=NOT_COMPARED)
    incremental_entries = [e for u in updates for e in u.entries]
    # Order: batch lists entries by sorted tuning_index, then by track; the
    # incremental stream is in arrival order, so compare as sorted by spur id.
    key = lambda e: json.dumps(e, sort_keys=True)
    inc_parity = compare(sorted(frozen["entries"], key=key),
                         sorted(incremental_entries, key=key))
    first_entry_at = next((i for i, u in enumerate(updates) if u.entries), None)
    lat = [u.ingest_seconds for u in updates if u.status == ACCEPTED]
    return {
        "evidence_dir": evidence_dir, "recompute": recompute,
        "tolerance": {"relative": FLOAT_REL_TOL, "absolute_floor": FLOAT_ABS_FLOOR,
                      "method": "hybrid"},
        "not_compared": NOT_COMPARED,
        "records_ingested": len(updates),
        "first_classified_entry_after_record": (first_entry_at + 1) if first_entry_at is not None else None,
        "intermediate_results_before_completion": first_entry_at is not None and first_entry_at + 1 < len(updates),
        "tunings_complete": sum(1 for u in updates if u.tuning_state == TUNING_COMPLETE),
        "final_vs_frozen_catalogue": {
            "ok": doc_parity.ok, "floats_compared": doc_parity.compared_floats,
            "discrete_compared": doc_parity.compared_discrete,
            "max_abs_float_diff": doc_parity.max_abs_float_diff,
            "max_rel_float_diff": doc_parity.max_rel_float_diff,
            "mismatches": doc_parity.mismatches[:20], "n_mismatches": len(doc_parity.mismatches)},
        "incremental_entries_vs_frozen_entries": {
            "ok": inc_parity.ok, "n_incremental": len(incremental_entries),
            "n_frozen": len(frozen["entries"]), "n_mismatches": len(inc_parity.mismatches),
            "mismatches": inc_parity.mismatches[:20]},
        "latency_seconds_per_record": {
            "p50": _percentile(lat, 0.50), "p95": _percentile(lat, 0.95),
            "p99": _percentile(lat, 0.99), "max": max(lat) if lat else float("nan")},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("evidence_dir", help="a preserved (ideally disposable) §5.21 run directory; opened read-only")
    ap.add_argument("--report", required=True, help="where to write the JSON report (outside evidence_dir)")
    ap.add_argument("--from-journal", action="store_true",
                    help="use the journal's derived data instead of recomputing from the spool")
    args = ap.parse_args(argv)
    ev = os.path.realpath(args.evidence_dir)
    rep = os.path.realpath(args.report)
    if rep == ev or rep.startswith(ev + os.sep):
        print("refusing: the report path is inside the evidence directory", file=sys.stderr)
        return 2
    result = run_parity(ev, recompute=not args.from_journal)
    with open(rep, "w") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    ok = (result["final_vs_frozen_catalogue"]["ok"]
          and result["incremental_entries_vs_frozen_entries"]["ok"])
    print(json.dumps({k: result[k] for k in (
        "records_ingested", "tunings_complete", "first_classified_entry_after_record",
        "intermediate_results_before_completion")}, sort_keys=True))
    print("PARITY", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
