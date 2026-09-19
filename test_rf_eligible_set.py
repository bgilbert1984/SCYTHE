"""§5.27: a seed cannot regenerate a measurement, so the rows are persisted.

The format and the reconstruction, exercised without a filesystem. The acts
that write and read them live in `test_rf_corpus_namespace`.
"""

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import rf_corpus_reconstruction as R
import rf_eligible_trials_artefact as A
from rf_capture_format import IQC_MAGIC
from rf_corpus_manifest import (
    IQM_FORMAT_VERSION, IQM_MAGIC, IQM_SCHEMA, MANIFEST_VERSION_REFUSED,
    ManifestRefused, frame_manifest, manifest_body, parse_manifest,
    required_manifest_fields,
)
from rf_corpus_reconstruction import (
    RECONSTRUCTION_DIGEST_DISAGREES, RECONSTRUCTION_FIELD_NO_AUTHORITY,
    RECONSTRUCTION_MAPPING_DISAGREES, RECONSTRUCTION_SELECTION_DISAGREES,
    ReconstructionRefused,
)
from rf_eligible_trials_artefact import (
    ELIGIBLE_COUNT_DISAGREES, ELIGIBLE_DIGEST_DISAGREES,
    ELIGIBLE_FRAMING_REFUSED, ELIGIBLE_KEY_REPEATED, ELIGIBLE_NOT_CANONICAL,
    ELIGIBLE_ORDER_NOT_CANONICAL, ELIGIBLE_RECORD_TOO_LARGE,
    ELIGIBLE_REFUSALS, ELIGIBLE_TRAILING_BYTES, EligibleSetRefused,
)
from rf_promotion_envelope import CapturePlanDeclaration, InstrumentChainEnvelope
from test_rf_promotion_envelope import _envelope, _plan


def _rows(plan):
    return sorted((t.to_dict() for t in plan.spur_allocation.eligible_trials),
                  key=A.sort_key)


class FindingTests(unittest.TestCase):
    """The measurement that made a sidecar necessary, kept as a test."""

    def setUp(self):
        self.plan = _plan(_envelope())

    def test_the_compact_plan_omits_the_eligible_rows(self):
        compact = self.plan.to_dict()["spur_allocation"]
        self.assertNotIn("eligible_trials", compact)
        self.assertIn("eligible_trials_digest", compact)
        self.assertIn("distinct_trial_units", compact)

    def test_the_selected_tuple_regenerates_and_the_eligible_one_cannot(self):
        """Selection is derived from (eligible, seed, revision). The eligible
        set has no production generator: each row carries an observed signed
        baseband offset, and no seed regenerates an observation."""
        from rf_promotion_envelope import select_spur_trials
        sa = self.plan.spur_allocation
        self.assertEqual(
            tuple(sa.selected_trials),
            select_spur_trials(eligible=sa.eligible_trials,
                               seed=sa.selection_seed,
                               required=len(sa.selected_trials),
                               selection_revision=sa.selection_revision))
        import rf_promotion_envelope as E
        self.assertFalse([n for n in dir(E) if n.startswith("generate_eligible")])

    def test_the_eligible_rows_would_not_fit_in_the_manifest(self):
        """Measured, and the reason the bound is not raised: this is one
        fixture, not the largest catalogue the protocol permits."""
        from rf_corpus_manifest import IQM_MAX_BODY_BYTES
        rows = _rows(self.plan)
        encoded = sum(len(A.canonical_record_bytes(r)) for r in rows)
        self.assertGreater(encoded, IQM_MAX_BODY_BYTES)


