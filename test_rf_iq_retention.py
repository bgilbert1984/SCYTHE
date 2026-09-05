"""Phase 1c: ownership, lifecycle and the retention status transition.

No DSP is exercised here.  The questions are who may allocate a ring, when it is
cleared and under which name, and whether the published status says what is
actually true about the allocation.
"""

import json
import os
import unittest
from unittest.mock import patch

import numpy as np

from rf_bridge import IQFFTProcessor, RFBridgeConfig, SDRPlusPlusBridge
from rf_iq_ring import DEFAULT_CAPACITY_SAMPLES, INVALIDATION_REASONS
from rf_iq_retention import (
    CHANNELIZER_STATE, CLOCK_GAP_S, INACTIVE_REASONS, MAX_INVALIDATION_HISTORY,
    RETENTION_NONE, RETENTION_RING, UNWIRED_REASONS, IQRetentionOwner, antenna_id,
    retention_enabled, signal_chain_hash,
)

RATE = 2_048_000.0


def _owner(**kwargs):
    defaults = dict(sensor_id="NESDR-SMART-V5", sample_type="uint8",
                    sample_rate_hz=2_048_000.0, owns_capture=True)
    defaults.update(kwargs)
    return IQRetentionOwner(**defaults)


def _samples(count=4096, value=0.25):
    return np.full(count, value, dtype=np.complex64)


class EnvironmentIsolatedTest(unittest.TestCase):
    """Retention reads a few env vars; none of them may leak between tests."""

    NAMES = ("SCYTHE_PROCESS_ROLE", "SCYTHE_RF_IQ_RETENTION", "SDRPP_ANTENNA_ID")

    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in self.NAMES}

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class RetentionActivationTests(EnvironmentIsolatedTest):
    """A class existing is not active retention."""

    def test_a_permitted_ring_with_no_samples_is_not_active_retention(self):
        owner = _owner()
        status = owner.status()
        self.assertTrue(owner.permitted, "the owner is allowed to retain")
        self.assertFalse(owner.active, "but nothing has been allocated")
        self.assertEqual(status["iq_retention"], RETENTION_NONE)
        self.assertFalse(status["iq_retention_active"])
        self.assertEqual(status["inactive_reason"], "NO_SAMPLES_YET")
        self.assertIn("A PERMITTED RING IS NOT AN ACTIVE ONE",
                      status["inactive_reason_note"])
        self.assertIsNone(status["ring"])

    def test_the_first_block_of_samples_flips_the_transition(self):
        owner = _owner()
        self.assertEqual(owner.append(_samples(), 1000.0), 4096)
        status = owner.status()
        self.assertEqual(status["iq_retention"], RETENTION_RING)
        self.assertTrue(status["iq_retention_active"])
        self.assertIsNone(status["inactive_reason"])
        self.assertEqual(status["configured_retention_ms"], 256.0)
        self.assertEqual(status["effective_retention_ms"], 256.0)
        self.assertFalse(status["capacity_limited"])
        self.assertEqual(status["capacity_samples"], 524_288)
        self.assertFalse(status["raw_iq_exposed"])
        self.assertEqual(status["channelizer_state"], "INTEGRATED_NO_CLASSIFICATION")
        self.assertEqual(status["ring"]["held_samples"], 4096)

    def test_the_operator_specified_transition_payload_is_exactly_published(self):
        owner = _owner()
        owner.append(_samples(), 1000.0)
        status = owner.status()
        self.assertEqual(
            {key: status[key] for key in ("iq_retention", "iq_retention_active",
                                          "effective_retention_ms", "capacity_samples",
                                          "raw_iq_exposed", "channelizer_state")},
            {"iq_retention": "PROCESS_LOCAL_BOUNDED_RING", "iq_retention_active": True,
             "effective_retention_ms": 256.0, "capacity_samples": 524288,
             "raw_iq_exposed": False, "channelizer_state": "INTEGRATED_NO_CLASSIFICATION"})

    def test_no_status_key_reports_a_bare_unqualified_retention_duration(self):
        """A single "retention_ms" cannot be both the request and the reality."""
        self.assertNotIn("retention_ms", _owner().status())

    def test_a_rate_above_the_allocation_reports_the_duration_it_actually_holds(self):
        """The exact case: 524,288 samples at 2.4 MS/s is 218.453 ms, not 256."""
        owner = _owner(sample_rate_hz=2_400_000.0)
        status = owner.status()
        self.assertEqual(status["capacity_samples"], 524_288)
        self.assertTrue(status["capacity_limited"])
        self.assertEqual(status["configured_retention_ms"], 256.0)
        self.assertAlmostEqual(status["effective_retention_ms"], 218.453, places=3)

    def test_a_rate_below_the_ceiling_is_not_capacity_limited(self):
        """Under the ceiling the window bounds the ring, and the two agree."""
        owner = _owner(sample_rate_hz=1_000_000.0)
        status = owner.status()
        self.assertEqual(status["capacity_samples"], 256_000)
        self.assertFalse(status["capacity_limited"])
        self.assertEqual(status["effective_retention_ms"], 256.0)
        self.assertEqual(status["configured_retention_ms"], 256.0)

    def test_the_effective_duration_matches_the_ring_that_is_actually_allocated(self):
        """The reported duration is not allowed to be a second, separate claim."""
        owner = _owner(sample_rate_hz=2_400_000.0)
        owner.append(_samples(), 1000.0)
        status = owner.status()
        self.assertEqual(status["ring"]["capacity_samples"], status["capacity_samples"])
        self.assertAlmostEqual(
            status["effective_retention_ms"],
            status["ring"]["capacity_samples"] / 2_400_000.0 * 1000.0, places=3)

    def test_the_channelizer_state_names_what_is_still_missing(self):
        """Wired to capture, consumed by nothing.

        Each state this has held named the missing half rather than the whole
        thing: NOT_IMPLEMENTED understated a tested module, AVAILABLE_NOT_
        INTEGRATED understated a wired one, and INTEGRATED alone would overstate
        products that nothing believes.
        """
        status = _owner().status()
        self.assertEqual(status["channelizer_state"], "INTEGRATED_NO_CLASSIFICATION")
        self.assertEqual(status["channelizer_state"], CHANNELIZER_STATE)
        self.assertIn("NO DETECTOR CONSUMES THEM", status["channelizer_note"])
        import rf_channelizer
        self.assertEqual(rf_channelizer.channelizer_status()["bridge_integration"],
                         "INTEGRATED")
        self.assertEqual(rf_channelizer.channelizer_status()["detector_integration"],
                         "NOT_IMPLEMENTED")


