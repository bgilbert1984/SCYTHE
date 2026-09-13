"""Slice 10c: the producer, validated entirely against controlled inputs."""

import ast
import os
import tempfile
import unittest

import scythe_derived_evidence_producer as producer_module
from rf_walk_transitions import walk_signature
from scythe_derived_evidence import (
    CONFIGURED_NOT_EXERCISED, DERIVED_SCHEMA_CONFORMANT,
    INSTRUMENT_CONFIGURED_IDLE, MINIMUM_RECORD_INTERVAL_NS,
    RF_MEASUREMENT_NOT_PERFORMED, WALK_COORDINATES, derived_walk_verdicts,
    read_artefact,
)
from scythe_invariant_ledger import Coordinate
from scythe_derived_evidence_producer import (
    ARTEFACT_PUBLICATION_REFUSED, MAX_PRODUCER_RECORDS, PRODUCER_BOUNDS_EXCEEDED,
    PRODUCER_INPUT_REFUSED, PRODUCER_NOT_ENABLED, PROVENANCE_CLAIMS,
    REQUEST_DIGEST_CONFLICT, STEP_MEASURE_COORDINATES, DerivedEvidenceProducer,
    ProducerRefused, require_signature,
)

SOURCES = {"device_id": "rf_bridge", "signal_chain_hash": "rf_receiver_state",
           "configuration_epoch": "rf_bridge", "monotonic_source_id": "rf_bridge"}
COMMON = dict(device_id="dev-1", receiver_state_chain_hash="rsc-1",
              monotonic_source_id="mono-1", signal_chain_hash="chain-1",
              configuration_epoch=1, pose_uncertainty_m=2.0, longitude=-0.12)


def pair(index, step_ns=500_000_000):
    before = walk_signature(latitude=51.5, observed_monotonic_ns=index * step_ns,
                            **COMMON)
    after = walk_signature(latitude=51.5 + index * 1e-5,
                           observed_monotonic_ns=(index + 1) * step_ns, **COMMON)
    return before, after


class ProducerTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name

    def _producer(self, **kw):
        fields = dict(directory=self.dir, run_id="run-1", device_id="dev-1",
                      signal_chain_hash="chain-1", configuration_epoch=1,
                      monotonic_source_id="mono-1", claim_sources=dict(SOURCES),
                      configuration_identity="sha256:cfg",
                      measurement_status=RF_MEASUREMENT_NOT_PERFORMED,
                      instrument_state=INSTRUMENT_CONFIGURED_IDLE,
                      enabled=True)
        fields.update(kw)
        return DerivedEvidenceProducer(**fields)

    def _filled(self, count=3, **kw):
        producer = self._producer(**kw)
        for index in range(count):
            producer.record_walk_step(*pair(index))
        return producer


