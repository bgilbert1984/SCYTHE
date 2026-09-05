"""The structure channel is a second lineage, not a wider measurement channel.

Four things have to hold, and none of them is "the number went up":

1.  The measurement lineage is untouched.  Its margin, its digest formula and
    therefore its published products stay exactly as they were, so a margin
    chosen to help a detector cannot retroactively change what an occupancy
    figure meant.
2.  The two lineages do not pool.  Same window, same width, different purpose ->
    different digest.
3.  A wide filter followed by aggressive decimation is refused, not delivered.
    That is the failure this whole change exists to prevent, and a product that
    says STRUCTURE_CHANNEL while unable to represent a symbol rate would be the
    same defect wearing better paperwork.
4.  The structure channel actually preserves the feature the measurement channel
    removes.  Measured end to end, not asserted.
"""

import math
import unittest

import numpy as np

from rf_channelizer import (CHANNEL_MARGIN, CHANNEL_POLICIES, DEFAULT_CHANNEL_PURPOSE,
                            STRUCTURE_CHANNEL_MARGIN, ChannelRequest, channelize,
                            channelizer_status, product_digest_valid,
                            recompute_product_digest)
from rf_detector_contract import channel_purpose, contract_status
from rf_iq_ring import BoundedIQRing
from rf_symbol_clock import detect, squared_envelope_statistic

RATE = 2_048_000.0
CAPTURE_CENTER_HZ = 100_000_000.0
COUNT = 524_288


def _raised_cosine(samples_per_symbol, beta=0.35, span=8):
    t = np.arange(-span * samples_per_symbol, span * samples_per_symbol + 1) / samples_per_symbol
    with np.errstate(divide="ignore", invalid="ignore"):
        pulse = np.sinc(t) * np.cos(np.pi * beta * t) / (1.0 - (2.0 * beta * t) ** 2)
    pulse[~np.isfinite(pulse)] = 0.0
    return pulse / np.sqrt((pulse ** 2).sum())


def _shaped(symbol_rate_hz, offset_hz, *, beta=0.35, count=COUNT, noise=0.0, seed=5):
    """A raised-cosine QPSK signal at a known rate, placed at a known offset."""
    rng = np.random.default_rng(seed)
    sps = int(round(RATE / symbol_rate_hz))
    symbols = int(math.ceil(count / sps)) + 32
    data = ((rng.integers(0, 2, symbols) * 2 - 1)
            + 1j * (rng.integers(0, 2, symbols) * 2 - 1))
    upsampled = np.zeros(symbols * sps, dtype=np.complex128)
    upsampled[::sps] = data
    baseband = np.convolve(upsampled, _raised_cosine(sps, beta), mode="same")[:count]
    t = np.arange(count) / RATE
    signal = baseband * np.exp(2j * np.pi * offset_hz * t)
    if noise:
        signal = signal + noise * (rng.normal(0, 1, count) + 1j * rng.normal(0, 1, count))
    return signal.astype(np.complex64), RATE / sps


def _windowed(samples, chain="chain-structure"):
    ring = BoundedIQRing(capacity_samples=samples.size, sample_rate_hz=RATE,
                         signal_chain_hash=chain)
    ring.append(samples, {"timestamp": 1000.0})
    return ring, ring.acquire_window(samples.size).window


def _channelize(samples, ring, window, *, purpose=DEFAULT_CHANNEL_PURPOSE, **kwargs):
    # The chain hash and epoch are asserted, not omitted: without them the
    # detector's provenance layer refuses the product and every verdict below
    # would be SOURCE_PRODUCT_UNVERIFIED regardless of what was measured.
    return channelize(window, ChannelRequest(
        capture_center_hz=CAPTURE_CENTER_HZ, channel_purpose=purpose,
        expected_signal_chain_hash=window.signal_chain_hash,
        expected_configuration_epoch=ring.configuration_epoch, **kwargs), ring=ring)