class ArtefactFramingTests(unittest.TestCase):

    def setUp(self):
        self.rows = _rows(_plan(_envelope()))
        self.blob = b"".join(A.frame_records(self.rows))

    def _read(self, blob, count=None):
        return list(A.read_records(
            io.BytesIO(blob).read,
            expected_count=len(self.rows) if count is None else count))

    def test_a_round_trip_reproduces_the_rows_and_the_frozen_digest(self):
        records = self._read(self.blob)
        self.assertEqual(A.parse_rows(records), self.rows)
        stored = _plan(_envelope()).to_dict()["spur_allocation"]
        self.assertEqual(A.digest_over_records(records),
                         stored["eligible_trials_digest"])

    def test_the_magic_has_the_shared_shape(self):
        self.assertEqual(len(A.IQE_MAGIC), 8)
        self.assertTrue(A.IQE_MAGIC[0] & 0x80)
        self.assertTrue(A.IQE_MAGIC.endswith(b"\r\n"))

    def test_the_magic_is_distinct_from_the_manifest_magic(self):
        self.assertNotEqual(A.IQE_MAGIC, IQM_MAGIC)

    def test_the_magic_is_distinct_from_the_window_magic(self):
        self.assertNotEqual(A.IQE_MAGIC, IQC_MAGIC)

    def _collision_detonates(self, other_value):
        """Import the real chain with `IQE_MAGIC` forced to collide, in a
        SUBPROCESS, by shadowing the module on `sys.path`.

        A collision raises at import, so an in-process assertion cannot witness
        it: every test in this file and in the namespace file disappears
        together, which is a detonation, not a discrimination. The two
        invariants also live in different modules -- IQE/IQC in the artefact
        module, IQM/IQE downstream in `rf_corpus_manifest` -- so the probe must
        import the chain rather than the one file, or it is blind to half of
        what it claims to cover.
        """
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here,
                               "rf_eligible_trials_artefact.py")) as handle:
            source = handle.read()
        needle = r'IQE_MAGIC = b"\x89SCYET\r\n"'
        self.assertIn(needle, source,
                      "the magic definition moved; this probe would be blind")
        shadow = tempfile.mkdtemp(prefix="scythe-magic-")
        self.addCleanup(shutil.rmtree, shadow, ignore_errors=True)
        with open(os.path.join(shadow, "rf_eligible_trials_artefact.py"),
                  "w") as handle:
            handle.write(source.replace(needle,
                                        "IQE_MAGIC = " + repr(other_value)))
        probe = ("import sys\n"
                 "sys.path.insert(0, %r)\n"
                 "sys.path.append(%r)\n"
                 "try:\n"
                 "    import rf_corpus_manifest\n"
                 "except Exception as exc:\n"
                 "    print(type(exc).__name__ + ': ' + str(exc))\n"
                 "else:\n"
                 "    print('NO REFUSAL')\n") % (shadow, here)
        result = subprocess.run([sys.executable, "-c", probe],
                                capture_output=True, text=True, timeout=120,
                                cwd=shadow)
        return result.stdout.strip() + result.stderr.strip()

    def test_a_manifest_magic_collision_is_refused_at_import_by_name(self):
        message = self._collision_detonates(IQM_MAGIC)
        self.assertNotIn("NO REFUSAL", message)
        self.assertIn("IQM_MAGIC", message,
                      "the manifest collision must be named specifically, or "
                      "this witness cannot be told from the window collision")

    def test_a_window_magic_collision_is_refused_at_import_by_name(self):
        message = self._collision_detonates(IQC_MAGIC)
        self.assertNotIn("NO REFUSAL", message)
        self.assertIn("IQC_MAGIC", message,
                      "the window collision must be named specifically, or "
                      "this witness cannot be told from the manifest collision")

    def test_a_manifest_is_not_readable_as_a_sidecar(self):
        framed = IQM_MAGIC + struct.pack("<H", 2) + struct.pack("<Q", 0)
        with self.assertRaises(EligibleSetRefused) as caught:
            self._read(framed, count=0)
        self.assertEqual(caught.exception.code, ELIGIBLE_FRAMING_REFUSED)

    def test_a_window_is_not_readable_as_a_sidecar(self):
        framed = IQC_MAGIC + struct.pack("<H", 1) + struct.pack("<Q", 0)
        with self.assertRaises(EligibleSetRefused) as caught:
            self._read(framed, count=0)
        self.assertIn(".iqc", caught.exception.detail)

    def test_a_count_that_disagrees_with_the_plan_refuses_before_any_row(self):
        """The PLAN's count against the framed one."""
        with self.assertRaises(EligibleSetRefused) as caught:
            self._read(self.blob, count=len(self.rows) - 1)
        self.assertEqual(caught.exception.code, ELIGIBLE_COUNT_DISAGREES)

    def test_a_framed_count_that_overstates_the_records_refuses(self):
        """The FRAMED count against the bytes actually present, which is a
        different check from the one above: that test compares the header to
        the plan, this one compares the header to the file. Sharing a witness
        let one mutation stand in for the other."""
        body = self.blob[A.IQE_FRAMING_PREFIX_BYTES:]
        lied = (A.IQE_MAGIC + struct.pack("<H", A.IQE_FORMAT_VERSION)
                + struct.pack("<Q", len(self.rows) + 1) + body)
        with self.assertRaises(EligibleSetRefused) as caught:
            self._read(lied, count=len(self.rows) + 1)
        self.assertEqual(caught.exception.code, ELIGIBLE_FRAMING_REFUSED)

    def test_trailing_bytes_are_refused_not_ignored(self):
        with self.assertRaises(EligibleSetRefused) as caught:
            self._read(self.blob + b"\x00")
        self.assertEqual(caught.exception.code, ELIGIBLE_TRAILING_BYTES)

    def test_a_truncated_artefact_refuses(self):
        with self.assertRaises(EligibleSetRefused) as caught:
            self._read(self.blob[:-1])
        self.assertEqual(caught.exception.code, ELIGIBLE_FRAMING_REFUSED)

    def test_a_record_length_over_the_bound_refuses_before_reading_it(self):
        head = A.IQE_FRAMING_PREFIX_BYTES
        lying = (self.blob[:head]
                 + struct.pack("<I", A.IQE_MAX_RECORD_BYTES + 1)
                 + self.blob[head + 4:])
        with self.assertRaises(EligibleSetRefused) as caught:
            self._read(lying)
        self.assertEqual(caught.exception.code, ELIGIBLE_RECORD_TOO_LARGE)

    def test_a_respaced_record_is_refused(self):
        loose = json.dumps(self.rows[0], sort_keys=True, indent=1).encode()
        rebuilt = (A.framing_prefix(1) + struct.pack("<I", len(loose)) + loose)
        with self.assertRaises(EligibleSetRefused) as caught:
            A.parse_rows(self._read(rebuilt, count=1))
        self.assertEqual(caught.exception.code, ELIGIBLE_NOT_CANONICAL)

    def test_rows_out_of_canonical_order_are_refused(self):
        swapped = list(self.rows)
        swapped[0], swapped[1] = swapped[1], swapped[0]
        blob = b"".join(A.frame_records(swapped))
        with self.assertRaises(EligibleSetRefused) as caught:
            A.parse_rows(self._read(blob))
        self.assertEqual(caught.exception.code, ELIGIBLE_ORDER_NOT_CANONICAL)

    def test_a_repeated_key_is_refused(self):
        duplicated = [self.rows[0], self.rows[0]] + self.rows[1:]
        blob = b"".join(A.frame_records(duplicated))
        with self.assertRaises(EligibleSetRefused) as caught:
            A.parse_rows(self._read(blob, count=len(duplicated)))
        self.assertEqual(caught.exception.code, ELIGIBLE_KEY_REPEATED)

    def test_a_bare_nan_is_refused_at_write_and_at_parse(self):
        with self.assertRaises(EligibleSetRefused):
            A.canonical_record_bytes(dict(self.rows[0],
                                          signed_baseband_hz=float("nan")))
        raw = b'{"signed_baseband_hz":NaN}'
        blob = A.framing_prefix(1) + struct.pack("<I", len(raw)) + raw
        with self.assertRaises(EligibleSetRefused) as caught:
            A.parse_rows(self._read(blob, count=1))
        self.assertEqual(caught.exception.code, ELIGIBLE_NOT_CANONICAL)

    def test_the_canonical_rule_is_ensure_ascii_true_and_that_is_deliberate(self):
        """The manifest uses False and this uses True. `_canonical_bytes` takes
        Python's default, so the ALREADY-FROZEN digest is over escaped bytes; a
        sidecar written the manifest's way would not reproduce it."""
        from rf_promotion_envelope import _canonical_bytes
        joined = b"[" + b",".join(
            A.canonical_record_bytes(r) for r in self.rows) + b"]"
        self.assertEqual(joined, _canonical_bytes(self.rows))
        row = dict(self.rows[0], spur_id="spur-é")
        self.assertIn(b"\\u00e9", A.canonical_record_bytes(row))

    def test_no_second_digest_is_introduced(self):
        self.assertFalse(A.format_declaration()["second_digest_introduced"])


