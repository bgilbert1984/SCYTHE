import os
import socketserver
import threading
import time
import unittest

import numpy as np

from dataclasses import replace

from rf_bridge import (
    IQFFTProcessor, RFBridgeConfig, RFObservationStore, RigctlClient,
    SDRPlusPlusBridge,
)
from rf_signal_family import METHOD_REGISTRY, POSITIVE_REASON_CODE

# No shipped method is validated, so the admit path is only reachable through an
# injected registry. Keeping it here rather than in rf_signal_family means the
# production build has no validated entry to accidentally inherit.
_VALIDATED = replace(
    METHOD_REGISTRY["squared-envelope-cyclic.v1"],
    method_revision="sha256:" + "a" * 64,
    validation_status="VALIDATED",
    validation_note="VALIDATED BY THE TEST CORPUS",
    calibration_revision="rf-family-calibration-test",
)
VALIDATED_REGISTRY = {_VALIDATED.method_id: _VALIDATED}

SYMBOL_CLOCK_CLAIM = {
    "information_structure": "SYMBOL_CLOCK_LIKE_FEATURE",
    "authority": "DERIVED_INFERENCE",
    "method": _VALIDATED.method_id,
    "method_revision": _VALIDATED.method_revision,
    "confidence": 0.82,
    "symbol_rate_hz": 9600.0,
    "detection_statistic": 12.6,
    "decision_threshold": 8.4,
    "statistic_direction": "GREATER_IS_STRONGER",
    "estimated_false_alarm_probability": 0.0004,
    "null_model": "CHANNELIZED_NOISE_PLUS_NONCYCLIC_SIGNAL",
    "sample_count": 524_288,
    "source_window_hash": "sha256:" + "b" * 64,
    "calibration_revision": "rf-family-calibration-test",
}


def _tone_bytes(config: RFBridgeConfig, offset_hz: float, amplitude: float = 0.7) -> bytes:
    sample_index = np.arange(config.fft_size, dtype=np.float32)
    tone = amplitude * np.exp(2j * np.pi * offset_hz * sample_index / config.sample_rate_hz)
    interleaved = np.empty(config.fft_size * 2, dtype="<i2")
    interleaved[0::2] = np.real(tone) * 32767
    interleaved[1::2] = np.imag(tone) * 32767
    return interleaved.tobytes()


class _RigctlHandler(socketserver.StreamRequestHandler):
    frequency = 145_350_000
    mode = "FM"
    bandwidth = 12_500

    def handle(self):
        command = self.rfile.readline(8192).decode("ascii").strip().split()
        if not command:
            return
        if command[0] == "F":
            self.__class__.frequency = int(command[1])
            self.wfile.write(b"RPRT 0\n")
        elif command[0] == "f":
            self.wfile.write(f"{self.__class__.frequency}\n".encode())
        elif command[0] == "M":
            self.__class__.mode = command[1]
            self.__class__.bandwidth = int(command[2])
            self.wfile.write(b"RPRT 0\n")
        elif command[0] == "m":
            self.wfile.write(f"{self.__class__.mode}\n{self.__class__.bandwidth}\n".encode())


class _IQHandler(socketserver.BaseRequestHandler):
    payload = b""

    def handle(self):
        for _ in range(8):
            try:
                self.request.sendall(self.__class__.payload)
            except OSError:
                return
            time.sleep(0.02)