class RetentionRefusalTests(EnvironmentIsolatedTest):
    """Every reason there is no ring is reported, never blanked."""

    def test_a_process_that_may_not_hold_raw_iq_is_refused_and_says_so(self):
        os.environ["SCYTHE_PROCESS_ROLE"] = "child"
        owner = _owner()
        self.assertEqual(owner.append(_samples(), 1.0), 0)
        status = owner.status()
        self.assertFalse(status["iq_retention_active"])
        self.assertEqual(status["inactive_reason"], "PROCESS_ROLE_REFUSED")
        self.assertIn("DERIVED PRODUCTS ONLY", status["inactive_reason_note"])
        self.assertIsNone(status["ring"])

    def test_a_refusal_is_recorded_once_and_never_reconsidered(self):
        """A process does not become eligible to hold raw IQ mid-life."""
        os.environ["SCYTHE_PROCESS_ROLE"] = "child"
        owner = _owner()
        owner.append(_samples(), 1.0)
        os.environ["SCYTHE_PROCESS_ROLE"] = "orchestrator"
        self.assertEqual(owner.append(_samples(), 2.0), 0)
        self.assertEqual(owner.status()["inactive_reason"], "PROCESS_ROLE_REFUSED")

    def test_a_process_that_does_not_own_capture_retains_nothing(self):
        owner = _owner(owns_capture=False)
        self.assertEqual(owner.append(_samples(), 1.0), 0)
        self.assertEqual(owner.status()["inactive_reason"], "NOT_CAPTURE_OWNER")
        self.assertEqual(owner.status()["iq_retention"], RETENTION_NONE)

    def test_the_kill_switch_disables_retention_without_a_code_change(self):
        for value in ("0", "off", "no", "false", "disabled", "none"):
            os.environ["SCYTHE_RF_IQ_RETENTION"] = value
            self.assertFalse(retention_enabled(), value)
            owner = _owner()
            self.assertEqual(owner.append(_samples(), 1.0), 0)
            self.assertEqual(owner.status()["inactive_reason"],
                             "DISABLED_BY_CONFIGURATION", value)
        os.environ["SCYTHE_RF_IQ_RETENTION"] = "enabled"
        self.assertTrue(retention_enabled())

    def test_retention_defaults_on_because_it_was_approved(self):
        os.environ.pop("SCYTHE_RF_IQ_RETENTION", None)
        self.assertTrue(retention_enabled())

    def test_every_inactive_reason_is_documented(self):
        for code, note in INACTIVE_REASONS.items():
            self.assertTrue(note.strip(), code)
            self.assertEqual(note, note.upper(), code)