class ManifestVersionTests(unittest.TestCase):

    def test_the_manifest_schema_is_v2(self):
        self.assertEqual(IQM_SCHEMA, "scythe.iq-corpus-manifest.v2")

    def test_the_manifest_format_version_is_2(self):
        self.assertEqual(IQM_FORMAT_VERSION, 2)

    def test_the_required_set_includes_iqe_schema(self):
        self.assertIn("iqe_schema", required_manifest_fields())

    def test_the_required_set_includes_iqe_format_version(self):
        self.assertIn("iqe_format_version", required_manifest_fields())

    def _unconditional_body(self):
        """A body built through a real frozen lock, shared by the two tests
        below so that each asserts exactly one field."""
        from rf_capture_admission import CapturedCorpusRetention
        from rf_validation_manifest import freeze_promotion_corpus
        env = _envelope()
        lock = freeze_promotion_corpus(
            corpus_id="c", method_revision="m", decision_threshold=6.0,
            preprocessing_revision="p", envelope=env, capture_plan=_plan(env),
            opened_at=1000.0)
        return manifest_body(lock=lock,
                             retention=CapturedCorpusRetention(2000.0))

    def test_the_iqe_schema_is_unconditional(self):
        """Not conditional on the plan having a spur allocation: a conditional
        field makes the required set a handwritten list with a branch in it."""
        body = self._unconditional_body()
        self.assertEqual(body["iqe_schema"], A.IQE_SCHEMA)

    def test_the_iqe_format_version_is_unconditional(self):
        body = self._unconditional_body()
        self.assertEqual(body["iqe_format_version"], A.IQE_FORMAT_VERSION)

    def test_a_v1_manifest_refuses_as_unsupported_and_is_not_upgraded(self):
        from rf_capture_admission import CapturedCorpusRetention
        from rf_validation_manifest import freeze_promotion_corpus
        env = _envelope()
        lock = freeze_promotion_corpus(
            corpus_id="c", method_revision="m", decision_threshold=6.0,
            preprocessing_revision="p", envelope=env, capture_plan=_plan(env),
            opened_at=1000.0)
        framed = frame_manifest(manifest_body(
            lock=lock, retention=CapturedCorpusRetention(2000.0)))
        v1 = framed[:8] + struct.pack("<H", 1) + framed[10:]
        with self.assertRaises(ManifestRefused) as caught:
            parse_manifest(v1)
        self.assertEqual(caught.exception.code, MANIFEST_VERSION_REFUSED)
        self.assertIn("cannot reconstruct", caught.exception.detail)


