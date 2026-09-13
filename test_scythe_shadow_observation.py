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
    APPARATUS_CERTIFICATION, DERIVED_EVIDENCE_UNAVAILABLE,
    EVIDENCE_DERIVED_ARTEFACT, LIVE_OBSERVATION, NOT_A_LIVE_OBSERVATION,
    EVIDENCE_CONSTRUCTED, LINEAGE_NOT_QUIESCENT, LINEAGE_QUIESCENT,
    MAX_OBSERVATION_DURATION_S, MAX_OBSERVATION_VERDICTS, NOT_A_PREDICTION,
    OBSERVATION_DURATION_REACHED, OBSERVATION_INTERRUPTED,
    OBSERVATION_LIMIT_INVALID, OBSERVATION_PATH_REFUSED,
    OBSERVATION_PUBLICATION_REFUSED, SYNTHETIC_REQUEST, VERDICT_COUNT_REACHED,
    CARRIED_FROM_ARTEFACT, DECLARATION_AUTHORITIES, NO_DECLARATION,
    NO_INSTRUMENT_DECLARATION, VERDICT_SOURCE_REFUSED, InstrumentDeclaration,
    ObservationOutcome, ObservationRefused, ObservationSubject,
    LINEAGE_HOLDS_NO_GENERATION, LINEAGE_PRESENT, NO_LINEAGE_NAMESPACE,
    ShadowObservation, VerdictSource, constructed_walk_verdicts,
    derived_walk_verdicts,
)
import dataclasses