class SignalChainTests(EnvironmentIsolatedTest):
    """What counts as the same signal chain, and what does not."""

    def test_the_chain_covers_sensor_antenna_decode_and_rate(self):
        base = dict(sensor_id="a", sample_type="uint8", sample_rate_hz=2_048_000.0,
                    antenna="ant-1")
        reference = signal_chain_hash(**base)
        self.assertTrue(reference.startswith("blake2s:"))
        for field, value in (("sensor_id", "b"), ("sample_type", "int16"),
                             ("sample_rate_hz", 2_400_000.0), ("antenna", "ant-2")):
            self.assertNotEqual(signal_chain_hash(**{**base, field: value}), reference,
                                f"{field} must change the signal chain")

    def test_a_retune_does_not_change_the_signal_chain(self):
        """Retuning is its own invalidation reason, not a different antenna."""
        owner = _owner()
        before = owner.signal_chain
        owner.append(_samples(), 1.0)
        owner.invalidate("RETUNE")
        self.assertEqual(owner.signal_chain, before)

    def test_an_undeclared_antenna_says_undeclared_not_unknown(self):
        os.environ.pop("SDRPP_ANTENNA_ID", None)
        self.assertEqual(antenna_id(), "UNDECLARED")
        self.assertEqual(_owner().status()["antenna_id"], "UNDECLARED")
        os.environ["SDRPP_ANTENNA_ID"] = "  "
        self.assertEqual(antenna_id(), "UNDECLARED",
                         "whitespace is an omission, not a declaration")
        os.environ["SDRPP_ANTENNA_ID"] = "nesdr-whip-1"
        self.assertEqual(antenna_id(), "nesdr-whip-1")


class CapacityTests(EnvironmentIsolatedTest):
    """256 ms at the configured rate, never above the approved allocation."""

    def test_capacity_follows_the_configured_rate(self):
        owner = _owner(sample_rate_hz=1_024_000.0)
        self.assertEqual(owner.status()["capacity_samples"], 262_144)
        owner.append(_samples(1024), 1.0)
        self.assertEqual(owner.ring.capacity_samples, 262_144)

    def test_capacity_is_capped_at_the_approved_allocation(self):
        owner = _owner(sample_rate_hz=20_000_000.0)
        self.assertEqual(owner.status()["capacity_samples"], DEFAULT_CAPACITY_SAMPLES)
        owner.append(_samples(1024), 1.0)
        self.assertLessEqual(owner.ring.capacity_samples, DEFAULT_CAPACITY_SAMPLES)
        self.assertLessEqual(owner.ring.allocated_bytes, 4_194_304)