class RFBridgeTests(unittest.TestCase):
    def test_observation_store_creates_bounded_evidence_and_filters(self):
        store = RFObservationStore(maxlen=16, min_snr_db=10, cooldown_s=1, bucket_hz=1000)
        received = []
        store.subscribe(received.append)
        frame = {
            "timestamp": 100.0, "sequence": 7, "sensor_id": "edge-a",
            "center_frequency_hz": 433_920_000, "peak_frequency_hz": 433_921_000,
            "sample_rate_hz": 1_000_000, "peak_dbfs": -30, "noise_floor_dbfs": -55,
            "bins_dbfs": [-55, -30],
        }
        observation = store.ingest_frame(frame)
        self.assertTrue(observation["evidence_id"].startswith("rf-"))
        self.assertEqual(observation["evidence_class"], "OBSERVED")
        self.assertEqual(observation["signal_family"], "UNCLASSIFIED")
        self.assertNotIn("bins_dbfs", observation)
        self.assertEqual(len(received), 1)
        self.assertIsNone(store.ingest_frame({**frame, "timestamp": 100.5, "sequence": 8}))
        self.assertEqual(len(store.query(frequency_hz=433_920_000, tolerance_hz=2_000)), 1)
        self.assertEqual(store.query(frequency_hz=100_000_000, tolerance_hz=2_000), [])

        # The shipped registry validates no method, so the store cannot record a
        # DIGITAL verdict however well evidenced the claim is. Injecting a
        # validated registry exercises the admit path without shipping one.
        store = RFObservationStore(maxlen=16, min_snr_db=10, cooldown_s=1, bucket_hz=1000,
                                   method_registry=VALIDATED_REGISTRY)
        store.ingest_frame(frame)
        classified = store.ingest_frame({**frame, "timestamp": 102.0, "sequence": 9,
            "peak_frequency_hz": 433_925_000,
            "signal_classification": {**SYMBOL_CLOCK_CLAIM,
                                      "window_start": 101.9, "window_end": 102.2}})
        self.assertEqual(classified["information_structure"], "SYMBOL_CLOCK_LIKE_FEATURE")
        # The other two axes stay at their declared absences: a symbol clock says
        # nothing about the carrier or the protocol.
        self.assertEqual(classified["modulation"], "UNRESOLVED")
        self.assertEqual(classified["protocol"], "UNRESOLVED")
        self.assertEqual(classified["signal_family"], "DIGITAL")
        self.assertEqual(classified["classification_authority"], "DERIVED_INFERENCE")
        self.assertEqual(classified["classification_reason_code"], POSITIVE_REASON_CODE)
        self.assertEqual(classified["classification_symbol_rate_hz"], 9600.0)
        stats = store.stats()
        self.assertEqual(stats["signal_classifications"],
                         {"digital": 1, "analogue": 0, "unclassified": 1, "total": 2})
        self.assertEqual(stats["signal_axes"]["information_structure"],
                         {"NOT_ATTEMPTED": 1, "NO_SYMBOL_CLOCK_DETECTED": 0,
                          "SYMBOL_CLOCK_LIKE_FEATURE": 1})
        self.assertEqual(stats["signal_axes"]["modulation"]["UNRESOLVED"], 2)
        self.assertEqual(stats["signal_axes"]["protocol"]["CONFIRMED_BY_DECODER"], 0)

    def test_the_shipped_store_cannot_record_a_symbol_clock(self):
        """No method has passed Phase 3, so live DIGITAL is unreachable."""
        store = RFObservationStore(min_snr_db=10, cooldown_s=0)
        item = store.ingest_frame({
            "timestamp": 100.0, "sequence": 1, "sensor_id": "edge-a",
            "center_frequency_hz": 100_000_000, "peak_frequency_hz": 100_010_000,
            "sample_rate_hz": 1_000_000, "peak_dbfs": -30, "noise_floor_dbfs": -55,
            "signal_classification": {**SYMBOL_CLOCK_CLAIM, "window_start": 99.9,
                                      "window_end": 100.2}})
        self.assertEqual(item["signal_family"], "UNCLASSIFIED")
        self.assertEqual(item["classification_reason_code"], "METHOD_NOT_VALIDATED")
        classifier = store.stats()["classifier"]
        self.assertFalse(classifier["digital_reachable"])
        self.assertEqual(classifier["validated_methods"], [])

    def test_observation_store_refuses_unqualified_signal_family_claims(self):
        store = RFObservationStore(min_snr_db=10, cooldown_s=0)
        base = {"timestamp": 100.0, "sequence": 1, "sensor_id": "edge-a",
                "center_frequency_hz": 100_000_000, "peak_frequency_hz": 100_010_000,
                "sample_rate_hz": 1_000_000, "peak_dbfs": -30, "noise_floor_dbfs": -55}
        clock = {"information_structure": "SYMBOL_CLOCK_LIKE_FEATURE"}
        for index, classification in enumerate((
                {**clock, "authority": "OBSERVED", "method": "guess", "confidence": .9},
                {**clock, "authority": "DERIVED_INFERENCE", "method": "", "confidence": .9},
                {"information_structure": "ALIEN", "authority": "DERIVED_INFERENCE",
                 "method": "x", "confidence": .9},
                # A well-formed ANALOGUE claim is still refused: no positive
                # analogue detector exists, so the summary is unreachable.
                {**SYMBOL_CLOCK_CLAIM, "family": "ANALOGUE",
                 "window_start": 102.5, "window_end": 103.5},
                # Spectral shape is not a symbol clock.
                {**{k: v for k, v in SYMBOL_CLOCK_CLAIM.items() if k != "symbol_rate_hz"},
                 "method": "spectral-flatness",
                 "window_start": 103.5, "window_end": 104.5})):
            item = store.ingest_frame({**base, "timestamp": 100 + index,
                "sequence": index + 1, "peak_frequency_hz": 100_010_000 + index * 5_000,
                "signal_classification": classification})
            self.assertEqual(item["signal_family"], "UNCLASSIFIED")
        stats = store.stats()
        self.assertEqual(stats["signal_classifications"]["unclassified"], 5)
        reasons = stats["classification_reasons"]
        self.assertEqual(reasons.get("ANALOGUE_DETECTOR_NOT_IMPLEMENTED"), 1)
        self.assertEqual(reasons.get("UNQUALIFIED_CLAIM"), 4)
        self.assertNotIn(POSITIVE_REASON_CODE, reasons)

    def test_observation_stats_declare_the_absent_classifier(self):
        store = RFObservationStore(min_snr_db=10, cooldown_s=0)
        classifier = store.stats()["classifier"]
        self.assertEqual(classifier["state"], "NOT_IMPLEMENTED")
        self.assertEqual(classifier["analogue_detector"], "NOT_IMPLEMENTED")
        # No summary is claimable at all: DIGITAL and ANALOGUE are derived.
        self.assertEqual(classifier["claimable_families"], [])
        self.assertEqual(classifier["reserved_families"], ["ANALOGUE"])
        self.assertEqual(classifier["family_summary"]["authority"], "DERIVED_SUMMARY")
        self.assertEqual(classifier["axes"]["modulation"]["detector"], "NOT_IMPLEMENTED")
        self.assertEqual(classifier["axes"]["protocol"]["decoder"], "NOT_IMPLEMENTED")
        self.assertIn("analogue_family", classifier["claims_withheld"])
        self.assertFalse(classifier["raw_iq_exposed"])
        self.assertEqual(store.stats()["classification_reasons"], {})

    def test_detector_may_report_which_nothing_it_found(self):
        store = RFObservationStore(min_snr_db=10, cooldown_s=0)
        item = store.ingest_frame({
            "timestamp": 100.0, "sequence": 1, "sensor_id": "edge-a",
            "center_frequency_hz": 100_000_000, "peak_frequency_hz": 100_010_000,
            "sample_rate_hz": 1_000_000, "peak_dbfs": -30, "noise_floor_dbfs": -55,
            "signal_classification": {"reason_code": "CONSTANT_ENVELOPE"}})
        self.assertEqual(item["signal_family"], "UNCLASSIFIED")
        self.assertEqual(item["classification_reason_code"], "CONSTANT_ENVELOPE")
        # The blind spot is not a negative result: recording it as one is how a
        # constant-envelope digital signal would acquire evidence of being analogue.
        self.assertEqual(item["information_structure"], "NOT_ATTEMPTED")
        self.assertEqual(store.stats()["classification_reasons"], {"CONSTANT_ENVELOPE": 1})

    def test_fft_processor_preserves_alignment_and_locates_peak(self):
        config = RFBridgeConfig(
            sample_rate_hz=1_024_000,
            center_frequency_hz=145_000_000,
            fft_size=1024,
            max_bins=256,
            frames_per_second=60,
        )
        processor = IQFFTProcessor(config)
        payload = _tone_bytes(config, offset_hz=128_000)

        self.assertEqual(list(processor.feed(payload[:3], now=1.0)), [])
        frames = list(processor.feed(payload[3:], now=1.1))

        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame["bin_count"], 256)
        self.assertAlmostEqual(frame["peak_frequency_hz"], 145_128_000, delta=1_100)
        self.assertEqual(frame["min_frequency_hz"], 144_488_000)
        self.assertEqual(frame["max_frequency_hz"], 145_512_000)
        self.assertTrue(all(np.isfinite(frame["bins_dbfs"])))

    def test_config_rejects_invalid_fft_and_sample_type(self):
        with self.assertRaisesRegex(ValueError, "power of two"):
            RFBridgeConfig(fft_size=1000).validated()
        with self.assertRaisesRegex(ValueError, "SAMPLE_TYPE"):
            RFBridgeConfig(sample_type="cs12").validated()

    def test_rigctl_client_tunes_and_reads_status(self):
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _RigctlHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = RigctlClient("127.0.0.1", server.server_address[1], timeout_s=1)
            client.set_frequency(433_920_000)
            client.set_mode("AM", 10_000)
            self.assertEqual(client.get_frequency(), 433_920_000)
            self.assertEqual(client.get_mode(), ("AM", 10_000))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_bridge_receives_iq_and_publishes_bounded_frames(self):
        config = RFBridgeConfig(
            iq_host="127.0.0.1",
            iq_port=1,
            sample_rate_hz=1_024_000,
            center_frequency_hz=100_000_000,
            fft_size=256,
            max_bins=64,
            frames_per_second=60,
            socket_timeout_s=0.5,
            reconnect_max_s=0.5,
        )
        _IQHandler.payload = _tone_bytes(config, 64_000)
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _IQHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        bridge = SDRPlusPlusBridge(
            RFBridgeConfig(**{**config.__dict__, "iq_port": server.server_address[1]})
        )
        try:
            self.assertTrue(bridge.start())
            frame = bridge.wait_for_frame(0, timeout_s=2)
            self.assertIsNotNone(frame)
            self.assertGreaterEqual(frame["sequence"], 1)
            self.assertEqual(frame["sensor_id"], "SDRPP-EDGE-01")
            self.assertEqual(len(frame["bins_dbfs"]), 64)
            self.assertGreater(bridge.status()["bytes_received"], 0)
        finally:
            bridge.stop()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_child_process_does_not_own_orchestrator_capture(self):
        previous_role = os.environ.get("SCYTHE_PROCESS_ROLE")
        os.environ["SCYTHE_PROCESS_ROLE"] = "child"
        try:
            config = RFBridgeConfig(capture_owner="orchestrator")
            self.assertFalse(config.owns_capture())
            bridge = SDRPlusPlusBridge(config)
            self.assertFalse(bridge.start())
            self.assertEqual(bridge.status()["bridge_state"], "delegated")
        finally:
            if previous_role is None:
                os.environ.pop("SCYTHE_PROCESS_ROLE", None)
            else:
                os.environ["SCYTHE_PROCESS_ROLE"] = previous_role


