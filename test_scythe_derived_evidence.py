"""Slice 10b: the derived-evidence reader, and what it refuses."""

import ast
import os
import tempfile
import unittest

import scythe_derived_evidence as reader_module
from scythe_derived_evidence import (
    ARTEFACT_PROVENANCE, ARTEFACT_SCHEMA, ARTEFACT_TOO_LARGE,
    ARTEFACT_UNREADABLE, CONFIGURED_NOT_EXERCISED, CONTENT_DIGEST_MISMATCH,
    COORDINATE_NOT_IN_SCHEMA, DERIVED_SCHEMA_CONFORMANT, EXERCISE_SUFFIX,
    FRAME_VERSION, INSTRUMENT_CONFIGURED_IDLE, INSTRUMENT_SETTINGS,
    INSTRUMENT_STATES, MEASUREMENT_STATUSES, RF_MEASUREMENT_NOT_PERFORMED,
    MAX_ARTEFACT_RECORDS,
    MAX_RECORD_BYTES, MAX_STRING_BYTES, MINIMUM_RECORD_INTERVAL_NS,
    NON_SCALAR_COORDINATE, OVERSIZED_COORDINATE, RECORD_INTERVAL_REFUSED,
    SAMPLE_BEARING_DETECTED, SAMPLE_STATUS_UNVERIFIABLE, WALK_STEP_PAIR,
    ArtefactRefused, artifact_identity, declared_instrument_state,
    declared_measurement_status, derived_walk_verdicts, encode_for_test,
    read_artefact, refuse_scalar,
)

COMMON = {"device_id": "dev-1", "receiver_state_chain_hash": "rsc-1",
          "monotonic_source_id": "mono-1", "signal_chain_hash": "chain-1",
          "configuration_epoch": 1, "pose_uncertainty_m": 2.0,
          "surface_rows_contributed": 0, "longitude": -0.12}

PROVENANCE = {"kind": ARTEFACT_PROVENANCE, "schema": ARTEFACT_SCHEMA,
              "frame_version": FRAME_VERSION, "device_id": "dev-1",
              "signal_chain_hash": "chain-1", "configuration_epoch": 1,
              "monotonic_source_id": "mono-1",
              "producer_attestation": "walk-aggregation-v1",
              "measurement_status": RF_MEASUREMENT_NOT_PERFORMED,
              "instrument_state": INSTRUMENT_CONFIGURED_IDLE}

# The shape M.4 exists for: an instrument named and configured, and idle. Every
# populated setting carries its own label.
CONFIGURED = dict(PROVENANCE, device_id="rtl2838-0bda:2838",
                  sample_rate_hz=2_400_000,
                  sample_rate_hz_exercise=CONFIGURED_NOT_EXERCISED,
                  gain_db=40.2, gain_db_exercise=CONFIGURED_NOT_EXERCISED)


def walk_record(index, *, step_ns=500_000_000, **overrides):
    before = dict(COMMON, latitude=51.5, observed_monotonic_ns=index * step_ns)
    after = dict(COMMON, latitude=51.5 + index * 1e-5,
                 observed_monotonic_ns=(index + 1) * step_ns)
    after.update(overrides)
    return {"kind": WALK_STEP_PAIR, "before": before, "after": after}


class ReaderTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.dir = self._dir.name

    def _write(self, records, provenance=None, name="artefact.jsonl",
               tamper=None):
        data = encode_for_test(dict(provenance or PROVENANCE), records)
        if tamper is not None:
            data = tamper(data)
        path = os.path.join(self.dir, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def _refused(self, code, records, **kw):
        path = self._write(records, **kw)
        with self.assertRaises(ArtefactRefused) as caught:
            read_artefact(path)
        self.assertEqual(caught.exception.code, code)
        return caught.exception


class AdmissionTests(ReaderTestCase):
    def test_a_conformant_artefact_is_read(self):
        artefact = read_artefact(self._write([walk_record(i) for i in range(4)]))
        self.assertEqual(artefact.assessment, DERIVED_SCHEMA_CONFORMANT)
        self.assertEqual(len(artefact.records), 4)
        self.assertTrue(artefact.conformant)

    def test_an_undeclared_coordinate_is_refused(self):
        self._refused(COORDINATE_NOT_IN_SCHEMA,
                      [walk_record(0, invented_field="x")])

    def test_a_container_value_is_refused(self):
        for value in ([1, 2, 3], {"a": 1}, (1,)):
            with self.subTest(value=value):
                self._refused(NON_SCALAR_COORDINATE,
                              [walk_record(0, latitude=value)])

    def test_a_boolean_is_not_a_measurement(self):
        self._refused(NON_SCALAR_COORDINATE,
                      [walk_record(0, configuration_epoch=True)])

    def test_an_oversized_string_is_refused(self):
        """Base64 is a string, and a long one is an archive."""
        import base64

        blob = base64.b64encode(b"\x01\x02" * 4096).decode("ascii")
        self.assertGreater(len(blob), MAX_STRING_BYTES)
        self._refused(OVERSIZED_COORDINATE, [walk_record(0, device_id=blob)])

    def test_a_numeric_string_is_refused(self):
        """It lets the string bound and the numeric bound each assume the other
        applied."""
        self._refused(NON_SCALAR_COORDINATE, [walk_record(0, device_id="1.0")])

    def test_non_finite_numbers_are_refused(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ArtefactRefused):
                    refuse_scalar("latitude", value)

    def test_excessive_precision_is_refused(self):
        """A float's mantissa is sixty-odd bits of anywhere the producer
        likes."""
        with self.assertRaises(ArtefactRefused) as caught:
            refuse_scalar("latitude", 51.50000000012345678)
        self.assertEqual(caught.exception.code, OVERSIZED_COORDINATE)
        refuse_scalar("latitude", 51.500000001)

    def test_ordinary_values_pass(self):
        for value in (51.5, 0, -12, "dev-1", 2.0):
            refuse_scalar("x", value)


class BoundTests(ReaderTestCase):
    def test_too_many_records_are_refused(self):
        records = [walk_record(i) for i in range(MAX_ARTEFACT_RECORDS + 1)]
        self._refused(ARTEFACT_TOO_LARGE, records)

    def test_an_oversized_frame_is_refused(self):
        fat = "x" * (MAX_STRING_BYTES - 1)
        record = walk_record(0)
        for index in range(80):
            record["after"][f"pad{index}"] = fat
        path = self._write([record])
        with self.assertRaises(ArtefactRefused) as caught:
            read_artefact(path)
        self.assertIn(caught.exception.code,
                      (ARTEFACT_TOO_LARGE, COORDINATE_NOT_IN_SCHEMA))

    def test_the_bounds_are_not_configurable(self):
        with open(reader_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertNotIn("getenv", attributes)
        self.assertNotIn("environ", attributes)
        signature = read_artefact.__code__.co_varnames[
            :read_artefact.__code__.co_argcount]
        self.assertEqual(list(signature), ["path"])


class CadenceTests(ReaderTestCase):
    """§13j K.2, and what it does not claim."""

    def test_a_sub_millisecond_stream_is_refused(self):
        records = [walk_record(i, step_ns=1000) for i in range(5)]
        error = self._refused(RECORD_INTERVAL_REFUSED, records)
        self.assertIn("high-rate record stream", error.detail)

    def test_timestamps_must_strictly_increase(self):
        record = walk_record(0)
        record["after"]["observed_monotonic_ns"] = 0
        self._refused(RECORD_INTERVAL_REFUSED, [walk_record(0), record])

    def test_a_missing_timestamp_is_refused(self):
        record = walk_record(0)
        del record["after"]["observed_monotonic_ns"]
        self._refused(RECORD_INTERVAL_REFUSED, [record])

    def test_a_float_timestamp_is_refused(self):
        """Integers only: a float conversion loses the low bits at exactly the
        magnitudes that matter."""
        self._refused(RECORD_INTERVAL_REFUSED,
                      [walk_record(0, observed_monotonic_ns=1.5e9)])

    def test_exactly_the_floor_is_admitted(self):
        records = [walk_record(i, step_ns=MINIMUM_RECORD_INTERVAL_NS)
                   for i in range(3)]
        read_artefact(self._write(records))

    def test_the_rule_does_not_claim_to_catch_slower_encoding(self):
        """A producer spacing encoded scalars a millisecond apart passes, and
        the contract says so. This test exists so the limit is asserted rather
        than assumed."""
        records = [walk_record(i, step_ns=MINIMUM_RECORD_INTERVAL_NS)
                   for i in range(10)]
        artefact = read_artefact(self._write(records))
        self.assertEqual(artefact.assessment, DERIVED_SCHEMA_CONFORMANT)

    def test_the_comparison_path_uses_no_float_or_wall_clock(self):
        with open(reader_module.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        cadence = [n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_refuse_cadence"]
        self.assertEqual(len(cadence), 1)
        names = {n.id for n in ast.walk(cadence[0]) if isinstance(n, ast.Name)}
        attributes = {n.attr for n in ast.walk(cadence[0])
                      if isinstance(n, ast.Attribute)}
        for absent in ("float", "time", "utc", "monotonic", "datetime"):
            self.assertNotIn(absent, names, absent)
            self.assertNotIn(absent, attributes, absent)


class SampleAssessmentTests(ReaderTestCase):
    """§13i J.3: three states, and only one derives the flag."""

    def test_a_sample_bearing_field_is_detected_recursively(self):
        record = walk_record(0)
        record["nested"] = {"deeper": {"iq_samples": "anything"}}
        path = self._write([record])
        with self.assertRaises(ArtefactRefused) as caught:
            list(derived_walk_verdicts(path))
        self.assertEqual(caught.exception.code, SAMPLE_BEARING_DETECTED)

    def test_a_missing_attestation_is_unverifiable(self):
        provenance = dict(PROVENANCE, producer_attestation="")
        path = self._write([walk_record(0)], provenance=provenance)
        artefact = read_artefact(path)
        self.assertEqual(artefact.assessment, SAMPLE_STATUS_UNVERIFIABLE)
        with self.assertRaises(ArtefactRefused):
            artefact.capsule()

    def test_only_a_conformant_artefact_derives_the_flag(self):
        artefact = read_artefact(self._write([walk_record(0)]))
        capsule = artefact.capsule()
        self.assertFalse(capsule.carries_samples)
        self.assertTrue(capsule.within_bounds)

    def test_a_caller_cannot_supply_carries_samples(self):
        import inspect

        signature = inspect.signature(reader_module.Artefact.capsule)
        self.assertEqual(list(signature.parameters), ["self"])

    def test_refusal_precedes_both_checkers(self):
        """No verdict is derived from evidence whose sample status is unknown:
        the uncertainty would enter a promotion identity where nothing
        downstream could recover it."""
        for provenance, expected in (
                (dict(PROVENANCE, producer_attestation=""),
                 SAMPLE_STATUS_UNVERIFIABLE),):
            path = self._write([walk_record(0)], provenance=provenance)
            with self.assertRaises(ArtefactRefused) as caught:
                list(derived_walk_verdicts(path))
            self.assertEqual(caught.exception.code, expected)

    def test_the_three_states_are_the_declared_set(self):
        from scythe_derived_evidence import SAMPLE_ASSESSMENTS

        self.assertEqual(SAMPLE_ASSESSMENTS,
                         (SAMPLE_BEARING_DETECTED, DERIVED_SCHEMA_CONFORMANT,
                          SAMPLE_STATUS_UNVERIFIABLE))


class DigestTests(ReaderTestCase):
    """§13i J.7, J.7a."""

    def test_a_tampered_record_is_a_digest_mismatch(self):
        def tamper(data):
            return data.replace(b"51.5", b"52.5", 1)

        self._refused(ARTEFACT_UNREADABLE, [walk_record(0)], tamper=tamper)

    def test_the_digest_excludes_the_provenance_record(self):
        artefact = read_artefact(self._write([walk_record(i) for i in range(3)]))
        expected = artifact_identity(artefact.provenance,
                                     artefact.content_digest)
        self.assertEqual(artefact.artifact_id, expected)

    def test_a_wrong_content_digest_is_refused(self):
        """The bytes consumed are not the bytes attested."""
        import json as _json

        records = [walk_record(0)]
        data = encode_for_test(dict(PROVENANCE), records)
        head, _, rest = data.partition(b"\n")
        provenance = _json.loads(head[18:].decode("utf-8"))
        provenance["content_digest"] = "sha256:" + "0" * 64
        path = os.path.join(self.dir, "broken.jsonl")
        with open(path, "wb") as handle:
            handle.write(reader_module.frame_of(provenance) + rest)
        with self.assertRaises(ArtefactRefused) as caught:
            read_artefact(path)
        self.assertEqual(caught.exception.code, CONTENT_DIGEST_MISMATCH)

    def test_trailing_bytes_are_refused(self):
        self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                      tamper=lambda data: data + b"leftover")

    def test_a_second_provenance_record_is_refused(self):
        data = encode_for_test(dict(PROVENANCE), [walk_record(0)])
        first = data.split(b"\n")[0] + b"\n"
        path = os.path.join(self.dir, "two.jsonl")
        with open(path, "wb") as handle:
            handle.write(data + first)
        with self.assertRaises(ArtefactRefused) as caught:
            read_artefact(path)
        self.assertEqual(caught.exception.code, ARTEFACT_UNREADABLE)

    def test_a_record_count_mismatch_is_refused(self):
        records = [walk_record(0), walk_record(1)]
        data = encode_for_test(dict(PROVENANCE), records)
        lines = data.split(b"\n")
        path = os.path.join(self.dir, "short.jsonl")
        with open(path, "wb") as handle:
            handle.write(b"\n".join(lines[:2]) + b"\n")
        with self.assertRaises(ArtefactRefused):
            read_artefact(path)


class ReadDisciplineTests(ReaderTestCase):
    """§13i J.7: one descriptor, a regular file, no reopening."""

    def test_a_directory_is_refused(self):
        with self.assertRaises(ArtefactRefused) as caught:
            read_artefact(self.dir)
        self.assertEqual(caught.exception.code, ARTEFACT_UNREADABLE)

    def test_a_character_device_is_refused(self):
        """A device is a stream and this reader's bounds are about a file.

        `/dev/null` rather than a FIFO: opening a FIFO read-only blocks until a
        writer arrives, so the test would hang rather than assert -- a first
        version of it did, which is a reminder that a test whose failure mode is
        a hang reports nothing at all.
        """
        if not os.path.exists("/dev/null"):
            self.skipTest("no character device available")
        with self.assertRaises(ArtefactRefused) as caught:
            read_artefact("/dev/null")
        self.assertEqual(caught.exception.code, ARTEFACT_UNREADABLE)

    def test_the_path_is_opened_exactly_once(self):
        path = self._write([walk_record(0)])
        opens = []
        real = os.open

        def counting(target, *args, **kw):
            if str(target) == path:
                opens.append(target)
            return real(target, *args, **kw)

        reader_module.os.open = counting
        try:
            read_artefact(path)
        finally:
            reader_module.os.open = real
        self.assertEqual(len(opens), 1)

    def test_a_symlink_is_read_through_its_descriptor(self):
        """The digest attests the bytes this descriptor produced, so repointing
        the name afterwards changes nothing that was attested."""
        real_path = self._write([walk_record(0)], name="real.jsonl")
        link = os.path.join(self.dir, "link.jsonl")
        os.symlink(real_path, link)
        first = read_artefact(link)
        other = self._write([walk_record(0), walk_record(1)], name="other.jsonl")
        os.unlink(link)
        os.symlink(other, link)
        second = read_artefact(link)
        self.assertNotEqual(first.content_digest, second.content_digest)
        self.assertNotEqual(first.artifact_id, second.artifact_id)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        with open(reader_module.__file__, encoding="utf-8") as handle:
            self.source = handle.read()
        self.tree = ast.parse(self.source)

    def test_the_reader_writes_nothing(self):
        called = {n.func.attr for n in ast.walk(self.tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        for verb in ("write", "rename", "unlink", "remove", "ftruncate",
                     "truncate", "mkdir", "fsync"):
            self.assertNotIn(verb, called, verb)
        opens = [n for n in ast.walk(self.tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "open"]
        for call in opens:
            flags = ast.dump(call)
            self.assertIn("O_RDONLY", flags)

    def test_the_reader_acquires_nothing(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            if isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
        for absent in ("socket", "subprocess", "threading", "rf_bridge",
                       "rf_iq_ring", "scythe_graphops_adapter",
                       "scythe_promotion_ledger_writer",
                       "scythe_promotion_ledger_ownership"):
            self.assertNotIn(absent, imported, absent)

    def test_the_reader_produces_no_promotion_decision(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            for statement in ast.walk(node):
                if isinstance(statement, ast.Return):
                    dumped = ast.dump(statement)
                    self.assertNotIn("PromotionDecision", dumped, node.name)
                    self.assertNotIn("PromotionRequest", dumped, node.name)

    def test_the_artefact_kinds_are_disjoint_from_the_ledgers(self):
        from scythe_derived_evidence import ARTEFACT_KINDS
        from scythe_promotion_ledger_store import KNOWN_KINDS

        self.assertEqual(set(ARTEFACT_KINDS) & set(KNOWN_KINDS), set())

    def test_an_artefact_is_not_readable_as_a_ledger(self):
        from scythe_promotion_ledger_store import LEDGER_UNREADABLE, read_ledger

        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "artefact.jsonl")
        with open(path, "wb") as handle:
            handle.write(encode_for_test(dict(PROVENANCE), [walk_record(0)]))
        self.assertEqual(read_ledger(path).readability, LEDGER_UNREADABLE)

    def test_there_is_no_constructed_evidence_fallback(self):
        self.assertNotIn("constructed_walk_verdicts", self.source)
        self.assertNotIn("EVIDENCE_CONSTRUCTED", self.source)


if __name__ == "__main__":
    unittest.main()


class MeasurementDeclarationTests(ReaderTestCase):
    """Slice 10d, §13l M.4: what the instrument did is declared, never inferred.

    The whole class exists because every fact the amendment cares about is one a
    reader could plausibly have guessed instead. An RTL2838 in the manifest, a
    sample rate that looks like a real sample rate and a gain that looks like a
    real gain are exactly the evidence a well-meaning classifier would use, and
    the artefact still says no measurement was taken.
    """

    def test_a_configured_idle_artefact_is_read_with_its_status_preserved(self):
        """The shape M.4 exists for, and the one that must not be refused.

        Populated settings are legitimate: the run declares the configuration it
        did not exercise. Refusing this artefact would push the producer toward
        omitting the configuration, which is worse -- the reader would then have
        no idea what was attached.
        """
        artefact = read_artefact(
            self._write([walk_record(i) for i in range(3)], provenance=CONFIGURED))
        self.assertEqual(artefact.measurement_status, RF_MEASUREMENT_NOT_PERFORMED)
        self.assertEqual(artefact.instrument_state, INSTRUMENT_CONFIGURED_IDLE)
        self.assertEqual(artefact.assessment, DERIVED_SCHEMA_CONFORMANT)
        self.assertEqual(artefact.provenance["sample_rate_hz"], 2_400_000)
        self.assertEqual(artefact.provenance["gain_db"], 40.2)

    def test_a_named_sdr_grants_no_measurement_authority(self):
        """Naming an instrument is not using one."""
        for device in ("rtl2838-0bda:2838", "RTL2838 DVB-T", "rtl_tcp:1234"):
            with self.subTest(device=device):
                artefact = read_artefact(self._write(
                    [walk_record(0)], provenance=dict(CONFIGURED, device_id=device)))
                self.assertEqual(artefact.measurement_status,
                                 RF_MEASUREMENT_NOT_PERFORMED)

    def test_plausible_settings_grant_no_measurement_authority(self):
        """Values a real capture would use, on a run that captured nothing."""
        for rate, gain in ((2_400_000, 40.2), (1_024_000, 0.0), (3_200_000, 49.6)):
            with self.subTest(rate=rate):
                artefact = read_artefact(self._write(
                    [walk_record(0)],
                    provenance=dict(CONFIGURED, sample_rate_hz=rate, gain_db=gain)))
                self.assertEqual(artefact.measurement_status,
                                 RF_MEASUREMENT_NOT_PERFORMED)
                self.assertEqual(artefact.instrument_state,
                                 INSTRUMENT_CONFIGURED_IDLE)

    def test_the_status_is_read_from_one_field_and_nothing_else(self):
        """`declared_measurement_status` given only the declaration still
        answers, which is the direct statement that nothing else feeds it."""
        self.assertEqual(
            declared_measurement_status({"measurement_status":
                                         RF_MEASUREMENT_NOT_PERFORMED}),
            RF_MEASUREMENT_NOT_PERFORMED)
        self.assertEqual(
            declared_instrument_state({"instrument_state":
                                       INSTRUMENT_CONFIGURED_IDLE}),
            INSTRUMENT_CONFIGURED_IDLE)

    # -- the fields are required ------------------------------------------

    def test_a_missing_declaration_is_refused(self):
        for field in ("measurement_status", "instrument_state"):
            with self.subTest(field=field):
                incomplete = {k: v for k, v in PROVENANCE.items() if k != field}
                refused = self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                                        provenance=incomplete)
                self.assertIn(field, refused.detail)

    def test_a_missing_declaration_receives_no_default(self):
        """The refusal is the whole behaviour: nothing is filled in."""
        incomplete = {k: v for k, v in PROVENANCE.items()
                      if k != "measurement_status"}
        path = self._write([walk_record(0)], provenance=incomplete)
        with self.assertRaises(ArtefactRefused):
            read_artefact(path)

    # -- the values are closed --------------------------------------------

    def test_an_unknown_measurement_status_is_refused(self):
        for value in ("RF_MEASUREMENT_PERFORMED", "MEASURED", "", None, True, 1):
            with self.subTest(value=value):
                self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                              provenance=dict(PROVENANCE,
                                              measurement_status=value))

    def test_an_unknown_instrument_state_is_refused(self):
        for value in ("INSTRUMENT_STREAMING", "IDLE", "", None, 0):
            with self.subTest(value=value):
                self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                              provenance=dict(PROVENANCE, instrument_state=value))

    def test_the_declared_sets_have_exactly_the_contracted_members(self):
        """A second member would be a name for a capability that does not
        exist, readable by a reader that has never seen one."""
        self.assertEqual(reader_module.MEASUREMENT_STATUSES,
                         (RF_MEASUREMENT_NOT_PERFORMED,))
        self.assertEqual(reader_module.INSTRUMENT_STATES,
                         (INSTRUMENT_CONFIGURED_IDLE,))

    # -- every populated setting carries its own label ---------------------

    def test_a_populated_setting_without_its_label_is_refused(self):
        for setting in INSTRUMENT_SETTINGS:
            with self.subTest(setting=setting):
                unlabelled = {k: v for k, v in CONFIGURED.items()
                              if k != setting + EXERCISE_SUFFIX}
                refused = self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                                        provenance=unlabelled)
                self.assertIn(setting + EXERCISE_SUFFIX, refused.detail)

    def test_a_label_without_its_setting_is_refused(self):
        for setting in INSTRUMENT_SETTINGS:
            with self.subTest(setting=setting):
                orphan = {k: v for k, v in CONFIGURED.items() if k != setting}
                self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                              provenance=orphan)

    def test_a_setting_labelled_anything_else_is_refused(self):
        for label in ("EXERCISED", "CONFIGURED", "", None, True):
            with self.subTest(label=label):
                self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                              provenance=dict(CONFIGURED,
                                              sample_rate_hz_exercise=label))

    def test_a_setting_is_bounded_like_every_other_scalar(self):
        self._refused(NON_SCALAR_COORDINATE, [walk_record(0)],
                      provenance=dict(CONFIGURED, gain_db=[40.2]))

    def test_an_undeclared_setting_is_refused(self):
        self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                      provenance=dict(CONFIGURED, if_frequency_hz=0))

    def test_settings_are_optional_and_absent_is_not_unlabelled(self):
        """PROVENANCE declares no settings at all, and is read."""
        artefact = read_artefact(self._write([walk_record(0)]))
        self.assertEqual(artefact.measurement_status, RF_MEASUREMENT_NOT_PERFORMED)
        for setting in INSTRUMENT_SETTINGS:
            self.assertNotIn(setting, artefact.provenance)

    def test_a_setting_is_covered_by_the_artefact_identity(self):
        """Otherwise a configuration could be edited without disturbing the
        identity that is supposed to name this artefact's contents."""
        one = artifact_identity(CONFIGURED, "sha256:x")
        other = artifact_identity(dict(CONFIGURED, gain_db=0.0), "sha256:x")
        self.assertNotEqual(one, other)


