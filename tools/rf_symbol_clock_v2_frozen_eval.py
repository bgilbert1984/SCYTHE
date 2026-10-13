#!/usr/bin/env python3
"""Frozen-protocol evaluation of symbol-clock v2 (detrended) against v1.

Reads docs/research/symbol_clock_v2/protocol.json, refuses to run unless it says
``"frozen": true``, records the protocol's and its own sha256 in the output, and applies
the gates exactly as written there.  Thresholds are read from the protocol; there is no
tuning path.  Production code is imported unchanged.

    python3 tools/rf_symbol_clock_v2_frozen_eval.py --out results.json
    python3 tools/rf_symbol_clock_v2_frozen_eval.py --smoke        # tiny, gates not meaningful
"""
from __future__ import annotations

import argparse, hashlib, json, math, os, sys
from multiprocessing import Pool

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)
PROTOCOL = os.path.join(ROOT, "docs", "research", "symbol_clock_v2", "protocol.json")

RATE_RAW = 2_048_000.0
COUNT = 524_288
CARRIER = np.exp(2j * np.pi * 400_000.0 * np.arange(COUNT) / RATE_RAW)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---------------------------------------------------------------- signal generation
def _white(seed):
    rng = np.random.default_rng(seed)
    return rng, rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT)


def _shape(white, amp):
    out = np.fft.ifft(np.fft.fft(white) * amp)
    return out / np.sqrt(np.mean(np.abs(out) ** 2))


def make_noise(family, width_hz, seed):
    import test_rf_symbol_clock as fx
    if family == "raised":
        return fx._band_limited_noise(width_hz, seed=seed)
    rng, white = _white(seed)
    f = np.fft.fftfreq(COUNT, 1.0 / RATE_RAW)
    floor = 0.01 * (rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT))
    if family == "gaussian":
        s2 = (width_hz / 2.0) ** 2 / (2.0 * np.log(100.0))
        amp = np.exp(-(f ** 2) / (4.0 * s2))
    elif family == "brick":
        amp = (np.abs(f) <= width_hz / 2.0).astype(float)
    elif family == "butterworth4":
        amp = np.sqrt(1.0 / (1.0 + (np.abs(f) / (width_hz / 2.0)) ** 8))
    elif family == "two_band":
        w = width_hz / 2.0
        amp = ((np.abs(np.abs(f) - width_hz / 2.0) <= w / 2.0)).astype(float)
    elif family == "am_random_walk":
        base = fx._band_limited_noise(width_hz, seed=seed).astype(np.complex128)
        walk = np.cumsum(rng.normal(0, 1, COUNT))
        k = 20_000
        walk = np.convolve(walk, np.ones(k) / k, mode="same")
        walk = (walk - walk.mean()) / (walk.std() + 1e-12)
        env = np.clip(1.0 + 0.5 * walk, 0.1, None)
        out = base * env
        out = out / np.sqrt(np.mean(np.abs(out) ** 2))
        return out.astype(np.complex64)
    else:
        raise ValueError(family)
    return (_shape(white, amp) + floor).astype(np.complex64)


def make_clock(rate_hz, beta, sigma, seed):
    import test_rf_symbol_clock as fx
    rng = np.random.default_rng(seed)
    sps = int(round(RATE_RAW / rate_hz))
    n_sym = int(np.ceil(COUNT / sps)) + 32
    sym = (rng.integers(0, 2, n_sym) * 2 - 1) + 1j * (rng.integers(0, 2, n_sym) * 2 - 1)
    up = np.zeros(n_sym * sps, dtype=complex)
    up[::sps] = sym
    sig = np.convolve(up, fx._raised_cosine(sps, beta=beta), mode="same")[:COUNT]
    sig = sig + sigma * (rng.normal(0, 1, COUNT) + 1j * rng.normal(0, 1, COUNT))
    return sig.astype(np.complex64), RATE_RAW / sps


