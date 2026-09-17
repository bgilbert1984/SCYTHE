"""§5.25: attestation is not admission, and a precondition is not a post-open failure.

§5.24 established what a window **is**. These are the tests for the different
question of whether it **belongs** -- and for the boundary that separates a
refusal leaving nothing behind from a failure §5.20 already governs.

No test here creates a directory, a file, a corpus, a lock on disk or a device.
The publication target is an anonymous pipe or `/dev/null`: bytes cross a
descriptor, which is what a writer does, and nothing is persisted.
"""

import ast
import json
import os
import pathlib
import struct
import threading
import unittest
from dataclasses import dataclass, replace
from unittest import mock

import numpy as np

import rf_capture_admission as admission
import rf_iq_ring as ring_module
from rf_capture_admission import (
    ADMISSION_ATTESTATION_TYPE_WRONG, ADMISSION_ATTESTATION_UNCONSTRUCTIBLE,
    ADMISSION_BINDING_CLAIMED_TWICE, ADMISSION_BINDING_NOT_EMITTED,
    ADMISSION_CANONICAL_FORM_REFUSED, ADMISSION_CHAIN_OUTSIDE_ENVELOPE,
    ADMISSION_CREATOR_NOT_CALLABLE, ADMISSION_FIELD_NO_AUTHORITY,
    ADMISSION_GEOMETRY_REFUSED, ADMISSION_LOCK_DIGEST_MOVED,
    ADMISSION_LOCK_NOT_FROZEN, ADMISSION_REFUSALS,
    ADMISSION_RETENTION_BEYOND_MAXIMUM, ADMISSION_RETENTION_EXPIRED,
    ADMISSION_RETENTION_NOT_SUPPLIED, ADMISSION_SCOPE_ENDED,
    ADMISSION_SCOPE_TYPE_WRONG, ADMISSION_SEQUENCE_NOT_THIS_STRATUM,
    ADMISSION_STRATA_DEFINITION_MOVED, ADMISSION_STRATUM_CAP_REACHED,
    ADMISSION_STRATUM_OUTSIDE_GRANT, CAPTURED_STRATA, HEADER_AUTHORITIES,
    PAYLOAD_ACTION_RECONCILES, PUBLICATION_FAILURES,
    PUBLICATION_FRAMING_WRITE_INCOMPLETE, PUBLICATION_LENGTH_MISMATCH,
    PUBLICATION_TARGET_NOT_A_DESCRIPTOR, RETENTION_MAXIMUM_SECONDS,
    STRATUM_ATTESTATION, CaptureRefused, CapturedCorpusRetention,
    CapturedStratumSequence, CapturedWindowPublication, GainStepAttestation,
    PublicationFailed, RetuneAttestation, admission_status,
    check_header_completeness, derive_canonical_header, record_gain_step,
    record_receiver_spur, record_retune_transient, required_header_fields,
)
from rf_capture_format import (
    IQC_FORMAT_VERSION, IQC_HEADER_SCHEMA, IQC_MAGIC, IQC_MAX_HEADER_BYTES,
    FramingRefused, canonical_header_bytes, framing_prefix,
)
from rf_corpus_vocabulary import CAPTURED
from rf_iq_ring import (
    BYTES_PER_SAMPLE, STORAGE_DTYPE, WINDOW_INTERVAL_OVERLAP,
    AttestedIQWindowScope, BoundedIQRing,
)
from rf_promotion_geometry import (
    PROMOTION_PAYLOAD_BYTES, PROMOTION_SAMPLE_RATE_HZ, PROMOTION_WINDOW_OVERLAP,
    PROMOTION_WINDOW_SAMPLES,
)
from rf_validation_manifest import (
    MINIMUM_WINDOWS_PER_STRATUM, STRATA_DEFINITION_REVISION,
    freeze_promotion_corpus,
)
from test_rf_promotion_envelope import _envelope, _plan

OPENED_AT = 1_000_000.0
NOW = OPENED_AT + 60.0
DEADLINE = OPENED_AT + 30 * 24 * 3600.0

# One 4 MiB pattern, built once. Every ring appends the same samples: the tests
# are about admission, and re-deriving four mebibytes per test would be paying
# for arithmetic none of them are asking about.
_SAMPLES = np.arange(PROMOTION_WINDOW_SAMPLES, dtype=STORAGE_DTYPE)


def _lock(corpus_id="corpus-a", envelope=None):
    envelope = _envelope() if envelope is None else envelope
    return freeze_promotion_corpus(
        corpus_id=corpus_id, method_revision="squared-envelope-cyclic.v1",
        decision_threshold=6.0, preprocessing_revision="pre.v1",
        envelope=envelope, capture_plan=_plan(envelope), opened_at=OPENED_AT)


def _ring(chain, samples=None, count=PROMOTION_WINDOW_SAMPLES, **kwargs):
    ring = BoundedIQRing(capacity_samples=count,
                         sample_rate_hz=PROMOTION_SAMPLE_RATE_HZ,
                         signal_chain_hash=chain, **kwargs)
    ring.append(_SAMPLES[:count] if samples is None else samples)
    return ring


def _window(ring, count=None):
    acquired = ring.acquire_window(count)
    assert acquired, acquired.reason_code
    return acquired.window


def _gain_attestation(**kwargs):
    fields = dict(event_id="gain-0001", gain_db_before=20.0,
                  gain_db_after=40.0, invalidation_epoch=1)
    fields.update(kwargs)
    return GainStepAttestation(**fields)


def _retune_attestation(**kwargs):
    fields = dict(event_id="retune-0001", centre_hz_before=433_000_000.0,
                  centre_hz_after=433_200_000.0, invalidation_epoch=2)
    fields.update(kwargs)
    return RetuneAttestation(**fields)


class _Creator:
    """A §5.20 step 3 that counts how often it ran, and whether it ran at all.

    The whole ordering claim rests on this: a precondition refusal is not a
    promise about the order of some lines, it is a creator that never ran.
    """

    def __init__(self, fd=None, result=None):
        self.calls = 0
        self._fd = fd
        self._result = result

    def __call__(self):
        self.calls += 1
        return self._fd if self._result is None else self._result


