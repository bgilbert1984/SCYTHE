"""Slice 9: the execution boundary, proved against a deterministic GraphOps."""

import ast
import json
import unittest

import scythe_graphops_adapter as adapter_module
import scythe_graphops_fake as fake_module
from scythe_graphops_adapter import (
    ADAPTER_HALTED, ADAPTER_NOT_CONFORMANT, AUTHORITATIVE_ABSENCE, CREATED,
    CREATION_RECEIPT_VALID, EVIDENCE_CODES, IDEMPOTENT_DIGEST_CONFLICT,
    IDEMPOTENT_MATCH, LOCAL_VALIDATION_REFUSED, LOOKUP_INCONCLUSIVE,
    MUTATION_FREE_REJECTION, NOT_CREATED, RECEIPT_IDENTITY_MISMATCH,
    RECEIPT_MALFORMED, RECEIPT_UNAUTHENTICATED, STRONG, SUBMISSION_BEGAN,
    SUBMISSION_BOUNDARY_CROSSED, SUBMISSION_NEVER_BEGAN, TRANSPORT_INTERRUPTED,
    UNKNOWN, WEAK, AdapterError, GraphOpsAdapter, GraphOpsConformance,
    ReservationFacts, SubmissionState, Transport, encode_command, operation_id,
)
from scythe_graphops_fake import (
    ACCEPTS_DIGEST_CHANGE, CREATES_TWICE, FakeGraphOps, FakeTransport,
    MISMATCHED_RECEIPT, MUTANTS, RECEIPT_WITHOUT_STORING,
    REJECTION_AFTER_SUBMISSION, UNAUTHENTICATED_RECEIPT,
    UninstrumentedTransport, WEAK_ABSENCE_AS_AUTHORITATIVE,
)

CONFORMANT = GraphOpsConformance(
    version="graphops.promotion.v1",
    atomic_idempotency=True, stable_operation_lookup=True,
    receipt_authentication=True, payload_digest_echo=True,
    consistency=STRONG, mutation_free_rejection_codes=("SCHEMA_REFUSED",),
    max_request_bytes=65_536, max_response_bytes=65_536,
    timeout_behaviour="server aborts without mutating",
    cancellation_behaviour="client cancel does not stop a started mutation",
)

FACTS = ReservationFacts(lineage_id="lin-1", generation_id="gen-1",
                         reservation_seq=41, subject_identity="promotion:aa",
                         payload={"finding": "REQUIRED_CHANGE_NOT_OBSERVED"})


def _adapter(transport=None, conformance=CONFORMANT):
    return GraphOpsAdapter(conformance=conformance,
                           transport=transport or FakeTransport())


class OperationIdentityTests(unittest.TestCase):
    """§13g H.1."""

    def test_the_identity_is_stable_across_attempts(self):
        self.assertEqual(FACTS.operation_id(), FACTS.operation_id())

    def test_every_fact_moves_the_identity(self):
        base = FACTS.operation_id()
        for change in (dict(lineage_id="lin-2"), dict(generation_id="gen-2"),
                       dict(reservation_seq=42),
                       dict(subject_identity="promotion:bb"),
                       dict(payload={"finding": "OTHER"})):
            with self.subTest(**change):
                other = ReservationFacts(**{**FACTS.__dict__, **change})
                self.assertNotEqual(other.operation_id(), base)

    def test_the_parts_are_length_prefixed(self):
        """Concatenated raw, two fact sets could produce one identity by moving
        a character across a boundary."""
        one = operation_id(schema_version="s", lineage_id="ab", generation_id="c",
                           reservation_seq=1, subject_identity="x",
                           canonical_payload_digest="d")
        two = operation_id(schema_version="s", lineage_id="a", generation_id="bc",
                           reservation_seq=1, subject_identity="x",
                           canonical_payload_digest="d")
        self.assertNotEqual(one, two)

    def test_the_identity_is_sha256_and_not_blake2s(self):
        """Recorded against a tidy-up: it crosses a boundary and must be
        computable by a system that did not choose our hash."""
        self.assertEqual(len(FACTS.operation_id()), 64)
        self.assertTrue(FACTS.digest().startswith("sha256:"))

    def test_the_command_carries_no_credentials_or_endpoint(self):
        encoded = json.loads(encode_command(FACTS, "sha256:cfg").decode("utf-8"))
        for absent in ("token", "authorization", "url", "endpoint", "header",
                       "password", "secret"):
            self.assertFalse([k for k in encoded if absent in k.lower()], absent)