# ---------------------------------------------------------------- measurement
def channelize_product(baseband):
    import test_rf_symbol_clock as fx
    return fx._real_channelization((baseband * CARRIER).astype(np.complex64))


def verdicts(result, ring):
    import rf_symbol_clock as v1
    import rf_symbol_clock_v2 as v2
    a, b = v1.detect(result, ring=ring), v2.detect(result, ring=ring)
    return {"samples": a.sample_count,
            "v1": [a.outcome, a.detection_statistic, a.symbol_rate_hz, a.cyclic_resolution_hz],
            "v2": [b.outcome, b.detection_statistic, b.symbol_rate_hz, b.cyclic_resolution_hz]}


def run_task(task):
    kind = task["kind"]
    try:
        if kind in ("adverse", "novel"):
            base = make_noise(task["family"], task["width_hz"], task["seed"])
            truth = None
        elif kind == "sensitivity":
            base, truth = make_clock(task["rate_hz"], task["beta"], task["sigma"], task["seed"])
        elif kind == "edge":
            return run_edge(task)
        else:
            raise ValueError(kind)
        result, ring = channelize_product(base)
        if result.product.outcome != "CHANNELIZED":
            return {**task, "status": "CHANNELIZER_" + result.product.outcome}
        out = verdicts(result, ring)
        status = ("INSUFFICIENT_WINDOW" if out["v1"][0] == "INSUFFICIENT_WINDOW" else "ANALYSED")
        return {**task, "status": status, "truth_rate_hz": truth, **out}
    except Exception as exc:  # recorded, never silently dropped
        return {**task, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}


def run_edge(task):
    import rf_symbol_clock as v1
    import rf_symbol_clock_v2 as v2
    from rf_detector_contract import admit_for_detection
    if task["what"] == "clock":
        base, _ = make_clock(task["rate_hz"], 0.35, 0.02, task["seed"])
    else:
        base = make_noise(task["family"], task["width_hz"], task["seed"])
    result, ring = channelize_product(base)
    if result.product.outcome != "CHANNELIZED":
        return {**task, "status": "CHANNELIZER_" + result.product.outcome}
    rate = float(admit_for_detection(result, ring=ring)["output_sample_rate_hz"])
    s = result.samples
    rows = {}
    for n in task["lengths"]:
        if s.size < n:
            rows[str(n)] = None
            continue
        rows[str(n)] = [v1.squared_envelope_statistic(s[:n], rate)[0],
                        v2.detrended_statistic(s[:n], rate)[0]]
    return {**task, "status": "ANALYSED", "available_samples": int(s.size), "direct": rows}


# ---------------------------------------------------------------- statistics
def wilson_upper(k, n, z=1.959964):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c + h) / d


def auc(pos, neg):
    if not pos or not neg:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks, i = {}, 0
    vals = [v for v, _ in allv]
    r = np.empty(len(vals))
    j = 0
    while j < len(vals):
        k = j
        while k + 1 < len(vals) and vals[k + 1] == vals[j]:
            k += 1
        r[j:k + 1] = (j + k) / 2 + 1
        j = k + 1
    rp = sum(r[idx] for idx, (_, lab) in enumerate(allv) if lab == 1)
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def crossed(v):  # v = [outcome, stat, ...]
    return v[0] == "SYMBOL_CLOCK_LIKE_FEATURE"