class CaptureFixture(unittest.TestCase):
    """One admissible corpus, one admissible window, and the pieces to break."""

    def setUp(self):
        self.envelope = _envelope()
        self.lock = _lock(envelope=self.envelope)
        self.chain = sorted(self.envelope.admissible_chain_hashes())[0]
        self.foreign = "blake2s:" + "0" * 32
        self.ring = _ring(self.chain)
        self.retention = CapturedCorpusRetention(delete_not_after=DEADLINE)
        self.sequence = CapturedStratumSequence(
            corpus_id=self.lock.corpus_id, stratum="GAIN_STEPS")

    def devnull(self):
        fd = os.open(os.devnull, os.O_WRONLY)
        self.addCleanup(os.close, fd)
        return fd

    def kwargs(self, **overrides):
        fields = dict(attestation=_gain_attestation(), lock=self.lock,
                      retention=self.retention, sequence=self.sequence,
                      now=NOW)
        fields.update(overrides)
        return fields

    def publish(self, scope, creator=None, **overrides):
        creator = _Creator(self.devnull()) if creator is None else creator
        published = record_gain_step(scope=scope, create_target=creator,
                                     **self.kwargs(**overrides))
        return published, creator

    def refuse(self, scope, **overrides):
        """Attempt a capture that must not happen, and report the creator."""
        creator = _Creator(self.devnull())
        with self.assertRaises(CaptureRefused) as caught:
            record_gain_step(scope=scope, create_target=creator,
                             **self.kwargs(**overrides))
        return caught.exception, creator


def _through_a_pipe(call):
    """Run `call(create_target)` against a pipe; return its result and the image.

    The whole file image is what crossed the descriptor, which is the only way
    to see what a writer wrote without writing a file.
    """
    read_fd, write_fd = os.pipe()
    blocks = []

    def drain():
        while True:
            block = os.read(read_fd, 1 << 16)
            if not block:
                return
            blocks.append(block)

    # Daemon, and joined with a timeout. A non-daemon reader stranded on a
    # blocked descriptor is how one of these suites once hung for thirteen
    # minutes; the harness timeout is the backstop and this is the fix.
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        result = call(lambda: write_fd)
    finally:
        os.close(write_fd)
        reader.join(timeout=60)
        os.close(read_fd)
    return result, b"".join(blocks)


class AttestationIsNotAdmissionTests(CaptureFixture):
    """The ring has never heard of a PromotionCorpusLock, and cannot."""

    def test_an_attested_window_from_a_foreign_chain_is_not_corpus(self):
        """Entry 14's gate, at the one place it belongs.

        The window attests perfectly: the ring issued it, the digest recomputes,
        the bytes are bound. It is still not corpus, because the lock names the
        instruments the corpus is made of.

        **The refusal only.** Whether it happens before anything is created is
        the next test, and the two are separated deliberately: a single test
        asserting both gave §5.25's controls 4 and 5 identical failing sets,
        which made "the envelope recorded and not enforced" and "a precondition
        refusal leaving an artefact" indistinguishable -- two different
        defects, one witness.
        """
        ring = _ring(self.foreign)
        with ring.attest_window(_window(ring)) as scope:
            refusal, _creator = self.refuse(scope)
        self.assertEqual(refusal.code, ADMISSION_CHAIN_OUTSIDE_ENVELOPE)

    def test_a_foreign_chain_is_refused_before_the_target_is_created(self):
        """Control 5's own case: the ordering, with the code left to the test
        above. A gate that refuses correctly **after** the create has moved a
        precondition refusal into the post-open regime, and the artefact it
        leaves is the cost of the mistake."""
        ring = _ring(self.foreign)
        with ring.attest_window(_window(ring)) as scope:
            _refusal, creator = self.refuse(scope)
        self.assertEqual(creator.calls, 0)

    def test_the_same_window_on_a_declared_chain_is_admitted(self):
        """The discrimination, and not merely a refusal that refuses everything."""
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, creator = self.publish(scope)
        self.assertEqual(creator.calls, 1)
        self.assertEqual(published.payload_bytes_written, PROMOTION_PAYLOAD_BYTES)

    def test_admission_is_never_told_the_answer(self):
        """No `admitted: bool`, and no list of chains. §13k L.1.

        The signature is the check: a caller that could supply the verdict, or
        the set the verdict is drawn from, would be supplying the thing
        admission exists to establish.
        """
        import inspect
        for entrypoint in (record_gain_step, record_retune_transient):
            names = set(inspect.signature(entrypoint).parameters)
            self.assertEqual(
                names, {"scope", "attestation", "lock", "retention",
                        "sequence", "create_target", "now"},
                entrypoint.__name__)
            self.assertNotIn("source", names)
            self.assertNotIn("stratum", names)

    def test_the_envelope_is_read_out_of_the_lock_and_not_out_of_the_call(self):
        """A lock whose envelope admits nothing refuses every window, however
        the caller frames the request."""
        other = _envelope(sensor="rtl2838-unit-2")
        lock = _lock(corpus_id="corpus-a", envelope=other)
        with self.ring.attest_window(_window(self.ring)) as scope:
            refusal, _creator = self.refuse(scope, lock=lock)
        self.assertEqual(refusal.code, ADMISSION_CHAIN_OUTSIDE_ENVELOPE)


