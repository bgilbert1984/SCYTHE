"""Uniform recompute of the canary-1 gate and a floor-rise table over every 8-tuning, 29.0 dB capture set.
One code version for every set: rf_canary_gate.py / rf_spur_catalogue_sequencer.py from /home/spectrcyde/SCYTHE-h5 (main ac4d5bd3 or later).
Floor = median of the averaged 4096-point Hann power spectrum (dB, file's own u8 scale). Rise = set's median floor at a tuning minus the pooled load-fitted median floor at that tuning."""
import sys, json, glob, os, datetime, hashlib, numpy as np
from multiprocessing import Pool
sys.path.insert(0, '/home/spectrcyde/SCYTHE-h5')
import rf_spur_catalogue_sequencer as seq, rf_canary_gate as g
W = seq.WINDOW_BYTES; N = 4096; WIN = np.hanning(N)
H = '/home/spectrcyde'
TUN = [0, 8, 16, 24, 32, 40, 48, 56]
def floor_db(x):
    acc = np.zeros(N); k = 0
    for s in range(0, len(x) - N + 1, N * 4):
        acc += np.abs(np.fft.fft(x[s:s + N] * WIN)) ** 2; k += 1
    return float(np.median(10 * np.log10(acc / k + 1e-9)))
def one(args):
    d, c = args
    raw = open(f'{d}/{c["file"]}', 'rb').read()
    wb = [raw[i * W:(i + 1) * W] for i in range(len(raw) // W)]
    dec = [seq._decode_iq(w) for w in wb]
    cs = seq.detect_candidates(dec)
    r = g.visit_gate(cs, wb, [-256000.0, 256000.0])
    ex = {}
    for side, s in (('neg', -1), ('pos', 1)):
        e = [x.excess_db for x in cs if abs(x.baseband_hz - s * 256000) < g.CANARY_MATCH_TOL_HZ]
        ex[side] = max(e) if e else None
    a = np.frombuffer(raw, dtype=np.uint8)
    fl = floor_db(np.asarray(dec[0]).ravel())
    return dict(file=c['file'], tuning_index=c['tuning_index'], delta_hz=c['delta_hz'], passed=r['passed'], reasons=r['reasons'], neg=ex['neg'], pos=ex['pos'],
                std=float((a.astype(np.float32) - 127.5).std()), clip_pct=r['clipped_pct'], n_cands=len(cs), floor_db=fl)
if __name__ == '__main__':
    sets = []
    for lp in sorted(glob.glob(H + '/slope_*/capture_log.json')):
        d = os.path.dirname(lp); j = json.load(open(lp))
        if os.path.basename(d).endswith('_archive'): continue
        if j.get('tunings') == TUN and j.get('gain_db') == 29.0 and len(j['captures']) == 32:
            pl = j['purpose'].lower()
            cond = 'load' if ('antenna-off' in pl or '50 ohm load' in pl) else 'antenna'
            sets.append((os.path.basename(d).replace('slope_', ''), d, cond, j))
    out = {}
    with Pool(4) as pool:
        for sid, d, cond, j in sets:
            rows = pool.map(one, [(d, c) for c in j['captures']])
            out[sid] = dict(condition=cond, start_unix=j['start_unix'], rows=rows)
            print(sid, cond, sum(r['passed'] for r in rows), '/', len(rows), flush=True)
    json.dump(out, open(H + '/analysis_uniform_gate/uniform_results.json', 'w'))
    print('DONE', flush=True)
