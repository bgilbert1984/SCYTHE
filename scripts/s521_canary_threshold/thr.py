import json,math,numpy as np
R=json.load(open('visits.json'))
def mn(r): return None if (r['neg'] is None or r['pos'] is None) else min(r['neg'],r['pos'])
good=[r for k in ('terminated_rerun1','quiet_520') for r in R[k]]
ant=R['hwy99_gain29']
deg=R['hwy99_strong_bands']+R['mukilteo_intermediate']
def rate(rows,x):
    k=sum((mn(r) is not None and mn(r)>=x) for r in rows); return k,len(rows)
print('min-side excess threshold X (dB) | terminated+quiet | antenna-on weak (Hwy99 g29) | strong/intermediate rows')
for x in (0,10,11,11.5,12,13,14):
    a=rate(good,x);b=rate(ant,x);c=rate(deg,x)
    print(f'{x:5.1f} | {a[0]}/{a[1]} | {b[0]}/{b[1]} | {c[0]}/{c[1]}')
print('either-side detection: good',sum(r['neg'] is not None or r['pos'] is not None for r in good),'/',len(good),' deg',sum(r['neg'] is not None or r['pos'] is not None for r in deg),'/',len(deg))
# upper 95% bound on false-fail with 0 failures in n (rule of three)
for n in (224,256): print('n',n,'zero-failure 95% upper bound',round(3/n,4))
# spur-excess vs n_cands relation in degraded rows
c=[(r['n_cands'],mn(r)) for r in deg if mn(r) is not None]; 
print('pass visits by ncand: median',np.median([a for a,b in c]))
