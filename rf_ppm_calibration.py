#!/usr/bin/env python3
"""XTR reference-frequency (ppm) calibration: plan, estimator, fit, decision. DRAFT.

Implements sections 6-8 of docs/521_XTR_PPM_CALIBRATION_PROCEDURE.md as code that
runs on recorded data. It does not touch a receiver (see rf_ppm_calibration_capture.py)
and it cannot amend any authorization: it emits a *proposal*, or says why there is none.

What is built here
  * a plan that is frozen and hashed before data exists (`freeze_plan`);
  * a narrow-line frequency estimator with a per-window Cramer-Rao standard error
    (`estimate_line`) and its synthetic validation (`validate_estimator`);
  * the per-session weighted fit offset = a + b*f, its acceptance tests, the
    cross-session combination, and the fixed decision rule (`fit_session`,
    `combine_sessions`);
  * an evidence manifest (`write_manifest`).

What is NOT built (the procedure's Tier A needs it)
  * an LTE (synchronisation-signal / cyclic-prefix) or GSM (FCCH) estimator. The line estimator
    below measures a steady narrow carrier: a CW source (the Tier B GPSDO route) or any
    unmodulated pilot. It does not demodulate cellular downlink, and it must not be pointed at
    one and trusted. Tier A has no estimator yet.
  * a verified reference accuracy. The plan carries the figure and whether it is confirmed
    against the cited specification; an unconfirmed figure blocks any proposal.

Sign convention: offset = f_measured - f_true (f_true is the line's nominal frequency, channel plus its
stated offset) and b = slope of offset against the LO frequency the receiver was tuned to. The
reference oscillator's fractional error is -b (an oscillator that runs fast moves the LO up and the
signal down in baseband). The decision uses |b|, so the sign does not change the proposal.

Systematic not modelled: the sample clock shares the reference, so a baseband frequency is
scaled by the same error. The effect is at most |b| * |baseband Hz|; keep the line close to
baseband zero (the result reports this bound per carrier).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

SOURCE_PATH = os.path.abspath(__file__)
LTE_SOURCE_PATH = os.path.join(os.path.dirname(SOURCE_PATH), "rf_lte_carrier_estimator.py")
ESTIMATORS = ("narrow_line", "lte_cp_pss")
# Duplex mode per E-UTRA operating band: TS 36.101 V20.1.0 (2026-09) Table 5.5-1, read 2026-11-06 by one reader.
# Only bands actually read are listed; a band not listed is refused until the table is read for it and this is extended.
LTE_BAND_DUPLEX = {**{b: "FDD" for b in (2, 3, 4, 5, 7, 12, 13, 14, 17, 20, 25, 26, 28, 66, 71)},
                   **{b: "TDD" for b in (38, 40, 41)}}


class CalibrationRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


# --------------------------------------------------------------------------- plan
PLAN_REQUIRED = ("schema", "receiver_serial", "gain_db", "sample_rate_hz", "n_windows",
                 "sessions_min", "carriers", "estimator", "thresholds",
                 "reference_accuracy", "precision_ppm", "catalogue_conditions",
                 "validation")
THRESHOLD_DEFAULTS = {
    "min_carriers": 3,
    "max_carrier_se_ppm": 0.1,
    "chi2_red_low": 0.3, "chi2_red_high": 3.0,
    "offset_sigma_multiple": 5.0, "offset_abs_limit_hz": 500.0,
    "consistency_k": 3.0,            # my choice, not the procedure's; frozen with the plan
    "max_expanded_uncertainty_ppm": 5.0,
    # Floor on a carrier's standard error. The Cramer-Rao bound is tiny at high SNR and is not the
    # estimator's real error; the floor ties the fit to the bias the synthetic validation can resolve.
    "se_floor_hz": 1.0,
}


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_plan(plan: Dict[str, Any]) -> None:
    missing = [k for k in PLAN_REQUIRED if k not in plan]
    if missing:
        raise CalibrationRefused("PLAN_INCOMPLETE", f"missing {missing}")
    th = plan["thresholds"]
    for k in THRESHOLD_DEFAULTS:
        if k not in th:
            raise CalibrationRefused("PLAN_INCOMPLETE", f"threshold {k!r} must be stated, not defaulted")
    carriers = plan["carriers"]
    if len(carriers) < th["min_carriers"]:
        raise CalibrationRefused("PLAN_TOO_FEW_CARRIERS",
                                 f"{len(carriers)} < {th['min_carriers']}")
    names = [c.get("name") for c in carriers]
    if len(set(names)) != len(names):
        raise CalibrationRefused("PLAN_DUPLICATE_CARRIER", "carrier names must be unique")
    for c in carriers:
        for k in ("name", "operator", "channel", "nominal_hz", "nominal_source",
                  "feature_offset_hz", "search_hz"):
            if k not in c or c[k] in (None, ""):
                raise CalibrationRefused("PLAN_CARRIER_INCOMPLETE", f"{c.get('name')!r}: {k}")
        if not math.isfinite(c["nominal_hz"]) or c["nominal_hz"] <= 0:
            raise CalibrationRefused("PLAN_CARRIER_INCOMPLETE", f"{c['name']}: nominal_hz")
    if len({round((c["nominal_hz"] + c["feature_offset_hz"]) / 1e6) for c in carriers}) < 2:
        raise CalibrationRefused("PLAN_NO_FREQUENCY_SPREAD",
                                 "carriers must span frequency or a and b cannot separate")
    est = plan["estimator"]
    if est.get("name") not in ESTIMATORS:
        raise CalibrationRefused("PLAN_ESTIMATOR_UNKNOWN", f"{est.get('name')!r} not in {ESTIMATORS}")
    if est["name"] == "lte_cp_pss":
        for k in ("min_cp_coeff", "min_pss_vote_share"):
            if k not in est:
                raise CalibrationRefused("PLAN_INCOMPLETE", f"estimator parameter {k!r} must be stated")
        if not isinstance(est.get("pss_definition_confirmed"), bool):
            raise CalibrationRefused("PLAN_INCOMPLETE", "estimator.pss_definition_confirmed(bool) is required")
        # The estimator does not refuse a TDD-position PSS or check the CP type (docs/521_ESTIMATOR_AND_NOMINAL_FREQUENCY_CHECKS.md);
        # the plan must therefore name FDD / normal-CP carriers and the band's duplex mode is checked here.
        for c in carriers:
            for k in ("band", "duplex", "duplex_source", "cp", "cp_source"):
                if c.get(k) in (None, ""):
                    raise CalibrationRefused("PLAN_CARRIER_INCOMPLETE",
                                             f"{c['name']}: LTE carriers need {k!r} (FDD normal-CP only)")
            if isinstance(c["band"], bool) or not isinstance(c["band"], int) or c["band"] not in LTE_BAND_DUPLEX:
                raise CalibrationRefused("PLAN_CARRIER_BAND_UNKNOWN",
                                         f"{c['name']}: band {c['band']!r} is not in the TS 36.101 table this code has; "
                                         f"read Table 5.5-1 for it and extend LTE_BAND_DUPLEX")
            if c["duplex"] != LTE_BAND_DUPLEX[c["band"]]:
                raise CalibrationRefused("PLAN_CARRIER_DUPLEX_MISMATCH",
                                         f"{c['name']}: plan says {c['duplex']!r}, Table 5.5-1 says {LTE_BAND_DUPLEX[c['band']]!r} "
                                         f"for band {c['band']}")
            if LTE_BAND_DUPLEX[c["band"]] != "FDD":
                raise CalibrationRefused("PLAN_CARRIER_NOT_FDD",
                                         f"{c['name']}: band {c['band']} is {LTE_BAND_DUPLEX[c['band']]}; the estimator is FDD only")
            if c["cp"] != "normal":
                raise CalibrationRefused("PLAN_CARRIER_CP_NOT_NORMAL",
                                         f"{c['name']}: cp {c['cp']!r}; the estimator is normal-CP only")
        for c in carriers:
            if c["feature_offset_hz"] != 0:
                raise CalibrationRefused("PLAN_CARRIER_INCOMPLETE",
                                         f"{c['name']}: an LTE carrier is measured at its centre; feature_offset_hz must be 0")
            if plan["n_windows"] != 8 or plan["sample_rate_hz"] != 2048000:
                raise CalibrationRefused("PLAN_WINDOWS", "the LTE estimator needs 2.048 MS/s windows")
    ra = plan["reference_accuracy"]
    if not {"ppm", "source", "confirmed"} <= set(ra) or not isinstance(ra["confirmed"], bool):
        raise CalibrationRefused("PLAN_INCOMPLETE", "reference_accuracy needs ppm, source, confirmed(bool)")
    if plan["sessions_min"] < 3:
        raise CalibrationRefused("PLAN_TOO_FEW_SESSIONS", "the procedure requires at least 3 sessions")
    if plan["n_windows"] != 8:
        raise CalibrationRefused("PLAN_WINDOWS", "windows per visit must be 8, as the sequencer takes them")


def freeze_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and hash. The hash covers the plan and the analysis source, so a plan cannot be
    paired with different code after data exists."""
    check_plan(plan)
    body = {k: v for k, v in plan.items()
            if k not in ("plan_sha256", "analysis_source_sha256", "estimator_source_sha256")}
    body["analysis_source_sha256"] = file_sha256(SOURCE_PATH)
    body["estimator_source_sha256"] = (file_sha256(LTE_SOURCE_PATH)
                                       if plan["estimator"]["name"] == "lte_cp_pss" else None)
    body["plan_sha256"] = sha256_text(canonical(body))
    return body


