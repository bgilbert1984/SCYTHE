"""Section 5.20 3d, piece 1: sequence state is durable and bound to its ring.

`CapturedStratumSequence` was process-local: a reopened corpus started every
stratum at zero, which is continuity by amnesia. These are the tests for the
reconstruction that replaces it -- verified history restored from the
reconciled membership view, refused rather than adopted when it belongs to
another ring lifetime -- and for the admission check that enforces the
binding on the live path.

The reconstruction is a pure function of verified history, so most of these
tests touch no filesystem at all. The admission tests hold a real corpus in
a temporary root, like the admission suite, because the lifetime check lives
in `_admit`'s precondition regime: a refusal there must leave the creator
uncalled.
"""

import os
import shutil
import tempfile
import unittest

from rf_capture_admission import (
    ADMISSION_RING_LIFETIME_MISMATCH, ADMISSION_SEQUENCE_HISTORY_REFUSED,
    ADMISSION_STRATUM_OUTSIDE_GRANT, CaptureRefused, CapturedCorpusRetention,
    CapturedStratumSequence, CapturedWindowPublication, CommittedWindow,
    admission_status, record_gain_step, reconstruct_stratum_sequence,
)
from rf_corpus_namespace import _create_corpus_namespace_with_clock
from test_rf_capture_admission import (
    DEADLINE, NOW, OPENED_AT, _Creator, _gain_attestation, _lock, _ring,
    _window,
)
from test_rf_promotion_envelope import _envelope


class ReconstructTests(unittest.TestCase):
    """The pure function: verified history in, a bound sequence out."""

    LIFE = "ring-lifetime-test-001"

    def _member(self, wid, first, last, life=None):
        return CommittedWindow(
            window_id=wid, first_sample_index=first, last_sample_index=last,
            ring_lifetime_id=self.LIFE if life is None else life)

    def _reconstruct(self, windows, life=None, **kwargs):
        params = dict(corpus_id="corpus-a", stratum="GAIN_STEPS",
                      ring_lifetime_id=self.LIFE if life is None else life,
                      committed_windows=tuple(windows))
        params.update(kwargs)
        return reconstruct_stratum_sequence(**params)

    def test_empty_is_creation(self):
        seq = self._reconstruct(())
        self.assertEqual(seq.accepted, 0)
        self.assertIsNone(seq.previous_window_id)
        self.assertIsNone(seq.previous_first_sample_index)
        self.assertIsNone(seq.previous_last_sample_index)
        self.assertEqual(seq.ring_lifetime_id, self.LIFE)

    def test_counts_and_takes_last_in_order(self):
        seq = self._reconstruct((
            self._member("w-1", 0, 1023),
            self._member("w-2", 1024, 2047),
            self._member("w-3", 2048, 3071),
        ))
        self.assertEqual(seq.accepted, 3)
        self.assertEqual(seq.previous_window_id, "w-3")
        self.assertEqual(seq.previous_first_sample_index, 2048)
        self.assertEqual(seq.previous_last_sample_index, 3071)

    def test_to_dict_carries_lifetime(self):
        seq = self._reconstruct((self._member("w-1", 0, 1023),))
        state = seq.to_dict()
        self.assertEqual(state["ring_lifetime_id"], self.LIFE)
        self.assertEqual(state["accepted"], 1)
        self.assertEqual(state["previous_window_id"], "w-1")

    def test_refuses_foreign_lifetime_window(self):
        """A mixed-lifetime history is reported, never adopted."""
        with self.assertRaises(CaptureRefused) as caught:
            self._reconstruct((
                self._member("w-1", 0, 1023),
                self._member("w-2", 1024, 2047, life="ring-lifetime-other"),
            ))
        self.assertEqual(caught.exception.code,
                         ADMISSION_RING_LIFETIME_MISMATCH)

    def test_refuses_empty_lifetime(self):
        with self.assertRaises(CaptureRefused) as caught:
            self._reconstruct((), life="")
        self.assertEqual(caught.exception.code,
                         ADMISSION_RING_LIFETIME_MISMATCH)

    def test_refuses_non_member(self):
        with self.assertRaises(CaptureRefused) as caught:
            self._reconstruct(({"window_id": "w-1"},))
        self.assertEqual(caught.exception.code,
                         ADMISSION_SEQUENCE_HISTORY_REFUSED)

    def test_refuses_inverted_interval(self):
        with self.assertRaises(CaptureRefused) as caught:
            self._reconstruct((self._member("w-1", 2048, 1024),))
        self.assertEqual(caught.exception.code,
                         ADMISSION_SEQUENCE_HISTORY_REFUSED)

    def test_refuses_negative_index(self):
        with self.assertRaises(CaptureRefused) as caught:
            self._reconstruct((self._member("w-1", -1, 1023),))
        self.assertEqual(caught.exception.code,
                         ADMISSION_SEQUENCE_HISTORY_REFUSED)

    def test_refuses_empty_window_id(self):
        with self.assertRaises(CaptureRefused) as caught:
            self._reconstruct((self._member("", 0, 1023),))
        self.assertEqual(caught.exception.code,
                         ADMISSION_SEQUENCE_HISTORY_REFUSED)

    def test_refuses_stratum_outside_grant(self):
        with self.assertRaises(CaptureRefused) as caught:
            self._reconstruct((), stratum="THERMAL_NO_INPUT")
        self.assertEqual(caught.exception.code, ADMISSION_STRATUM_OUTSIDE_GRANT)

    def test_constructor_requires_lifetime(self):
        """The binding is not optional: no lifetime, no sequence."""
        with self.assertRaises(TypeError):
            CapturedStratumSequence(corpus_id="corpus-a",
                                    stratum="GAIN_STEPS")
        with self.assertRaises(CaptureRefused) as caught:
            CapturedStratumSequence(corpus_id="corpus-a",
                                    stratum="GAIN_STEPS", ring_lifetime_id="")
        self.assertEqual(caught.exception.code,
                         ADMISSION_RING_LIFETIME_MISMATCH)

    def test_status_surface_reports_binding(self):
        self.assertTrue(admission_status()["sequence_ring_lifetime_bound"])


