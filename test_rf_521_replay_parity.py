#!/usr/bin/env python3
"""ADR 0010 replay slice: incremental processing of a preserved §5.21 run.

One live epoch is run once through the real `run_epoch` and a fake receiver (the
fixture of test_rf_521_reanalysis_equivalence), then replayed record by record
through `ReplayProcessor`. The checks are the ADR's acceptance table, at fixture
scale: parity with the batch result, intermediate results before completion,
restart recovery, replay determinism, and no write to the evidence directory.
The full-size RERUN2 measurement is a separate, manual run of the CLI.
"""
import copy
import hashlib
import json
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rf_spur_catalogue_sequencer as seq
import rf_spur_replay_processor as rp
import test_rf_521_reanalysis_equivalence as eq


def _tree_hash(root):
    h = hashlib.sha256()
    for dp, dn, fn in sorted(os.walk(root)):
        dn.sort()
        for name in sorted(fn):
            p = os.path.join(dp, name)
            h.update(os.path.relpath(p, root).encode())
            with open(p, "rb") as handle:
                h.update(hashlib.sha256(handle.read()).digest())
    return h.hexdigest()


class ReplayBase(eq.ReanalysisEquivalenceBase):
    """Reuses the one live epoch; adds the replay helpers."""

    def records(self):
        return eq._read_journal(self.out)


class ParityTests(ReplayBase):
    def test_final_document_matches_live_catalogue_from_spool_and_from_journal(self):
        for recompute in (True, False):
            with eq._Plan():
                proc, _ = rp.replay(self.out, recompute=recompute)
                final = proc.final_document()
            report = rp.compare(self.live_document, final, skip=rp.NOT_COMPARED)
            self.assertTrue(report.ok, (recompute, report.mismatches[:5]))
            self.assertGreater(report.compared_floats, 0)  # not vacuous
            self.assertTrue(self.live_document["entries"])
            ident = lambda d: [(e["type"], e["position"], e.get("tuning_id")) for e in d["events"]]
            self.assertEqual(ident(self.live_document), ident(final))

    def test_incremental_entries_equal_batch_entries(self):
        with eq._Plan():
            _, updates = rp.replay(self.out)
        key = lambda e: json.dumps(e, sort_keys=True)
        incremental = [e for u in updates for e in u.entries]
        report = rp.compare(sorted(self.live_document["entries"], key=key),
                            sorted(incremental, key=key))
        self.assertTrue(report.ok, report.mismatches[:5])
        self.assertEqual(len(incremental), len(self.live_document["entries"]))


class IncrementalTests(ReplayBase):
    def test_results_exist_before_replay_completes_and_tuning_is_not_classified_early(self):
        with eq._Plan():
            _, updates = rp.replay(self.out)
        self.assertEqual([u.tuning_state for u in updates],
                         [rp.PROVISIONAL, rp.PROVISIONAL, rp.TUNING_COMPLETE] * 2)
        self.assertEqual([bool(u.entries) for u in updates][:2], [False, False])
        first = next(i for i, u in enumerate(updates) if u.entries)
        self.assertLess(first + 1, len(updates))          # before completion
        self.assertEqual([u.visits_in for u in updates][:3], [1, 2, 3])
        self.assertTrue(all(u.retained_candidates >= 0 for u in updates))

    def test_duplicate_is_a_noop_and_conflict_is_refused(self):
        recs = self.records()
        with eq._Plan():
            proc = rp.ReplayProcessor(self.out)
            proc.ingest(recs[0])
            digests = proc.committed_digests()
            self.assertEqual(proc.ingest(recs[0]).status, rp.DUPLICATE)
            self.assertEqual(proc.committed_digests(), digests)
            changed = copy.deepcopy(recs[0])
            changed["elapsed_s"] = changed["elapsed_s"] + 1.0
            with self.assertRaises(rp.ReplayRefused) as ctx:
                proc.ingest(changed)
            self.assertEqual(ctx.exception.code, "POSITION_CONFLICT")
            self.assertEqual(proc.committed_digests(), digests)

    def test_restart_by_reingesting_committed_records_has_no_loss_or_duplication(self):
        recs = self.records()
        with eq._Plan():
            first = rp.ReplayProcessor(self.out)
            for r in recs[:4]:
                first.ingest(r)
            committed = first.committed_digests()
            second = rp.ReplayProcessor(self.out)       # the "restarted" process
            for r in recs[:4]:
                second.ingest(r)
            self.assertEqual(second.committed_digests(), committed)
            for r in recs:                               # source re-delivers everything
                second.ingest(r)
            self.assertEqual(sorted(second.committed_digests()),
                             [r["position"] for r in recs])
            report = rp.compare(self.live_document, second.final_document(), skip=rp.NOT_COMPARED)
        self.assertTrue(report.ok, report.mismatches[:5])

    def test_arrival_order_does_not_change_the_result(self):
        recs = self.records()
        shuffled = list(recs)
        random.Random(7).shuffle(shuffled)
        with eq._Plan():
            proc, _ = rp.replay(self.out, records=shuffled)
            report = rp.compare(self.live_document, proc.final_document(), skip=rp.NOT_COMPARED)
        self.assertTrue(report.ok, report.mismatches[:5])

    def test_replay_is_deterministic(self):
        with eq._Plan():
            a, _ = rp.replay(self.out)
            b, _ = rp.replay(self.out)
            self.assertEqual(json.dumps(a.final_document(), sort_keys=True),
                             json.dumps(b.final_document(), sort_keys=True))


