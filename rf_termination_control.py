"""§5.21 R1: a measured termination control for the catalogue run.

The catalogue only means something if the receiver input is terminated: with an
antenna (or an open port) real signals reach the data. Until now "terminated" rested on
the operator's statement. This module MEASURES it on the same chain the run uses
(rtl_tcp, the declared tuner family and gain) and writes a hashed artifact that the
sequencer verifies BEFORE any catalogue tuner contact.

At a few reference centres chosen for strong ambient signals (FM broadcast, a
600 MHz LTE band, an 850 MHz LTE band) it reads two windows and measures
(a) the sample standard deviation in LSB (wideband: LTE raises it a lot) and
(b) the averaged-spectrum peak-to-median ratio in dB (narrowband: an FM carrier
or a spur stands far above the median; terminated noise sits within a few dB).
It is a one-sided physical check, not an identification of the load: a terminated
input passes, and an antenna-connected input must fail. Thresholds are calibrated
from measured data (see docs/521_TERMINATION_CONTROL.md), never chosen here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from typing import Any, Dict, Optional

import numpy as np

import rf_spur_catalogue_sequencer as seq

CONTROL_REVISION = "term-ctl-1"
SAMPLE_RATE_HZ = seq.SAMPLE_RATE_HZ
CENTRES_HZ = (93_300_000, 99_000_000, 102_000_000, 750_000_000, 881_000_000)
WINDOWS_PER_CENTRE = 2
SEGMENT = 4096
DC_EXCLUDE_HZ = 50_000.0     # covers the Hann skirt of the E4000 DC spike (+-21 kHz peaks seen at 20 kHz)
EDGE_FRACTION = 0.8          # ignore the outer 20% of the band (anti-alias roll-off)
MAX_ARTIFACT_AGE_S = 3600.0

# Per-centre limits for a TERMINATED input: {centre_hz: (max_std_lsb, max_peak_to_median_db)}.
# Calibrated 2026-11-14 (gain 29.0 dB, E4000, SN 71874552) from 8 repeats terminated (at 132 128th St SW,
# Everett) and 8 repeats antenna-on (at 520 128th St SW Ste# A3, Everett), plus held-out terminated runs at
# the second venue (docs/521_TERMINATION_CONTROL.md):
#   FM 93.3/99.0/102.0: terminated std <= 0.83, p/m <= 9.7 dB; antenna std 48-104, p/m >= 25.5 dB
#   750 and 881 MHz:    terminated std <= 2.25 (leak of ambient LTE; 1.2-1.4 at the first venue, up to 2.25 at
#                       the second), p/m <= 12.5 dB; antenna std 23-38  -> limit 4.0
# VENUE-SCOPED: ambient differs by place (LTE leaks into the unit even when terminated, FM stations
# change), so re-validate the terminated side at a new venue before relying on these.
TERMINATED_LIMITS: Dict[int, tuple] = {
    93_300_000: (1.5, 15.0),
    99_000_000: (1.5, 15.0),
    102_000_000: (1.5, 15.0),
    750_000_000: (4.0, 20.0),
    881_000_000: (4.0, 20.0),
}


def measure_window(raw: bytes) -> Dict[str, float]:
    """Sample std (LSB, I and Q together) and averaged-spectrum peak/median (dB)."""
    a = np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 127.5
    std = float(a.std())
    c = a[0::2] + 1j * a[1::2]
    n = len(c) // SEGMENT
    segs = c[:n * SEGMENT].reshape(n, SEGMENT) * np.hanning(SEGMENT)
    power = (np.abs(np.fft.fftshift(np.fft.fft(segs, axis=1), axes=1)) ** 2).mean(axis=0)
    freq = (np.arange(SEGMENT) - SEGMENT // 2) * (SAMPLE_RATE_HZ / SEGMENT)
    band = (np.abs(freq) > DC_EXCLUDE_HZ) & (np.abs(freq) < EDGE_FRACTION * SAMPLE_RATE_HZ / 2)
    pb, fb = power[band], freq[band]
    k = int(np.argmax(pb))
    return {"std_lsb": std,
            "peak_to_median_db": float(10 * math.log10(pb[k] / np.median(pb))),
            "peak_offset_hz": float(fb[k])}


def _canonical_hash(artifact: Dict[str, Any]) -> str:
    body = {k: v for k, v in artifact.items() if k != "artifact_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def run_control(tuner, *, sensor_id: str, tuner_family: str, gain_db: float,
                limits: Optional[Dict[int, tuple]] = None,
                centres_hz=CENTRES_HZ, now: Optional[float] = None) -> Dict[str, Any]:
    """Measure the input at the reference centres and return the artifact.

    `tuner` is a connected seq.RtlTcpTuner (or anything with the same methods).
    Verdict: STREAM_NOT_LIVE if any window is not a live stream; UNCALIBRATED if a
    centre has no limits; else TERMINATED only if every centre is inside its limits,
    otherwise NOT_TERMINATED.
    """
    limits = TERMINATED_LIMITS if limits is None else limits
    tuner.set_manual_gain_db(gain_db, declared_family=tuner_family)
    per_centre, verdict = [], "TERMINATED"
    for centre in centres_hz:
        tuner.set_lo_hz(float(centre))
        tuner.discard(seq.SETTLE_S)
        windows = [tuner.read_window_raw() for _ in range(WINDOWS_PER_CENTRE)]
        live = all(seq.stream_liveness(w)["live"] for w in windows)
        m = [measure_window(w) for w in windows]
        row = {"centre_hz": int(centre), "live": live,
               "std_lsb": float(np.mean([x["std_lsb"] for x in m])),
               "peak_to_median_db": float(max(x["peak_to_median_db"] for x in m)),
               "peak_offset_hz": m[int(np.argmax([x["peak_to_median_db"] for x in m]))]["peak_offset_hz"]}
        lim = limits.get(int(centre))
        row["limits"] = list(lim) if lim else None
        if not live:
            row["result"] = "STREAM_NOT_LIVE"
        elif lim is None:
            row["result"] = "UNCALIBRATED"
        else:
            row["result"] = ("TERMINATED" if row["std_lsb"] <= lim[0] and row["peak_to_median_db"] <= lim[1]
                             else "NOT_TERMINATED")
        per_centre.append(row)
    results = [r["result"] for r in per_centre]
    if "STREAM_NOT_LIVE" in results: verdict = "STREAM_NOT_LIVE"
    elif "UNCALIBRATED" in results: verdict = "UNCALIBRATED"
    elif "NOT_TERMINATED" in results: verdict = "NOT_TERMINATED"
    artifact = {"control_revision": CONTROL_REVISION, "created_unix": time.time() if now is None else now,
                "sensor_id": sensor_id, "tuner": tuner_family, "gain_db": gain_db,
                "sample_rate_hz": SAMPLE_RATE_HZ, "windows_per_centre": WINDOWS_PER_CENTRE,
                "centres": per_centre, "verdict": verdict}
    artifact["artifact_sha256"] = _canonical_hash(artifact)
    return artifact


def verify_artifact(path: str, expected_sha256: str, decl: Dict[str, Any],
                    now: Optional[float] = None) -> Dict[str, Any]:
    """Pre-contact check of a control artifact against a run declaration.

    Raises SequencerRefused(TERMINATION_CONTROL_*) unless the file's hash equals the
    declared one, its own canonical hash is intact, the verdict is TERMINATED, it
    is for this receiver/tuner/gain, from this control revision and thresholds, and
    is no older than MAX_ARTIFACT_AGE_S.
    """
    def refuse(code, msg): raise seq.SequencerRefused("TERMINATION_CONTROL_" + code, msg)
    with open(path, "rb") as h: data = h.read()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        refuse("HASH_MISMATCH", "the control file's SHA-256 is not the one the declaration names")
    art = json.loads(data)
    if art.get("artifact_sha256") != _canonical_hash(art):
        refuse("ALTERED", "the control artifact's own content hash does not verify")
    if art.get("control_revision") != CONTROL_REVISION:
        refuse("REVISION", f"control revision {art.get('control_revision')!r} is not {CONTROL_REVISION!r}")
    if art.get("verdict") != "TERMINATED":
        refuse("NOT_TERMINATED", f"the control verdict is {art.get('verdict')!r}, not TERMINATED")
    if (art["sensor_id"], art["tuner"], art["gain_db"]) != (
            decl["receiver"]["sensor_id"], seq.declared_tuner_family(decl), decl["gain_db"]):
        refuse("CHAIN_MISMATCH", "the control was measured on a different receiver, tuner or gain than the declaration")
    for c in art["centres"]:
        if tuple(c.get("limits") or ()) != tuple(TERMINATED_LIMITS.get(c["centre_hz"], ())):
            refuse("LIMITS", f"limits at {c['centre_hz']} Hz are not the current calibrated limits")
    age = (time.time() if now is None else now) - art["created_unix"]
    if age > MAX_ARTIFACT_AGE_S or age < -60:
        refuse("STALE", f"the control is {age:.0f} s old (limit {MAX_ARTIFACT_AGE_S:.0f} s)")
    return art


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="§5.21 R1 termination control (reads the receiver through rtl_tcp)")
    ap.add_argument("--host", default="127.0.0.1"); ap.add_argument("--port", type=int, default=1234)
    ap.add_argument("--sensor-id", required=True); ap.add_argument("--family", default="E4000")
    ap.add_argument("--gain", type=float, required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--centres-mhz", default=None,
                    help="comma-separated centres in MHz; CALIBRATION ONLY (the artifact then lists them and verdicts are UNCALIBRATED)")
    ap.add_argument("--expect", choices=("TERMINATED", "NOT_TERMINATED", "none"), default="TERMINATED",
                    help="exit 0 only if the verdict equals this (none: always 0, for calibration)")
    a = ap.parse_args(argv)
    with seq.RtlTcpTuner(a.host, a.port) as tuner:
        centres = CENTRES_HZ if a.centres_mhz is None else tuple(int(round(float(x) * 1e6)) for x in a.centres_mhz.split(","))
        art = run_control(tuner, sensor_id=a.sensor_id, tuner_family=a.family, gain_db=a.gain, centres_hz=centres)
    data = (json.dumps(art, indent=1, sort_keys=True) + "\n").encode()
    with open(a.out, "xb") as h: h.write(data)
    print(f"verdict {art['verdict']}; file sha256 {hashlib.sha256(data).hexdigest()}")
    for c in art["centres"]:
        print(f"  {c['centre_hz']/1e6:8.3f} MHz  std {c['std_lsb']:6.3f} LSB  peak/median {c['peak_to_median_db']:5.1f} dB at {c['peak_offset_hz']/1e3:+8.1f} kHz  {c['result']}")
    return 0 if a.expect == "none" or art["verdict"] == a.expect else 1


if __name__ == "__main__":
    sys.exit(main())