class LineageTests(unittest.TestCase):
    """Two purposes, two identities, one of them frozen."""

    def test_the_measurement_margin_did_not_move(self):
        self.assertEqual(CHANNEL_MARGIN, 1.25)
        self.assertEqual(CHANNEL_POLICIES["MEASUREMENT_CHANNEL"].channel_margin, 1.25)
        self.assertEqual(CHANNEL_POLICIES["MEASUREMENT_CHANNEL"].margin_status, "FROZEN")

    def test_the_default_purpose_is_the_measurement_channel(self):
        """An existing caller gets the product it already got, byte for byte."""
        self.assertEqual(DEFAULT_CHANNEL_PURPOSE, "MEASUREMENT_CHANNEL")
        request = ChannelRequest(capture_center_hz=CAPTURE_CENTER_HZ,
                                 target_frequency_hz=100_200_000.0)
        self.assertEqual(request.channel_purpose, "MEASUREMENT_CHANNEL")

    def test_the_same_window_and_width_digest_differently_per_purpose(self):
        """The lineages must not pool, and the digest is what stops them."""
        samples, _rate = _shaped(50_000.0, 300_000.0)
        digests = {}
        for purpose in ("MEASUREMENT_CHANNEL", "STRUCTURE_CHANNEL"):
            ring, window = _windowed(samples)
            product = _channelize(samples, ring, window, purpose=purpose,
                                  target_frequency_hz=100_300_000.0,
                                  channel_bandwidth_hz=120_000.0).product
            self.assertEqual(product.outcome, "CHANNELIZED", purpose)
            digests[purpose] = product.product_digest
        self.assertNotEqual(digests["MEASUREMENT_CHANNEL"], digests["STRUCTURE_CHANNEL"])

    def test_every_lineage_can_prove_its_own_digest(self):
        """The digest must recompute from the product's own fields, per purpose.

        Adding the policy to the digest inputs without adding it to
        recompute_product_digest made structure-channel products fail their own
        check, and the detector's provenance layer refused every one of them with
        PRODUCT_DIGEST_VALID. That is the failure this test pins.
        """
        samples, _rate = _shaped(50_000.0, 300_000.0)
        for purpose in ("MEASUREMENT_CHANNEL", "STRUCTURE_CHANNEL"):
            ring, window = _windowed(samples)
            product = _channelize(samples, ring, window, purpose=purpose,
                                  target_frequency_hz=100_300_000.0).product
            self.assertEqual(product.outcome, "CHANNELIZED", purpose)
            self.assertTrue(product_digest_valid(product), purpose)
            self.assertEqual(recompute_product_digest(product),
                             product.product_digest, purpose)

    def test_a_refusal_can_prove_its_digest_too(self):
        samples, _rate = _shaped(50_000.0, 300_000.0)
        ring, window = _windowed(samples)
        product = _channelize(samples, ring, window, purpose="STRUCTURE_CHANNEL",
                              target_frequency_hz=100_300_000.0,
                              channel_bandwidth_hz=100_000.0, decimation=9).product
        self.assertEqual(product.outcome, "STRUCTURE_RATE_UNSATISFIABLE")
        self.assertTrue(product_digest_valid(product))

    def test_the_structure_margin_is_frozen_with_its_evidence(self):
        """2.0 arrives at the number it started at, so the record matters more."""
        status = channelizer_status()
        self.assertEqual(STRUCTURE_CHANNEL_MARGIN, 2.0)
        self.assertEqual(status["structure_channel_margin_status"],
                         "SELECTED_FROM_SWEEP_FROZEN")
        self.assertEqual(status["channel_policies"]["MEASUREMENT_CHANNEL"]["margin_status"],
                         "FROZEN")

    def test_the_frozen_margin_publishes_what_it_does_not_cover(self):
        """A frozen constant with no declared weaknesses is one nobody has
        looked at hard enough. Three of these are reasons to doubt the choice."""
        caveats = channelizer_status()["structure_channel_margin_caveats"]
        self.assertIn("P5_RETENTION_SITS_ON_A_CLIFF", caveats)
        self.assertIn("SYNTHETIC_ONLY", caveats)
        # The one that matters most: a different statistic, chosen after the
        # fact, would have picked a different margin -- and was not used.
        self.assertIn("A_DIFFERENT_STATISTIC_WOULD_HAVE_CHOSEN_2.5", caveats)

    def test_an_unknown_purpose_is_refused_rather_than_defaulted(self):
        samples, _rate = _shaped(50_000.0, 300_000.0)
        ring, window = _windowed(samples)
        product = _channelize(samples, ring, window, purpose="WIDER_PLEASE",
                              target_frequency_hz=100_300_000.0).product
        self.assertEqual(product.outcome, "UNKNOWN_CHANNEL_PURPOSE")
        # The refusal still names what was asked for.
        self.assertEqual(product.channel_purpose, "WIDER_PLEASE")
        self.assertEqual(product.bandwidth_policy, "NOT_SELECTED")