class LifecycleTests(EnvironmentIsolatedTest):
    """Which events clear the ring, and under which name."""

    def test_an_unnamed_reason_is_refused_at_the_owner_too(self):
        owner = _owner()
        owner.append(_samples(), 1.0)
        for bad in ("", "because", "RETUNE PLEASE", None):
            with self.assertRaises(ValueError):
                owner.invalidate(bad)
        self.assertEqual(owner.status()["ring"]["held_samples"], 4096)

    def test_clearing_before_any_allocation_is_a_no_op_not_an_error(self):
        self.assertIsNone(_owner().invalidate("DISCONNECT"))

    def test_the_history_preserves_the_cause_a_later_clear_would_overwrite(self):
        owner = _owner()
        owner.append(_samples(), 1.0)
        for reason in ("RETUNE", "ORCHESTRATOR_STOP", "RECONNECT"):
            owner.invalidate(reason)
        history = [entry["reason"] for entry in owner.status()["invalidation_history"]]
        self.assertEqual(history, ["RETUNE", "ORCHESTRATOR_STOP", "RECONNECT"])
        # The ring alone would have said only the last one.
        self.assertEqual(owner.status()["ring"]["last_invalidation_reason"], "RECONNECT")

    def test_the_history_is_bounded(self):
        owner = _owner()
        owner.append(_samples(), 1.0)
        for _ in range(MAX_INVALIDATION_HISTORY + 8):
            owner.invalidate("DISCONNECT")
        self.assertEqual(len(owner.status()["invalidation_history"]),
                         MAX_INVALIDATION_HISTORY)

    def test_a_rate_change_reallocates_rather_than_resizing(self):
        owner = _owner(sample_rate_hz=1_024_000.0)
        owner.append(_samples(), 1.0)
        first = owner.ring
        owner.reconfigure(sample_rate_hz=2_048_000.0, reason="SAMPLE_RATE_CHANGE")
        self.assertIsNone(owner.ring, "the old allocation must be discarded")
        self.assertEqual(owner.status()["inactive_reason"], "NO_SAMPLES_YET")
        owner.append(_samples(), 2.0)
        self.assertIsNot(owner.ring, first)
        self.assertEqual(owner.ring.capacity_samples, 524_288)
        self.assertEqual(owner.status()["invalidation_history"][-1]["reason"],
                         "SAMPLE_RATE_CHANGE")

    def test_a_decode_change_changes_the_chain_and_discards_the_ring(self):
        owner = _owner(sample_type="uint8")
        owner.append(_samples(), 1.0)
        before = owner.signal_chain
        owner.reconfigure(sample_type="int16")
        self.assertNotEqual(owner.signal_chain, before)
        self.assertIsNone(owner.ring)

    def test_close_clears_and_releases_the_allocation(self):
        owner = _owner()
        owner.append(_samples(), 1.0)
        owner.close()
        self.assertIsNone(owner.ring)
        self.assertFalse(owner.status()["iq_retention_active"])
        self.assertEqual(owner.status()["invalidation_history"][-1]["reason"],
                         "ORCHESTRATOR_STOP")

    def test_the_reasons_with_no_bridge_event_are_declared_rather_than_hidden(self):
        """Only DIRECT_SAMPLING_CHANGE is unwired now; the other two have sources."""
        status = _owner().status()
        self.assertEqual(status["unwired_invalidation_reasons"], list(UNWIRED_REASONS))
        self.assertEqual(list(UNWIRED_REASONS), ["DIRECT_SAMPLING_CHANGE"])
        self.assertIn("NO DIRECT-SAMPLING CONTROL", status["unwired_invalidation_note"])
        for reason in UNWIRED_REASONS:
            self.assertIn(reason, INVALIDATION_REASONS,
                          "an unwired reason must still be a real one")
        # A wired reason names what calls it, so "wired" is checkable rather than
        # a claim that has to be taken on faith from a commit message.
        sources = status["wired_invalidation_sources"]
        self.assertIn("GAIN_CHANGE", sources)
        self.assertIn("CLOCK_DISCONTINUITY", sources)
        for reason in sources:
            self.assertNotIn(reason, UNWIRED_REASONS)

    def test_a_gain_change_clears_the_ring_and_moves_the_signal_chain(self):
        owner = _owner()
        owner.append(_samples(), 1.0)
        before = owner.status()["signal_chain_hash"]
        result = owner.set_gain_db(28.0)
        self.assertTrue(result["changed"])
        status = owner.status()
        self.assertEqual(status["invalidation_history"][-1]["reason"], "GAIN_CHANGE")
        self.assertNotEqual(status["signal_chain_hash"], before)
        self.assertEqual(status["gain_db"], 28.0)
        self.assertEqual(status["signal_chain"]["gain"],
                         {"value_db": 28.0, "authority": "OPERATOR_DECLARED"})
        # Setting the same gain again is not an event, so it must not clear.
        history = len(status["invalidation_history"])
        self.assertFalse(owner.set_gain_db(28.0)["changed"])
        self.assertEqual(len(owner.status()["invalidation_history"]), history)

    def test_an_undeclared_gain_reports_undeclared_rather_than_a_number(self):
        status = _owner().status()
        self.assertIsNone(status["gain_db"])
        self.assertEqual(status["signal_chain"]["gain"],
                         {"value_db": None, "authority": "UNDECLARED"})

    def test_a_timing_break_clears_the_ring_under_clock_discontinuity(self):
        """Samples either side of a gap are not contiguous, whatever the count says."""
        owner = _owner()
        owner.append(_samples(), 1.0)
        self.assertEqual(owner.status()["clock_continuity"]["detected_discontinuities"], 0)
        # A gap far longer than a stream at this rate can be silent.
        owner.append(_samples(), 1.0 + CLOCK_GAP_S + 5.0)
        status = owner.status()
        self.assertEqual(status["clock_continuity"]["detected_discontinuities"], 1)
        self.assertEqual(status["clock_continuity"]["last_discontinuity"]["kind"], "GAP")
        self.assertEqual(status["invalidation_history"][-1]["reason"],
                         "CLOCK_DISCONTINUITY")

    def test_buffering_jitter_is_not_a_clock_discontinuity(self):
        """Measured live: 2 s windows swing 4.3%, 20 s cumulative drift is 0.007%."""
        owner = _owner()
        block = _samples()
        at = 1.0
        per_block = block.size / RATE
        for index in range(60):
            # Alternating +/-4% arrival jitter, cumulatively near zero.
            at += per_block * (1.04 if index % 2 else 0.96)
            owner.append(block, at)
        self.assertEqual(owner.status()["clock_continuity"]["detected_discontinuities"], 0)