class ReconstructionTests(unittest.TestCase):
    """Exact nominal objects, or a refusal. Never a partial one."""

    def setUp(self):
        self.env = _envelope()
        self.plan = _plan(self.env)
        self.rows = _rows(self.plan)

    def _plan_again(self, mapping=None, rows=None, digest=None):
        return R.capture_plan(
            self.plan.to_dict() if mapping is None else mapping,
            eligible_rows=self.rows if rows is None else rows,
            frozen_digest=self.plan.digest() if digest is None else digest)

    def test_the_plan_rebuilds_to_the_exact_nominal_object(self):
        rebuilt = self._plan_again()
        self.assertEqual(type(rebuilt), CapturePlanDeclaration)
        self.assertEqual(rebuilt.to_dict(), self.plan.to_dict())
        self.assertEqual(rebuilt.digest(), self.plan.digest())
        self.assertEqual(len(rebuilt.spur_allocation.eligible_trials),
                         len(self.plan.spur_allocation.eligible_trials))

    def test_the_envelope_rebuilds_to_the_exact_nominal_object(self):
        rebuilt = R.envelope(self.env.to_dict(),
                             frozen_digest=self.env.digest())
        self.assertEqual(type(rebuilt), InstrumentChainEnvelope)
        self.assertEqual(rebuilt.to_dict(), self.env.to_dict())
        self.assertEqual(rebuilt.digest(), self.env.digest())

    def test_the_selection_is_regenerated_and_not_read(self):
        rebuilt = self._plan_again()
        self.assertEqual(tuple(rebuilt.spur_allocation.selected_trials),
                         tuple(self.plan.spur_allocation.selected_trials))

    def test_one_changed_eligible_row_refuses(self):
        rows = [dict(r) for r in self.rows]
        rows[0]["signed_baseband_hz"] = rows[0]["signed_baseband_hz"] + 1.0
        rows.sort(key=A.sort_key)
        with self.assertRaises(ReconstructionRefused) as caught:
            self._plan_again(rows=rows)
        self.assertEqual(caught.exception.code, RECONSTRUCTION_DIGEST_DISAGREES)

    def test_a_row_omitted_refuses_even_if_the_count_is_repaired(self):
        short = self.rows[:-1]
        mapping = json.loads(json.dumps(self.plan.to_dict()))
        mapping["spur_allocation"]["distinct_trial_units"] = len(short)
        with self.assertRaises(ReconstructionRefused) as caught:
            self._plan_again(mapping=mapping, rows=short)
        self.assertEqual(caught.exception.code, RECONSTRUCTION_DIGEST_DISAGREES)

    def test_every_valid_corpus_has_a_spur_allocation_today(self):
        """Recorded rather than assumed. `_trial_plans` declares every stratum
        and a lock refuses a plan missing one, so RECEIVER_SPURS is always
        planned and a catalogue is always required. Two of §5.27's four
        presence states are therefore unreachable THROUGH A VALID LOCK, and the
        code that handles them is reached directly instead -- see
        `test_rf_corpus_namespace`. If a stratum ever becomes optional, this
        test is where that shows up."""
        from rf_promotion_envelope import EnvelopeRefused
        from test_rf_promotion_envelope import _plan as build
        with self.assertRaises(EnvelopeRefused) as caught:
            build(self.env, spur_allocation=None)
        self.assertEqual(caught.exception.code, "PLAN_SPUR_CATALOGUE_ABSENT")

    def test_a_field_no_authority_declares_refuses(self):
        mapping = json.loads(json.dumps(self.plan.to_dict()))
        mapping["bands"][0]["operator_note"] = "looked fine"
        with self.assertRaises(ReconstructionRefused) as caught:
            self._plan_again(mapping=mapping)
        self.assertEqual(caught.exception.code,
                         RECONSTRUCTION_FIELD_NO_AUTHORITY)

    def test_a_mapping_that_does_not_round_trip_refuses(self):
        mapping = json.loads(json.dumps(self.plan.to_dict()))
        mapping["total_trials"] = mapping["total_trials"] + 1
        with self.assertRaises(ReconstructionRefused) as caught:
            self._plan_again(mapping=mapping)
        self.assertEqual(caught.exception.code,
                         RECONSTRUCTION_MAPPING_DISAGREES)

    def test_a_caller_supplied_selected_tuple_is_refused(self):
        """The selection is derived. A stored one would be a selection made
        after seeing the eligible population, which is the post-hoc choice the
        frozen sample exists to prevent."""
        mapping = json.loads(json.dumps(self.plan.to_dict()))
        mapping["spur_allocation"]["selected_trials"] = [
            list(k) for k in self.plan.spur_allocation.selected_trials[:3]]
        with self.assertRaises(ReconstructionRefused) as caught:
            self._plan_again(mapping=mapping)
        self.assertEqual(caught.exception.code,
                         RECONSTRUCTION_FIELD_NO_AUTHORITY)

    def test_a_plan_bound_to_another_digest_refuses(self):
        with self.assertRaises(ReconstructionRefused) as caught:
            self._plan_again(digest="blake2s:" + "0" * 32)
        self.assertEqual(caught.exception.code, RECONSTRUCTION_DIGEST_DISAGREES)

    def test_a_regenerated_selection_that_disagrees_refuses(self):
        """The stored selected_trials_digest is what makes regeneration
        checked rather than merely performed."""
        mapping = json.loads(json.dumps(self.plan.to_dict()))
        mapping["spur_allocation"]["selected_trials_digest"] = \
            "blake2s:" + "0" * 32
        with self.assertRaises(ReconstructionRefused) as caught:
            self._plan_again(mapping=mapping)
        self.assertEqual(caught.exception.code,
                         RECONSTRUCTION_SELECTION_DISAGREES)

    def test_one_changed_row_reports_the_stored_cause_not_the_derived_one(self):
        """A wrong eligible set also produces a wrong selection. The eligible
        digest is checked first so the refusal names the stored cause."""
        rows = [dict(r) for r in self.rows]
        rows[0]["signed_baseband_hz"] = rows[0]["signed_baseband_hz"] + 1.0
        rows.sort(key=A.sort_key)
        with self.assertRaises(ReconstructionRefused) as caught:
            self._plan_again(rows=rows)
        self.assertEqual(caught.exception.code, RECONSTRUCTION_DIGEST_DISAGREES)