class PreconditionRegimeTests(CaptureFixture):
    """A refusal before any target is opened leaves nothing behind."""

    def test_every_admission_refusal_leaves_the_creator_uncalled(self):
        """Controls 5 and 6, as one enumeration over the declared vocabulary.

        Not "the checks I remembered to test": every code in
        `ADMISSION_REFUSALS` is produced here, and each one is produced with a
        recording creator that must not have run. An admission or authority
        check moved below the creation is caught by the creator that ran, and a
        code nobody can produce is caught by the coverage assertion below.
        """
        produced = {}
        for code, scenario in sorted(self._scenarios().items()):
            with self.subTest(code=code):
                refusal, creator = scenario()
                self.assertEqual(refusal.code, code)
                self.assertEqual(creator.calls, 0,
                                 f"{code} ran the creator before refusing")
                produced[code] = refusal.detail
        self.assertEqual(sorted(produced), sorted(ADMISSION_REFUSALS),
                         "a declared refusal nothing can produce looks exactly "
                         "like a bug")

    def test_an_ended_scope_is_refused(self):
        """The refusal. Split from the ordering for the same reason the
        foreign-chain pair is split."""
        scope = self.ring.attest_window(_window(self.ring))
        scope.__exit__(None, None, None)
        refusal, _creator = self.refuse(scope)
        self.assertEqual(refusal.code, ADMISSION_SCOPE_ENDED)

    def test_an_ended_scope_is_refused_before_the_target_is_created(self):
        """Control 6's own case: type, provenance and lifetime are established
        before §5.20's publication step 3, not after it."""
        scope = self.ring.attest_window(_window(self.ring))
        scope.__exit__(None, None, None)
        _refusal, creator = self.refuse(scope)
        self.assertEqual(creator.calls, 0)

    def test_a_refusal_carries_no_publication_code(self):
        """The two regimes are kept apart by type and by vocabulary."""
        self.assertEqual(set(ADMISSION_REFUSALS) & set(PUBLICATION_FAILURES),
                         set())
        self.assertFalse(issubclass(CaptureRefused, PublicationFailed))
        self.assertFalse(issubclass(PublicationFailed, CaptureRefused))

    def test_a_post_open_failure_is_not_caught_as_a_refusal(self):
        """A caller that caught one and handled the other would be deciding an
        orphan temporary does not exist."""
        with self.ring.attest_window(_window(self.ring)) as scope:
            creator = _Creator(result="not a descriptor")
            with self.assertRaises(PublicationFailed) as caught:
                record_gain_step(scope=scope, create_target=creator,
                                 **self.kwargs())
            self.assertEqual(caught.exception.code,
                             PUBLICATION_TARGET_NOT_A_DESCRIPTOR)
            self.assertNotIsInstance(caught.exception, CaptureRefused)
        self.assertEqual(creator.calls, 1)

    # -- one scenario per declared precondition refusal ---------------------

    def _scenarios(self):
        return {
            ADMISSION_SCOPE_TYPE_WRONG: self._scope_type_wrong,
            ADMISSION_SCOPE_ENDED: self._scope_ended,
            ADMISSION_LOCK_NOT_FROZEN: self._lock_not_frozen,
            ADMISSION_LOCK_DIGEST_MOVED: self._lock_digest_moved,
            ADMISSION_STRATA_DEFINITION_MOVED: self._strata_moved,
            ADMISSION_RETENTION_NOT_SUPPLIED: self._retention_absent,
            ADMISSION_RETENTION_BEYOND_MAXIMUM: self._retention_too_far,
            ADMISSION_RETENTION_EXPIRED: self._retention_expired,
            ADMISSION_STRATUM_OUTSIDE_GRANT: self._stratum_outside_grant,
            ADMISSION_ATTESTATION_UNCONSTRUCTIBLE: self._spur_stratum,
            ADMISSION_ATTESTATION_TYPE_WRONG: self._attestation_type_wrong,
            ADMISSION_GEOMETRY_REFUSED: self._geometry_refused,
            ADMISSION_CHAIN_OUTSIDE_ENVELOPE: self._chain_outside,
            ADMISSION_SEQUENCE_NOT_THIS_STRATUM: self._sequence_mismatched,
            ADMISSION_STRATUM_CAP_REACHED: self._cap_reached,
            ADMISSION_BINDING_CLAIMED_TWICE: self._claimed_twice,
            ADMISSION_BINDING_NOT_EMITTED: self._binding_missing,
            ADMISSION_FIELD_NO_AUTHORITY: self._field_undeclared,
            ADMISSION_CANONICAL_FORM_REFUSED: self._not_frameable,
            ADMISSION_CREATOR_NOT_CALLABLE: self._creator_not_callable,
            WINDOW_INTERVAL_OVERLAP: self._overlaps_previous,
        }

    def _live(self):
        scope = self.ring.attest_window(_window(self.ring))
        self.addCleanup(scope.__exit__, None, None, None)
        return scope

    def _scope_type_wrong(self):
        return self.refuse(object())

    def _scope_ended(self):
        scope = self.ring.attest_window(_window(self.ring))
        scope.__exit__(None, None, None)
        return self.refuse(scope)

    def _lock_not_frozen(self):
        return self.refuse(self._live(), lock={"envelope": self.envelope})

    def _lock_digest_moved(self):
        return self.refuse(self._live(),
                           lock=replace(self.lock, envelope_digest="blake2s:00"))

    def _strata_moved(self):
        return self.refuse(
            self._live(),
            lock=replace(self.lock, strata_definition_revision="rf-null-strata.v9"))

    def _retention_absent(self):
        return self.refuse(self._live(), retention=DEADLINE)

    def _retention_too_far(self):
        far = CapturedCorpusRetention(
            delete_not_after=OPENED_AT + RETENTION_MAXIMUM_SECONDS + 1.0)
        return self.refuse(self._live(), retention=far)

    def _retention_expired(self):
        return self.refuse(self._live(), now=DEADLINE + 1.0)

    def _stratum_outside_grant(self):
        """Reached through the private boundary, because the three public
        entrypoints each fix their own stratum and cannot be told another."""
        creator = _Creator(self.devnull())
        with self.assertRaises(CaptureRefused) as caught:
            admission._record(scope=self._live(), stratum="THERMAL_NO_INPUT",
                              create_target=creator, **self.kwargs())
        return caught.exception, creator

    def _spur_stratum(self):
        creator = _Creator(self.devnull())
        with self.assertRaises(CaptureRefused) as caught:
            admission._record(scope=self._live(), stratum="RECEIVER_SPURS",
                              create_target=creator, **self.kwargs())
        return caught.exception, creator

    def _attestation_type_wrong(self):
        return self.refuse(self._live(), attestation=_retune_attestation())

    def _geometry_refused(self):
        half = PROMOTION_WINDOW_SAMPLES // 2
        ring = _ring(self.chain, count=half)
        scope = ring.attest_window(_window(ring))
        self.addCleanup(scope.__exit__, None, None, None)
        return self.refuse(scope)

    def _chain_outside(self):
        ring = _ring(self.foreign)
        scope = ring.attest_window(_window(ring))
        self.addCleanup(scope.__exit__, None, None, None)
        return self.refuse(scope)

    def _sequence_mismatched(self):
        other = CapturedStratumSequence(corpus_id="corpus-b",
                                        stratum="GAIN_STEPS")
        return self.refuse(self._live(), sequence=other)

    def _cap_reached(self):
        """Set directly: there is no public route to 5 561 publications that
        does not involve performing them, and the cap is what stops the
        5 562nd rather than something a count can be talked into."""
        full = CapturedStratumSequence(corpus_id=self.lock.corpus_id,
                                       stratum="GAIN_STEPS")
        full._accepted = MINIMUM_WINDOWS_PER_STRATUM
        return self.refuse(self._live(), sequence=full)

    def _overlaps_previous(self):
        """Two freshly issued ids over identical retained samples.

        `acquire_window` copies and removes nothing, so the second call returns
        the same samples under a different id, a different digest and a
        different timestamp. The indices are the only part that does not move.
        """
        scope = self._live()
        with self.ring.attest_window(_window(self.ring)) as first:
            published, _creator = self.publish(first)
        self.sequence.count_published(published)
        return self.refuse(scope)

    def _claimed_twice(self):
        real = admission._merge

        def two_authorities(header, authority, contribution, claimed):
            return real(header, authority,
                        dict(contribution, corpus_id="x"), claimed)

        with mock.patch.object(admission, "_merge", two_authorities):
            return self.refuse(self._live())

    def _binding_missing(self):
        def short(**kwargs):
            header = derive_canonical_header(**kwargs)
            header.pop("capture_plan_digest")
            return header
        with mock.patch.object(admission, "derive_canonical_header", short):
            return self.refuse(self._live())

    def _field_undeclared(self):
        def decorated(**kwargs):
            header = derive_canonical_header(**kwargs)
            header["file_sha256"] = "0" * 64
            return header
        with mock.patch.object(admission, "derive_canonical_header", decorated):
            return self.refuse(self._live())

    def _not_frameable(self):
        def with_a_nan(**kwargs):
            header = derive_canonical_header(**kwargs)
            header["capture_start_time"] = float("nan")
            return header
        with mock.patch.object(admission, "derive_canonical_header", with_a_nan):
            return self.refuse(self._live())

    def _creator_not_callable(self):
        creator = _Creator(self.devnull())
        with self.assertRaises(CaptureRefused) as caught:
            record_gain_step(scope=self._live(), create_target=self.devnull(),
                             **self.kwargs())
        return caught.exception, creator