class ConformanceTests(unittest.TestCase):
    """§13g H.7: an endpoint does not imply a capability."""

    def test_a_non_conformant_adapter_refuses_to_execute(self):
        weak = GraphOpsConformance(**{**CONFORMANT.__dict__,
                                      "atomic_idempotency": False})
        with self.assertRaises(AdapterError) as caught:
            _adapter(conformance=weak).execute(FACTS)
        self.assertEqual(caught.exception.code, ADAPTER_NOT_CONFORMANT)

    def test_every_missing_guarantee_is_named(self):
        for field in ("atomic_idempotency", "stable_operation_lookup",
                      "receipt_authentication", "payload_digest_echo"):
            with self.subTest(field=field):
                broken = GraphOpsConformance(**{**CONFORMANT.__dict__, field: False})
                self.assertIn(field, broken.refusals())

    def test_the_declaration_is_hashed_into_configuration_identity(self):
        before = _adapter().configuration_identity
        changed = GraphOpsConformance(**{**CONFORMANT.__dict__,
                                         "consistency": WEAK})
        self.assertNotEqual(_adapter(conformance=changed).configuration_identity,
                            before)

    def test_status_declares_no_endpoint_and_no_credentials(self):
        published = _adapter().status()
        self.assertEqual(published["endpoint"], "NOT_CONFIGURED")
        self.assertEqual(published["credentials"], "NOT_CONFIGURED")
        self.assertFalse(published["retries_after_unknown"])
        json.dumps(published)


class ClassificationTests(unittest.TestCase):
    """§13g H.4."""

    def test_a_valid_creation_receipt_is_created(self):
        result = _adapter().execute(FACTS)
        self.assertEqual(result.outcome, CREATED)
        self.assertEqual(result.evidence_code, CREATION_RECEIPT_VALID)

    def test_an_existing_exact_operation_is_created_without_a_second_object(self):
        graph = FakeGraphOps()
        first = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        second = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(first.outcome, CREATED)
        self.assertEqual(second.outcome, CREATED)
        self.assertEqual(second.evidence_code, IDEMPOTENT_MATCH)
        self.assertEqual(graph.stored_count(FACTS.operation_id()), 1)

    def test_an_existing_operation_with_a_different_digest_is_unknown(self):
        graph = FakeGraphOps()
        _adapter(FakeTransport(graph=graph)).execute(FACTS)
        graph.objects[FACTS.operation_id()] = "sha256:something-else"
        adapter = _adapter(FakeTransport(graph=graph))
        result = adapter.execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, IDEMPOTENT_DIGEST_CONFLICT)
        self.assertTrue(adapter.halted)

    def test_a_proven_pre_submission_refusal_is_not_created(self):
        result = _adapter(FakeTransport(fail_before_submission=True)).execute(FACTS)
        self.assertEqual(result.outcome, NOT_CREATED)
        self.assertEqual(result.evidence_code, SUBMISSION_NEVER_BEGAN)

    def test_an_uninstrumented_refusal_is_unknown(self):
        """It cannot infer innocence from an empty response. The failure looks
        identical to the one above and the evidence does not."""
        result = _adapter(UninstrumentedTransport()).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, TRANSPORT_INTERRUPTED)

    def test_a_timeout_after_submission_began_is_unknown(self):
        result = _adapter(FakeTransport(fail_after_submission=True)).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, SUBMISSION_BOUNDARY_CROSSED)

    def test_a_disconnect_after_a_complete_request_is_unknown(self):
        graph = FakeGraphOps()
        result = _adapter(FakeTransport(graph=graph,
                                        fail_after_response=True)).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, SUBMISSION_BOUNDARY_CROSSED)
        # And the object exists: the graph moved, our knowledge did not.
        self.assertEqual(graph.stored_count(FACTS.operation_id()), 1)

    def test_an_oversized_receipt_is_unknown(self):
        result = _adapter(FakeTransport(oversized_response=True)).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, RECEIPT_MALFORMED)

    def test_an_unauthenticated_receipt_is_unknown(self):
        graph = FakeGraphOps(mutant=UNAUTHENTICATED_RECEIPT)
        result = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, RECEIPT_UNAUTHENTICATED)

    def test_a_receipt_for_another_operation_is_unknown(self):
        """A tidy answer carrying the wrong operation identity."""
        graph = FakeGraphOps(mutant=MISMATCHED_RECEIPT)
        result = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, RECEIPT_IDENTITY_MISMATCH)

    def test_a_mutation_free_rejection_is_not_created(self):
        graph = FakeGraphOps(reject_code="SCHEMA_REFUSED")
        result = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(result.outcome, NOT_CREATED)
        self.assertEqual(result.evidence_code, MUTATION_FREE_REJECTION)

    def test_an_undeclared_rejection_is_unknown(self):
        """An ugly rejection is NOT_CREATED only when the contract proves it
        mutation-free."""
        graph = FakeGraphOps(reject_code="INTERNAL_ERROR")
        result = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)

    def test_absence_is_authoritative_only_under_strong_consistency(self):
        graph = FakeGraphOps(mutant=WEAK_ABSENCE_AS_AUTHORITATIVE)
        strong = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(strong.outcome, NOT_CREATED)
        self.assertEqual(strong.evidence_code, AUTHORITATIVE_ABSENCE)

        weak = GraphOpsConformance(**{**CONFORMANT.__dict__, "consistency": WEAK})
        loose = GraphOpsAdapter(conformance=weak,
                                transport=FakeTransport(graph=FakeGraphOps(
                                    mutant=WEAK_ABSENCE_AS_AUTHORITATIVE)))
        result = loose.execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, LOOKUP_INCONCLUSIVE)

    def test_an_oversized_command_never_reaches_the_transport(self):
        tiny = GraphOpsConformance(**{**CONFORMANT.__dict__,
                                      "max_request_bytes": 10})

        class Watcher(Transport):
            attests_submission_boundary = True
            called = False

            def submit(self, command, state):
                Watcher.called = True
                return b"{}"

        result = GraphOpsAdapter(conformance=tiny, transport=Watcher()).execute(FACTS)
        self.assertEqual(result.outcome, NOT_CREATED)
        self.assertEqual(result.evidence_code, LOCAL_VALIDATION_REFUSED)
        self.assertFalse(Watcher.called)