class ReachabilityTests(ProducerTestCase):
    """§13k L.5: disabled by default carries the rest."""

    def test_the_producer_is_disabled_by_default(self):
        fields = dict(directory=self.dir, run_id="r", device_id="d",
                      signal_chain_hash="c", configuration_epoch=1,
                      monotonic_source_id="m", claim_sources=dict(SOURCES),
                      configuration_identity="sha256:cfg",
                      measurement_status=RF_MEASUREMENT_NOT_PERFORMED,
                      instrument_state=INSTRUMENT_CONFIGURED_IDLE)
        producer = DerivedEvidenceProducer(**fields)
        self.assertFalse(producer.enabled)
        with self.assertRaises(ProducerRefused) as caught:
            producer.record_walk_step(*pair(0))
        self.assertEqual(caught.exception.code, PRODUCER_NOT_ENABLED)

    def test_a_disabled_producer_publishes_nothing(self):
        producer = self._producer(enabled=False)
        with self.assertRaises(ProducerRefused):
            producer.publish()
        self.assertEqual(os.listdir(self.dir), [])

    def test_the_module_acquires_nothing_and_schedules_nothing(self):
        with open(producer_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        for absent in ("socket", "subprocess", "threading", "signal", "sched",
                       "rf_bridge", "rf_iq_ring", "scythe_graphops_adapter",
                       "scythe_promotion_ledger"):
            self.assertNotIn(absent, imported, absent)

    def test_no_pid_is_named_anywhere(self):
        with open(producer_module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("315535", source)
        tree = ast.parse(source)
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for absent in ("kill", "getpid", "waitpid", "Popen"):
            self.assertNotIn(absent, attributes, absent)


class RuntimeGateTests(ProducerTestCase):
    """§13k L.1a: the annotation is not the boundary."""

    def test_a_bytes_like_object_is_refused(self):
        producer = self._producer()
        for impostor in (b"\x00\x01", bytearray(b"\x00"), memoryview(b"\x00")):
            with self.subTest(impostor=type(impostor).__name__):
                with self.assertRaises(ProducerRefused) as caught:
                    producer.record_walk_step(impostor, impostor)
                self.assertEqual(caught.exception.code, PRODUCER_INPUT_REFUSED)

    def test_a_generic_mapping_is_refused(self):
        producer = self._producer()
        with self.assertRaises(ProducerRefused):
            producer.record_walk_step({"anything": "at all"}, {"x": 1})

    def test_a_dict_subclass_is_refused_nominally(self):
        """`isinstance` admits a subclass, and a subclass may override an
        accessor to return anything at the moment the serializer asks."""
        before, after = pair(0)

        class Impostor(dict):
            def items(self):
                return [("latitude", Coordinate("VALUE", 51.5))]

        sneaky = Impostor(before)
        self.assertIsInstance(sneaky, dict)
        with self.assertRaises(ProducerRefused) as caught:
            require_signature("before", sneaky)
        self.assertIn("nominal", caught.exception.detail)

    def test_a_duck_typed_substitute_is_refused(self):
        class Quacks:
            def keys(self):
                return ["latitude"]

            def items(self):
                return [("latitude", Coordinate("VALUE", 51.5))]

            def __getitem__(self, key):
                return Coordinate("VALUE", 51.5)

        with self.assertRaises(ProducerRefused):
            require_signature("before", Quacks())

    def test_a_coordinate_subclass_is_refused(self):
        class Sneaky(Coordinate):
            pass

        with self.assertRaises(ProducerRefused):
            require_signature("before", {"latitude": Sneaky("VALUE", 1.0)})

    def test_validation_precedes_any_filesystem_operation(self):
        """A refusal after a descriptor exists has already touched the
        filesystem."""
        producer = self._producer()
        with self.assertRaises(ProducerRefused):
            producer.record_walk_step(b"buffer", b"buffer")
        self.assertEqual(os.listdir(self.dir), [])

    def test_the_annotations_are_present_and_are_not_the_gate(self):
        signature = producer_module.DerivedEvidenceProducer.record_walk_step
        self.assertIn("before", signature.__annotations__)
        with open(producer_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        gate = [n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "require_signature"]
        self.assertEqual(len(gate), 1)
        source = ast.dump(gate[0])
        self.assertIn("'is'", source.replace('"', "'").replace("IsNot", "'is'"))


class EntrypointTests(ProducerTestCase):
    """§13k L.1: family-specific, never a generic writer."""

    def test_there_is_no_generic_record_writer(self):
        surface = [name for name in dir(DerivedEvidenceProducer)
                   if not name.startswith("_")]
        self.assertIn("record_walk_step", surface)
        for generic in ("write_record", "write", "add", "append", "record"):
            self.assertNotIn(generic, surface, generic)

    def test_every_public_entrypoint_names_its_family(self):
        recorders = [name for name in dir(DerivedEvidenceProducer)
                     if name.startswith("record_")]
        self.assertEqual(recorders, ["record_walk_step"])

    def test_step_measures_are_accepted_and_never_recorded(self):
        """The checker computes them; recording them would duplicate checker
        mathematics, and the reader recomputes them from the inputs.

        The inputs here have already been through `with_step_measures`, which is
        what a caller holding a checked pair actually has. A first version used
        fresh signatures, where the measures are ABSENT and carry no value -- so
        the producer dropped them for the wrong reason and the control that
        removed the filter could not fail.
        """
        from rf_walk_transitions import with_step_measures

        producer = self._producer()
        for index in range(2):
            before, after = pair(index)
            before, after = with_step_measures(before, after,
                                               speed_before_mps=1.4,
                                               speed_after_mps=1.4)
            self.assertIsNotNone(after["displacement_m"].value,
                                 "the fixture must carry computed measures")
            producer.record_walk_step(before, after)

        for record in producer._records:
            for side in ("before", "after"):
                for name in STEP_MEASURE_COORDINATES:
                    self.assertNotIn(name, record[side], name)
            self.assertTrue(set(record["before"]) <= set(WALK_COORDINATES))

    def test_the_producer_never_writes_carries_samples(self):
        """Read from the AST, not the text.

        A first version scanned the module source and matched
        `"writes_carries_samples": False` in `status()` -- the field that
        declares the producer does not write it. The ninth appearance of that
        false positive in this repository, and the first where the matched text
        was an assertion of the very property under test.
        """
        result = self._filled(1).publish()
        with open(result["path"], "rb") as handle:
            self.assertNotIn(b'"carries_samples"', handle.read())

        with open(producer_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        written = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        written.add(key.value)
        self.assertNotIn("carries_samples", written)
        parameters = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                parameters.update(a.arg for a in node.args.args + node.args.kwonlyargs)
        self.assertNotIn("carries_samples", parameters)


class ProvenanceTests(ProducerTestCase):
    """§13k L.2: attested upstream, and each claim names its source."""

    def test_a_claim_with_no_source_refuses_publication(self):
        for claim in PROVENANCE_CLAIMS:
            sources = dict(SOURCES)
            sources[claim] = ""
            with self.subTest(claim=claim):
                producer = self._filled(1, claim_sources=sources)
                with self.assertRaises(ProducerRefused) as caught:
                    producer.publish()
                self.assertEqual(caught.exception.code,
                                 ARTEFACT_PUBLICATION_REFUSED)

    def test_the_attestation_names_every_source(self):
        producer = self._filled(1)
        producer.publish()
        artefact = read_artefact(producer.artefact_path())
        attestation = artefact.provenance["producer_attestation"]
        for claim in PROVENANCE_CLAIMS:
            self.assertIn(claim, attestation)

    def test_publication_refuses_before_creating_anything(self):
        sources = dict(SOURCES, device_id="")
        producer = self._filled(1, claim_sources=sources)
        with self.assertRaises(ProducerRefused):
            producer.publish()
        self.assertEqual(os.listdir(self.dir), [])


class PublicationTests(ProducerTestCase):
    """§13k L.3."""

    def test_publication_follows_the_five_steps_in_order(self):
        from scythe_promotion_lineage import Syscalls

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

        self._filled(2, syscalls=Recording()).publish()
        self.assertEqual(calls, ["OPEN_EXCLUSIVE", "WRITE", "FSYNC_FILE",
                                 "RENAME", "FSYNC_DIRECTORY"])

    def test_a_repeated_run_identity_rediscovers(self):
        first = self._filled(3).publish()
        second = self._filled(3).publish()
        self.assertFalse(first["rediscovered"])
        self.assertTrue(second["rediscovered"])
        self.assertEqual(first["path"], second["path"])
        self.assertEqual(len([n for n in os.listdir(self.dir)
                              if n.endswith(".jsonl")]), 1)

    def test_differing_content_under_one_identity_conflicts(self):
        self._filled(3).publish()
        with self.assertRaises(ProducerRefused) as caught:
            self._filled(4).publish()
        self.assertEqual(caught.exception.code, REQUEST_DIGEST_CONFLICT)

    def test_a_foreign_temporary_is_never_removed(self):
        foreign = os.path.join(self.dir, "derived.deadbeef.jsonl.abc.partial")
        with open(foreign, "wb") as handle:
            handle.write(b"another run was here\n")
        self._filled(2).publish()
        with open(foreign, "rb") as handle:
            self.assertEqual(handle.read(), b"another run was here\n")

    def test_there_is_no_current_artefact_pointer(self):
        self._filled(2).publish()
        names = sorted(os.listdir(self.dir))
        self.assertEqual(len(names), 1)
        self.assertNotIn("current", names[0])
        self.assertNotIn("latest", names[0])

    def test_an_empty_artefact_is_refused(self):
        with self.assertRaises(ProducerRefused) as caught:
            self._producer().publish()
        self.assertEqual(caught.exception.code, ARTEFACT_PUBLICATION_REFUSED)


class FailClosedTests(ProducerTestCase):
    """§13k L.4: defence in depth, and nothing half-written gets a name."""

    def test_a_sub_millisecond_cadence_is_refused(self):
        producer = self._producer()
        producer.record_walk_step(*pair(0, step_ns=1000))
        with self.assertRaises(ProducerRefused) as caught:
            producer.record_walk_step(*pair(1, step_ns=1000))
        self.assertEqual(caught.exception.code, PRODUCER_INPUT_REFUSED)

    def test_exactly_the_floor_is_accepted(self):
        producer = self._producer()
        for index in range(3):
            producer.record_walk_step(*pair(index,
                                            step_ns=MINIMUM_RECORD_INTERVAL_NS))

    def test_a_forbidden_value_aborts_before_any_record_is_kept(self):
        producer = self._producer()
        before, after = pair(0)
        after["latitude"] = Coordinate("VALUE", float("nan"))
        with self.assertRaises(ProducerRefused):
            producer.record_walk_step(before, after)
        self.assertEqual(producer._records, [])
        self.assertEqual(os.listdir(self.dir), [])

    def test_the_record_bound_is_enforced(self):
        producer = self._producer()
        producer._records = [{"kind": "x"}] * MAX_PRODUCER_RECORDS
        with self.assertRaises(ProducerRefused) as caught:
            producer.record_walk_step(*pair(0))
        self.assertEqual(caught.exception.code, PRODUCER_BOUNDS_EXCEEDED)

    def test_the_producer_reuses_the_readers_scalar_rules(self):
        """The same checks, not merely similar ones: two implementations are
        two answers waiting to disagree."""
        with open(producer_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported = {a.name for n in ast.walk(tree)
                    if isinstance(n, ast.ImportFrom) for a in n.names}
        self.assertIn("refuse_scalar", imported)


class RoundTripTests(ProducerTestCase):
    """What the producer writes, the merged 10b reader admits."""

    def test_the_reader_admits_a_produced_artefact(self):
        result = self._filled(4).publish()
        artefact = read_artefact(result["path"])
        self.assertEqual(artefact.assessment, DERIVED_SCHEMA_CONFORMANT)
        self.assertEqual(artefact.artifact_id, result["artifact_id"])
        self.assertEqual(artefact.content_digest, result["content_digest"])

    def test_verdicts_follow_from_a_produced_artefact(self):
        result = self._filled(4).publish()
        verdicts = [v.verdict for v, _s, _c in derived_walk_verdicts(result["path"])]
        self.assertEqual(len(verdicts), 4)

    def test_the_capsule_is_reader_derived(self):
        result = self._filled(2).publish()
        capsule = read_artefact(result["path"]).capsule()
        self.assertFalse(capsule.carries_samples)

    def test_a_produced_artefact_is_not_a_ledger_generation(self):
        from scythe_promotion_ledger_store import LEDGER_UNREADABLE, read_ledger

        result = self._filled(2).publish()
        self.assertEqual(read_ledger(result["path"]).readability,
                         LEDGER_UNREADABLE)

    def test_no_artefact_written_by_a_test_is_real_evidence(self):
        """Recorded rather than assumed: entry 7 drains at a separately
        authorized live act, and a tempdir is not one."""
        result = self._filled(2).publish()
        self.assertTrue(result["path"].startswith(tempfile.gettempdir()))


if __name__ == "__main__":
    unittest.main()


class DeclarationTests(ProducerTestCase):
    """Slice 10d, §13l M.4: the producer states, and never derives."""

    def _published(self, **kw):
        producer = self._filled(**kw)
        return read_artefact(producer.publish()["path"])

    def test_the_declarations_reach_the_artefact(self):
        artefact = self._published()
        self.assertEqual(artefact.measurement_status, RF_MEASUREMENT_NOT_PERFORMED)
        self.assertEqual(artefact.instrument_state, INSTRUMENT_CONFIGURED_IDLE)

    def test_a_labelled_configuration_is_carried_and_stays_unexercised(self):
        artefact = self._published(
            device_id="rtl2838-0bda:2838",
            instrument_settings={"sample_rate_hz": 2_400_000,
                                 "sample_rate_hz_exercise": CONFIGURED_NOT_EXERCISED,
                                 "gain_db": 40.2,
                                 "gain_db_exercise": CONFIGURED_NOT_EXERCISED})
        self.assertEqual(artefact.provenance["sample_rate_hz"], 2_400_000)
        self.assertEqual(artefact.provenance["gain_db_exercise"],
                         CONFIGURED_NOT_EXERCISED)
        self.assertEqual(artefact.measurement_status, RF_MEASUREMENT_NOT_PERFORMED)

    def test_an_unlabelled_setting_is_refused_rather_than_labelled(self):
        """The producer does not supply the missing claim. Labelling on the
        caller's behalf would make the label unable to be wrong."""
        producer = self._filled(instrument_settings={"gain_db": 40.2})
        with self.assertRaises(ProducerRefused) as caught:
            producer.publish()
        self.assertEqual(caught.exception.code, ARTEFACT_PUBLICATION_REFUSED)
        self.assertEqual(os.listdir(self.dir), [])

    def test_an_undeclared_setting_name_is_refused(self):
        producer = self._filled(instrument_settings={"if_frequency_hz": 0})
        with self.assertRaises(ProducerRefused) as caught:
            producer.publish()
        self.assertEqual(caught.exception.code, ARTEFACT_PUBLICATION_REFUSED)

    def test_an_unknown_declaration_never_reaches_the_filesystem(self):
        for kw in ({"measurement_status": "RF_MEASUREMENT_PERFORMED"},
                   {"instrument_state": "INSTRUMENT_STREAMING"}):
            with self.subTest(**kw):
                producer = self._filled(run_id=str(kw), **kw)
                with self.assertRaises(ProducerRefused):
                    producer.publish()
                self.assertEqual(os.listdir(self.dir), [])

    def test_the_declarations_have_no_defaults(self):
        """A default would be the claim made silently, which is the shape M.4
        exists to prevent."""
        import dataclasses
        fields = {f.name: f for f in dataclasses.fields(DerivedEvidenceProducer)}
        for name in ("measurement_status", "instrument_state"):
            with self.subTest(name=name):
                self.assertIs(fields[name].default, dataclasses.MISSING)
                self.assertIs(fields[name].default_factory, dataclasses.MISSING)

    def test_status_reports_what_was_declared(self):
        self.assertEqual(self._producer().status()["measurement_status"],
                         RF_MEASUREMENT_NOT_PERFORMED)
        self.assertEqual(self._producer().status()["instrument_state"],
                         INSTRUMENT_CONFIGURED_IDLE)

    def test_the_producer_repeats_the_readers_check_rather_than_its_own(self):
        """Defence in depth means the *same* check. Two implementations are two
        answers waiting to disagree."""
        with open("scythe_derived_evidence_producer.py") as handle:
            source = ast.parse(handle.read())
        called = {node.func.id for node in ast.walk(source)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertIn("refuse_instrument_declaration", called)