class DerivedEnvelopeKeyTests(unittest.TestCase):
    """The four derived keys are recomputed outputs, not allow-listed inputs.

    Listing them in `_DERIVED` stops the strict field check refusing a valid
    envelope. It would be a defect if listing them ALSO let them reach the
    constructor -- an allow-list that silently becomes an input channel is the
    caller-supplied-set defect in miniature.
    """

    DERIVED = ("schema", "sensor_id", "receiver_identity_authority",
               "member_count")

    def setUp(self):
        self.env = _envelope()
        self.mapping = json.loads(json.dumps(self.env.to_dict()))

    def _mutated(self, key):
        mapping = json.loads(json.dumps(self.mapping))
        value = mapping[key]
        mapping[key] = value + 1 if isinstance(value, int) else f"{value}-moved"
        return mapping

    def test_each_derived_key_is_recomputed_and_never_reaches_the_constructor(self):
        for key in self.DERIVED:
            with self.subTest(key=key):
                rebuilt = R.instrument_chain_envelope(self._mutated(key))
                # Built from the members alone, so the mutation left no trace.
                self.assertEqual(rebuilt.to_dict(), self.mapping)

    def test_each_derived_key_refuses_through_the_canonical_comparison(self):
        for key in self.DERIVED:
            with self.subTest(key=key):
                with self.assertRaises(ReconstructionRefused) as caught:
                    R.envelope(self._mutated(key),
                               frozen_digest=self.env.digest())
                self.assertEqual(caught.exception.code,
                                 RECONSTRUCTION_MAPPING_DISAGREES)

    def test_the_chain_hash_is_derived_for_each_member_too(self):
        mapping = json.loads(json.dumps(self.mapping))
        mapping["members"][0]["chain_hash"] = "blake2s:" + "0" * 32
        rebuilt = R.instrument_chain_envelope(mapping)
        self.assertEqual(rebuilt.to_dict()["members"][0]["chain_hash"],
                         self.mapping["members"][0]["chain_hash"])
        with self.assertRaises(ReconstructionRefused) as caught:
            R.envelope(mapping, frozen_digest=self.env.digest())
        self.assertEqual(caught.exception.code,
                         RECONSTRUCTION_MAPPING_DISAGREES)


class VocabularyTests(unittest.TestCase):

    def test_the_refusal_sets_are_disjoint_and_named_disjointly(self):
        self.assertEqual(set(ELIGIBLE_REFUSALS) & set(R.RECONSTRUCTION_REFUSALS),
                         set())
        for code in ELIGIBLE_REFUSALS:
            self.assertTrue(code.startswith("ELIGIBLE_"), code)
        for code in R.RECONSTRUCTION_REFUSALS:
            self.assertTrue(code.startswith("RECONSTRUCTION_"), code)


if __name__ == "__main__":
    unittest.main()