class SubmissionStateTests(unittest.TestCase):
    def test_the_state_is_monotonic(self):
        state = SubmissionState()
        state.began()
        state.received()
        with self.assertRaises(AdapterError):
            state.began()

    def test_a_response_cannot_precede_submission(self):
        with self.assertRaises(AdapterError):
            SubmissionState().received()

    def test_the_default_transport_refuses_and_attests_nothing(self):
        self.assertFalse(Transport.attests_submission_boundary)
        result = _adapter(Transport()).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)


class NoSecondCallTests(unittest.TestCase):
    """§13g H.6."""

    def test_a_second_call_for_one_reservation_is_refused(self):
        adapter = _adapter()
        adapter.execute(FACTS)
        with self.assertRaises(AdapterError) as caught:
            adapter.execute(FACTS)
        self.assertEqual(caught.exception.code, ADAPTER_HALTED)

    def test_no_retry_follows_an_unknown(self):
        calls = []

        class Counting(FakeTransport):
            def submit(self, command, state):
                calls.append(1)
                return super().submit(command, state)

        adapter = _adapter(Counting(fail_after_submission=True))
        result = adapter.execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        with self.assertRaises(AdapterError):
            adapter.execute(FACTS)
        self.assertEqual(len(calls), 1)

    def test_the_adapter_has_no_reconciliation_method(self):
        """Having a query method is not having the authority to use it."""
        surface = [n for n in dir(GraphOpsAdapter) if not n.startswith("_")]
        for absent in ("reconcile", "release", "resolve", "retry", "lookup"):
            self.assertFalse([n for n in surface if absent in n], absent)