class LengthInvariantTests(CaptureFixture):
    """Three quantities from three independent places, and what they prove."""

    def test_the_three_agree_on_an_admitted_window(self):
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope)
        header = json.loads(published.header_bytes)
        self.assertEqual(header["declared_payload_bytes"],
                         PROMOTION_WINDOW_SAMPLES * BYTES_PER_SAMPLE)
        self.assertEqual(published.declared_payload_bytes,
                         published.payload_bytes_written)
        self.assertEqual(published.payload_bytes_written, PROMOTION_PAYLOAD_BYTES)

    def test_a_header_declaring_the_wrong_count_refuses(self):
        """Control 1. The header and the ring's record disagreeing."""
        self._publish_with(header_edit=lambda h: h.update(
            declared_payload_bytes=PROMOTION_PAYLOAD_BYTES + 8))

    def test_an_incomplete_write_refuses(self):
        """Control 2. Correct arithmetic, and a write that stopped short."""
        self._publish_with(written=PROMOTION_PAYLOAD_BYTES - 4096)

    def test_a_falsified_write_count_refuses(self):
        """Control 3. The reconciliation must not trust the writer's number."""
        self._publish_with(written=PROMOTION_PAYLOAD_BYTES + 4096)

    def test_byte_count_agreement_is_not_content_identity(self):
        """The equation holds exactly for a window of the right length carrying
        the wrong samples. §5.24's recomputed digest is what refuses that, and
        it refuses before admission is ever reached."""
        substituted = np.frombuffer(
            (_SAMPLES[::-1] + 0).tobytes(order="C"), dtype=STORAGE_DTYPE)
        window = _window(self.ring)
        forged = replace(window, samples=substituted)
        self.assertEqual(forged.samples.nbytes, PROMOTION_PAYLOAD_BYTES)
        with self.assertRaises(ring_module.AttestationRefused) as caught:
            self.ring.attest_window(forged)
        self.assertEqual(caught.exception.code,
                         ring_module.ATTESTATION_DIGEST_MISMATCH)

    def _publish_with(self, header_edit=None, written=None):
        real = AttestedIQWindowScope._write_payload_to_fd

        def short(scope, fd):
            real(scope, fd)
            return written

        def edited(**kwargs):
            header = derive_canonical_header(**kwargs)
            header_edit(header)
            return header

        patches = []
        if header_edit is not None:
            patches.append(mock.patch.object(
                admission, "derive_canonical_header", edited))
        if written is not None:
            patches.append(mock.patch.object(
                AttestedIQWindowScope, "_write_payload_to_fd", short))
        with self.ring.attest_window(_window(self.ring)) as scope:
            for patch in patches:
                patch.start()
            try:
                with self.assertRaises(PublicationFailed) as caught:
                    self.publish(scope)
            finally:
                for patch in reversed(patches):
                    patch.stop()
        self.assertEqual(caught.exception.code, PUBLICATION_LENGTH_MISMATCH)
        return caught.exception


