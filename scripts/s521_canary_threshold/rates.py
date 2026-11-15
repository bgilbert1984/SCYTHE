import json,math,numpy as np
R=json.load(open('visits.json'))
def wilson(k,n,z=1.96):
    if n==0: return (0,1)
    p=k/n; d=1+z*z/n; c=p+z*z/(2*n); h=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))
    return ((c-h)/d,(c+h)/d)
def both(r): return r['neg'] is not None and r['pos'] is not None
def either(r): return r['neg'] is not None or r['pos'] is not None
print('set | n | both | either | excess_db(min/med/max, when both)')
for k,rows in R.items():
    n=len(rows); b=sum(both(r) for r in rows); e=sum(either(r) for r in rows)
    ex=[min(r['neg'],r['pos']) for r in rows if both(r)]
    lo,hi=wilson(b,n)
    print(f"{k} | {n} | {b}/{n} (95% CI {lo:.3f}-{hi:.3f}) | {e}/{n} | "+(f"{min(ex):.1f}/{np.median(ex):.1f}/{max(ex):.1f}" if ex else '-'))
# terminated baseline excess distribution (min of the two sides)
t=[min(r['neg'],r['pos']) for r in R['terminated_rerun1'] if both(r)]
print('terminated min-side excess dB: p1/p5/p50',np.percentile(t,[1,5,50]).round(1))
# per row of degraded sets
for k in ('hwy99_strong_bands','mukilteo_intermediate'):
    print(k)
    import collections
    G=collections.defaultdict(list)
    for r in R[k]: G[(r['gain_db'],r['anchor_hz'])].append(r)
    for key,L in sorted(G.items(),key=lambda kv:(kv[0][1],-kv[0][0])):
        ex=[min(r['neg'],r['pos']) for r in L if both(r)]
        print(f"  gain {key[0]:+.1f} {key[1]/1e6:.1f}MHz both {sum(both(r) for r in L)}/{len(L)} excess "+(f"{min(ex):.1f}-{max(ex):.1f}" if ex else '-')+f" clip_max {max(r['clip_pct'] for r in L):.4f} ncand_med {int(np.median([r['n_cands'] for r in L]))}")