class GovernanceTests(ReplayBase):
    def test_unauthorized_declaration_is_refused(self):
        with eq._Plan():
            pass
        with self.assertRaises(seq.SequencerRefused) as ctx:
            rp.ReplayProcessor(self.out)                 # real authorization check
        self.assertEqual(ctx.exception.code, "DECLARATION_NOT_AUTHORIZED")

    def test_record_not_bound_to_schedule_is_refused(self):
        recs = copy.deepcopy(self.records())
        recs[0]["tuning_id"] = "not-the-scheduled-tuning"
        with eq._Plan():
            proc = rp.ReplayProcessor(self.out)
            with self.assertRaises(rp.ReplayRefused) as ctx:
                proc.ingest(recs[0])
        self.assertEqual(ctx.exception.code, "RECORD_NOT_BOUND_TO_SCHEDULE")

    def test_unknown_record_type_and_unscheduled_position_are_refused(self):
        with eq._Plan():
            proc = rp.ReplayProcessor(self.out)
            with self.assertRaises(rp.ReplayRefused):
                proc.ingest({"type": "mystery", "position": 0})
            rec = copy.deepcopy(self.records()[0])
            rec["position"] = 999
            with self.assertRaises(rp.ReplayRefused) as ctx:
                proc.ingest(rec)
        self.assertEqual(ctx.exception.code, "POSITION_NOT_SCHEDULED")

    def test_spool_corruption_is_refused_when_recomputing(self):
        damaged = self.copy_of_evidence()
        rec = [r for r in eq._read_journal(damaged) if r["type"] == "visit_acquired"][0]
        path = os.path.join(damaged, rec["spool_path"])
        os.chmod(path, 0o644)
        with open(path, "r+b") as handle:
            handle.write(b"\xff")
        with eq._Plan():
            proc = rp.ReplayProcessor(damaged)
            with self.assertRaises(seq.SequencerRefused) as ctx:
                proc.ingest(rec)
        self.assertEqual(ctx.exception.code, "SPOOL_CORRUPT")

    def test_evidence_directory_is_not_modified(self):
        before = _tree_hash(self.out)
        with eq._Plan():
            proc, _ = rp.replay(self.out)
            proc.final_document()
        self.assertEqual(_tree_hash(self.out), before)

    def test_cli_refuses_a_report_inside_the_evidence_directory(self):
        rc = rp.main([self.out, "--report", os.path.join(self.out, "report.json")])
        self.assertEqual(rc, 2)
        self.assertFalse(os.path.exists(os.path.join(self.out, "report.json")))


class CompareTests(unittest.TestCase):
    def test_floats_use_the_frozen_hybrid_tolerance(self):
        self.assertTrue(rp.compare({"x": 1.0}, {"x": 1.0 + 0.5e-9}).ok)
        self.assertFalse(rp.compare({"x": 1.0}, {"x": 1.0 + 5e-9}).ok)
        self.assertTrue(rp.compare({"x": 0.0}, {"x": 5e-13}).ok)       # absolute floor
        self.assertFalse(rp.compare({"x": 0.0}, {"x": 5e-11}).ok)

    def test_discrete_quantities_are_exact_and_typed(self):
        self.assertFalse(rp.compare({"n": 3}, {"n": 4}).ok)
        self.assertFalse(rp.compare({"n": 1}, {"n": True}).ok)          # bool is not int
        self.assertFalse(rp.compare({"s": "a"}, {"s": "b"}).ok)
        self.assertFalse(rp.compare({"n": 3}, {"n": 3.0}).ok)           # int is not float
        self.assertFalse(rp.compare({"a": 1}, {"a": 1, "b": 2}).ok)     # key sets
        self.assertFalse(rp.compare([1, 2], [2, 1]).ok)                 # order
        self.assertFalse(rp.compare([1], [1, 2]).ok)

    def test_nan_and_inf(self):
        nan = float("nan")
        self.assertTrue(rp.compare({"x": nan}, {"x": nan}).ok)
        self.assertFalse(rp.compare({"x": nan}, {"x": 1.0}).ok)
        self.assertFalse(rp.compare({"x": float("inf")}, {"x": 1e308}).ok)

    def test_skip_applies_only_at_top_level(self):
        self.assertTrue(rp.compare({"tuner_type": 1, "a": 2}, {"tuner_type": None, "a": 2},
                                   skip=rp.NOT_COMPARED).ok)
        self.assertFalse(rp.compare({"a": {"tuner_type": 1}}, {"a": {"tuner_type": None}},
                                    skip=rp.NOT_COMPARED).ok)

    def test_tolerance_is_the_one_frozen_for_the_estimator(self):
        self.assertEqual(rp.FLOAT_REL_TOL, eq.FLOAT_TOLERANCE)


if __name__ == "__main__":
    unittest.main()