def verify_frozen_plan(plan: Dict[str, Any]) -> None:
    check_plan(plan)
    claimed = plan.get("plan_sha256")
    body = {k: v for k, v in plan.items() if k != "plan_sha256"}
    if claimed != sha256_text(canonical(body)):
        raise CalibrationRefused("PLAN_HASH_MISMATCH", "the plan was changed after it was frozen")
    if plan.get("analysis_source_sha256") != file_sha256(SOURCE_PATH):
        raise CalibrationRefused("ANALYSIS_SOURCE_CHANGED",
                                 "this analysis code is not the code the plan was frozen against")
    wanted = file_sha256(LTE_SOURCE_PATH) if plan["estimator"]["name"] == "lte_cp_pss" else None
    if plan.get("estimator_source_sha256") != wanted:
        raise CalibrationRefused("ANALYSIS_SOURCE_CHANGED",
                                 "the estimator code is not the code the plan was frozen against")


# --------------------------------------------------------------------- estimator
def _hann(n: int) -> np.ndarray:
    return 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n) / n)


def estimate_line(window: np.ndarray, fs: float,
                  search_hz: Tuple[float, float]) -> Dict[str, float]:
    """Baseband frequency of the strongest narrow line inside search_hz, with its
    Cramer-Rao standard error. Hann window; coarse FFT peak, then local parabolic refinement of ln|S(f)| on the continuous
    windowed spectrum."""
    n = len(window)
    w = _hann(n)
    spec = np.fft.fft(window * w)
    mag = np.abs(spec)
    freqs = np.fft.fftfreq(n, 1.0 / fs)
    lo, hi = search_hz
    idx = np.nonzero((freqs >= lo) & (freqs <= hi))[0]
    if len(idx) < 3:
        raise CalibrationRefused("SEARCH_BAND_EMPTY", f"{search_hz} holds fewer than 3 bins")
    k = int(idx[np.argmax(mag[idx])])
    f_c = float(freqs[k])
    # Refine on the continuous windowed spectrum, not on the FFT bins: interpolating three FFT bins
    # leaves a position-dependent error of about 0.01 bin that does not shrink with SNR.
    xw = window * w
    t = np.arange(n) / fs

    def ln_mag(f: float) -> float:
        return math.log(abs(np.dot(xw, np.exp(-2j * np.pi * f * t))) + 1e-300)

    h = 0.5 * fs / n          # the true peak is within half a bin of the largest bin
    peak_ln = ln_mag(f_c)
    for _ in range(4):
        a, b, c = ln_mag(f_c - h), ln_mag(f_c), ln_mag(f_c + h)
        denom = a - 2.0 * b + c
        delta = 0.5 * (a - c) / denom if denom < 0 else 0.0
        delta = max(-1.0, min(1.0, delta))
        peak_ln = b - 0.25 * (a - c) * delta
        f_c += delta * h
        h *= 0.5
    f_hz = f_c
    # per-sample SNR of the line: tone power from the interpolated peak, noise from the median bin
    power_noise_bin = float(np.median(mag ** 2) / math.log(2.0))
    s1, s2 = float(w.sum()), float((w ** 2).sum())
    rho = (math.exp(2.0 * peak_ln) / s1 ** 2) / (power_noise_bin / s2)
    crb_hz = math.sqrt(6.0 / (rho * n * (n * n - 1.0))) * fs / (2.0 * math.pi) if rho > 0 else float("inf")
    return {"baseband_hz": float(f_hz), "snr_db": 10.0 * math.log10(max(rho, 1e-30)),
            "crb_hz": float(crb_hz)}