class SchemaVersionTests(ReaderTestCase):
    """Slice 10d: the closed set gained required fields, so the name changed."""

    def test_the_artefact_schema_is_v2(self):
        self.assertEqual(ARTEFACT_SCHEMA, "scythe.derived-evidence-artefact.v2")

    def test_a_v1_artefact_is_refused_as_foreign(self):
        """No migration and no defaults. A v1 artefact would be one whose
        provenance says nothing about measurement, and reading it would mean
        deciding on its author's behalf what it did not say."""
        old = {k: v for k, v in PROVENANCE.items()
               if k not in ("measurement_status", "instrument_state")}
        old["schema"] = "scythe.derived-evidence-artefact.v1"
        refused = self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                                provenance=old)
        self.assertIn("foreign artefact schema", refused.detail)

    def test_a_v1_artefact_is_refused_even_carrying_the_new_fields(self):
        """The version is not a hint to be overridden by what is present."""
        self._refused(ARTEFACT_UNREADABLE, [walk_record(0)],
                      provenance=dict(PROVENANCE,
                                      schema="scythe.derived-evidence-artefact.v1"))


class DeclarationNameCheckTests(unittest.TestCase):
    """SCYTHE_VERDICT_VOCABULARIES.md §3, applied to the three new names.

    Mechanical and against the discovered universe, not against the names the
    author happened to remember.
    """

    def test_no_new_declaration_collides_unjudged(self):
        from test_scythe_verdict_vocabularies import (
            cross_set_collisions, discovered_tokens, judged,
        )
        tokens = discovered_tokens()
        for candidate in (RF_MEASUREMENT_NOT_PERFORMED, INSTRUMENT_CONFIGURED_IDLE,
                          CONFIGURED_NOT_EXERCISED):
            with self.subTest(candidate=candidate):
                self.assertIn(candidate, tokens)
                unjudged = [hit for hit in cross_set_collisions(candidate, tokens)
                            if not judged(candidate, hit)]
                self.assertEqual(unjudged, [])

    def test_the_rejected_candidate_would_have_collided(self):
        """M.9 rejected DECLARED_NOT_EXERCISED rather than judging it. This is
        the mechanical statement of why, so the reason survives the prose."""
        from test_scythe_verdict_vocabularies import collisions, discovered_tokens
        hits = collisions("DECLARED_NOT_EXERCISED", set(discovered_tokens()))
        self.assertIn("LEDGER_GENERATION_UNDECLARED", hits)
        self.assertEqual(collisions(CONFIGURED_NOT_EXERCISED,
                                    set(discovered_tokens())),
                         [CONFIGURED_NOT_EXERCISED])