class Uint8IQDecodeTests(unittest.TestCase):
    """rtl_tcp forwards the RTL2832U's native offset-binary uint8 stream."""

    def _processor(self, sample_type):
        from rf_bridge import IQFFTProcessor, RFBridgeConfig
        config = RFBridgeConfig(sample_type=sample_type, fft_size=64, max_bins=32,
                                sample_rate_hz=2_048_000, frames_per_second=60)
        return IQFFTProcessor(config)

    def test_a_centred_uint8_stream_decodes_to_near_zero_dc(self):
        import numpy as np
        # 127/128 straddle the offset-binary centre of 127.5: a signal with no
        # DC component. Decoded correctly the mean is ~0.
        payload = bytes([127, 128] * 32)   # under one 64-point FFT block, so nothing drains
        processor = self._processor("uint8")
        list(processor.feed(payload))
        self.assertLess(abs(complex(np.mean(processor._samples))), 1e-2)

    def test_the_same_bytes_read_as_int8_manufacture_a_dc_carrier(self):
        import numpy as np
        payload = bytes([127, 128] * 32)   # under one 64-point FFT block, so nothing drains
        processor = self._processor("int8")
        list(processor.feed(payload))
        # 128 wraps to -128 under int8, so the identical bytes acquire a large
        # offset. This is the failure uint8 support exists to prevent.
        self.assertGreater(abs(complex(np.mean(processor._samples))), 0.4)

    def test_uint8_is_an_accepted_configuration(self):
        from rf_bridge import RFBridgeConfig
        self.assertEqual(RFBridgeConfig(sample_type="uint8").validated().sample_type, "uint8")