class HeaderAuthorityTests(CaptureFixture):
    """Control 8: completeness and exclusivity are different properties."""

    def test_the_required_set_is_the_union_of_the_declarations(self):
        union = set()
        for authority in HEADER_AUTHORITIES:
            union.update(authority.declares("GAIN_STEPS"))
        self.assertEqual(required_header_fields("GAIN_STEPS"), union)
        self.assertEqual(len(HEADER_AUTHORITIES), 6)

    def test_no_authority_contributes_nothing(self):
        """Control 8a, and only 8a.

        Dropping an authority from the aggregator empties its whole
        contribution at once. This asks the narrow question -- *did any
        authority supply none of what it declared* -- so that omitting a
        **single** binding, which is 8b, leaves it passing. The two were one
        test once, and one test cannot tell two defects apart.
        """
        header = self._header()
        for authority in HEADER_AUTHORITIES:
            declared = authority.declares("GAIN_STEPS")
            self.assertTrue(declared, authority.name)
            present = [field for field in declared if field in header]
            self.assertNotEqual(
                present, [],
                f"{authority.name} declares {len(declared)} bindings and "
                "supplied none of them")

    def test_the_derived_header_omits_no_declared_binding(self):
        """Control 8b. Completeness, on its own."""
        missing = sorted(required_header_fields("GAIN_STEPS")
                         - frozenset(self._header()))
        self.assertEqual(missing, [])

    def test_the_derived_header_adds_nothing_no_authority_declared(self):
        """Control 8c. Exclusivity, on its own -- and it is a different
        property: completeness alone permits every required binding to be
        present and something else to be present too."""
        extra = sorted(frozenset(self._header())
                       - required_header_fields("GAIN_STEPS"))
        self.assertEqual(extra, [])

    def test_the_declared_length_is_the_attested_count_times_the_width(self):
        """Control 1, taken off the publication path.

        Every header-wide mutation breaks every publishing test, so a control
        that moves this arithmetic is otherwise indistinguishable from one that
        drops an authority. Derived directly, this fails for the arithmetic and
        for nothing else: 8a, 8b and 8c all leave the number correct.
        """
        header = self._header()
        self.assertEqual(header["declared_payload_bytes"],
                         PROMOTION_WINDOW_SAMPLES * BYTES_PER_SAMPLE)
        self.assertEqual(header["declared_payload_bytes"],
                         PROMOTION_PAYLOAD_BYTES)

    def test_a_binding_that_is_declared_and_not_emitted_refuses(self):
        header = self._header()
        header.pop("envelope_digest")
        with self.assertRaises(CaptureRefused) as caught:
            check_header_completeness(header, "GAIN_STEPS")
        self.assertEqual(caught.exception.code, ADMISSION_BINDING_NOT_EMITTED)

    def test_a_field_no_authority_declared_refuses(self):
        """Control 8c. Completeness alone permits it: every required binding is
        present, and something else is too."""
        header = self._header()
        header["operator_note"] = "looked fine"
        with self.assertRaises(CaptureRefused) as caught:
            check_header_completeness(header, "GAIN_STEPS")
        self.assertEqual(caught.exception.code, ADMISSION_FIELD_NO_AUTHORITY)

    def test_the_forbidden_file_digest_is_refused_by_the_same_mechanism(self):
        """§5.20's identity control: a header quietly accepting `file_sha256`.

        A digest cannot cover the header that carries it. No authority declares
        it, so exclusivity refuses it without a rule of its own."""
        self.assertNotIn("file_sha256", required_header_fields("GAIN_STEPS"))
        header = self._header()
        header["file_sha256"] = "0" * 64
        with self.assertRaises(CaptureRefused) as caught:
            check_header_completeness(header, "GAIN_STEPS")
        self.assertEqual(caught.exception.code, ADMISSION_FIELD_NO_AUTHORITY)

    def test_the_three_amended_fields_are_emitted(self):
        """The amendment §5.25 makes to §5.20's header, and exactly it.

        Control 8 exists because the first seven mutate everything except this.
        A header that simply omitted the capture-plan digest would satisfy all
        of them: the byte counts still agree and admission still refuses a
        foreign chain.
        """
        header = self._header()
        for field in ("declared_payload_bytes", "envelope_digest",
                      "capture_plan_digest"):
            self.assertIn(field, header, field)
        self.assertEqual(header["envelope_digest"], self.lock.envelope.digest())
        self.assertEqual(header["capture_plan_digest"],
                         self.lock.capture_plan.digest())

    def test_a_binding_added_to_a_declaration_becomes_required(self):
        """Without anyone editing a list. A hand-written list would pass,
        having been written before the field existed."""
        @dataclass(frozen=True)
        class WiderGainStep:
            event_id: str
            gain_db_before: float
            gain_db_after: float
            invalidation_epoch: int
            settling_ms: float

        with mock.patch.dict(STRATUM_ATTESTATION,
                             {"GAIN_STEPS": WiderGainStep}):
            self.assertIn("attestation_settling_ms",
                          required_header_fields("GAIN_STEPS"))
            header = dict.fromkeys(required_header_fields("GAIN_STEPS"), 0)
            header.pop("attestation_settling_ms")
            with self.assertRaises(CaptureRefused) as caught:
                check_header_completeness(header, "GAIN_STEPS")
        self.assertEqual(caught.exception.code, ADMISSION_BINDING_NOT_EMITTED)

    def test_two_authorities_supplying_one_field_refuses(self):
        header, claimed = {}, {}
        admission._merge(header, "first", {"corpus_id": "a"}, claimed)
        with self.assertRaises(CaptureRefused) as caught:
            admission._merge(header, "second", {"corpus_id": "b"}, claimed)
        self.assertEqual(caught.exception.code, ADMISSION_BINDING_CLAIMED_TWICE)

    def test_the_write_count_is_declared_and_is_not_a_header_field(self):
        """§5.25 names the payload action as the authority for `payload_sha256`
        **and the completed write count**. The count cannot be a header field:
        the header is written first, so at serialisation time the write has not
        happened. It is reconciled instead."""
        self.assertNotIn(PAYLOAD_ACTION_RECONCILES,
                         required_header_fields("GAIN_STEPS"))
        self.assertEqual(PAYLOAD_ACTION_RECONCILES, "completed_write_count")

    def _header(self):
        with self.ring.attest_window(_window(self.ring)) as scope:
            return derive_canonical_header(
                metadata=scope.to_dict(), lock=self.lock,
                retention=self.retention, stratum="GAIN_STEPS",
                attestation=_gain_attestation(), sequence=self.sequence,
                payload_sha256="0" * 64)