def estimate_carrier(plan: Dict[str, Any], window: np.ndarray, carrier: Dict[str, Any]) -> Dict[str, Any]:
    """The plan's estimator, on one window. Refuses (never guesses) when the carrier is not found."""
    fs = plan["sample_rate_hz"]
    est = plan["estimator"]
    if est["name"] == "narrow_line":
        return estimate_line(window, fs, tuple(carrier["search_hz"]))
    import rf_lte_carrier_estimator as lte
    try:
        return lte.estimate_lte_carrier(window, fs, tuple(carrier["search_hz"]), est)
    except lte.LteNotDetected as exc:
        raise CalibrationRefused("CARRIER_NOT_DETECTED", f"{carrier['name']}: {exc}") from exc


# ----------------------------------------------------------------- session fit
def fit_session(carriers: Sequence[Dict[str, float]], th: Dict[str, float]) -> Dict[str, Any]:
    """carriers: [{name, f_nominal_hz, offset_hz, se_hz}]. Weighted fit offset = a + b*f."""
    n = len(carriers)
    f = np.array([c["f_nominal_hz"] for c in carriers], float)
    y = np.array([c["offset_hz"] for c in carriers], float)
    se = np.array([c["se_hz"] for c in carriers], float)
    reasons: List[str] = []
    if n < th["min_carriers"]:
        return {"accepted": False, "reasons": [f"{n} carriers < {th['min_carriers']}"], "n_carriers": n}
    if not np.all(np.isfinite(se)) or np.any(se <= 0):
        return {"accepted": False, "reasons": ["a carrier has no usable standard error"], "n_carriers": n}
    w = 1.0 / se ** 2
    f0 = float((w * f).sum() / w.sum())
    x = f - f0
    sw, swx, swxx = w.sum(), (w * x).sum(), (w * x * x).sum()
    det = sw * swxx - swx ** 2
    a_c = ((swxx * (w * y).sum()) - swx * (w * x * y).sum()) / det
    b = ((sw * (w * x * y).sum()) - swx * (w * y).sum()) / det
    var_a_c, var_b = swxx / det, sw / det
    cov_ab_c = -swx / det
    a = a_c - b * f0
    var_a = var_a_c + f0 ** 2 * var_b - 2 * f0 * cov_ab_c
    resid = y - (a_c + b * x)
    chi2 = float((w * resid ** 2).sum())
    dof = n - 2
    chi2_red = chi2 / dof if dof > 0 else float("nan")
    ppm_i = y / f * 1e6
    se_ppm_i = se / f * 1e6
    if np.any(se_ppm_i > th["max_carrier_se_ppm"]):
        reasons.append("a carrier's standard error exceeds %.3g ppm" % th["max_carrier_se_ppm"])
    if not (th["chi2_red_low"] <= chi2_red <= th["chi2_red_high"]):
        reasons.append(f"reduced chi-square {chi2_red:.3g} outside [{th['chi2_red_low']}, {th['chi2_red_high']}]")
    u_a = math.sqrt(var_a)
    if abs(a) > th["offset_sigma_multiple"] * u_a or abs(a) > th["offset_abs_limit_hz"]:
        reasons.append(f"fixed offset a={a:.3g} Hz fails the pure-ppm test (u(a)={u_a:.3g} Hz)")
    for i in range(n):
        for j in range(i + 1, n):
            lim = th["consistency_k"] * math.hypot(se_ppm_i[i], se_ppm_i[j])
            if abs(ppm_i[i] - ppm_i[j]) > lim:
                reasons.append(f"{carriers[i]['name']} and {carriers[j]['name']} disagree "
                               f"({ppm_i[i]:.3g} vs {ppm_i[j]:.3g} ppm)")
    return {"accepted": not reasons, "reasons": reasons, "n_carriers": n,
            "b_ppm": float(b * 1e6), "u_b_ppm": float(math.sqrt(var_b) * 1e6),
            "a_hz": float(a), "u_a_hz": u_a, "chi2_red": chi2_red,
            "residuals_hz": [float(r) for r in resid],
            "per_carrier_ppm": [{"name": c["name"], "ppm": float(p), "se_ppm": float(s)}
                                for c, p, s in zip(carriers, ppm_i, se_ppm_i)]}