class DongleHeaderTests(unittest.TestCase):
    """rtl_tcp's 12-byte dongle_info block is metadata, not samples."""

    def _processor(self):
        config = RFBridgeConfig(sample_type="uint8", fft_size=64, max_bins=32,
                                sample_rate_hz=2_048_000, frames_per_second=60)
        return IQFFTProcessor(config)

    def _header(self, tuner_code=5, gain_count=29):
        return (b"RTL0" + tuner_code.to_bytes(4, "big")
                + gain_count.to_bytes(4, "big"))

    def test_the_header_is_consumed_rather_than_decoded_as_samples(self):
        """It was not stripped, and became six samples at every reconnect.

        Tuner type and gain count are small integers, so most of those bytes are
        0x00 -- which in offset-binary uint8 is negative full scale. Every
        connection began with a full-scale transient the FFT and the ring both
        saw as signal.
        """
        processor = self._processor()
        # 127/128 straddle the offset-binary centre: no DC component.
        list(processor.feed(self._header() + bytes([127, 128] * 32)))
        self.assertLess(abs(complex(np.mean(processor._samples))), 1e-2)
        self.assertEqual(processor._samples.size, 32)

    def test_the_device_declaration_comes_from_the_device(self):
        processor = self._processor()
        list(processor.feed(self._header() + bytes([127, 128] * 32)))
        info = processor.dongle_info
        self.assertEqual(info["tuner_type"], "R820T")
        self.assertEqual(info["tuner_gain_count"], 29)
        self.assertEqual(info["device_authority"], "DEVICE_DECLARED")

    def test_a_header_split_across_two_reads_is_not_half_decoded(self):
        processor = self._processor()
        header = self._header()
        self.assertEqual(list(processor.feed(header[:5])), [])
        list(processor.feed(header[5:] + bytes([127, 128] * 32)))
        self.assertIsNotNone(processor.dongle_info)
        self.assertEqual(processor._samples.size, 32)

    def test_a_stream_without_the_magic_declares_no_tuner_and_keeps_its_bytes(self):
        """Any other IQ source is not an error; it simply declares nothing."""
        processor = self._processor()
        list(processor.feed(bytes([127, 128] * 32)))
        self.assertIsNone(processor.dongle_info)
        self.assertEqual(processor._samples.size, 32)