def evaluate(proto, rows):
    ok = [r for r in rows if r["status"] == "ANALYSED"]
    th = {m: proto["methods"][m]["threshold"] for m in ("v1", "v2")}
    out = {"counts": {}, "G1": {}, "G2": {}, "G3": {}, "G4": {}, "diagnostics": {}}
    for r in rows:
        out["counts"][r["status"]] = out["counts"].get(r["status"], 0) + 1

    # G1
    cells = {}
    for r in ok:
        if r["kind"] == "adverse":
            cells.setdefault((r["family"], r["width_hz"]), []).append(r)
    g1_pass = True
    for key, rs in sorted(cells.items()):
        c1 = sum(crossed(r["v1"]) for r in rs); c2 = sum(crossed(r["v2"]) for r in rs)
        up = wilson_upper(c2, len(rs))
        cell_ok = c2 == 0 and up is not None and up <= 0.05
        g1_pass &= cell_ok
        out["G1"][f"{key[0]}@{int(key[1])}"] = {"n": len(rs), "v1_crossings": c1, "v2_crossings": c2,
                                                "v2_wilson_upper": round(up, 4), "pass": cell_ok,
                                                "v2_stat_max": max(r["v2"][1] for r in rs if r["v2"][1] is not None)}
    expected = len(proto["adverse_noise"]["masks"]) * len(proto["adverse_noise"]["widths_hz"])
    g1_pass &= len(cells) == expected
    out["G1_pass"] = bool(g1_pass)

    # G2
    sens = {}
    for r in ok:
        if r["kind"] == "sensitivity":
            sens.setdefault((r["rate_hz"], r["beta"], r["sigma"]), []).append(r)
    neg1 = [r["v1"][1] for rs in cells.values() for r in rs if r["v1"][1] is not None]
    neg2 = [r["v2"][1] for rs in cells.values() for r in rs if r["v2"][1] is not None]
    g2_pass = True
    for key, rs in sorted(sens.items()):
        d1 = sum(crossed(r["v1"]) for r in rs); d2 = sum(crossed(r["v2"]) for r in rs)
        n = len(rs)
        binding = d1 / n >= 0.9
        cell_ok = (d2 / n >= 0.9) if binding else True
        g2_pass &= cell_ok
        res = rs[0]["v1"][3] or 0

        def right_rate(idx):
            return sum(1 for r in rs if crossed(r[idx]) and r[idx][2] is not None and
                       abs(r[idx][2] - r["truth_rate_hz"]) <= 5 * (r[idx][3] or 1e9)) 
        out["G2"][f"rate={int(key[0])},beta={key[1]},sigma={key[2]}"] = {
            "n": n, "v1_detect": d1, "v2_detect": d2, "binding": binding, "pass": cell_ok,
            "v1_correct_rate": right_rate("v1"), "v2_correct_rate": right_rate("v2"),
            "auc_v1": auc([r["v1"][1] for r in rs if r["v1"][1] is not None], neg1),
            "auc_v2": auc([r["v2"][1] for r in rs if r["v2"][1] is not None], neg2)}
    g2_pass &= bool(sens)
    out["G2_pass"] = bool(g2_pass)

    # G3
    edge = [r for r in ok if r["kind"] == "edge"]
    g3_pass = True
    for n in proto["edge_window"]["lengths"]:
        nz1 = nz2 = nn = 0
        cl1 = cl2 = cn = 0
        for r in edge:
            d = r["direct"].get(str(n))
            if d is None:
                continue
            c1 = d[0] is not None and d[0] >= th["v1"]; c2 = d[1] is not None and d[1] >= th["v2"]
            if r["what"] == "noise":
                nn += 1; nz1 += c1; nz2 += c2
            else:
                cn += 1; cl1 += c1; cl2 += c2
        cell_ok = (nz2 == 0) and (cl2 >= cl1 - 3) and nn > 0 and cn > 0
        g3_pass &= cell_ok
        out["G3"][str(n)] = {"noise_n": nn, "v1_noise_cross": nz1, "v2_noise_cross": nz2,
                             "clock_n": cn, "v1_clock_det": cl1, "v2_clock_det": cl2, "pass": cell_ok}
    out["G3_pass"] = bool(g3_pass)

    # G4 (reported)
    nov = {}
    for r in ok:
        if r["kind"] == "novel":
            nov.setdefault((r["family"], r["width_hz"]), []).append(r)
    block = False
    for key, rs in sorted(nov.items()):
        c1 = sum(crossed(r["v1"]) for r in rs); c2 = sum(crossed(r["v2"]) for r in rs)
        block |= c2 > 0
        out["G4"][f"{key[0]}@{int(key[1])}"] = {"n": len(rs), "v1_crossings": c1, "v2_crossings": c2,
                                               "v2_stat_max": max(r["v2"][1] for r in rs if r["v2"][1] is not None)}
    out["G4_blocking_finding"] = bool(block)
    out["decision"] = ("SUPPORTED FOR FURTHER SHADOW VALIDATION"
                       if (g1_pass and g2_pass and g3_pass) else "NOT SUPPORTED")
    out["decision_note"] = ("NOT A REPLACEMENT RECOMMENDATION; no production threshold or method is changed. "
                            + ("G4 BLOCKING FINDING PRESENT. " if block else ""))
    return out