# ------------------------------------------------------------- decision rule
def combine_sessions(sessions: Sequence[Dict[str, Any]], plan: Dict[str, Any]) -> Dict[str, Any]:
    """sessions: [{session_id, conditions:[...], fit:<fit_session result>}].
    Returns the decision. A proposal exists only if every gate passes."""
    th, ra = plan["thresholds"], plan["reference_accuracy"]
    blockers: List[str] = []
    fits = [s["fit"] for s in sessions]
    if len(sessions) < plan["sessions_min"]:
        blockers.append(f"{len(sessions)} sessions < {plan['sessions_min']}")
    failed = [s["session_id"] for s in sessions if not s["fit"].get("accepted")]
    if failed:
        blockers.append(f"sessions failed acceptance: {failed}")
    out: Dict[str, Any] = {"n_sessions": len(sessions), "blockers": blockers,
                           "reference_ppm_current": 100, "reference_ppm_proposed": None,
                           "amendment_permitted": False}
    if failed or not fits or any("b_ppm" not in f for f in fits):
        return out
    b = np.array([f["b_ppm"] for f in fits])
    m = float(b.mean())
    u_stat = math.sqrt(sum(f["u_b_ppm"] ** 2 for f in fits)) / len(fits)
    u_session = float(b.std(ddof=1)) if len(b) > 1 else float("nan")
    u_ref = float(ra["ppm"])
    U = 2.0 * math.sqrt(u_stat ** 2 + u_session ** 2 + u_ref ** 2)
    d = float(b.max() - b.min())
    sampled = {c for s in sessions for c in s.get("conditions", [])}
    unsampled = sorted(set(plan["catalogue_conditions"]) - sampled)
    out.update({"mean_ppm": m, "u_stat_ppm": u_stat, "u_session_ppm": u_session, "u_ref_ppm": u_ref,
                "expanded_uncertainty_ppm": U, "drift_allowance_ppm": d,
                "session_b_ppm": [float(x) for x in b], "conditions_unsampled": unsampled})
    if plan["estimator"]["name"] == "lte_cp_pss" and not plan["estimator"]["pss_definition_confirmed"]:
        blockers.append("the LTE PSS definition is not confirmed against the normative 3GPP text")
    if not ra["confirmed"]:
        blockers.append("reference accuracy %.3g ppm is not confirmed against %s"
                        % (u_ref, ra["source"]))
    if not math.isfinite(u_session):
        blockers.append("session scatter is not defined")
    if U > th["max_expanded_uncertainty_ppm"]:
        blockers.append(f"expanded uncertainty {U:.3g} ppm > {th['max_expanded_uncertainty_ppm']}")
    if d > U:
        blockers.append(f"session means disagree by {d:.3g} ppm, more than U={U:.3g}")
    if unsampled:
        blockers.append(f"catalogue conditions not sampled: {unsampled}; drift allowance not established")
    if not blockers:
        step = plan["precision_ppm"]
        proposed = math.ceil((abs(m) + U + d) / step - 1e-12) * step
        out["reference_ppm_proposed"] = max(proposed, step)
        out["amendment_permitted"] = True
        out["note"] = ("a proposal only: tightening reference_ppm needs a new frozen authorization and an "
                       "amendment note; the reference is not corrected by the mean inside the catalogue")
    return out