class GainControlTests(unittest.TestCase):
    """Manual gain is restricted to the values the tuner actually reports."""

    def _bridge(self, dongle_info):
        bridge = SDRPlusPlusBridge(RFBridgeConfig(
            sample_type="uint8", sample_rate_hz=2_048_000, frames_per_second=60))
        bridge._dongle_info = dongle_info
        bridge._sent = []
        bridge._send_rtl_command = lambda opcode, value: bridge._sent.append((opcode, value))
        return bridge

    def _r820t(self):
        return {"tuner_type": "R820T", "tuner_type_code": 5,
                "tuner_gain_count": 29, "device_authority": "DEVICE_DECLARED"}

    def test_the_catalogue_is_confirmed_by_the_devices_own_gain_count(self):
        catalogue = self._bridge(self._r820t()).supported_gains_db()
        self.assertTrue(catalogue["available"])
        self.assertEqual(len(catalogue["gains_db"]), 29)
        self.assertEqual(catalogue["gains_db"][0], 0.0)
        self.assertEqual(catalogue["gains_db"][-1], 49.6)
        self.assertEqual(catalogue["authority"],
                         "DRIVER_DECLARED_CONFIRMED_BY_DEVICE_COUNT")

    def test_a_gain_count_disagreeing_with_the_device_refuses_manual_gain(self):
        """A driver table that has moved would set the wrong value silently."""
        catalogue = self._bridge(dict(self._r820t(), tuner_gain_count=14)
                                 ).supported_gains_db()
        self.assertFalse(catalogue["available"])
        self.assertEqual(catalogue["reason"], "GAIN_COUNT_DISAGREES_WITH_DEVICE")
        self.assertEqual(catalogue["gains_db"], [])

    def test_no_header_means_no_manual_gain(self):
        catalogue = self._bridge(None).supported_gains_db()
        self.assertFalse(catalogue["available"])
        self.assertEqual(catalogue["reason"], "NO_DEVICE_HEADER_SEEN")

    def test_a_gain_between_supported_values_is_refused_not_rounded(self):
        """The R820T's steps are irregular, so "between" is not a setting."""
        bridge = self._bridge(self._r820t())
        with self.assertRaises(ValueError):
            bridge.set_gain(30.0)          # sits between 29.7 and 32.8
        self.assertEqual(bridge._sent, [], "nothing may be sent for a refused gain")

    def test_a_supported_gain_is_sent_and_clears_the_ring(self):
        bridge = self._bridge(self._r820t())
        before = bridge.retention.status()["signal_chain_hash"]
        result = bridge.set_gain(28.0)
        self.assertEqual(result["mode"], "MANUAL")
        self.assertTrue(result["changed"])
        # Manual mode first, then the value in tenths of a dB.
        self.assertEqual(bridge._sent, [(0x03, 1), (0x04, 280)])
        status = bridge.retention.status()
        self.assertEqual(status["gain_db"], 28.0)
        self.assertNotEqual(status["signal_chain_hash"], before)

    def test_returning_to_automatic_declares_the_gain_undeclared_again(self):
        bridge = self._bridge(self._r820t())
        bridge.set_gain(28.0)
        bridge._sent.clear()
        result = bridge.set_gain(None)
        self.assertEqual(result["mode"], "AUTOMATIC")
        self.assertEqual(bridge._sent, [(0x03, 0)])
        status = bridge.retention.status()
        self.assertIsNone(status["gain_db"])
        # An automatic-gain receiver has a gain; this process does not know it.
        self.assertEqual(status["signal_chain"]["gain"],
                         {"value_db": None, "authority": "UNDECLARED"})


