import os
import socketserver
import threading
import time
import unittest

import numpy as np

from rf_bridge import (
    IQFFTProcessor, RFBridgeConfig, RFObservationStore, RigctlClient,
    SDRPlusPlusBridge,
)


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
        self.assertNotIn("bins_dbfs", observation)
        self.assertEqual(len(received), 1)
        self.assertIsNone(store.ingest_frame({**frame, "timestamp": 100.5, "sequence": 8}))
        self.assertEqual(len(store.query(frequency_hz=433_920_000, tolerance_hz=2_000)), 1)
        self.assertEqual(store.query(frequency_hz=100_000_000, tolerance_hz=2_000), [])

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
