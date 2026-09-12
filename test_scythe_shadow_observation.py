"""Slice 10: bounded live observation of the apparatus."""

import ast
import json
import os
import tempfile
import unittest

import scythe_shadow_observation as observation_module
from scythe_promotion_ledger_ownership import LedgerOwnership
from scythe_promotion_ledger_writer import LedgerWriter
from scythe_promotion_lineage import Syscalls, generation_path
from scythe_shadow_observation import (
    EVIDENCE_CONSTRUCTED, LINEAGE_NOT_QUIESCENT, LINEAGE_QUIESCENT,
    MAX_OBSERVATION_DURATION_S, MAX_OBSERVATION_VERDICTS, NOT_A_PREDICTION,
    OBSERVATION_DURATION_REACHED, OBSERVATION_INTERRUPTED,
    OBSERVATION_LIMIT_INVALID, OBSERVATION_PATH_REFUSED,
    OBSERVATION_PUBLICATION_REFUSED, SYNTHETIC_REQUEST, VERDICT_COUNT_REACHED,
    ObservationOutcome, ObservationRefused, ObservationSubject,
    ShadowObservation, constructed_walk_verdicts,
)

OWNER = {"boot_id": "boot-a", "pid": 4242, "start_ticks": 99}


class ObservationTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.state = os.path.join(self._dir.name, "state")
        self.out = os.path.join(self._dir.name, "out")
        os.makedirs(self.state)
        os.makedirs(self.out)
        self.root = os.path.join(self.state, "promotion")
        self.record = os.path.join(self.out, "observation.json")

    def _observer(self, **kw):
        fields = dict(lineage_root=self.root, record_path=self.record,
                      verdict_limit=10, duration_s=30.0)
        fields.update(kw)
        return ShadowObservation(**fields)

    def _ledger(self):
        owner = LedgerOwnership(lineage_root=self.root, mounts=(("/", "ext4"),),
                                refused_prefixes=(),
                                devices={os.stat(self.state).st_dev: "ext4"})
        self.addCleanup(owner.release)
        owner.acquire()
        writer = LedgerWriter(path=generation_path(self.root, 0),
                              ownership=owner.scope, refused_prefixes=())
        with writer.owned() as session:
            session.initialize()
            session.declare_generation("gen-1", OWNER)
        return owner, writer


class ClaimTests(ObservationTestCase):
    """§13h I.1, I.2: the record says what it is."""

    def test_the_record_declares_itself_not_a_prediction(self):
        record = self._observer().run(constructed_walk_verdicts(5))
        self.assertEqual(record["claim"], NOT_A_PREDICTION)
        self.assertIn("NOT A FORECAST", record["claim_note"])
        self.assertIn("DOES NOT EXIST", record["claim_note"])

    def test_the_request_origin_is_marked_synthetic(self):
        record = self._observer().run(constructed_walk_verdicts(5))
        self.assertEqual(record["request_origin"], SYNTHETIC_REQUEST)

    def test_the_evidence_source_is_recorded_rather_than_assumed(self):
        """Nothing here stores derived RF evidence, so a run today is driven by
        constructed inputs through the real checkers. Saying so is the
        difference between an observation and a test with a record attached."""
        record = self._observer().run(constructed_walk_verdicts(5))
        self.assertEqual(record["evidence_source"], EVIDENCE_CONSTRUCTED)

    def test_the_verdicts_come_from_the_production_checker(self):
        verdicts = [v for v, _s, _c in constructed_walk_verdicts(40)]
        self.assertGreater(len({v.verdict for v in verdicts}), 1,
                           "a source with one outcome observes nothing")

    def test_the_two_vocabularies_are_counted_apart(self):
        record = self._observer().run(constructed_walk_verdicts(40))
        self.assertIn("merit_refusals", record)
        self.assertIn("executability_refusals", record)
        self.assertNotIn("total_refusals", record)
        self.assertTrue(record["merit_refusals"])