import scythe_derived_evidence as reader_module
import test_scythe_derived_evidence as test_reader
from scythe_derived_evidence import (
    INSTRUMENT_CONFIGURED_IDLE, RF_MEASUREMENT_NOT_PERFORMED,
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
        verdicts = [v for v, _s, _c in constructed_walk_verdicts(40).verdicts]
        self.assertGreater(len({v.verdict for v in verdicts}), 1,
                           "a source with one outcome observes nothing")

    def test_the_two_vocabularies_are_counted_apart(self):
        record = self._observer().run(constructed_walk_verdicts(40))
        self.assertIn("merit_refusals", record)
        self.assertIn("executability_refusals", record)
        self.assertNotIn("total_refusals", record)
        self.assertTrue(record["merit_refusals"])


class LiveClassTests(ObservationTestCase):
    """Slice 10a. Constructed evidence certifies; it does not observe.

    Running production checkers over invented inputs is worth having and is not
    the fifth stage. The verdicts are genuine and the evidence is not live, and
    the first does not make the second true.
    """

    def test_a_constructed_run_reports_itself_as_certification(self):
        record = self._observer().run(constructed_walk_verdicts(5))
        self.assertEqual(record["run_class"], APPARATUS_CERTIFICATION)
        self.assertFalse(record["live_observation"])
        self.assertIn(NOT_A_LIVE_OBSERVATION, record["live_note"])

    def test_constructed_evidence_cannot_report_itself_as_live(self):
        record = self._observer().run(constructed_walk_verdicts(5))
        self.assertNotEqual(record["run_class"], LIVE_OBSERVATION)
        self.assertNotIn(LIVE_OBSERVATION, json.dumps(record["run_class"]))

    def test_requiring_live_refuses_rather_than_substituting(self):
        """A bounded refusal, never a silent substitution: the substitution
        would be invisible in the record that exists to prevent it."""
        with self.assertRaises(ObservationRefused) as caught:
            self._observer(require_live=True).run(constructed_walk_verdicts(5))
        self.assertEqual(caught.exception.code, DERIVED_EVIDENCE_UNAVAILABLE)
        self.assertFalse(os.path.exists(self.record))

    def test_only_a_derived_artefact_is_eligible_to_be_live(self):
        observer = self._observer(evidence_source=EVIDENCE_DERIVED_ARTEFACT)
        self.assertEqual(observer.run_class, LIVE_OBSERVATION)
        self.assertEqual(self._observer().run_class, APPARATUS_CERTIFICATION)

    def test_a_reader_exists_and_no_artefact_does(self):
        """The honest state of slice 10, narrowed by slice 10b.

        The previous version asserted that no derived source existed at all,
        and said that when a derived-evidence interface arrived this test was
        what would have to change. It arrived, and this is the change.

        What exists now is a *reader*. What still does not exist is **evidence**:
        no artefact ships in this repository, and none can be produced here,
        because the writer is a separately authorized act (§13i J.4). Entry 7
        waits on the second thing, not the first.
        """
        sources = sorted(name for name, value in vars(observation_module).items()
                         if callable(value) and name.endswith("_verdicts")
                         and not name.startswith("_"))
        self.assertEqual(sources, ["constructed_walk_verdicts",
                                   "derived_walk_verdicts"])

        root = os.path.dirname(os.path.abspath(observation_module.__file__))
        bundled = [name for name in os.listdir(root)
                   if name.endswith(".jsonl") or name.endswith(".artefact")]
        self.assertEqual(bundled, [], "an artefact would complete slice 10")


class PureCoreTests(unittest.TestCase):
    """§13h I.2a: one set of rules, two wrappers, no conversion."""

    def test_the_observation_module_constructs_no_promotion_request(self):
        with open(observation_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("PromotionRequest", called)
        self.assertNotIn("decide_promotion", called)
        imported = {a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        self.assertNotIn("PromotionRequest", imported)
        self.assertNotIn("decide_promotion", imported)

    def test_the_shared_core_returns_no_promotion_decision(self):
        from scythe_promotion_policy import (
            PolicyConclusion, PolicyFacts, PromotionDecision, evaluate_policy,
        )
        from test_scythe_promotion_ledger import CAPSULE, _verdict

        conclusion = evaluate_policy(
            _verdict(), PolicyFacts(requested_by="OPERATOR",
                                    target_graph="scythe.graphops.evidence",
                                    justification_source="OPERATOR"), CAPSULE)
        self.assertIsInstance(conclusion, PolicyConclusion)
        self.assertNotIsInstance(conclusion, PromotionDecision)

    def test_facts_flow_one_way_only(self):
        from scythe_promotion_policy import PolicyFacts

        self.assertFalse([n for n in dir(PolicyFacts) if "request" in n.lower()])
        self.assertFalse([n for n in dir(ObservationSubject)
                          if n.startswith("to_") or n.startswith("as_")])

    def test_both_wrappers_reach_identical_conclusions(self):
        """The equivalence that makes the type boundary free: identical facts
        produce identical policy conclusions through both wrappers, so the
        separation costs no divergence."""
        from scythe_promotion_policy import (
            CapsuleIdentity, PromotionRequest, decide_promotion,
        )
        from test_scythe_promotion_ledger import _distinct, _satisfied

        capsules = [
            CapsuleIdentity(schema="scythe.invariant-capsule.v1",
                            digest="blake2s:aa", within_bounds=True,
                            carries_samples=False),
            CapsuleIdentity(schema="scythe.invariant-capsule.v1",
                            digest="blake2s:bb", within_bounds=False,
                            carries_samples=False),
            None,
        ]
        askers = [("OPERATOR", "scythe.graphops.evidence", "OPERATOR"),
                  ("MODEL", "scythe.graphops.evidence", "MODEL"),
                  ("OPERATOR", "somewhere.else", "OPERATOR")]
        verdicts = [_distinct(0), _distinct(1), _satisfied()]

        for capsule in capsules:
            for who, graph, source in askers:
                for verdict in verdicts:
                    with self.subTest(capsule=capsule, who=who, verdict=verdict):
                        decision = decide_promotion(
                            verdict,
                            PromotionRequest(requested_by=who, target_graph=graph,
                                             justification_source=source),
                            capsule, already_promoted=())
                        outcome = observation_module._evaluate(
                            verdict,
                            ObservationSubject(requested_by=who,
                                               target_graph=graph,
                                               justification_source=source),
                            capsule, ())
                        self.assertEqual(outcome.disposition,
                                         decision.disposition)
                        self.assertEqual(outcome.merit_refusals,
                                         decision.refusals)
                        if decision.idempotency_key:
                            self.assertEqual(outcome.identity,
                                             decision.idempotency_key)


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
            for item in constructed_walk_verdicts(5).verdicts:
                with writer.owned() as session:
                    session.append_reserved({"identity": "promotion:other"})
                yield item

        record = self._observer().run(
            VerdictSource(declaration=NO_DECLARATION, verdicts=writing_source()))
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
            for index, item in enumerate(constructed_walk_verdicts(50).verdicts):
                if index == 3:
                    raise RuntimeError("the source gave out")
                yield item

        record = self._observer().run(
            VerdictSource(declaration=NO_DECLARATION, verdicts=breaks()))
        self.assertEqual(record["ending"], OBSERVATION_INTERRUPTED)
        self.assertEqual(record["interrupted_by"], "RuntimeError")
        self.assertEqual(record["verdicts_observed"], 3)
        self.assertTrue(os.path.exists(self.record))

    def test_the_record_carries_no_exception_message(self):
        secret = "https://internal/x?token=hunter2"

        def breaks():
            raise ConnectionError(secret)
            yield

        record = self._observer().run(
            VerdictSource(declaration=NO_DECLARATION, verdicts=breaks()))
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


class InstrumentDeclarationTests(ObservationTestCase):
    """Slice 10e, §13l M.4 and 37b: the observer carries, and never states.

    The property under test is an absence. Nowhere in this module can a caller,
    or the module itself, put a measurement status into a record -- the only
    route is an `Artefact` a reader admitted from bytes on disk.
    """

    def _artefact(self, **overrides):
        from scythe_derived_evidence import encode_for_test
        provenance = dict(test_reader.CONFIGURED, **overrides)
        data = encode_for_test(provenance,
                               [test_reader.walk_record(i) for i in range(3)])
        path = os.path.join(self.out, "artefact.jsonl")
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    # -- a constructed run has no instrument at all ------------------------

    def test_a_constructed_run_declares_no_instrument(self):
        record = self._observer().run(constructed_walk_verdicts(5))
        declaration = record["instrument_declaration"]
        self.assertEqual(declaration["authority"], NO_INSTRUMENT_DECLARATION)
        self.assertEqual(declaration["measurement_status"],
                         NO_INSTRUMENT_DECLARATION)
        self.assertEqual(declaration["instrument_state"],
                         NO_INSTRUMENT_DECLARATION)

    def test_a_constructed_run_may_not_carry_an_instrument_status(self):
        """The record would then describe an instrument that was never there."""
        carried = InstrumentDeclaration(
            authority=CARRIED_FROM_ARTEFACT,
            measurement_status=RF_MEASUREMENT_NOT_PERFORMED,
            instrument_state=INSTRUMENT_CONFIGURED_IDLE)
        with self.assertRaises(ObservationRefused) as caught:
            self._observer().run(VerdictSource(
                declaration=carried,
                verdicts=constructed_walk_verdicts(3).verdicts))
        self.assertEqual(caught.exception.code, VERDICT_SOURCE_REFUSED)
        self.assertFalse(os.path.exists(self.record))

    # -- a live run carries the artefact's declaration ---------------------

    def test_a_live_run_carries_the_artefacts_declaration(self):
        """37b: the artefact and the observation record say the same thing,
        because the second copied the first."""
        source = derived_walk_verdicts(self._artefact())
        record = self._observer(evidence_source=EVIDENCE_DERIVED_ARTEFACT,
                                require_live=True).run(source)
        self.assertEqual(record["run_class"], LIVE_OBSERVATION)
        self.assertEqual(record["instrument_declaration"], {
            "authority": CARRIED_FROM_ARTEFACT,
            "measurement_status": RF_MEASUREMENT_NOT_PERFORMED,
            "instrument_state": INSTRUMENT_CONFIGURED_IDLE})

    def test_a_live_run_refuses_a_declaration_from_nowhere(self):
        with self.assertRaises(ObservationRefused) as caught:
            self._observer(evidence_source=EVIDENCE_DERIVED_ARTEFACT,
                           require_live=True).run(constructed_walk_verdicts(3))
        self.assertEqual(caught.exception.code, VERDICT_SOURCE_REFUSED)
        self.assertFalse(os.path.exists(self.record))

    def test_the_declaration_and_the_verdicts_come_from_one_read(self):
        """§13i J.7. Two opens would let the record describe an instrument
        belonging to a different file than the verdicts came from."""
        opened = []
        real = reader_module.read_artefact

        def counting(path):
            opened.append(path)
            return real(path)

        reader_module.read_artefact = counting
        self.addCleanup(setattr, reader_module, "read_artefact", real)
        source = derived_walk_verdicts(self._artefact())
        list(source.verdicts)
        self.assertEqual(len(opened), 1)

    # -- the observer cannot state a status --------------------------------

    def test_the_module_never_names_a_measurement_value(self):
        """AST over the whole module: neither value appears as a literal or a
        name. They can only arrive on an Artefact."""
        with open(observation_module.__file__) as handle:
            tree = ast.parse(handle.read())
        strings = {node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Attribute)}
        for token in (RF_MEASUREMENT_NOT_PERFORMED, INSTRUMENT_CONFIGURED_IDLE):
            # Containment, not equality: a token spliced into a longer string
            # would reach a record just as surely as one standing alone.
            self.assertEqual([text for text in strings if token in text], [],
                             token)
            self.assertNotIn(token, names, token)

    def test_there_is_no_declaration_field_on_the_observer(self):
        """A constructor argument would be the observer stating it."""
        fields = {f.name for f in dataclasses.fields(ShadowObservation)}
        self.assertNotIn("measurement_status", fields)
        self.assertNotIn("instrument_state", fields)
        self.assertNotIn("instrument_declaration", fields)
        self.assertNotIn("declaration", fields)

    def test_from_artefact_is_the_only_route_to_a_carried_status(self):
        """Every CARRIED_FROM_ARTEFACT declaration in this module is built by
        `from_artefact`, and that classmethod takes an Artefact nominally."""
        with open(observation_module.__file__) as handle:
            tree = ast.parse(handle.read())
        built = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)
                 and node.func.id == "InstrumentDeclaration"]
        for call in built:
            authorities = [kw.value.id for kw in call.keywords
                           if kw.arg == "authority" and isinstance(kw.value, ast.Name)]
            self.assertEqual(authorities, [NO_INSTRUMENT_DECLARATION], ast.dump(call))

    def test_from_artefact_refuses_anything_that_is_not_one(self):
        class Lookalike:
            measurement_status = RF_MEASUREMENT_NOT_PERFORMED
            instrument_state = INSTRUMENT_CONFIGURED_IDLE

        for impostor in (Lookalike(), {"measurement_status": "x"}, None, "art"):
            with self.subTest(impostor=type(impostor).__name__):
                with self.assertRaises(ObservationRefused) as caught:
                    InstrumentDeclaration.from_artefact(impostor)
                self.assertEqual(caught.exception.code, VERDICT_SOURCE_REFUSED)

    # -- an inconsistent declaration cannot be built ------------------------

    def test_an_unknown_authority_is_refused(self):
        for authority in ("ARTEFACT_DECLARED", "", None, True):
            with self.subTest(authority=authority):
                with self.assertRaises(ObservationRefused):
                    InstrumentDeclaration(authority=authority,
                                          measurement_status=NO_INSTRUMENT_DECLARATION,
                                          instrument_state=NO_INSTRUMENT_DECLARATION)

    def test_a_carried_declaration_takes_only_declared_values(self):
        for status, state in (("RF_MEASUREMENT_PERFORMED", INSTRUMENT_CONFIGURED_IDLE),
                              (RF_MEASUREMENT_NOT_PERFORMED, "INSTRUMENT_STREAMING"),
                              (NO_INSTRUMENT_DECLARATION, INSTRUMENT_CONFIGURED_IDLE)):
            with self.subTest(status=status, state=state):
                with self.assertRaises(ObservationRefused):
                    InstrumentDeclaration(authority=CARRIED_FROM_ARTEFACT,
                                          measurement_status=status,
                                          instrument_state=state)

    def test_a_declaration_from_nowhere_carries_nothing(self):
        with self.assertRaises(ObservationRefused) as caught:
            InstrumentDeclaration(authority=NO_INSTRUMENT_DECLARATION,
                                  measurement_status=RF_MEASUREMENT_NOT_PERFORMED,
                                  instrument_state=INSTRUMENT_CONFIGURED_IDLE)
        self.assertEqual(caught.exception.code, VERDICT_SOURCE_REFUSED)

    # -- the source is a type, not an iterable ------------------------------

    def test_a_bare_iterable_is_refused(self):
        """A bare iterable carries no declaration, and a record that simply
        omitted the instrument would be the silence 10e exists to end."""
        for source in ([], iter([]), constructed_walk_verdicts(3).verdicts):
            with self.subTest(source=type(source).__name__):
                with self.assertRaises(ObservationRefused) as caught:
                    self._observer().run(source)
                self.assertEqual(caught.exception.code, VERDICT_SOURCE_REFUSED)
                self.assertFalse(os.path.exists(self.record))

    def test_a_lookalike_source_is_refused_nominally(self):
        class Lookalike:
            declaration = NO_DECLARATION
            verdicts = ()

        with self.assertRaises(ObservationRefused):
            self._observer().run(Lookalike())

    def test_a_source_carrying_a_lookalike_declaration_is_refused(self):
        class Lookalike:
            authority = NO_INSTRUMENT_DECLARATION
            measurement_status = NO_INSTRUMENT_DECLARATION
            instrument_state = NO_INSTRUMENT_DECLARATION

        with self.assertRaises(ObservationRefused):
            VerdictSource(declaration=Lookalike(), verdicts=())

    # -- the vocabulary -----------------------------------------------------

    def test_the_authorities_are_closed_and_disjoint_from_the_readers(self):
        from scythe_derived_evidence import INSTRUMENT_STATES, MEASUREMENT_STATUSES
        self.assertEqual(observation_module.DECLARATION_AUTHORITIES,
                         (CARRIED_FROM_ARTEFACT, NO_INSTRUMENT_DECLARATION))
        overlap = set(DECLARATION_AUTHORITIES) & (set(MEASUREMENT_STATUSES)
                                                  | set(INSTRUMENT_STATES))
        self.assertEqual(overlap, set())

    def test_the_new_names_collide_with_nothing_unjudged(self):
        from test_scythe_verdict_vocabularies import (
            cross_set_collisions, discovered_tokens, judged,
        )
        tokens = discovered_tokens()
        for candidate in (CARRIED_FROM_ARTEFACT, NO_INSTRUMENT_DECLARATION,
                          VERDICT_SOURCE_REFUSED):
            with self.subTest(candidate=candidate):
                self.assertIn(candidate, tokens)
                unjudged = [hit for hit in cross_set_collisions(candidate, tokens)
                            if not judged(candidate, hit)]
                self.assertEqual(unjudged, [])

    def test_the_rejected_authority_names_would_have_collided(self):
        """CARRIED_FROM_ARTEFACT was chosen after ARTEFACT_DECLARED and
        ARTEFACT_ATTESTED were checked and rejected, not after they were
        argued about."""
        from test_scythe_verdict_vocabularies import collisions, discovered_tokens
        universe = set(discovered_tokens())
        self.assertIn("LEDGER_GENERATION_UNDECLARED",
                      collisions("ARTEFACT_DECLARED", universe))
        self.assertIn("LOCK_EXCLUSION_UNATTESTED",
                      collisions("ARTEFACT_ATTESTED", universe))
        self.assertEqual(collisions(CARRIED_FROM_ARTEFACT, universe),
                         [CARRIED_FROM_ARTEFACT])


