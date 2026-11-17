import sys, json, glob, numpy as np
sys.path.insert(0,'/home/spectrcyde/SCYTHE-canary')
import rf_spur_catalogue_sequencer as seq, rf_canary_gate as g
W=seq.WINDOW_BYTES
log=json.load(open('/home/spectrcyde/slope_mukilteo2_20261117/capture_log.json'))['captures']
rows=[];
for c in log:
    raw=open('/home/spectrcyde/slope_mukilteo2_20261117/'+c['file'],'rb').read()
    wb=[raw[i*W:(i+1)*W] for i in range(len(raw)//W)]
    cs=seq.detect_candidates([seq._decode_iq(w) for w in wb])
    r=g.visit_gate(cs,wb,[-256000.0,256000.0])
    a=np.frombuffer(raw,dtype=np.uint8)
    ex={}
    for side,s in (('neg',-1),('pos',1)):
        e=[x.excess_db for x in cs if abs(x.baseband_hz-s*256000)<60]; ex[side]=max(e) if e else None
    rows.append(dict(file=c['file'],tuning_index=c['tuning_index'],centre_hz=c['centre_hz'],gain_db=29.0,passed=r['passed'],reasons=r['reasons'],
      neg=ex['neg'],pos=ex['pos'],std=float((a.astype(np.float32)-127.5).std()),clip_pct=r['clipped_pct'],n_cands=len(cs)))
json.dump(rows,open('/home/spectrcyde/slope_mukilteo2_20261117/visits_mukilteo2.json','w'),indent=1)
p=sum(r['passed'] for r in rows); print('gate pass',p,'/',len(rows))
mins=[min(r['neg'],r['pos']) for r in rows if r['neg'] is not None and r['pos'] is not None]
print('detected both',len(mins),'min-side excess min/med/max',min(mins) if mins else None,float(np.median(mins)) if mins else None,max(mins) if mins else None)
print('std min/med/max',min(r['std'] for r in rows),float(np.median([r['std'] for r in rows])),max(r['std'] for r in rows))
print('clip max',max(r['clip_pct'] for r in rows),'cands med',float(np.median([r['n_cands'] for r in rows])))
from collections import Counter; print(Counter(x for r in rows for x in r['reasons']))
for r in rows:
    if not r['passed']: print(r['file'],r['reasons'],r['neg'],r['pos'],round(r['std'],2))