class AdmitLifetimeTests(unittest.TestCase):
    """The live path: admission refuses a sequence from a dead ring."""

    def setUp(self):
        self.envelope = _envelope()
        self.lock = _lock(envelope=self.envelope)
        self.chain = sorted(self.envelope.admissible_chain_hashes())[0]
        self.ring = _ring(self.chain)
        self.retention = CapturedCorpusRetention(delete_not_after=DEADLINE)
        root = tempfile.mkdtemp(prefix="scythe-sequence-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.corpus = _create_corpus_namespace_with_clock(
            corpus_id=self.lock.corpus_id, lock=self.lock,
            retention=self.retention, root=root, clock=lambda: NOW)
        self.addCleanup(self.corpus.release)

    def _creator(self):
        devnull = open(os.devnull, "wb")
        self.addCleanup(devnull.close)
        creator = _Creator(devnull.fileno())
        return creator

    def test_admit_accepts_matching_lifetime(self):
        sequence = reconstruct_stratum_sequence(
            corpus_id=self.lock.corpus_id, stratum="GAIN_STEPS",
            ring_lifetime_id=self.ring.ring_lifetime_id,
            committed_windows=())
        creator = self._creator()
        with self.ring.attest_window(_window(self.ring)) as scope:
            published = record_gain_step(
                scope=scope, attestation=_gain_attestation(),
                corpus=self.corpus, sequence=sequence,
                create_target=creator)
        self.assertEqual(creator.calls, 1)
        self.assertEqual(published.stratum, "GAIN_STEPS")

    def test_admit_refuses_dead_ring_lifetime(self):
        """The precondition regime: the creator never runs."""
        dead = CapturedStratumSequence(
            corpus_id=self.lock.corpus_id, stratum="GAIN_STEPS",
            ring_lifetime_id="ring-lifetime-dead-000")
        creator = self._creator()
        with self.ring.attest_window(_window(self.ring)) as scope:
            with self.assertRaises(CaptureRefused) as caught:
                record_gain_step(
                    scope=scope, attestation=_gain_attestation(),
                    corpus=self.corpus, sequence=dead,
                    create_target=creator)
        self.assertEqual(caught.exception.code,
                         ADMISSION_RING_LIFETIME_MISMATCH)
        self.assertEqual(creator.calls, 0)

    def test_count_published_advances_reconstructed(self):
        """Restored history keeps counting from where it stopped."""
        sequence = reconstruct_stratum_sequence(
            corpus_id=self.lock.corpus_id, stratum="GAIN_STEPS",
            ring_lifetime_id=self.ring.ring_lifetime_id,
            committed_windows=(
                CommittedWindow(window_id="w-1", first_sample_index=0,
                                last_sample_index=1023,
                                ring_lifetime_id=self.ring.ring_lifetime_id),
                CommittedWindow(window_id="w-2", first_sample_index=1024,
                                last_sample_index=2047,
                                ring_lifetime_id=self.ring.ring_lifetime_id),
            ))
        publication = CapturedWindowPublication(
            corpus_id=self.lock.corpus_id, stratum="GAIN_STEPS",
            window_id="w-3", first_sample_index=2048, last_sample_index=3071,
            declared_payload_bytes=1024, payload_bytes_written=1024,
            framing_bytes_written=64, payload_sha256="0" * 64,
            header_bytes=b"", framing_prefix_bytes=b"")
        self.assertEqual(sequence.count_published(publication), 3)
        self.assertEqual(sequence.previous_window_id, "w-3")
        self.assertEqual(sequence.previous_last_sample_index, 3071)


if __name__ == "__main__":
    unittest.main()
