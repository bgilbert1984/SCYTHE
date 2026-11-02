#!/usr/bin/env python3
"""Experiment E Stage 0: output-byte statistics for an RTL2832U-style u8 I/Q stream.

usage: stage0_stats.py [--block-s 10] [--rate 2400000] LABEL=file.u8 [...]   |  --selftest

The first LABEL is the baseline. Stdlib only; files are streamed in chunks.

Statistics per 0.1 s window (2 B/sample, I then Q):
  hw        mean bits set per byte
  tog_iq    bit-toggle rate I->Q within a sample
  tog_qi    bit-toggle rate Q->next I between samples
  tog_ii    bit-toggle rate I->next I
  clip      fraction of bytes at 0 or 255

Windows are NOT independent, so inference is done on non-overlapping blocks
(default 10 s) of window means. A verdict needs >= MIN_BLOCKS blocks per condition.

Gate semantics: "not demonstrated" means THESE output-byte statistics showed no
response at the tested sensitivity. It does not rule out other leakage paths.
"""
import sys, math, random, statistics as st

MIN_BLOCKS = 8
# two-sided 95% Student t critical values by df (conservative: use floor df)
TCRIT = {1:12.71,2:4.30,3:3.18,4:2.78,5:2.57,6:2.45,7:2.36,8:2.31,9:2.26,10:2.23,
         12:2.18,15:2.13,20:2.09,30:2.04,60:2.00}
STATS = ("hw", "tog_iq", "tog_qi", "tog_ii", "clip")
HW = bytes(bin(i).count("1") for i in range(256))
CLIPMAP = bytes(1 if i in (0, 255) else 0 for i in range(256))

def tcrit(df):
    ks = [k for k in TCRIT if k <= df]
    return TCRIT[max(ks)] if ks else float("inf")

def _tog(a, b):
    n = min(len(a), len(b))
    if n == 0: return float("nan")
    return bin(int.from_bytes(a[:n], "big") ^ int.from_bytes(b[:n], "big")).count("1") / (8 * n)

def window_stats(w):
    i, q = w[0::2], w[1::2]
    return {
        "hw": sum(w.translate(HW)) / len(w),
        "tog_iq": _tog(i, q),
        "tog_qi": _tog(q, i[1:]),
        "tog_ii": _tog(i[:-1], i[1:]),
        "clip": sum(w.translate(CLIPMAP)) / len(w),
    }

def stream_windows(fh, wbytes):
    while True:
        w = fh.read(wbytes)
        if len(w) < wbytes: return          # drop partial tail
        yield window_stats(w)

def block_means(wins, per_block):
    blocks = []
    for k in range(0, len(wins) - per_block + 1, per_block):
        chunk = wins[k:k+per_block]
        blocks.append({s: st.mean(x[s] for x in chunk) for s in STATS})
    return blocks

def compare(base, other, s):
    a, b = [x[s] for x in base], [x[s] for x in other]
    na, nb = len(a), len(b)
    va, vb = st.variance(a), st.variance(b)
    diff = st.mean(b) - st.mean(a)
    se = math.sqrt(va/na + vb/nb)
    if se == 0: return diff, float("nan"), float("nan"), (diff, diff)
    # Welch-Satterthwaite df
    df = (va/na + vb/nb)**2 / ((va/na)**2/(na-1) + (vb/nb)**2/(nb-1))
    tc = tcrit(int(df))
    sp = math.sqrt((va + vb) / 2)
    d = diff / sp if sp else float("nan")
    return diff, diff/se, d, (diff - tc*se, diff + tc*se)

def report(data, rate=2_400_000, block_s=10.0):
    wbytes = int(rate * 0.1) * 2
    per_block = max(1, int(round(block_s / 0.1)))
    res = {}
    for label, path in data.items():
        with open(path, "rb") as fh:
            wins = list(stream_windows(fh, wbytes))
        blocks = block_means(wins, per_block)
        res[label] = blocks
        print(f"{label:16s} windows={len(wins):6d} blocks={len(blocks):4d}")
        if len(blocks) < MIN_BLOCKS:
            print(f"  WARNING: <{MIN_BLOCKS} blocks; no verdict possible. Capture longer.")
    labels = list(res); base = labels[0]
    for l in labels[1:]:
        if min(len(res[base]), len(res[l])) < MIN_BLOCKS:
            print(f"{l} vs {base}: insufficient data"); continue
        print(f"\n{l} vs {base}")
        for s in STATS:
            diff, t, d, (lo, hi) = compare(res[base], res[l], s)
            v = "RESPONSE" if (lo > 0 or hi < 0) else "not demonstrated"
            print(f"  {s:7s} diff={diff:+.6f} 95%CI[{lo:+.6f},{hi:+.6f}] t={t:+.1f} d={d:+.2f}  {v}")
        if res[l] and res[base]:
            print(f"  clip fraction base={st.mean(x['clip'] for x in res[base]):.5f} "
                  f"{l}={st.mean(x['clip'] for x in res[l]):.5f}  (high => ADC saturation, interpret with care)")

def selftest():
    import tempfile, os
    random.seed(1)
    wb = 480000
    def gen(sigma, nwin):
        return bytes(min(255, max(0, int(random.gauss(127.5, sigma)))) for _ in range(wb * nwin))
    d = tempfile.mkdtemp(); paths = {}
    for name, sg in (("sigma10", 10), ("sigma10b", 10), ("sigma40", 40)):
        p = os.path.join(d, name); open(p, "wb").write(gen(sg, 80)); paths[name] = p
    report(paths, block_s=1.0)
    # short input must not crash
    p = os.path.join(d, "short"); open(p, "wb").write(b"\x00" * 1000)
    report({"sigma10": paths["sigma10"], "short": p}, block_s=1.0)

if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]: selftest(); sys.exit()
    rate, block_s = 2_400_000, 10.0
    while args and args[0].startswith("--"):
        if args[0] == "--rate": rate = int(args[1])
        elif args[0] == "--block-s": block_s = float(args[1])
        else: sys.exit(__doc__)
        args = args[2:]
    if len(args) < 2: sys.exit(__doc__)
    report({a.split("=")[0]: a.split("=", 1)[1] for a in args}, rate, block_s)