# ---------------------------------------------------------------- driver
def build_tasks(p, smoke):
    base = p["seeds"]["holdout_base"]
    tasks = []
    A, N, S, E = p["adverse_noise"], p["novel_noise"], p["sensitivity"], p["edge_window"]
    k = (lambda n: 2) if smoke else (lambda n: n)
    for fam in A["masks"]:
        for w in A["widths_hz"][:2] if smoke else A["widths_hz"]:
            for s in range(k(A["seeds_per_cell"])):
                tasks.append(dict(kind="adverse", family=fam, width_hz=float(w), seed=base + s))
    for fam in N["families"]:
        for w in N["widths_hz"][:1] if smoke else N["widths_hz"]:
            for s in range(k(N["seeds_per_cell"])):
                tasks.append(dict(kind="novel", family=fam, width_hz=float(w), seed=base + s))
    for r in S["symbol_rates_hz"][:2] if smoke else S["symbol_rates_hz"]:
        for b in S["rolloffs"]:
            for sg in S["noise_sigma"]:
                for s in range(k(S["seeds_per_cell"])):
                    tasks.append(dict(kind="sensitivity", rate_hz=float(r), beta=b, sigma=sg, seed=base + s))
    for fam, w in E["noise_cases"]:
        for s in range(k(E["seeds_per_cell"])):
            tasks.append(dict(kind="edge", what="noise", family=fam, width_hz=float(w), seed=base + s,
                              lengths=E["lengths"]))
    for s in range(k(E["seeds_per_cell"])):
        tasks.append(dict(kind="edge", what="clock", rate_hz=float(E["clock_rate_hz"]), seed=base + s,
                          lengths=E["lengths"]))
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    proto = json.load(open(PROTOCOL))
    if proto.get("frozen") is not True:
        raise SystemExit("protocol is not frozen; refusing to run")
    tasks = build_tasks(proto, a.smoke)
    print(f"tasks={len(tasks)} protocol_sha256={sha(PROTOCOL)}", flush=True)
    rows = []
    with Pool(proto.get("parallelism", 4)) as pool:
        for i, r in enumerate(pool.imap_unordered(run_task, tasks, chunksize=4), 1):
            rows.append(r)
            if i % 200 == 0:
                print(f"  {i}/{len(tasks)}", flush=True)
    rows.sort(key=lambda r: json.dumps({k: r[k] for k in ("kind", "seed") if k in r}, sort_keys=True))
    ev = evaluate(proto, rows)
    doc = {"schema": "scythe.symbol-clock-v2-frozen-eval.v1", "smoke": a.smoke,
           "protocol_sha256": sha(PROTOCOL), "runner_sha256": sha(__file__),
           "code_sha256": {f: sha(os.path.join(ROOT, f)) for f in
                           ("rf_symbol_clock.py", "rf_symbol_clock_v2.py")},
           "numpy": np.__version__, "evaluation": ev, "trials": rows}
    text = json.dumps(doc, indent=1, default=str)
    if a.out:
        open(a.out, "w").write(text)
    brief = {k: v for k, v in ev.items()}
    print(json.dumps(brief, indent=1, default=str))


if __name__ == "__main__":
    main()