# ---------------------------------------------------------------- synthetic
def synthetic_window(n: int, fs: float, line_hz: float, snr_db: float, rng: np.random.Generator,
                     fading: bool) -> np.ndarray:
    t = np.arange(n) / fs
    amp = 1.0
    sig = np.exp(2j * np.pi * line_hz * t + 1j * rng.uniform(0, 2 * np.pi))
    if fading:   # slow amplitude wander plus a weak echo 60 kHz away (frequency-selective)
        env = 1.0 + 0.3 * np.cumsum(rng.standard_normal(n)) / np.sqrt(n)
        sig = sig * np.clip(env, 0.2, None) + 0.1 * np.exp(2j * np.pi * (line_hz + 60e3) * t)
    sigma2 = amp ** 2 / (10 ** (snr_db / 10.0))
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * math.sqrt(sigma2 / 2)
    return (sig + noise).astype(np.complex128)


def validate_estimator(plan: Dict[str, Any], n: int = 524288) -> Dict[str, Any]:
    """Bias of `estimate_line` on synthetic data with a known injected offset, against the bias
    limit in the plan (ppm at the lowest carrier). Run BEFORE any real data is analysed."""
    v = plan["validation"]
    fs = plan["sample_rate_hz"]
    if plan["estimator"]["name"] == "lte_cp_pss":
        return _validate_lte(plan)
    rng = np.random.default_rng(v["seed"])
    f_min = min(c["nominal_hz"] + c["feature_offset_hz"] for c in plan["carriers"])
    limit_hz = v["bias_limit_ppm"] * 1e-6 * f_min
    cells = []
    for snr in v["snr_db"]:
        for fading in (False, True):
            errs, crbs = [], []
            for _ in range(v["trials"]):
                true = rng.uniform(*v["line_hz_range"])
                r = estimate_line(synthetic_window(n, fs, true, snr, rng, fading), fs,
                                  (v["line_hz_range"][0] - 2e3, v["line_hz_range"][1] + 2e3))
                errs.append(r["baseband_hz"] - true)
                crbs.append(r["crb_hz"])
            errs = np.array(errs)
            se_mean = float(errs.std(ddof=1) / math.sqrt(len(errs)))
            cells.append({"snr_db": snr, "fading": fading, "bias_hz": float(errs.mean()),
                          "bias_se_hz": se_mean, "rms_hz": float(np.sqrt((errs ** 2).mean())),
                          "mean_crb_hz": float(np.mean(crbs)),
                          "passes": bool(abs(errs.mean()) + 2 * se_mean < limit_hz)})
    return {"bias_limit_hz": limit_hz, "bias_limit_ppm": v["bias_limit_ppm"],
            "at_lowest_carrier_hz": f_min, "n_window_samples": n, "cells": cells,
            "passes": all(c["passes"] for c in cells)}