class FramingTests(CaptureFixture):
    """§5.20's `.iqc` framing, exercised through what actually crosses the fd."""

    def test_the_written_image_is_magic_version_length_header_payload_eof(self):
        with self.ring.attest_window(_window(self.ring)) as scope:
            def run(create_target):
                return record_gain_step(scope=scope,
                                        create_target=create_target,
                                        **self.kwargs())
            published, image = _through_a_pipe(run)

        self.assertEqual(image[:8], IQC_MAGIC)
        self.assertEqual(struct.unpack("<H", image[8:10])[0], IQC_FORMAT_VERSION)
        header_length = struct.unpack("<I", image[10:14])[0]
        header_bytes = image[14:14 + header_length]
        self.assertEqual(header_bytes, published.header_bytes)
        payload = image[14 + header_length:]
        self.assertEqual(len(payload), PROMOTION_PAYLOAD_BYTES)
        # EOF immediately after the payload: nothing follows, and a reader that
        # ignored what it did not expect would be a reader that can be appended
        # to.
        self.assertEqual(len(image), 14 + header_length + len(payload))

    def test_the_payload_written_is_the_attested_payload(self):
        import hashlib
        with self.ring.attest_window(_window(self.ring)) as scope:
            def run(create_target):
                return record_gain_step(scope=scope,
                                        create_target=create_target,
                                        **self.kwargs())
            published, image = _through_a_pipe(run)
        header_length = struct.unpack("<I", image[10:14])[0]
        payload = image[14 + header_length:]
        self.assertEqual(hashlib.sha256(payload).hexdigest(),
                         published.payload_sha256)
        self.assertEqual(payload, _SAMPLES.tobytes(order="C"))

    def test_the_header_reserialises_to_the_same_bytes(self):
        """Canonicality. Two spellings of one header are two `file_sha256`
        values and two filenames for one window."""
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope)
        parsed = json.loads(published.header_bytes.decode("utf-8"))
        self.assertEqual(canonical_header_bytes(parsed),
                         published.header_bytes)
        self.assertEqual(published.header_bytes,
                         json.dumps(parsed, sort_keys=True,
                                    separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8"))

    def test_a_bare_nan_never_serialises(self):
        with self.assertRaises(FramingRefused):
            canonical_header_bytes({"x": float("nan")})
        with self.assertRaises(FramingRefused):
            canonical_header_bytes({"x": float("inf")})

    def test_a_header_over_the_maximum_refuses_before_allocating(self):
        with self.assertRaises(FramingRefused):
            canonical_header_bytes({"x": "y" * (IQC_MAX_HEADER_BYTES + 1)})
        with self.assertRaises(FramingRefused):
            framing_prefix(b"z" * (IQC_MAX_HEADER_BYTES + 1))

    def test_the_framing_prefix_records_the_header_length(self):
        """The header is located before it is read, never discovered by
        scanning -- so the length field is the prefix's whole job, and it is
        checked without publishing anything."""
        prefix = framing_prefix(b"x" * 321)
        self.assertEqual(len(prefix), 14)
        self.assertEqual(struct.unpack("<I", prefix[10:14])[0], 321)

    def test_the_magic_is_eight_bytes_with_the_high_bit_set(self):
        self.assertEqual(len(IQC_MAGIC), 8)
        self.assertTrue(IQC_MAGIC[0] & 0x80)
        self.assertTrue(IQC_MAGIC.endswith(b"\r\n"))

    def test_the_schema_string_is_not_the_magic(self):
        """A reader must be able to reject a file it cannot parse without
        parsing it."""
        self.assertNotIn(IQC_HEADER_SCHEMA.encode(), IQC_MAGIC)

    def test_an_incomplete_framing_write_is_a_post_open_failure(self):
        with self.ring.attest_window(_window(self.ring)) as scope:
            with mock.patch.object(admission.os, "write",
                                   lambda fd, data: 0):
                with self.assertRaises(PublicationFailed) as caught:
                    self.publish(scope)
        self.assertEqual(caught.exception.code,
                         PUBLICATION_FRAMING_WRITE_INCOMPLETE)

    def test_a_partial_framing_write_is_completed(self):
        real = os.write
        calls = []

        def one_byte_at_a_time(fd, data):
            # Only the framing. The same patch applied to the payload would
            # drive four million syscalls through this test for no extra
            # property, and a slow suite is a suite people stop running.
            calls.append(len(data))
            return real(fd, data[:1] if len(data) < 1 << 16 else data)

        with self.ring.attest_window(_window(self.ring)) as scope:
            with mock.patch.object(admission.os, "write", one_byte_at_a_time):
                published, _creator = self.publish(scope)
        self.assertGreater(len(calls), 100)
        self.assertEqual(published.framing_bytes_written,
                         14 + len(published.header_bytes))


class TypedBoundaryTests(CaptureFixture):
    """§5.20: three nominal entrypoints, and a closed union."""

    def test_record_receiver_spur_refuses_by_construction(self):
        with self.assertRaises(CaptureRefused) as caught:
            record_receiver_spur()
        self.assertEqual(caught.exception.code,
                         ADMISSION_ATTESTATION_UNCONSTRUCTIBLE)

    def test_the_spur_stratum_has_no_constructible_member(self):
        self.assertIn("RECEIVER_SPURS", STRATUM_ATTESTATION)
        self.assertIsNone(STRATUM_ATTESTATION["RECEIVER_SPURS"])
        self.assertEqual(
            admission_status()["strata_with_no_attestation_member"],
            ["RECEIVER_SPURS"])

    def test_there_is_no_generic_writer(self):
        """`write_window(window, stratum)` is the §13k L.1 hole. The public
        surface has three entrypoints and none takes a label."""
        public = sorted(name for name in dir(admission)
                        if name.startswith("record_"))
        self.assertEqual(public, ["record_gain_step", "record_receiver_spur",
                                  "record_retune_transient"])

    def test_the_attestation_must_be_the_exact_nominal_member(self):
        class LooksLikeAGainStep(GainStepAttestation):
            pass

        impostor = LooksLikeAGainStep(event_id="g", gain_db_before=1.0,
                                      gain_db_after=2.0, invalidation_epoch=1)
        with self.ring.attest_window(_window(self.ring)) as scope:
            refusal, creator = self.refuse(scope, attestation=impostor)
        self.assertEqual(refusal.code, ADMISSION_ATTESTATION_TYPE_WRONG)
        self.assertEqual(creator.calls, 0)

    def test_a_retune_window_carries_every_field_of_its_own_attestation(self):
        sequence = CapturedStratumSequence(corpus_id=self.lock.corpus_id,
                                           stratum="RETUNE_TRANSIENTS")
        with self.ring.attest_window(_window(self.ring)) as scope:
            published = record_retune_transient(
                scope=scope, attestation=_retune_attestation(),
                lock=self.lock, retention=self.retention, sequence=sequence,
                create_target=_Creator(self.devnull()), now=NOW)
        header = json.loads(published.header_bytes)
        self.assertEqual(header["stratum"], "RETUNE_TRANSIENTS")
        self.assertEqual(header["attestation_kind"], "RetuneAttestation")
        self.assertEqual(header["attestation_centre_hz_after"], 433_200_000.0)
        self.assertNotIn("attestation_gain_db_after", header)

    def test_the_granted_strata_are_strata(self):
        from rf_validation_manifest import STRATUM_KEYS
        self.assertTrue(set(CAPTURED_STRATA) <= set(STRATUM_KEYS))
        self.assertEqual(len(CAPTURED_STRATA), 3)

    def test_the_source_label_is_the_formats_and_not_a_callers(self):
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope)
        self.assertEqual(json.loads(published.header_bytes)["source"], CAPTURED)


class SequenceTests(CaptureFixture):
    """Non-overlap is auditable from the files, not from the process."""

    def test_the_first_window_in_a_stratum_has_no_predecessor(self):
        """The path that needs its own control rather than a special case
        nobody tests."""
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope)
        header = json.loads(published.header_bytes)
        self.assertIsNone(header["previous_window_id"])
        self.assertIsNone(header["previous_window_sample_interval"])
        self.assertEqual(header["window_overlap"], PROMOTION_WINDOW_OVERLAP)

    def test_the_second_window_records_its_predecessor_and_the_interval(self):
        header, first, second = self._two_windows()
        self.assertEqual(header["previous_window_id"], first.window_id)
        self.assertEqual(header["previous_window_sample_interval"],
                         PROMOTION_WINDOW_SAMPLES)
        self.assertEqual(second.first_sample_index, first.last_sample_index)

    def test_non_overlap_is_auditable_from_the_header_alone(self):
        """§5.20 correction A: auditable from the files rather than from the
        process that wrote them.

        The interval is start to start, so a reader holding nothing but this
        header checks it against the `sample_count` beside it. A gap measured
        from the predecessor's end would read zero for two adjacent windows and
        would need a length the reader would have to go and find.
        """
        header, _first, _second = self._two_windows()
        self.assertGreaterEqual(header["previous_window_sample_interval"],
                                header["sample_count"])

    def _two_windows(self):
        with self.ring.attest_window(_window(self.ring)) as scope:
            first, _creator = self.publish(scope)
        self.sequence.count_published(first)
        self.ring.append(_SAMPLES)
        with self.ring.attest_window(_window(self.ring)) as scope:
            second, _creator = self.publish(scope)
        return json.loads(second.header_bytes), first, second

    def test_a_publication_does_not_count_itself(self):
        """§5.20 counts at step 8, after the readback, and step 8 is unbuilt."""
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope)
        self.assertEqual(self.sequence.accepted, 0)
        self.assertEqual(self.sequence.count_published(published), 1)

    def test_a_publication_from_another_stratum_is_not_counted_here(self):
        other = CapturedStratumSequence(corpus_id=self.lock.corpus_id,
                                        stratum="RETUNE_TRANSIENTS")
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope)
        with self.assertRaises(CaptureRefused) as caught:
            other.count_published(published)
        self.assertEqual(caught.exception.code,
                         ADMISSION_SEQUENCE_NOT_THIS_STRATUM)

    def test_a_claim_is_not_a_publication(self):
        with self.assertRaises(CaptureRefused):
            self.sequence.count_published({"window_id": "iqw-0"})

    def test_the_cap_refuses_the_next_window_before_anything_is_written(self):
        """The sample is fixed before it is collected. The 5 562nd is refused
        before anything is written, not trimmed afterwards."""
        self.sequence._accepted = MINIMUM_WINDOWS_PER_STRATUM
        with self.ring.attest_window(_window(self.ring)) as scope:
            refusal, creator = self.refuse(scope)
        self.assertEqual(refusal.code, ADMISSION_STRATUM_CAP_REACHED)
        self.assertEqual(creator.calls, 0)
        self.assertEqual(MINIMUM_WINDOWS_PER_STRATUM, 5_561)

    def test_a_window_that_overlaps_its_predecessor_is_refused(self):
        """Its own witness, not only the enumeration.

        Four checks -- this one, the geometry, the lock digests and the strata
        revision -- were each proved solely by
        `test_every_admission_refusal_leaves_the_creator_uncalled`, which made
        their four mutations produce identical failing sets. Four different
        defects with one witness is one witness too few.
        """
        with self.ring.attest_window(_window(self.ring)) as first:
            published, _creator = self.publish(first)
        self.sequence.count_published(published)
        with self.ring.attest_window(_window(self.ring)) as again:
            refusal, _creator = self.refuse(again)
        self.assertEqual(refusal.code, WINDOW_INTERVAL_OVERLAP)

    def test_a_stratum_outside_the_grant_has_no_sequence(self):
        with self.assertRaises(CaptureRefused) as caught:
            CapturedStratumSequence(corpus_id="corpus-a",
                                    stratum="THERMAL_NO_INPUT")
        self.assertEqual(caught.exception.code, ADMISSION_STRATUM_OUTSIDE_GRANT)