class RateFloorTests(unittest.TestCase):
    """A wide filter plus aggressive decimation is the same murder."""

    def test_the_structure_channel_delivers_its_declared_samples_per_symbol(self):
        samples, symbol_rate = _shaped(50_000.0, 300_000.0)
        ring, window = _windowed(samples)
        product = _channelize(samples, ring, window, purpose="STRUCTURE_CHANNEL",
                              target_frequency_hz=100_300_000.0).product
        self.assertEqual(product.outcome, "CHANNELIZED")
        self.assertEqual(product.output_samples_per_candidate_symbol, 4.0)
        self.assertGreaterEqual(product.output_samples_per_symbol_achieved, 4.0)
        # And the floor is real: the output rate clears four times the fastest
        # symbol rate the measured occupancy leaves room for.
        self.assertGreaterEqual(product.output_sample_rate_hz,
                                4.0 * product.candidate_symbol_rate_upper_hz)

    def test_the_measurement_channel_makes_no_such_claim(self):
        """A purpose that does not name a cycle frequency must not imply one."""
        samples, _rate = _shaped(50_000.0, 300_000.0)
        ring, window = _windowed(samples)
        product = _channelize(samples, ring, window,
                              target_frequency_hz=100_300_000.0).product
        self.assertEqual(product.outcome, "CHANNELIZED")
        self.assertIsNone(product.output_samples_per_candidate_symbol)

    def test_the_rate_floor_comes_from_measured_occupancy_not_requested_width(self):
        """A requested width is not evidence about the signal, here either.

        Deriving the floor from the width the caller asked for would let a caller
        lower the floor by asking for a narrow channel -- the rate requirement
        would then be a restatement of the request rather than a fact about what
        the signal needs.
        """
        samples, _rate = _shaped(50_000.0, 300_000.0)
        ring, window = _windowed(samples)
        product = _channelize(samples, ring, window, purpose="STRUCTURE_CHANNEL",
                              target_frequency_hz=100_300_000.0,
                              channel_bandwidth_hz=400_000.0).product
        self.assertEqual(product.outcome, "CHANNELIZED")
        # 400 kHz asked for; the walk measures about 60 kHz on a 50 kBd raised
        # cosine -- its -20 dB edge sits inside the theoretical R(1+beta) of
        # 69 kHz -- and that measurement is what the floor is built from.
        self.assertLess(product.candidate_symbol_rate_upper_hz, 100_000.0)
        self.assertGreater(product.candidate_symbol_rate_upper_hz, 40_000.0)

    def test_an_explicit_decimation_that_breaks_the_floor_is_refused(self):
        """Honouring it would produce a STRUCTURE_CHANNEL that represents nothing."""
        samples, _rate = _shaped(50_000.0, 300_000.0)
        ring, window = _windowed(samples)
        result = _channelize(samples, ring, window, purpose="STRUCTURE_CHANNEL",
                             target_frequency_hz=100_300_000.0,
                             channel_bandwidth_hz=100_000.0,
                             # Legal for Nyquist -- 227.6 kS/s clears the 200 kHz
                             # a 100 kHz channel needs -- and below four samples
                             # per the ~60 kHz candidate symbol rate, which needs
                             # about 239 kS/s. Nyquist is checked first, so this
                             # case can only be reached by the cycle floor.
                             decimation=9)
        self.assertEqual(result.product.outcome, "STRUCTURE_RATE_UNSATISFIABLE")
        self.assertIsNone(result.samples)
        # A refusal is a complete product and still names its lineage.
        self.assertEqual(result.product.channel_purpose, "STRUCTURE_CHANNEL")
        self.assertEqual(result.product.configuration_revision, "structure-channel.v1")

    def test_the_same_request_is_honoured_for_a_measurement_channel(self):
        """The floor belongs to the policy, not to the channelizer as a whole.

        Identical window, identical width, identical decimation: only the purpose
        differs, and only the purpose makes a claim about cycle frequencies.
        """
        samples, _rate = _shaped(50_000.0, 300_000.0)
        ring, window = _windowed(samples)
        product = _channelize(samples, ring, window,
                              target_frequency_hz=100_300_000.0,
                              channel_bandwidth_hz=100_000.0, decimation=9).product
        self.assertEqual(product.outcome, "CHANNELIZED")
        self.assertEqual(product.decimation, 9)

    def test_a_capture_too_slow_for_the_floor_is_refused_not_narrowed(self):
        """No decimation can fix a capture rate below four samples per symbol."""
        # 600 kBd in a 2.048 MS/s capture: four samples per symbol needs 2.4 MS/s.
        samples, _rate = _shaped(600_000.0, 500_000.0)
        ring, window = _windowed(samples)
        product = _channelize(samples, ring, window, purpose="STRUCTURE_CHANNEL",
                              target_frequency_hz=100_500_000.0,
                              channel_bandwidth_hz=900_000.0).product
        self.assertEqual(product.outcome, "STRUCTURE_RATE_UNSATISFIABLE")