class BridgeIntegrationTests(EnvironmentIsolatedTest):
    """The bridge owns exactly one ring and drives it from its own events."""

    def _bridge(self, **overrides):
        settings = dict(sensor_id="NESDR-TEST", sample_type="uint8",
                        sample_rate_hz=1_024_000.0, fft_size=1024, max_bins=256,
                        capture_owner="standalone")
        settings.update(overrides)
        return SDRPlusPlusBridge(RFBridgeConfig(**settings))

    def test_the_bridge_publishes_the_retention_block_in_its_status(self):
        bridge = self._bridge()
        block = bridge.status()["iq_retention"]
        self.assertEqual(block["iq_retention"], RETENTION_NONE)
        self.assertFalse(block["iq_retention_active"])
        self.assertEqual(block["owner"], "ORCHESTRATOR_BRIDGE")
        json.dumps(bridge.status()["iq_retention"])

    def test_a_child_process_bridge_constructs_but_retains_nothing(self):
        os.environ["SCYTHE_PROCESS_ROLE"] = "child"
        bridge = self._bridge(capture_owner="orchestrator")
        self.assertFalse(bridge.config.owns_capture())
        bridge.retention.append(_samples(), 1.0)
        self.assertEqual(bridge.status()["iq_retention"]["inactive_reason"],
                         "NOT_CAPTURE_OWNER")

    def test_the_decoded_samples_reach_the_ring_through_the_processor_sink(self):
        bridge = self._bridge()
        processor = IQFFTProcessor(bridge.config, sample_sink=bridge.retention.append)
        # 2048 uint8 pairs = 2048 complex samples, well under one FFT block.
        processor.feed(bytes([127, 128] * 2048), now=1000.0)
        status = bridge.status()["iq_retention"]
        self.assertTrue(status["iq_retention_active"])
        self.assertEqual(status["ring"]["held_samples"], 2048)

    def test_every_decoded_sample_is_retained_not_one_per_published_frame(self):
        """The FPS throttle governs publication, not what was captured."""
        bridge = self._bridge(frames_per_second=0.5)
        processor = IQFFTProcessor(bridge.config, sample_sink=bridge.retention.append)
        frames = list(processor.feed(bytes([127, 128] * 4096), now=1000.0))
        self.assertLessEqual(len(frames), 1, "the throttle should suppress frames")
        self.assertEqual(bridge.status()["iq_retention"]["ring"]["held_samples"], 4096)

    def test_a_failing_sink_cannot_stop_frame_production(self):
        def explode(samples, now):
            raise RuntimeError("retention is broken")
        config = self._bridge().config
        processor = IQFFTProcessor(config, sample_sink=explode)
        frames = list(processor.feed(bytes([127, 200] * 2048), now=1000.0))
        self.assertEqual(len(frames), 1, "a retention failure must not blind the bridge")

    def test_the_default_processor_retains_nothing_at_all(self):
        """Without a sink the pre-Phase-1c behaviour is byte for byte unchanged."""
        processor = IQFFTProcessor(self._bridge().config)
        self.assertIsNone(processor._sample_sink)
        self.assertEqual(len(list(processor.feed(bytes([127, 128] * 2048), now=1.0))), 1)

    def test_a_centre_frequency_change_clears_for_retune(self):
        bridge = self._bridge()
        bridge.retention.append(_samples(), 1.0)
        bridge.configure_stream(center_frequency_hz=433_920_000.0)
        self.assertEqual(bridge.status()["iq_retention"]["invalidation_history"][-1]["reason"],
                         "RETUNE")
        self.assertIsNotNone(bridge.retention.ring, "a retune clears, it does not discard")

    def test_a_rate_change_discards_the_allocation_for_its_own_reason(self):
        bridge = self._bridge(sample_rate_hz=1_024_000.0)
        bridge.retention.append(_samples(), 1.0)
        bridge.configure_stream(sample_rate_hz=2_048_000.0)
        status = bridge.status()["iq_retention"]
        self.assertEqual(status["invalidation_history"][-1]["reason"], "SAMPLE_RATE_CHANGE")
        self.assertIsNone(bridge.retention.ring)
        self.assertEqual(status["capacity_samples"], 524_288)

    def test_a_decode_change_is_a_signal_chain_change(self):
        bridge = self._bridge(sample_type="uint8")
        bridge.retention.append(_samples(), 1.0)
        before = bridge.retention.signal_chain
        bridge.configure_stream(sample_type="int16")
        self.assertEqual(bridge.status()["iq_retention"]["invalidation_history"][-1]["reason"],
                         "SIGNAL_CHAIN_CHANGE")
        self.assertNotEqual(bridge.retention.signal_chain, before)

    def test_stopping_the_bridge_clears_for_orchestrator_stop(self):
        bridge = self._bridge()
        bridge.retention.append(_samples(), 1.0)
        bridge.stop()
        self.assertEqual(bridge.status()["iq_retention"]["invalidation_history"][-1]["reason"],
                         "ORCHESTRATOR_STOP")

    def test_tuning_clears_for_retune_before_the_stream_is_reconfigured(self):
        bridge = self._bridge()
        bridge.retention.append(_samples(), 1.0)
        with patch.object(bridge.rigctl, "set_frequency"), \
             patch.object(bridge, "control_status", return_value={}):
            bridge.tune(433_920_000.0)
        history = [entry["reason"]
                   for entry in bridge.status()["iq_retention"]["invalidation_history"]]
        self.assertEqual(history[0], "RETUNE",
                         "the first recorded cause must be the real one")

    def test_the_retention_status_carries_no_samples_and_serializes(self):
        bridge = self._bridge()
        bridge.retention.append(np.full(2048, 1234.5, dtype=np.complex64), 1.0)
        payload = json.dumps(bridge.status()["iq_retention"])
        self.assertNotIn("1234", payload)
        self.assertIn('"raw_iq_exposed": false', payload)