class FrozenCorpusTests(CaptureFixture):
    """What the lock has to be before its envelope is worth consulting."""

    def test_a_window_outside_the_promotion_geometry_is_refused(self):
        """§5.20 correction A: promotion capture refuses any geometry but
        256 ms, because that is the ring the windows come out of and the window
        the corpus was sized against."""
        ring = _ring(self.chain, count=PROMOTION_WINDOW_SAMPLES // 2)
        with ring.attest_window(_window(ring)) as scope:
            refusal, _creator = self.refuse(scope)
        self.assertEqual(refusal.code, ADMISSION_GEOMETRY_REFUSED)

    def test_a_lock_whose_frozen_digest_is_not_its_envelopes_is_refused(self):
        """The lock is a frozen dataclass and a caller can build one. What a
        caller cannot do is make the frozen digests agree with the objects
        beside them, so they are recomputed rather than read."""
        for field in ("envelope_digest", "capture_plan_digest"):
            with self.subTest(field=field):
                with self.ring.attest_window(_window(self.ring)) as scope:
                    refusal, _creator = self.refuse(
                        scope, lock=replace(self.lock, **{field: "blake2s:00"}))
                self.assertEqual(refusal.code, ADMISSION_LOCK_DIGEST_MOVED)

    def test_a_lock_opened_under_other_strata_definitions_is_refused(self):
        """§5.20 correction D: a stratum can be redefined while its name, count
        and buildability stay put. A window captured now would be a trial of a
        different population."""
        moved = replace(self.lock, strata_definition_revision="rf-null-strata.v9")
        with self.ring.attest_window(_window(self.ring)) as scope:
            refusal, _creator = self.refuse(scope, lock=moved)
        self.assertEqual(refusal.code, ADMISSION_STRATA_DEFINITION_MOVED)
        self.assertEqual(self.lock.strata_definition_revision,
                         STRATA_DEFINITION_REVISION)


class RetentionTests(CaptureFixture):
    """A bound that can be checked when it is supplied, not a promise."""

    def test_a_deadline_ninety_days_out_is_accepted(self):
        edge = CapturedCorpusRetention(
            delete_not_after=OPENED_AT + RETENTION_MAXIMUM_SECONDS)
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope, retention=edge)
        self.assertEqual(json.loads(published.header_bytes)["delete_not_after"],
                         edge.delete_not_after)

    def test_one_second_past_ninety_days_refuses(self):
        far = CapturedCorpusRetention(
            delete_not_after=OPENED_AT + RETENTION_MAXIMUM_SECONDS + 1.0)
        with self.ring.attest_window(_window(self.ring)) as scope:
            refusal, creator = self.refuse(scope, retention=far)
        self.assertEqual(refusal.code, ADMISSION_RETENTION_BEYOND_MAXIMUM)
        self.assertEqual(creator.calls, 0)

    def test_a_deadline_already_passed_refuses(self):
        with self.ring.attest_window(_window(self.ring)) as scope:
            refusal, _creator = self.refuse(scope, now=DEADLINE + 1.0)
        self.assertEqual(refusal.code, ADMISSION_RETENTION_EXPIRED)

    def test_a_deadline_before_the_corpus_opened_refuses(self):
        early = CapturedCorpusRetention(delete_not_after=OPENED_AT - 1.0)
        with self.ring.attest_window(_window(self.ring)) as scope:
            refusal, _creator = self.refuse(scope, retention=early)
        self.assertEqual(refusal.code, ADMISSION_RETENTION_EXPIRED)

    def test_a_deadline_that_is_not_a_finite_instant_is_not_a_deadline(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(CaptureRefused) as caught:
                CapturedCorpusRetention(delete_not_after=value)
            self.assertEqual(caught.exception.code,
                             ADMISSION_RETENTION_NOT_SUPPLIED)


class ObservationBoundaryTests(unittest.TestCase):
    """§5.20: the capture writer must not import or invoke the detector."""

    # What "the detector" is, enumerated rather than implied. Importing a
    # module executes its graph, so a pure function reached through an impure
    # module is not a pure function -- which is why this is a closure and not a
    # scan of one file.
    DETECTOR_MODULES = ("rf_signal_family", "rf_symbol_clock", "rf_channelizer",
                        "rf_detector_contract", "rf_sparse_analyzer",
                        "rf_spectrum_contract")

    def test_the_writers_import_closure_does_not_reach_the_detector(self):
        import scythe_position_act
        root = os.path.dirname(os.path.abspath(__file__))
        closure = scythe_position_act._first_party_closure(
            root, "rf_capture_admission")
        reached = sorted(set(closure) & set(self.DETECTOR_MODULES))
        self.assertEqual(reached, [], f"the writer can reach {reached}")
        self.assertIn("rf_validation_manifest", closure)

    def test_the_instrument_would_notice(self):
        """A closure that found nothing anywhere would pass vacuously."""
        import scythe_position_act
        root = os.path.dirname(os.path.abspath(__file__))
        closure = scythe_position_act._first_party_closure(
            root, "rf_signal_family")
        self.assertIn("rf_signal_family", closure)

    def test_nothing_here_opens_a_device_or_a_socket(self):
        """A static scan of the boundary's own source. The run may declare the
        receiver's presence; it must not open, tune, reset or talk to it."""
        source = pathlib.Path("rf_capture_admission.py").read_text(
            encoding="utf-8")
        tree = ast.parse(source)
        called = {node.func.attr for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute)}
        for forbidden in ("socket", "connect", "open", "mkdir", "makedirs",
                          "rename", "unlink", "fsync", "remove"):
            self.assertNotIn(forbidden, called, forbidden)