class StructurePreservationTests(unittest.TestCase):
    """The end-to-end claim, measured rather than asserted."""

    def _statistic(self, purpose, *, symbol_rate=50_000.0):
        samples, realised = _shaped(symbol_rate, 400_000.0, noise=0.02)
        ring, window = _windowed(samples)
        result = _channelize(samples, ring, window, purpose=purpose,
                             target_frequency_hz=CAPTURE_CENTER_HZ + 400_000.0)
        self.assertEqual(result.product.outcome, "CHANNELIZED", purpose)
        statistic, found, _res, _cv, _floor = squared_envelope_statistic(
            result.samples, float(result.product.output_sample_rate_hz))
        return statistic, found, realised, result.product

    def test_the_measurement_channel_removes_the_cyclic_feature(self):
        """The finding this change came from, kept as a regression test.

        This is not a defect in the measurement channel. A snug filter is right
        for occupancy and wrong for cyclostationarity, and the number below is
        why the two cannot be the same product.
        """
        statistic, _found, _true, _product = self._statistic("MEASUREMENT_CHANNEL")
        self.assertIsNotNone(statistic)
        self.assertLess(statistic, 8.0)

    def test_the_structure_channel_preserves_it(self):
        statistic, found, true_rate, _product = self._statistic("STRUCTURE_CHANNEL")
        self.assertIsNotNone(statistic)
        self.assertGreater(statistic, 8.0)
        # And it finds the right rate, against the realised rate rather than the
        # requested one: samples per symbol is an integer.
        self.assertAlmostEqual(found, true_rate, delta=true_rate * 0.05)

    def test_the_structure_channel_beats_the_measurement_channel_on_the_same_signal(self):
        narrow, _f1, _t1, _p1 = self._statistic("MEASUREMENT_CHANNEL")
        wide, _f2, _t2, _p2 = self._statistic("STRUCTURE_CHANNEL")
        self.assertGreater(wide, narrow * 2.0)


class VerdictStratificationTests(unittest.TestCase):
    """A verdict must say which lineage it came from, or it will be pooled."""

    def _verdict(self, purpose):
        samples, _rate = _shaped(100_000.0, 400_000.0, noise=0.02)
        ring, window = _windowed(samples)
        result = _channelize(samples, ring, window, purpose=purpose,
                             target_frequency_hz=CAPTURE_CENTER_HZ + 400_000.0)
        self.assertEqual(result.product.outcome, "CHANNELIZED", purpose)
        return detect(result, ring=ring)

    def test_each_lineage_is_named_on_its_verdict(self):
        self.assertEqual(self._verdict("STRUCTURE_CHANNEL").channel_purpose,
                         "STRUCTURE_CHANNEL")
        self.assertEqual(self._verdict("MEASUREMENT_CHANNEL").channel_purpose,
                         "MEASUREMENT_CHANNEL")

    def test_a_product_predating_the_split_is_undeclared_not_assumed(self):
        """Guessing the answer -- true though it would be -- is still a guess."""
        self.assertEqual(channel_purpose({}), "CHANNEL_PURPOSE_UNDECLARED")
        self.assertEqual(channel_purpose({"channel_configuration": {}}),
                         "CHANNEL_PURPOSE_UNDECLARED")

    def test_an_unrecognised_purpose_is_not_pooled_with_either_lineage(self):
        """A build that does not know a policy must not file it under one it does."""
        self.assertEqual(
            channel_purpose({"channel_configuration": {"channel_purpose": "SWEEP_MARGIN_3"}}),
            "CHANNEL_PURPOSE_UNDECLARED")

    def test_the_purpose_is_a_covariate_and_never_an_admission_condition(self):
        """Both lineages are admitted. Only the meaning of the verdict differs."""
        status = contract_status()
        self.assertEqual(status["channel_purpose_role"],
                         "VALIDATION_COVARIATE_NOT_ADMISSION_AUTHORITY")
        self.assertEqual(status["admission_rule"], "transformation.outcome == CHANNELIZED")


if __name__ == "__main__":
    unittest.main()
