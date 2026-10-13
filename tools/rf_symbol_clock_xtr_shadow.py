#!/usr/bin/env python3
"""Run symbol-clock v1 and v2 in shadow over retained, terminated-input XTR captures.

Read-only and offline: it opens retained ``.iq`` files (rtl_sdr u8 interleaved I/Q),
verifies each against its sha256 sidecar first, and never touches a device.

The captures are a 50-ohm terminated input.  There is no emitter, so every
SYMBOL_CLOCK_LIKE_FEATURE here is a false crossing by construction, and a refusal
by the channelizer is the *expected, correct* outcome for receiver noise.  This is
a specificity probe on real receiver noise; it says nothing about sensitivity.

Two paths per window:
  production   ring -> channelize -> v1.detect / v2.detect (the contract path)
  direct       statistic on the unchannelized baseband.  OUT OF CONTRACT; shown
               only because the production path may refuse noise before the
               detector runs, which would otherwise hide the comparison.
"""
import argparse, hashlib, json, os, sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import test_rf_channelizer_snr as fixtures
import rf_symbol_clock as v1
import rf_symbol_clock_v2 as v2
from rf_channelizer import ChannelRequest, channelize
from rf_iq_ring import BoundedIQRing

RATE = 2_048_000.0
WINDOW = 524_288
OFFSETS_HZ = (0.0, 100_000.0, 400_000.0, -400_000.0)


def verify(path):
    want = open(path + ".sha256").read().split()[0]
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest() == want


def window(path, index, n_windows):
    count = os.path.getsize(path) // 2
    start = (count - WINDOW) * index // max(1, n_windows - 1)
    raw = np.fromfile(path, dtype=np.uint8, count=2 * WINDOW, offset=2 * start)
    z = (raw[0::2].astype(np.float32) - 127.5) + 1j * (raw[1::2].astype(np.float32) - 127.5)
    return (z / 127.5).astype(np.complex64), start


def production(samples, center_hz, offset_hz):
    ring = BoundedIQRing(capacity_samples=samples.size, sample_rate_hz=RATE,
                         signal_chain_hash=fixtures.CHAIN)
    ring.append(samples)
    acq = ring.acquire_window()
    req = ChannelRequest(capture_center_hz=center_hz, target_frequency_hz=center_hz + offset_hz,
                         expected_signal_chain_hash=fixtures.CHAIN,
                         expected_configuration_epoch=ring.configuration_epoch,
                         channel_purpose="STRUCTURE_CHANNEL")
    result = channelize(acq.window, req, ring=ring)
    if result.product.outcome != "CHANNELIZED":
        return {"channelizer": result.product.outcome}
    a, b = v1.detect(result, ring=ring), v2.detect(result, ring=ring)
    return {"channelizer": "CHANNELIZED", "samples": a.sample_count,
            "v1": [a.outcome, a.detection_statistic], "v2": [b.outcome, b.detection_statistic]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", required=True)
    ap.add_argument("--windows", type=int, default=6)
    ap.add_argument("--out")
    args = ap.parse_args()
    report = []
    for d in args.dir:
        for name, mhz in (("f100", 100e6), ("f400", 400e6), ("f1500", 1500e6), ("f1500r", 1500.05e6)):
            path = os.path.join(d, name + ".iq")
            if not os.path.exists(path):
                continue
            ok = verify(path)
            entry = {"file": path, "sha256_ok": ok, "windows": []}
            if not ok:
                report.append(entry); continue
            for i in range(args.windows):
                z, start = window(path, i, args.windows)
                w = {"start_sample": start,
                     "direct_v1": v1.squared_envelope_statistic(z, RATE)[0],
                     "direct_v2": v2.detrended_statistic(z, RATE)[0], "production": {}}
                for off in OFFSETS_HZ:
                    w["production"][str(int(off))] = production(z, mhz, off)
                entry["windows"].append(w)
            report.append(entry)
    text = json.dumps({"schema": "scythe.rf-symbol-clock-xtr-shadow.v0", "terminated_input": True,
                       "report": report}, indent=1)
    if args.out:
        open(args.out, "w").write(text)
    print(text)


if __name__ == "__main__":
    main()
