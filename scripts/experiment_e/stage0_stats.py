#!/usr/bin/env python3
"""Experiment E Stage 0: byte-stream Hamming weight / toggle statistics.
usage: stage0_stats.py LABEL=file.u8 [LABEL=file.u8 ...]   |  --selftest
Stdlib only. Windows of 480000 bytes (0.1 s at 2.4 MS/s, 2 B/sample)."""
import sys, math, random, statistics as st
W = 480000
HW = bytes(bin(i).count('1') for i in range(256))

def win_stats(buf):
    out = []
    for o in range(0, len(buf) - W + 1, W):
        w = buf[o:o+W]
        hw = sum(w.translate(HW)) / W                    # mean bits set per byte
        x = int.from_bytes(w[:-1], 'big') ^ int.from_bytes(w[1:], 'big')
        tg = bin(x).count('1') / (8 * (W - 1))           # bit toggle rate between adjacent bytes
        out.append((hw, tg))
    return out

def welch(a, b):
    ma, mb = st.mean(a), st.mean(b)
    va, vb = st.variance(a), st.variance(b)
    se = math.sqrt(va/len(a) + vb/len(b))
    return (ma - mb) / se if se else float('nan')

def report(data):
    labels = list(data)
    for l in labels:
        s = win_stats(data[l])
        hw, tg = [p[0] for p in s], [p[1] for p in s]
        print(f"{l:20s} n={len(s):4d} HW={st.mean(hw):.5f}+-{st.stdev(hw):.5f} toggle={st.mean(tg):.5f}+-{st.stdev(tg):.5f}")
        data[l] = (hw, tg)
    base = labels[0]
    for l in labels[1:]:
        print(f"{l} vs {base}: t(HW)={welch(data[l][0], data[base][0]):+.1f}  t(toggle)={welch(data[l][1], data[base][1]):+.1f}")

def selftest():
    random.seed(1)
    def gen(sigma, n=W*20):
        return bytes(min(255, max(0, int(random.gauss(127.5, sigma)))) for _ in range(n))
    report({"sigma=10": gen(10), "sigma=10b": gen(10), "sigma=40": gen(40)})

if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]: selftest()
    else: report({a.split('=')[0]: open(a.split('=',1)[1],'rb').read() for a in sys.argv[1:]})
