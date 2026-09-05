#!/usr/bin/env python3
"""Select the structure channel's margin from evidence rather than from one point.

A 1.25x measurement channel was measured to cut a 50 kBd raised-cosine signal's
squared-envelope cyclic statistic from 56.07 to 1.38 -- below the detector's own
threshold.  That is one point, and 2.0 was chosen from it as a starting value.
This sweep is what may replace it.

What is measured, per cell
--------------------------
``retention``          the channelized cyclic statistic over the unchannelized
                       reference computed on the same realisation.  Not the
                       absolute statistic: an absolute number confounds the
                       channel's effect with the signal's own detectability.
``symbol_rate_error``  |measured - true| / true, on cells that detected.
``false_clock``        a symbol rate reported on a cell that has no symbol clock
                       (noise-only and constant-envelope controls).
``contamination_db``   output power with a neighbour present over the same cell
                       without one.  This is the cost the wider channel buys.
``usable_samples``     what survives the FIR transient and decimation.
``sps_achieved``       output samples per candidate symbol actually delivered.

The channelization runs through the production ``channelize`` on a real
``BoundedIQRing`` window, with each candidate margin injected as a real
``ChannelPolicy``.  A sweep that reimplemented the filter would be measuring the
sweep.

Alias rejection is measured separately, in ``measure_alias_rejection``: it needs
an out-of-channel tone, which is a different signal, not a different cell.

Usage
-----
    .venv/bin/python tools/rf_structure_channel_sweep.py --out sweep.json
    .venv/bin/python tools/rf_structure_channel_sweep.py --smoke   # 1 cell/axis
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

sys.path.insert(0, "/home/spectrcyde/SCYTHE")

from rf_channelizer import (CHANNEL_POLICIES, ChannelPolicy, ChannelRequest,  # noqa: E402
                            channelize, estimate_occupied_bandwidth)
from rf_iq_ring import BoundedIQRing                                          # noqa: E402
from rf_symbol_clock import squared_envelope_statistic                        # noqa: E402

CAPTURE_RATE_HZ = 2_048_000.0
# The channelizer's own oversample floor, mirrored here only to size the
# reference; the channelizer remains the authority that enforces it.
MIN_OVERSAMPLE_REF = 2.0
CAPTURE_CENTER_HZ = 100_000_000.0
# The production ring capacity. A shorter window changes which symbol rates are
# reachable at all, so the sweep must run at the length the system actually uses.
WINDOW_SAMPLES = 524_288

MARGINS = (1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
SYMBOL_RATES_HZ = (20_000.0, 50_000.0, 100_000.0, 200_000.0)
ROLL_OFFS = (0.0, 0.2, 0.35, 0.5, 1.0)
SNRS_DB = (-10.0, -5.0, 0.0, 5.0, 10.0, 20.0)
# Where the signal sits relative to the capture centre, as a fraction of the
# half-span. The DC guard makes the true centre unusable, so "centre" here means
# the smallest offset the channelizer will accept rather than zero.
OFFSETS = (("center", 0.15), ("quarter", 0.25), ("near_edge", 0.42))
NEIGHBOURS = ("none", "weak_adjacent", "strong_adjacent")
NEIGHBOUR_LEVEL_DB = {"none": None, "weak_adjacent": -10.0, "strong_adjacent": +10.0}

# Promotion requirement, stated before the data is collected so that the data
# cannot quietly redefine what "good" means.
PROMOTION_REQUIREMENT = {
    "selection_basis": "MARGIN_TIMES_MEASURED_OCCUPANCY_AS_IN_PRODUCTION",
    # Selection is against the LOWER TAIL of flat coverage, not its median. A
    # nominal margin means little when the selection stage underestimates the
    # signal before multiplying: the median cell can clear 1.0 while a quarter of
    # cells sit at 0.9 with their shoulders in the skirt, and those are exactly
    # the cells a detector is for.
    "p5_flat_coverage_min": 1.0,
    "median_retention_min": 0.90,
    "p5_retention_min": 0.75,
    "false_clock_rate_max": 0.0,
    "adjacent_channel_condition": (
        "ACHIEVING COVERAGE MUST NOT MATERIALLY WORSEN ADJACENT-CHANNEL FALSE "
        "POSITIVES. THE CONTAMINATION AND NEIGHBOUR COLUMNS ARE REPORTED BESIDE "
        "RETENTION SO THE TRADE IS VISIBLE RATHER THAN NETTED OUT"
    ),
    "note": ("A WIDER CHANNEL PRESERVES STRUCTURE AND ADMITS MORE NEIGHBOURS. "
             "BOTH ARE REPORTED. NEITHER IS COMPRESSED INTO A SINGLE BETTER-MARGIN "
             "CLAIM, AND NO MARGIN IS PROMOTED BY THIS SCRIPT"),
}


# --------------------------------------------------------------------------
# signal generation
# --------------------------------------------------------------------------

def raised_cosine(samples_per_symbol: int, beta: float, span: int = 8) -> np.ndarray:
    """A pulse whose excess bandwidth is the thing the detector looks for.

    At beta = 0 this is a sinc: the minimum-bandwidth pulse, with *no* excess
    bandwidth and therefore no squared-envelope timing line in theory. It is in
    the sweep on purpose, as the case where retention is undefined rather than
    poor -- there is nothing to retain.
    """
    t = np.arange(-span * samples_per_symbol, span * samples_per_symbol + 1) / samples_per_symbol
    with np.errstate(divide="ignore", invalid="ignore"):
        pulse = np.sinc(t) * np.cos(np.pi * beta * t) / (1.0 - (2.0 * beta * t) ** 2)
    pulse[~np.isfinite(pulse)] = 0.0
    return pulse / np.sqrt((pulse ** 2).sum())


def shaped_qpsk(symbol_rate_hz: float, beta: float, count: int,
                rate_hz: float, rng: np.random.Generator) -> Tuple[np.ndarray, float]:
    """QPSK through a raised cosine. Returns the signal and its realised rate.

    The realised rate is not the requested one: samples per symbol is an integer,
    so 100 kBd at 2.048 MS/s is really 102.4 kBd. Every error figure is taken
    against the realised rate, because asserting against the request would be
    testing the request rather than the signal.
    """
    sps = int(round(rate_hz / symbol_rate_hz))
    realised = rate_hz / sps
    symbol_count = int(math.ceil(count / sps)) + 2 * 8 + 4
    symbols = ((rng.integers(0, 2, symbol_count) * 2 - 1)
               + 1j * (rng.integers(0, 2, symbol_count) * 2 - 1)) / math.sqrt(2.0)
    upsampled = np.zeros(symbol_count * sps, dtype=np.complex128)
    upsampled[::sps] = symbols
    shaped = np.convolve(upsampled, raised_cosine(sps, beta), mode="same")[:count]
    return shaped, realised


def build_capture(symbol_rate_hz: float, beta: float, snr_db: float,
                  offset_fraction: float, neighbour: str, *,
                  seed: int) -> Dict[str, Any]:
    """One capture: signal at its offset, optional neighbour, calibrated noise."""
    rng = np.random.default_rng(seed)
    n = WINDOW_SAMPLES
    t = np.arange(n) / CAPTURE_RATE_HZ
    offset_hz = offset_fraction * CAPTURE_RATE_HZ / 2.0

    baseband, realised_rate = shaped_qpsk(symbol_rate_hz, beta, n, CAPTURE_RATE_HZ, rng)
    signal_power = float(np.mean(np.abs(baseband) ** 2))
    capture = baseband * np.exp(2j * np.pi * offset_hz * t)

    neighbour_hz = None
    level = NEIGHBOUR_LEVEL_DB[neighbour]
    if level is not None:
        # One channel width away at the widest margin under test, so the same
        # neighbour is genuinely adjacent for a snug channel and genuinely inside
        # reach for a wide one. That asymmetry is the tradeoff being measured.
        occupied = realised_rate * (1.0 + beta)
        neighbour_hz = offset_hz + occupied * 1.5
        other, _ = shaped_qpsk(symbol_rate_hz * 0.7, 0.35, n, CAPTURE_RATE_HZ,
                               np.random.default_rng(seed + 991))
        scale = math.sqrt(signal_power * (10.0 ** (level / 10.0))
                          / max(float(np.mean(np.abs(other) ** 2)), 1e-30))
        capture = capture + scale * other * np.exp(2j * np.pi * neighbour_hz * t)

    # SNR is defined in the signal's own occupied bandwidth, not across the whole
    # capture: a capture-wide definition would make every narrow signal look
    # worse at the same physical level purely because the span is wide.
    occupied = realised_rate * (1.0 + beta)
    noise_psd = signal_power / (10.0 ** (snr_db / 10.0)) / occupied
    noise_sigma = math.sqrt(noise_psd * CAPTURE_RATE_HZ / 2.0)
    capture = capture + noise_sigma * (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n))

    return {
        "samples": capture.astype(np.complex64),
        "realised_symbol_rate_hz": realised_rate,
        "occupied_bandwidth_hz": occupied,
        "target_hz": CAPTURE_CENTER_HZ + offset_hz,
        "offset_hz": offset_hz,
        "neighbour_hz": (CAPTURE_CENTER_HZ + neighbour_hz) if neighbour_hz else None,
    }


# --------------------------------------------------------------------------
# reference and channelized statistics
# --------------------------------------------------------------------------

# The reference is a *wide channel*, not the absence of one, and is named that
# way. The first version of this harness mixed to baseband and decimated with no
# filter at all, which aliases the whole 2.048 MHz of noise into the output band
# and drives the reference statistic down -- committing, in the reference, the
# exact fault the sweep exists to measure. A retention figure against that
# reference is not conservative, it is meaningless.
#
# 6.0 is far past the widest margin under test, so the FIR's flat region clears
# the signal's shoulders entirely and the reference cannot itself be removing
# structure. It is capped per cell by Nyquist and by the capture span, and the
# margin actually used is published: a cell whose reference could not reach past
# the margins under test is not a fair baseline and says so.
REFERENCE_MARGIN = 6.0


def wide_reference(capture: Dict[str, Any]) -> Dict[str, Any]:
    """The statistic through a channel wide enough not to be the variable."""
    occupied = capture["occupied_bandwidth_hz"]
    half_span = CAPTURE_RATE_HZ / 2.0
    offset = abs(capture["offset_hz"])
    # Bounded by four things: Nyquist on the capture, the distance to the nearer
    # edge of the span, the distance to DC -- the channelizer refuses a channel
    # that straddles the zero-IF artefact, and a 6x reference around a signal
    # 150 kHz from centre does straddle it -- and the declared margin. The 0.95
    # keeps the edge off the guard rather than exactly on it.
    room = 2.0 * 0.95 * min(half_span - offset, offset, half_span / MIN_OVERSAMPLE_REF)
    margin = min(REFERENCE_MARGIN, room / occupied) if occupied > 0 else 0.0
    if margin < 1.0:
        return {"statistic": None, "symbol_rate_hz": None, "margin_used": margin,
                "limited": True, "outcome": "REFERENCE_DOES_NOT_FIT"}
    samples = capture["samples"]
    ring = BoundedIQRing(capacity_samples=samples.size, sample_rate_hz=CAPTURE_RATE_HZ,
                         signal_chain_hash="blake2s:sweep-reference")
    ring.append(samples, {"timestamp": 1000.0})
    window = ring.acquire_window(samples.size).window
    result = channelize(window, ChannelRequest(
        capture_center_hz=CAPTURE_CENTER_HZ,
        target_frequency_hz=capture["target_hz"],
        channel_bandwidth_hz=occupied * margin,
        channel_purpose=_sweep_policy(margin, prefix="SWEEP_REFERENCE")), ring=ring)
    if result.samples is None:
        return {"statistic": None, "symbol_rate_hz": None, "margin_used": margin,
                "limited": True, "outcome": result.product.outcome}
    statistic, symbol_rate, resolution, cv, floor_hz = squared_envelope_statistic(
        result.samples, float(result.product.output_sample_rate_hz))
    return {"statistic": statistic, "symbol_rate_hz": symbol_rate,
            "margin_used": round(margin, 4),
            # A reference that could not clear the widest margin under test is
            # not a baseline those margins can be scored against.
            "limited": margin < max(MARGINS),
            "outcome": result.product.outcome, "envelope_cv": cv,
            "sample_count": int(result.samples.size), "search_floor_hz": floor_hz,
            "resolution_hz": resolution, "snr_db": result.product.snr_db}


def _sweep_policy(margin: float, *, prefix: str = "SWEEP_MARGIN") -> str:
    """Register a candidate margin as a real policy, so the real path runs it."""
    name = f"{prefix}_{margin:g}"
    if name not in CHANNEL_POLICIES:
        CHANNEL_POLICIES[name] = ChannelPolicy(
            channel_purpose=name,
            bandwidth_policy="CYCLIC_STRUCTURE_PRESERVING_V1",
            channel_margin=margin,
            configuration_revision=f"structure-channel-sweep.{margin:g}",
            output_samples_per_candidate_symbol=(
                CHANNEL_POLICIES["STRUCTURE_CHANNEL"].output_samples_per_candidate_symbol),
            margin_status="SWEEP_CANDIDATE_NOT_PROMOTED",
        )
    return name


def measured_occupancy(capture: Dict[str, Any]) -> Optional[float]:
    """What the production occupancy walk returns for this capture.

    This is the base the margin actually multiplies, and it is *not* the
    theoretical R(1+beta). The walk closes at -20 dB, which for a raised cosine
    sits inside the brick wall: 59.75 kHz measured against 69.1 kHz true for a
    50 kBd beta=0.35 signal, a ratio of 0.865.

    The first run of this sweep requested `theoretical occupancy x margin` and
    concluded that margin 1.25 retains 166% of the reference statistic -- while
    the production path at the same margin had been measured at 1.38 from 56.07.
    Both numbers were right. They were answers to different questions, because
    requesting the theoretical width had quietly removed the underestimate that
    caused the problem. The margin is not the only term:

        flat coverage = margin x (measured / true) x PASSBAND_REFERENCE_FRACTION

    and it is flat coverage, not margin, that decides whether the shoulders
    carrying the timing line are inside the filter's flat region.
    """
    # The walk itself, not a whole channelization around it: `channelize` calls
    # exactly this function to form its candidate, and running the FIR and the
    # measurement pass as well would triple the sweep's runtime to learn nothing.
    _centre, bandwidth, _method = estimate_occupied_bandwidth(
        capture["samples"], CAPTURE_RATE_HZ, CAPTURE_CENTER_HZ, capture["target_hz"])
    return float(bandwidth) if bandwidth and bandwidth > 0 else None


def run_cell(capture: Dict[str, Any], margin: float,
             reference: Dict[str, Any]) -> Dict[str, Any]:
    samples = capture["samples"]
    ring = BoundedIQRing(capacity_samples=samples.size, sample_rate_hz=CAPTURE_RATE_HZ,
                         signal_chain_hash="blake2s:sweep")
    ring.append(samples, {"timestamp": 1000.0})
    window = ring.acquire_window(samples.size).window
    started = time.perf_counter()
    # margin x MEASURED occupancy, which is what production does. The width is
    # still asked for rather than walked -- the walk is run once per capture and
    # reused, so every margin sees the same base and the margin stays the only
    # variable -- but the base is the walk's answer, not the theoretical one.
    base = capture.get("measured_occupancy_hz") or capture["occupied_bandwidth_hz"]
    result = channelize(window, ChannelRequest(
        capture_center_hz=CAPTURE_CENTER_HZ,
        target_frequency_hz=capture["target_hz"],
        channel_bandwidth_hz=base * margin,
        channel_purpose=_sweep_policy(margin)), ring=ring)
    elapsed = time.perf_counter() - started
    product = result.product
    true_occupied = capture["occupied_bandwidth_hz"]
    cell: Dict[str, Any] = {
        "margin": margin,
        "outcome": product.outcome,
        "occupancy_base_hz": base,
        "occupancy_base_is_measured": capture.get("measured_occupancy_hz") is not None,
        # The quantity that actually decides whether the shoulders survive.
        "flat_coverage_ratio": (round(0.85 * (base * margin) / true_occupied, 4)
                                if true_occupied > 0 else None),
        "runtime_s": round(elapsed, 4),
        "channel_bandwidth_hz": product.channel_bandwidth_hz,
        "usable_samples": product.sample_count,
        "transient_discarded": product.transient_samples_discarded,
        "sps_achieved": product.output_samples_per_symbol_achieved,
        "decimation": product.decimation,
        "output_rate_hz": product.output_sample_rate_hz,
        "snr_db": product.snr_db,
        "snr_reason_code": product.snr_reason_code,
        "statistic": None, "symbol_rate_hz": None, "retention": None,
        "symbol_rate_error": None, "output_power": None, "envelope_cv": None,
    }
    if not product.channelized or result.samples is None:
        return cell
    statistic, symbol_rate, _resolution, cv, _floor = squared_envelope_statistic(
        result.samples, float(product.output_sample_rate_hz))
    cell["statistic"] = statistic
    cell["symbol_rate_hz"] = symbol_rate
    cell["envelope_cv"] = cv
    cell["output_power"] = float(np.mean(np.abs(result.samples) ** 2))
    if statistic is not None and reference["statistic"]:
        cell["retention"] = statistic / reference["statistic"]
    if symbol_rate:
        true_rate = capture["realised_symbol_rate_hz"]
        cell["symbol_rate_error"] = abs(symbol_rate - true_rate) / true_rate
    return cell


# --------------------------------------------------------------------------
# alias rejection: a different signal, so a different measurement
# --------------------------------------------------------------------------

def measure_alias_rejection(margins=MARGINS) -> List[Dict[str, Any]]:
    """How much out-of-channel energy folds into the output band.

    A strong tone is placed above the output Nyquist that each margin's
    decimation implies. If the FIR is doing its job the tone should not appear;
    if decimation outruns the filter it appears somewhere inside the output
    band and is indistinguishable from signal.
    """
    rng = np.random.default_rng(4242)
    rows = []
    for margin in margins:
        capture = build_capture(50_000.0, 0.35, 20.0, 0.15, "none", seed=11)
        n = capture["samples"].size
        t = np.arange(n) / CAPTURE_RATE_HZ
        clean = capture["samples"].copy()
        channel_bw = capture["occupied_bandwidth_hz"] * margin
        # Well outside the channel, and outside the reference band too.
        tone_hz = capture["offset_hz"] + channel_bw * 3.0
        tone = (3.0 * np.exp(2j * np.pi * tone_hz * t)).astype(np.complex64)
        contaminated = (clean + tone).astype(np.complex64)
        powers = {}
        for label, samples in (("clean", clean), ("with_tone", contaminated)):
            ring = BoundedIQRing(capacity_samples=samples.size,
                                 sample_rate_hz=CAPTURE_RATE_HZ,
                                 signal_chain_hash="blake2s:sweep-alias")
            ring.append(samples, {"timestamp": 1000.0})
            window = ring.acquire_window(samples.size).window
            result = channelize(window, ChannelRequest(
                capture_center_hz=CAPTURE_CENTER_HZ,
                target_frequency_hz=capture["target_hz"],
                channel_bandwidth_hz=channel_bw,
                channel_purpose=_sweep_policy(margin)), ring=ring)
            powers[label] = (float(np.mean(np.abs(result.samples) ** 2))
                             if result.samples is not None else None)
        # float32 accumulation cannot resolve a power difference below roughly
        # 1e-7 of the total, so anything smaller is reported as a floor and not
        # as a number. The first run of this returned 309 dB of rejection, which
        # is not a filter measurement -- it is the width of a mantissa.
        rejection_db = None
        below_floor = False
        if powers["clean"] and powers["with_tone"]:
            resolvable = powers["clean"] * 1e-7
            leak = powers["with_tone"] - powers["clean"]
            tone_power = float(np.mean(np.abs(tone) ** 2))
            if leak < resolvable:
                below_floor = True
                rejection_db = 10.0 * math.log10(tone_power / resolvable)
            else:
                rejection_db = 10.0 * math.log10(tone_power / leak)
        rows.append({"margin": margin,
                     "tone_offset_from_channel_edge_hz": channel_bw * 2.5,
                     "alias_rejection_db": (round(rejection_db, 2)
                                            if rejection_db is not None else None),
                     "at_measurement_floor": below_floor,
                     **{f"power_{k}": v for k, v in powers.items()}})
    return rows


# --------------------------------------------------------------------------
# false-clock controls: cells that have no symbol clock to find
# --------------------------------------------------------------------------

def measure_false_clock(margins=MARGINS, trials: int = 24) -> List[Dict[str, Any]]:
    """Noise-only captures through each margin. Any reported rate is false.

    This is not a validated false-alarm probability and is not published as one.
    Twenty-four trials per margin cannot measure a 1e-3 rate; it can only show
    whether widening the channel makes the detector obviously louder on nothing.
    """
    rows = []
    for margin in margins:
        fired = 0
        attempted = 0
        for trial in range(trials):
            rng = np.random.default_rng(90_000 + trial)
            n = WINDOW_SAMPLES
            samples = (rng.normal(0, 0.1, n) + 1j * rng.normal(0, 0.1, n)).astype(np.complex64)
            ring = BoundedIQRing(capacity_samples=n, sample_rate_hz=CAPTURE_RATE_HZ,
                                 signal_chain_hash="blake2s:sweep-null")
            ring.append(samples, {"timestamp": 1000.0})
            window = ring.acquire_window(n).window
            result = channelize(window, ChannelRequest(
                capture_center_hz=CAPTURE_CENTER_HZ,
                target_frequency_hz=CAPTURE_CENTER_HZ + 0.15 * CAPTURE_RATE_HZ / 2.0,
                channel_bandwidth_hz=50_000.0 * 1.35 * margin,
                channel_purpose=_sweep_policy(margin)), ring=ring)
            if result.samples is None:
                continue
            attempted += 1
            statistic, _rate, _res, _cv, _floor = squared_envelope_statistic(
                result.samples, float(result.product.output_sample_rate_hz))
            if statistic is not None and statistic >= 2.5:
                fired += 1
        rows.append({"margin": margin, "trials": attempted, "threshold_crossings": fired,
                     "rate": (fired / attempted) if attempted else None,
                     "note": "NOT A VALIDATED FALSE-ALARM PROBABILITY"})
    return rows


# --------------------------------------------------------------------------

def sweep(*, smoke: bool = False) -> Dict[str, Any]:
    margins = MARGINS[:2] if smoke else MARGINS
    symbol_rates = SYMBOL_RATES_HZ[1:2] if smoke else SYMBOL_RATES_HZ
    roll_offs = ROLL_OFFS[2:3] if smoke else ROLL_OFFS
    snrs = SNRS_DB[-1:] if smoke else SNRS_DB
    offsets = OFFSETS[:1] if smoke else OFFSETS
    neighbours = NEIGHBOURS[:1] if smoke else NEIGHBOURS

    cells: List[Dict[str, Any]] = []
    total = (len(symbol_rates) * len(roll_offs) * len(snrs)
             * len(offsets) * len(neighbours))
    done = 0
    started = time.perf_counter()
    seed = 1000
    for symbol_rate in symbol_rates:
        for beta in roll_offs:
            for snr_db in snrs:
                for offset_name, offset_fraction in offsets:
                    for neighbour in neighbours:
                        seed += 1
                        capture = build_capture(symbol_rate, beta, snr_db,
                                                offset_fraction, neighbour, seed=seed)
                        # One walk per capture, shared by every margin.
                        capture["measured_occupancy_hz"] = measured_occupancy(capture)
                        reference = wide_reference(capture)
                        for margin in margins:
                            cell = run_cell(capture, margin, reference)
                            cell.update({
                                "symbol_rate_requested_hz": symbol_rate,
                                "symbol_rate_true_hz": capture["realised_symbol_rate_hz"],
                                "roll_off": beta, "snr_db_requested": snr_db,
                                "offset": offset_name, "neighbour": neighbour,
                                "measured_occupancy_hz": capture["measured_occupancy_hz"],
                                "true_occupied_bandwidth_hz": capture["occupied_bandwidth_hz"],
                                "reference_statistic": reference["statistic"],
                                "reference_symbol_rate_hz": reference["symbol_rate_hz"],
                            })
                            cells.append(cell)
                        done += 1
                        if done % 20 == 0 or done == total:
                            rate = (time.perf_counter() - started) / done
                            print(f"  {done}/{total} captures "
                                  f"({rate:.2f}s each, "
                                  f"~{rate * (total - done) / 60:.1f} min left)",
                                  flush=True)
    return {
        "schema": "scythe.rf-structure-channel-sweep.v1",
        "capture_rate_hz": CAPTURE_RATE_HZ,
        "window_samples": WINDOW_SAMPLES,
        "margins": list(margins),
        "promotion_requirement": PROMOTION_REQUIREMENT,
        "grid": {"symbol_rates_hz": list(symbol_rates), "roll_offs": list(roll_offs),
                 "snrs_db": list(snrs), "offsets": [name for name, _ in offsets],
                 "neighbours": list(neighbours)},
        "cells": cells,
        "alias_rejection": measure_alias_rejection(margins),
        "false_clock": measure_false_clock(margins, trials=4 if smoke else 24),
        "elapsed_s": round(time.perf_counter() - started, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="structure_channel_sweep.json")
    parser.add_argument("--smoke", action="store_true",
                        help="one point per axis, to check the harness runs")
    args = parser.parse_args()
    result = sweep(smoke=args.smoke)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=1)
    print(f"wrote {args.out}: {len(result['cells'])} cells in {result['elapsed_s']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