class StatusTests(CaptureFixture):
    """What the boundary says about itself."""

    def test_it_says_which_publication_steps_exist(self):
        status = admission_status()
        self.assertTrue(status["creates_nothing"])
        self.assertEqual(status["publication_steps_unbuilt"], "5.20 STEPS 5-8")
        self.assertEqual(status["cap_per_stratum"], MINIMUM_WINDOWS_PER_STRATUM)
        self.assertEqual(status["forbidden_header_field"], "file_sha256")

    def test_a_publication_exposes_no_samples(self):
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope)
        data = published.to_dict()
        self.assertFalse(data["raw_iq_exposed"])
        self.assertNotIn("samples", data)
        self.assertNotIn("payload", data)
        self.assertEqual(type(published), CapturedWindowPublication)

    def test_the_clock_that_stamped_the_times_is_named(self):
        """§5.25: capture times **and the named clock authority**. Named rather
        than praised -- POSIX_REALTIME is settable and is not monotonic."""
        with self.ring.attest_window(_window(self.ring)) as scope:
            published, _creator = self.publish(scope)
        header = json.loads(published.header_bytes)
        self.assertEqual(header["clock_authority"],
                         ring_module.CLOCK_AUTHORITY_POSIX_REALTIME)

    def test_a_ring_handed_a_clock_nobody_named_declares_the_absence(self):
        ring = _ring(self.chain, now=lambda: NOW)
        self.assertEqual(ring.clock_authority, "UNDECLARED")
        with ring.attest_window(_window(ring)) as scope:
            published, _creator = self.publish(scope)
        self.assertEqual(json.loads(published.header_bytes)["clock_authority"],
                         "UNDECLARED")


if __name__ == "__main__":
    unittest.main()