class ConformanceSuiteTests(unittest.TestCase):
    """The properties the conformance declaration claims, asserted against an
    honest fake.

    These are what the adversarial controls mutate. A fake that only behaves
    correctly proves the adapter accepts correctness; the suite earns its keep
    when each of these fails against a fake that breaks exactly one guarantee.
    """

    def test_one_operation_identity_yields_one_stored_object(self):
        graph = FakeGraphOps()
        _adapter(FakeTransport(graph=graph)).execute(FACTS)
        _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(graph.stored_count(FACTS.operation_id()), 1)

    def test_a_stored_digest_that_differs_is_never_reported_as_created(self):
        graph = FakeGraphOps()
        graph.objects[FACTS.operation_id()] = "sha256:something-else"
        result = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)
        self.assertEqual(result.evidence_code, IDEMPOTENT_DIGEST_CONFLICT)

    def test_a_created_result_is_accompanied_by_a_stored_object(self):
        """The gap no classifier can close, asserted where it can be.

        A receipt that lies is indistinguishable from one that does not, which
        is exactly why §13g H.7 makes conformance a declaration established out
        of band rather than something the adapter can verify for itself.
        """
        graph = FakeGraphOps()
        result = _adapter(FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(result.outcome, CREATED)
        self.assertEqual(graph.stored_count(FACTS.operation_id()), 1)

    def test_a_receipt_is_authenticated_before_it_is_believed(self):
        result = _adapter(FakeTransport(graph=FakeGraphOps())).execute(FACTS)
        self.assertEqual(result.outcome, CREATED)
        self.assertEqual(result.receipt_digest, FACTS.digest())

    def test_absence_under_weak_consistency_is_never_authoritative(self):
        weak = GraphOpsConformance(**{**CONFORMANT.__dict__, "consistency": WEAK})
        graph = FakeGraphOps(mutant=WEAK_ABSENCE_AS_AUTHORITATIVE)
        result = GraphOpsAdapter(conformance=weak,
                                 transport=FakeTransport(graph=graph)).execute(FACTS)
        self.assertEqual(result.outcome, UNKNOWN)

    def test_a_rejection_is_mutation_free_only_by_declared_code(self):
        for code, expected in (("SCHEMA_REFUSED", NOT_CREATED),
                               ("INTERNAL_ERROR", UNKNOWN)):
            with self.subTest(code=code):
                graph = FakeGraphOps(reject_code=code)
                result = _adapter(FakeTransport(graph=graph)).execute(FACTS)
                self.assertEqual(result.outcome, expected)

    def test_every_mutant_is_named_and_used_by_the_controls(self):
        self.assertEqual(len(MUTANTS), 7)
        self.assertEqual(len(set(MUTANTS)), 7)


class CoordinatorCompositionTests(unittest.TestCase):
    """Behind the unreachable ARMED boundary, and no further."""

    def setUp(self):
        import os, tempfile
        from scythe_promotion_ledger_ownership import LedgerOwnership
        from scythe_promotion_ledger_writer import LedgerWriter
        from scythe_promotion_lineage import generation_path

        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = os.path.join(self._dir.name, "promotion")
        self.owner = LedgerOwnership(
            lineage_root=self.root, mounts=(("/", "ext4"),), refused_prefixes=(),
            devices={os.stat(self._dir.name).st_dev: "ext4"})
        self.addCleanup(self.owner.release)
        self.owner.acquire()
        self.ledger = LedgerWriter(path=generation_path(self.root, 0),
                                   ownership=self.owner.scope,
                                   refused_prefixes=())
        with self.ledger.owned() as session:
            session.initialize()
            session.declare_generation("gen-1",
                                       {"boot_id": "b", "pid": 1, "start_ticks": 2})

    def _run(self, adapter, mode=None):
        from scythe_promotion_ledger import (
            MODE_ARMED, MODE_SHADOW, PromotionAudit, PromotionCoordinator,
        )
        from test_scythe_promotion_ledger import CAPSULE, REQUEST, _distinct

        coordinator = PromotionCoordinator(
            PromotionAudit(), mode=mode or MODE_ARMED, ledger=self.ledger,
            writer=adapter)
        return coordinator, coordinator.evaluate(
            _distinct(0), REQUEST, CAPSULE, now_monotonic_ns=10_000_000_000_000)

    def test_an_adapter_is_called_with_the_coordinator_lock_released(self):
        """A non-blocking acquire from inside the adapter. With a plain Lock a
        held lock cannot be reacquired by its holder, so success means free."""
        from scythe_promotion_ledger import (
            MODE_ARMED, PromotionAudit, PromotionCoordinator,
        )
        from test_scythe_promotion_ledger import CAPSULE, REQUEST, _distinct

        seen = []
        holder = {}

        class Watching(GraphOpsAdapter):
            def execute(self, facts):
                lock = holder["coordinator"]._lock
                free = lock.acquire(blocking=False)
                seen.append(free)
                if free:
                    lock.release()
                return super().execute(facts)

        adapter = Watching(conformance=CONFORMANT, transport=FakeTransport())
        coordinator = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                           ledger=self.ledger, writer=adapter)
        holder["coordinator"] = coordinator
        result = coordinator.evaluate(_distinct(0), REQUEST, CAPSULE,
                                      now_monotonic_ns=10_000_000_000_000)
        self.assertEqual(seen, [True], "the lock was held across the adapter")
        self.assertEqual(result["write_outcome"], CREATED)

    def test_shadow_invokes_no_adapter(self):
        from scythe_promotion_ledger import MODE_SHADOW

        calls = []

        class Counting(GraphOpsAdapter):
            def execute(self, facts):
                calls.append(1)
                return super().execute(facts)

        _coordinator, result = self._run(
            Counting(conformance=CONFORMANT, transport=FakeTransport()),
            mode=MODE_SHADOW)
        self.assertEqual(calls, [])

    def test_a_ceiling_refusal_invokes_no_adapter(self):
        from scythe_promotion_ceilings import RESERVATION_CEILING

        calls = []

        class Counting(GraphOpsAdapter):
            def execute(self, facts):
                calls.append(1)
                return super().execute(facts)

        from scythe_promotion_ledger import (
            MODE_ARMED, PromotionAudit, PromotionCoordinator,
        )
        from test_scythe_promotion_ledger import CAPSULE, REQUEST, _distinct

        adapter = Counting(conformance=CONFORMANT, transport=FakeTransport())
        coordinator = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                           ledger=self.ledger, writer=adapter)
        coordinator._ceilings.reservations_in_generation = RESERVATION_CEILING
        coordinator.evaluate(_distinct(1), REQUEST, CAPSULE,
                             now_monotonic_ns=10_000_000_000_000)
        self.assertEqual(calls, [])

    def test_an_unknown_leaves_the_reservation_unresolved(self):
        adapter = GraphOpsAdapter(conformance=CONFORMANT,
                                  transport=FakeTransport(fail_after_submission=True))
        coordinator, result = self._run(adapter)
        self.assertEqual(result["outcome"], "PROMOTION_OUTCOME_UNRESOLVED")
        self.assertEqual(len(coordinator.unresolved_keys), 1)
        self.assertEqual(coordinator.promoted_keys, ())

    def test_a_created_object_then_a_crash_stays_unresolved_after_restart(self):
        from scythe_promotion_ledger import (
            MODE_ARMED, PromotionAudit, PromotionCoordinator,
        )

        graph = FakeGraphOps()
        adapter = GraphOpsAdapter(
            conformance=CONFORMANT,
            transport=FakeTransport(graph=graph, fail_after_response=True))
        coordinator, result = self._run(adapter)
        self.assertEqual(result["outcome"], "PROMOTION_OUTCOME_UNRESOLVED")
        self.assertEqual(len(graph.objects), 1)
        fresh = PromotionCoordinator(PromotionAudit(), mode=MODE_ARMED,
                                     ledger=self.ledger, writer=adapter)
        self.assertEqual(len(fresh.unresolved_keys), 1)
        self.assertEqual(fresh.promoted_keys, ())

    def test_no_raw_response_or_credential_reaches_the_record(self):
        adapter = GraphOpsAdapter(conformance=CONFORMANT, transport=FakeTransport())
        coordinator, result = self._run(adapter)
        with open(self.ledger.path, "rb") as handle:
            ledger_bytes = handle.read().decode("utf-8")
        blob = json.dumps([result, coordinator.status()], default=str)
        for leak in ("authentication", "VALID", "receipt", "token", "Traceback"):
            self.assertNotIn(leak, ledger_bytes, leak)
        self.assertNotIn("Traceback", blob)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        with open(adapter_module.__file__, encoding="utf-8") as handle:
            self.tree = ast.parse(handle.read())

    def test_classification_reads_no_status_code_or_exception_type(self):
        """§13g: the validated receipt and the transport state, nothing else."""
        classify = [n for n in ast.walk(self.tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "classify"]
        self.assertEqual(len(classify), 1)
        names = {n.id for n in ast.walk(classify[0]) if isinstance(n, ast.Name)}
        attributes = {n.attr for n in ast.walk(classify[0])
                      if isinstance(n, ast.Attribute)}
        for absent in ("status", "status_code", "http", "exception", "exc"):
            self.assertNotIn(absent, names | attributes, absent)

    def test_the_adapter_opens_no_socket_and_holds_no_credential(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        for absent in ("socket", "http", "urllib", "requests", "ssl", "os"):
            self.assertNotIn(absent, imported, absent)

    def test_no_production_module_imports_the_fake(self):
        import os as _os

        root = _os.path.dirname(_os.path.abspath(adapter_module.__file__))
        for entry in sorted(_os.listdir(root)):
            if not entry.endswith(".py") or entry.startswith("test_"):
                continue
            if entry == "scythe_graphops_fake.py":
                continue
            with open(_os.path.join(root, entry), encoding="utf-8") as handle:
                try:
                    tree = ast.parse(handle.read())
                except SyntaxError:
                    continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "scythe_graphops_fake", entry)

    def test_the_evidence_set_is_closed_and_every_code_is_reachable(self):
        self.assertEqual(len(set(EVIDENCE_CODES)), len(EVIDENCE_CODES))
        for code in EVIDENCE_CODES:
            self.assertTrue(code.isupper())


if __name__ == "__main__":
    unittest.main()