class DetectionCoverageTests(EnvironmentIsolatedTest):
    """What the monitor found, not what is true of the stream."""

    def test_a_quiet_stream_reports_zero_detected_discontinuities(self):
        """Not "zero discontinuities". The monitor is not the arbiter of truth."""
        owner = _owner()
        owner.append(_samples(), 1.0)
        continuity = owner.status()["clock_continuity"]
        self.assertEqual(continuity["continuity_claim"], "ZERO_DETECTED_DISCONTINUITIES")
        self.assertEqual(continuity["detected_discontinuities"], 0)
        self.assertEqual(continuity["detection_coverage"],
                         "BOUNDED_BY_DRIFT_TOLERANCE_AND_CHECK_INTERVAL")
        self.assertIn("NOT OMNISCIENCE", continuity["coverage_note"])

    def test_the_claim_changes_when_something_is_detected(self):
        owner = _owner()
        owner.append(_samples(), 1.0)
        owner.append(_samples(), 1.0 + CLOCK_GAP_S + 5.0)
        self.assertEqual(owner.status()["clock_continuity"]["continuity_claim"],
                         "DISCONTINUITIES_DETECTED")


class DirectSamplingDeclarationTests(EnvironmentIsolatedTest):
    """The gap is published rather than papered over."""

    def _direct(self):
        return _owner().status()["direct_sampling"]

    def test_the_state_leads_and_the_expectation_is_subordinate(self):
        """A flattened log must not be able to read the expectation as the fact.

        The first version published `direct_sampling_regime: TUNER_QUADRATURE`
        beside an authority field. Every word was true and the regime still read
        as the primary claim once a UI dropped the qualifier.
        """
        direct = self._direct()
        self.assertEqual(direct["direct_sampling"], "UNDECLARED")
        self.assertEqual(direct["expected_capture_regime"], "TUNER_QUADRATURE")
        self.assertEqual(direct["expected_regime_authority"],
                         "INFERRED_FROM_CONFIGURATION")
        # Naming, not position, is what actually defends this. The status route
        # serialises with sorted keys, so the object arrives alphabetically and
        # `attestation_note` leads on the wire whatever order it was built in.
        # Every field that is not the state therefore has to say what it is in
        # its own name -- "expected_", not "regime" -- because a reader that
        # reaches for the first plausible key must land on a qualified one.
        for key in direct:
            if key in ("direct_sampling", "control", "control_transaction",
                       "invalidation_wiring", "runtime_attestation",
                       "attestation_note"):
                continue
            self.assertTrue(key.startswith("expected_"), key)

    def test_no_runtime_attestation_is_claimed(self):
        """An installed R820T does not prove the active stream uses it."""
        self.assertEqual(self._direct()["runtime_attestation"], "UNAVAILABLE")

    def test_the_absent_control_is_declared_absent(self):
        direct = self._direct()
        self.assertEqual(direct["control"], "NOT_IMPLEMENTED")
        self.assertEqual(direct["invalidation_wiring"],
                         "REQUIRED_BEFORE_CONTROL_ENABLEMENT")
        self.assertIn("DIRECT_SAMPLING_CHANGE",
                      _owner().status()["unwired_invalidation_reasons"])

    def test_the_expectation_did_not_reach_the_hashed_manifest(self):
        """Promoting an inference into the instrument's identity would move the hash."""
        status = _owner().status()
        self.assertEqual(status["signal_chain"]["direct_sampling"], "UNDECLARED")
        self.assertEqual(status["direct_sampling"]["direct_sampling"], "UNDECLARED")

    def test_the_control_transaction_is_specified_before_it_exists(self):
        """The order is the content: the ring is discarded before the regime moves."""
        steps = self._direct()["control_transaction"]
        self.assertEqual(steps[0], "STOP_CAPTURE")
        self.assertLess(steps.index("INVALIDATE_AND_DISCARD_RING"),
                        steps.index("CHANGE_REGIME"))
        self.assertLess(steps.index("CHANGE_REGIME"),
                        steps.index("ADVANCE_SIGNAL_CHAIN_MANIFEST_AND_HASH"))
        self.assertEqual(steps[-1],
                         "REFUSE_COMPARISON_WITH_TUNER_QUADRATURE_PRODUCTS")


if __name__ == "__main__":
    unittest.main()