class BoundTests(ObservationTestCase):
    """§13h I.5: both active, both capped, invalid refuses before anything."""

    def test_the_verdict_bound_ends_the_run(self):
        record = self._observer(verdict_limit=7).run(constructed_walk_verdicts(500))
        self.assertEqual(record["ending"], VERDICT_COUNT_REACHED)
        self.assertEqual(record["verdicts_observed"], 7)

    def test_the_duration_bound_ends_the_run(self):
        ticks = iter([0.0] + [99.0] * 50)

        record = self._observer(duration_s=1.0,
                                clock=lambda: next(ticks)).run(
            constructed_walk_verdicts(500))
        self.assertEqual(record["ending"], OBSERVATION_DURATION_REACHED)

    def test_a_limit_above_the_contract_maximum_is_refused(self):
        for bad in (0, -1, MAX_OBSERVATION_VERDICTS + 1, True, 1.5):
            with self.subTest(bad=bad), self.assertRaises(ObservationRefused) as c:
                self._observer(verdict_limit=bad).run(constructed_walk_verdicts(3))
            self.assertEqual(c.exception.code, OBSERVATION_LIMIT_INVALID)

    def test_a_duration_above_the_contract_maximum_is_refused(self):
        for bad in (0, -1.0, MAX_OBSERVATION_DURATION_S + 1, True):
            with self.subTest(bad=bad), self.assertRaises(ObservationRefused) as c:
                self._observer(duration_s=bad).run(constructed_walk_verdicts(3))
            self.assertEqual(c.exception.code, OBSERVATION_LIMIT_INVALID)

    def test_an_invalid_limit_publishes_nothing(self):
        """A run that never started has nothing to report."""
        with self.assertRaises(ObservationRefused):
            self._observer(verdict_limit=0).run(constructed_walk_verdicts(3))
        self.assertFalse(os.path.exists(self.record))

    def test_the_maxima_are_not_configurable(self):
        with open(observation_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertNotIn("getenv", attributes)
        self.assertNotIn("environ", attributes)
        fields = set(ShadowObservation.__dataclass_fields__)
        for name in fields:
            self.assertNotIn("max", name.lower())
        record = self._observer().run(constructed_walk_verdicts(3))
        self.assertFalse(record["bounds"]["bounds_are_configurable"])


class PublicationTests(ObservationTestCase):
    """§13h I.4."""

    def test_a_record_inside_the_lineage_namespace_is_refused(self):
        inside = os.path.join(self.state, "observation.json")
        with self.assertRaises(ObservationRefused) as caught:
            self._observer(record_path=inside).run(constructed_walk_verdicts(3))
        self.assertEqual(caught.exception.code, OBSERVATION_PATH_REFUSED)
        self.assertFalse(os.path.exists(inside))

    def test_a_symlink_into_the_lineage_is_refused(self):
        """Resolved, not compared as strings: a symlink satisfies a prefix
        check and places a non-ledger among the ledgers."""
        link = os.path.join(self.out, "sneaky")
        os.symlink(self.state, link)
        with self.assertRaises(ObservationRefused) as caught:
            self._observer(record_path=os.path.join(link, "observation.json")
                           ).run(constructed_walk_verdicts(3))
        self.assertEqual(caught.exception.code, OBSERVATION_PATH_REFUSED)

    def test_a_second_run_at_one_path_is_refused(self):
        self._observer().run(constructed_walk_verdicts(3))
        with open(self.record, "rb") as handle:
            before = handle.read()
        with self.assertRaises(ObservationRefused) as caught:
            self._observer().run(constructed_walk_verdicts(3))
        self.assertEqual(caught.exception.code, OBSERVATION_PUBLICATION_REFUSED)
        with open(self.record, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_publication_follows_the_five_steps_in_order(self):
        calls = []

        class Recording(Syscalls):
            def open_exclusive(self, path):
                calls.append("OPEN_EXCLUSIVE")
                return super().open_exclusive(path)

            def write(self, fd, data):
                super().write(fd, data)
                calls.append("WRITE")

            def fsync(self, fd):
                super().fsync(fd)
                calls.append("FSYNC_FILE")

            def rename(self, source, target):
                super().rename(source, target)
                calls.append("RENAME")

            def fsync_directory(self, path):
                super().fsync_directory(path)
                calls.append("FSYNC_DIRECTORY")

        self._observer(syscalls=Recording()).run(constructed_walk_verdicts(3))
        self.assertEqual(calls, ["OPEN_EXCLUSIVE", "WRITE", "FSYNC_FILE",
                                 "RENAME", "FSYNC_DIRECTORY"])

    def test_nothing_exists_under_the_final_name_before_the_rename(self):
        seen = []
        record = self.record

        class Watching(Syscalls):
            def write(self, fd, data):
                seen.append(os.path.exists(record))
                super().write(fd, data)

        self._observer(syscalls=Watching()).run(constructed_walk_verdicts(3))
        self.assertEqual(seen, [False])

    def test_the_record_is_serialisable_and_carries_no_free_text(self):
        record = self._observer().run(constructed_walk_verdicts(5))
        blob = json.dumps(record)
        for leak in ("Traceback", "token=", "password", "/home/"):
            self.assertNotIn(leak, blob, leak)


class QuiescenceTests(ObservationTestCase):
    """§13h I.4a: what equal digests prove, and what they do not."""

    def test_an_untouched_lineage_is_quiescent(self):
        self._ledger()
        record = self._observer().run(constructed_walk_verdicts(5))
        self.assertEqual(record["quiescence"], LINEAGE_QUIESCENT)

    def test_a_lineage_written_during_the_run_is_not_quiescent(self):
        owner, writer = self._ledger()

        def writing_source():
            for item in constructed_walk_verdicts(5):
                with writer.owned() as session:
                    session.append_reserved({"identity": "promotion:other"})
                yield item

        record = self._observer().run(writing_source())
        self.assertEqual(record["quiescence"], LINEAGE_NOT_QUIESCENT)
        self.assertNotEqual(record["lineage_digest_before"],
                            record["lineage_digest_after"])

    def test_the_record_makes_no_causal_claim(self):
        """§12 permits SHADOW beside an ARMED writer, so the observer cannot
        tell its own writes from another's. Silence about attribution is the
        honest report of that."""
        self._ledger()
        record = self._observer().run(constructed_walk_verdicts(5))
        note = record["quiescence_note"]
        self.assertIn("DO NOT ATTRIBUTE", note)
        self.assertIn("CANNOT TELL ITS OWN WRITES", note)
        self.assertFalse(record["appends"])

    def test_the_ledger_is_byte_identical_across_an_observation(self):
        owner, writer = self._ledger()
        path = generation_path(self.root, 0)
        with open(path, "rb") as handle:
            before = handle.read()
        self._observer().run(constructed_walk_verdicts(20))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), before)