class CaptureRateAuthorityTests(unittest.TestCase):
    """The published rate is a launch parameter, and must say so.

    rtl_tcp has no SET_SAMPLE_RATE opcode and never acknowledges the rate it
    applied, so a configured rate and a confirmed rate are indistinguishable
    from inside this process. They must not be indistinguishable in the
    evidence.
    """

    def _bridge(self, **overrides):
        config = RFBridgeConfig(
            **{**RFBridgeConfig(sample_rate_hz=2_048_000, fft_size=4096).__dict__,
               **overrides})
        return SDRPlusPlusBridge(config)

    def test_the_rate_travels_with_the_authority_that_stands_behind_it(self):
        declaration = self._bridge().capture_rate_declaration()
        self.assertEqual(declaration["sample_rate_hz"], 2_048_000)
        self.assertEqual(declaration["sample_rate_authority"],
                         "SHARED_LAUNCH_CONFIGURATION")
        self.assertEqual(declaration["runtime_attestation"], "UNAVAILABLE")

    def test_the_bin_width_is_conditional_on_the_configured_rate(self):
        declaration = self._bridge().capture_rate_declaration()
        self.assertAlmostEqual(declaration["native_bin_width_hz"], 500.0)
        # The same arithmetic the trace's frequency axis uses. If these ever
        # disagree the axis is labelled by something other than the declared
        # rate, which is the failure this block exists to make visible.
        bridge = self._bridge()
        self.assertAlmostEqual(
            declaration["native_bin_width_hz"],
            bridge.config.sample_rate_hz / bridge.config.fft_size)

    def test_no_field_claims_the_hardware_was_measured(self):
        declaration = self._bridge().capture_rate_declaration()
        for key, value in declaration.items():
            if isinstance(value, str):
                self.assertNotIn("MEASURED", value.upper(),
                                 f"{key} claims measurement of an unattested rate")
                self.assertNotIn("USB", value.upper(),
                                 f"{key} implies USB attestation that rtl_tcp never gives")

    def test_the_declaration_is_published_beside_the_config_it_qualifies(self):
        status = self._bridge().status()
        self.assertIn("capture_rate_declaration", status)
        self.assertEqual(status["config"]["sample_rate_hz"],
                         status["capture_rate_declaration"]["sample_rate_hz"],
                         "the qualified rate must be the same number as the raw one")

    def test_an_unreachable_source_does_not_invent_a_cause(self):
        # Never started, so it cannot be streaming.
        source = self._bridge().capture_source_declaration()
        self.assertEqual(source["availability"], "SOURCE_UNREACHABLE")
        self.assertEqual(source["unreachable_cause"],
                         "NOT_DETERMINABLE_FROM_THIS_PROCESS")
        # The tempting-but-unfounded claim under WSL. A refused socket is not
        # evidence about a USB device.
        self.assertNotIn("USB", source["availability"].upper())
        for key, value in source.items():
            if isinstance(value, str) and key != "cause_note":
                self.assertNotIn("WAITING_FOR_USB", value.upper())

    def test_a_connected_source_carries_no_cause_fields_at_all(self):
        bridge = self._bridge()
        bridge._state = "streaming"
        source = bridge.capture_source_declaration()
        self.assertEqual(source["availability"], "SOURCE_CONNECTED")
        self.assertNotIn("unreachable_cause", source)
        self.assertNotIn("cause_note", source)