class LineagePresenceTests(ObservationTestCase):
    """Slice 10f: an empty digest map is not a generation.

    `{}` has three causes -- no namespace, a namespace holding no generation,
    and a read that failed -- and a record reporting only `{}` and
    LINEAGE_QUIESCENT can be read as *a valid empty generation was observed and
    did not change*. That is a claim nobody made.
    """

    def _absent_root(self):
        """A root whose *namespace* -- its parent directory -- is not there."""
        return os.path.join(self._dir.name, "nowhere", "promotion")

    def test_a_missing_namespace_is_reported_as_one(self):
        record = self._observer(lineage_root=self._absent_root()).run(
            constructed_walk_verdicts(3))
        self.assertEqual(record["lineage_presence_before"], NO_LINEAGE_NAMESPACE)
        self.assertEqual(record["lineage_presence_after"], NO_LINEAGE_NAMESPACE)
        self.assertEqual(record["lineage_digest_before"], {})
        self.assertEqual(record["quiescence"], LINEAGE_QUIESCENT)
        self.assertNotIn("lineage_present", record)

    def test_an_empty_namespace_is_not_a_missing_one(self):
        record = self._observer().run(constructed_walk_verdicts(3))
        self.assertEqual(record["lineage_presence_before"],
                         LINEAGE_HOLDS_NO_GENERATION)
        self.assertEqual(record["lineage_digest_before"], {})

    def test_a_populated_lineage_is_reported_present(self):
        self._ledger()
        record = self._observer().run(constructed_walk_verdicts(3))
        self.assertEqual(record["lineage_presence_before"], LINEAGE_PRESENT)
        self.assertTrue(record["lineage_digest_before"])

    def test_quiescence_alone_cannot_be_read_as_a_generation(self):
        """The three states are distinguishable in the record even though all
        three can report LINEAGE_QUIESCENT with an empty digest map."""
        absent = self._observer(lineage_root=self._absent_root()).run(
            constructed_walk_verdicts(3))
        os.remove(self.record)
        empty = self._observer().run(constructed_walk_verdicts(3))
        self.assertEqual(absent["quiescence"], empty["quiescence"])
        self.assertEqual(absent["lineage_digest_before"],
                         empty["lineage_digest_before"])
        self.assertNotEqual(absent["lineage_presence_before"],
                            empty["lineage_presence_before"])

    def test_the_presence_states_are_closed(self):
        self.assertEqual(observation_module.LINEAGE_PRESENCES,
                         (NO_LINEAGE_NAMESPACE, LINEAGE_HOLDS_NO_GENERATION,
                          LINEAGE_PRESENT))
        for record_presence in ("lineage_presence_before", "lineage_presence_after"):
            record = self._observer().run(constructed_walk_verdicts(1))
            self.assertIn(record[record_presence],
                          observation_module.LINEAGE_PRESENCES)
            os.remove(self.record)

    def test_the_record_stores_no_second_answer(self):
        """A serialized boolean beside the enum is a second answer that can
        disagree with it. The convenience exists on the snapshot, derived."""
        record = self._observer().run(constructed_walk_verdicts(1))
        self.assertNotIn("lineage_present", record)
        self.assertNotIn("presence_note", record)
        snapshot = observation_module.lineage_snapshot(self.root)
        self.assertIs(snapshot.present, snapshot.presence == LINEAGE_PRESENT)
        self.assertFalse(dataclasses.fields(observation_module.LineageSnapshot)[0]
                         .name == "present")

    def test_an_unlistable_namespace_refuses_rather_than_reads_empty(self):
        """`{}` would be this observer deciding that what it could not read was
        not there."""
        blocked = os.path.join(self._dir.name, "blocked")
        os.makedirs(blocked)
        os.chmod(blocked, 0o000)
        self.addCleanup(os.chmod, blocked, 0o700)
        if os.access(blocked, os.R_OK):
            self.skipTest("this user can list an unreadable directory")
        with self.assertRaises(ObservationRefused) as caught:
            observation_module.lineage_snapshot(os.path.join(blocked, "promotion"))
        self.assertEqual(caught.exception.code,
                         observation_module.LINEAGE_INSPECTION_REFUSED)

    def test_an_unlistable_namespace_publishes_no_record(self):
        blocked = os.path.join(self._dir.name, "blocked2")
        os.makedirs(blocked)
        os.chmod(blocked, 0o000)
        self.addCleanup(os.chmod, blocked, 0o700)
        if os.access(blocked, os.R_OK):
            self.skipTest("this user can list an unreadable directory")
        with self.assertRaises(ObservationRefused):
            self._observer(lineage_root=os.path.join(blocked, "promotion")).run(
                constructed_walk_verdicts(1))
        self.assertFalse(os.path.exists(self.record))

    def test_the_snapshot_lists_the_namespace_once(self):
        """Two listings can describe two filesystem instants."""
        listed = []
        real = observation_module.os.listdir

        def counting(path):
            listed.append(path)
            return real(path)

        observation_module.os.listdir = counting
        self.addCleanup(setattr, observation_module.os, "listdir", real)
        self._ledger()
        listed.clear()
        observation_module.lineage_snapshot(self.root)
        self.assertEqual(len(listed), 1)

    def test_the_presence_names_collide_with_nothing_unjudged(self):
        from test_scythe_verdict_vocabularies import (
            cross_set_collisions, discovered_tokens, judged,
        )
        tokens = discovered_tokens()
        for candidate in observation_module.LINEAGE_PRESENCES + (
                observation_module.LINEAGE_INSPECTION_REFUSED,):
            with self.subTest(candidate=candidate):
                self.assertIn(candidate, tokens)
                unjudged = [hit for hit in cross_set_collisions(candidate, tokens)
                            if not judged(candidate, hit)]
                self.assertEqual(unjudged, [])

    def test_the_rejected_presence_name_would_have_collided(self):
        """LINEAGE_ABSENT was checked against the universe and rejected: it
        collides with the ledger's ABSENT coordinate kind."""
        from test_scythe_verdict_vocabularies import collisions, discovered_tokens
        universe = set(discovered_tokens())
        self.assertIn("ABSENT", collisions("LINEAGE_ABSENT", universe))
        # And LINEAGE_INSPECTION_FAILED, which is a substring root of FAILED.
        self.assertIn("FAILED", collisions("LINEAGE_INSPECTION_FAILED", universe))
        self.assertEqual(
            collisions(observation_module.LINEAGE_INSPECTION_REFUSED, universe),
            [observation_module.LINEAGE_INSPECTION_REFUSED])