class InterruptionTests(ObservationTestCase):
    """§13h I.7."""

    def test_a_failing_source_still_publishes(self):
        def breaks():
            for index, item in enumerate(constructed_walk_verdicts(50)):
                if index == 3:
                    raise RuntimeError("the source gave out")
                yield item

        record = self._observer().run(breaks())
        self.assertEqual(record["ending"], OBSERVATION_INTERRUPTED)
        self.assertEqual(record["interrupted_by"], "RuntimeError")
        self.assertEqual(record["verdicts_observed"], 3)
        self.assertTrue(os.path.exists(self.record))

    def test_the_record_carries_no_exception_message(self):
        secret = "https://internal/x?token=hunter2"

        def breaks():
            raise ConnectionError(secret)
            yield

        record = self._observer().run(breaks())
        blob = json.dumps(record)
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("internal", blob)
        self.assertEqual(record["interrupted_by"], "ConnectionError")

    def test_the_record_claims_nothing_about_sigkill(self):
        record = self._observer().run(constructed_walk_verdicts(3))
        self.assertFalse(record["survives_sigkill"])
        self.assertIn("CLAIMS NOTHING ABOUT SIGKILL", record["survival_note"])


class NonExecutableTests(ObservationTestCase):
    """§13h I.2a: distinct types, and nothing escapes."""

    def test_the_subject_is_not_a_promotion_request(self):
        from scythe_promotion_policy import PromotionRequest

        subject = ObservationSubject(requested_by="OPERATOR",
                                     target_graph="g", justification_source="s")
        self.assertNotIsInstance(subject, PromotionRequest)
        self.assertEqual(subject.origin, SYNTHETIC_REQUEST)
        self.assertFalse([n for n in dir(subject) if "request" in n.lower()
                          and not n.startswith("requested")])

    def test_the_outcome_is_not_a_promotion_decision(self):
        from scythe_promotion_policy import PromotionDecision

        outcome = ObservationOutcome(identity="x", disposition="d",
                                     merit_refusals=(), executability_refusals=())
        self.assertNotIsInstance(outcome, PromotionDecision)

    def test_no_public_function_returns_a_decision_or_a_request(self):
        """The guarantee that matters: every act path takes a
        PromotionDecision, and nothing public here produces one."""
        with open(observation_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
            for statement in returns:
                source = ast.dump(statement)
                self.assertNotIn("PromotionDecision", source, node.name)
                self.assertNotIn("PromotionRequest", source, node.name)

    def test_the_observer_holds_no_writer_adapter_or_scope(self):
        fields = set(ShadowObservation.__dataclass_fields__)
        for absent in ("writer", "adapter", "ownership", "scope", "session"):
            self.assertFalse([f for f in fields if absent in f], absent)

    def test_the_module_acquires_nothing_and_opens_no_socket(self):
        with open(observation_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        for absent in ("socket", "subprocess", "threading", "rf_bridge",
                       "rf_iq_ring", "scythe_graphops_adapter",
                       "scythe_promotion_ledger_ownership"):
            self.assertNotIn(absent, imported, absent)

    def test_the_module_declares_no_armed_mode(self):
        with open(observation_module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        tree = ast.parse(source)
        assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                    for t in n.targets if isinstance(t, ast.Name)}
        self.assertNotIn("MODE_ARMED", assigned)
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("PromotionCoordinator", called)
        self.assertNotIn("GraphOpsAdapter", called)


if __name__ == "__main__":
    unittest.main()