def _validate_lte(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Bias of the LTE estimator on a synthetic 5 MHz LTE downlink with known carrier offset. The
    'fading' cells add two-tap multipath and an 80 ppm receiver sample-clock error together, the
    combination that produced the largest bias in development. Uses the same PSS definition as the
    estimator, so it cannot detect an error in that definition (see rf_lte_carrier_estimator)."""
    import rf_lte_carrier_estimator as lte
    v = plan["validation"]
    n = 524288
    rng = np.random.default_rng(v["seed"])
    f_min = min(c["nominal_hz"] for c in plan["carriers"])
    limit_hz = v["bias_limit_ppm"] * 1e-6 * f_min
    bound = max(abs(x) for c in plan["carriers"] for x in c["search_hz"])
    cells = []
    for snr in v["snr_db"]:
        for hard in (False, True):
            errs, crbs, refused = [], [], 0
            for _ in range(v["trials"]):
                true = rng.uniform(-0.8 * bound, 0.8 * bound)
                clean = lte.synthetic_lte_clean(n, true, rng, multipath=hard,
                                                sample_clock_ppm=80.0 if hard else 0.0)
                try:
                    r = estimate_carrier(plan, lte.add_noise(clean, snr, rng),
                                         {"name": "validation", "search_hz": [-bound, bound]})
                except CalibrationRefused:
                    refused += 1
                    continue
                errs.append(r["baseband_hz"] - true); crbs.append(r["crb_hz"])
            if len(errs) < 2:
                cells.append({"snr_db": snr, "fading": hard, "refused": refused, "passes": False})
                continue
            errs = np.array(errs)
            se_mean = float(errs.std(ddof=1) / math.sqrt(len(errs)))
            cells.append({"snr_db": snr, "fading": hard, "bias_hz": float(errs.mean()),
                          "bias_se_hz": se_mean, "rms_hz": float(np.sqrt((errs ** 2).mean())),
                          "mean_crb_hz": float(np.mean(crbs)), "refused": refused,
                          "passes": bool(refused == 0 and abs(errs.mean()) + 2 * se_mean < limit_hz)})
    return {"bias_limit_hz": limit_hz, "bias_limit_ppm": v["bias_limit_ppm"], "at_lowest_carrier_hz": f_min,
            "n_window_samples": n, "cells": cells, "passes": all(c["passes"] for c in cells)}


# ---------------------------------------------------------------- manifest
def write_manifest(directory: str, name: str = "MANIFEST.json") -> str:
    entries = {}
    for dp, dn, fn in os.walk(directory):
        dn.sort()
        for f in sorted(fn):
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, directory)
            if rel == name:
                continue
            entries[rel] = {"sha256": file_sha256(p), "bytes": os.path.getsize(p)}
    path = os.path.join(directory, name)
    with open(path, "w") as handle:
        json.dump({"files": entries, "analysis_source_sha256": file_sha256(SOURCE_PATH)},
                  handle, indent=2, sort_keys=True)
    return path


# ------------------------------------------------------------------- analysis
def analyze_session(session_dir: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    """Re-derive every number from the spooled bytes. Verifies each spool file's SHA-256 first."""
    verify_frozen_plan(plan)
    with open(os.path.join(session_dir, "session.json")) as handle:
        session = json.load(handle)
    if session["plan_sha256"] != plan["plan_sha256"]:
        raise CalibrationRefused("SESSION_PLAN_MISMATCH", "session was captured under a different plan")
    by_name = {c["name"]: c for c in plan["carriers"]}
    fs = plan["sample_rate_hz"]
    carriers, per_window = [], []
    for visit in session["visits"]:
        c = by_name[visit["carrier"]]
        with open(os.path.join(session_dir, visit["spool"]), "rb") as handle:
            raw = handle.read()
        if hashlib.sha256(raw).hexdigest() != visit["sha256"] or len(raw) != visit["bytes"]:
            raise CalibrationRefused("SPOOL_CORRUPT", f"{visit['spool']}")
        wb = visit["bytes"] // plan["n_windows"]
        ests = []
        for i in range(plan["n_windows"]):
            u8 = np.frombuffer(raw[i * wb:(i + 1) * wb], np.uint8).astype(np.float64)
            iq = (u8[0::2] - 127.5) + 1j * (u8[1::2] - 127.5)
            ests.append(estimate_carrier(plan, iq, c))
        truth = c["nominal_hz"] + c["feature_offset_hz"]
        offs = np.array([visit["lo_hz"] + e["baseband_hz"] - truth for e in ests])
        n = len(offs)
        se = max(float(offs.std(ddof=1) / math.sqrt(n)),
                 float(np.mean([e["crb_hz"] for e in ests]) / math.sqrt(n)),
                 float(plan["thresholds"]["se_floor_hz"]))
        # The regressor is the LO (the channel frequency the receiver was tuned to): the reference
        # error scales the LO, so measured - true = -e * LO. The line's own offset from the LO is
        # read in baseband and is only touched by the sample-clock scaling bounded below.
        carriers.append({"name": c["name"], "f_nominal_hz": float(visit["lo_hz"]),
                         "offset_hz": float(offs.mean()), "se_hz": se,
                         # bound on the shared-clock scaling of the baseband reading, at the
                         # accepted uncalibrated 100 ppm
                         "max_baseband_scale_bias_hz": max(abs(e["baseband_hz"]) for e in ests) * 100e-6})
        per_window.append({"carrier": c["name"], "windows": ests, "offsets_hz": [float(o) for o in offs]})
    return {"session_id": session["session_id"], "conditions": session["conditions"],
            "carriers": carriers, "per_window": per_window,
            "fit": fit_session(carriers, plan["thresholds"])}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze"); f.add_argument("plan"); f.add_argument("out")
    v = sub.add_parser("validate-estimator"); v.add_argument("frozen_plan"); v.add_argument("out")
    a = sub.add_parser("analyze"); a.add_argument("frozen_plan"); a.add_argument("out")
    a.add_argument("session_dirs", nargs="+")
    args = ap.parse_args(argv)
    if args.cmd == "freeze":
        with open(args.plan) as h:
            frozen = freeze_plan(json.load(h))
        with open(args.out, "w") as h:
            json.dump(frozen, h, indent=2, sort_keys=True)
        print("plan_sha256", frozen["plan_sha256"])
        return 0
    with open(args.frozen_plan) as h:
        plan = json.load(h)
    verify_frozen_plan(plan)
    if args.cmd == "validate-estimator":
        res = validate_estimator(plan)
        with open(args.out, "w") as h:
            json.dump(res, h, indent=2, sort_keys=True)
        print("ESTIMATOR VALIDATION", "PASSED" if res["passes"] else "FAILED")
        return 0 if res["passes"] else 1
    sessions = [analyze_session(d, plan) for d in args.session_dirs]
    decision = combine_sessions([{"session_id": s["session_id"], "conditions": s["conditions"],
                                  "fit": s["fit"]} for s in sessions], plan)
    with open(args.out, "w") as h:
        json.dump({"plan_sha256": plan["plan_sha256"], "sessions": sessions, "decision": decision},
                  h, indent=2, sort_keys=True)
    print(json.dumps({k: decision[k] for k in ("amendment_permitted", "reference_ppm_proposed", "blockers")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
