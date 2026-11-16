import sys, json, collections, numpy as np
sys.path.insert(0,'/home/spectrcyde/SCYTHE-canary')
import rf_spur_catalogue_sequencer as seq, rf_canary_gate as g
W=seq.WINDOW_BYTES
def run(d, keyf):
    log=json.load(open(d+'/capture_log.json'))['captures']; rows=[]; offs=collections.defaultdict(collections.Counter); n=collections.Counter()
    for c in log:
        raw=open(d+'/'+c['file'],'rb').read(); wb=[raw[i*W:(i+1)*W] for i in range(len(raw)//W)]
        cs=seq.detect_candidates([seq._decode_iq(w) for w in wb]); r=g.visit_gate(cs,wb,[-256000.0,256000.0])
        a=np.frombuffer(raw,dtype=np.uint8); k=keyf(c); n[k]+=1
        for b in {round(x.baseband_hz/24.0) for x in cs if x.excess_db>=8}: offs[k][b]+=1
        rows.append(dict(file=c['file'],key=k,passed=r['passed'],reasons=r['reasons'],canary_excess_db=r['canary_excess_db'],std=float((a.astype(np.float32)-127.5).std()),clip_pct=r['clipped_pct'],n_cands=len(cs),centre_hz=c['centre_hz']))
    return rows,offs,n
out={}
for name,d,keyf in (('strong_control','/home/spectrcyde/slope_load_strong_20261115',lambda c:'g%.1f_c%dM'%(c['gain_db'],c['anchor_hz']//1000000)),
                    ('gains','/home/spectrcyde/slope_load_gains_20261115',lambda c:'g%.1f'%c['gain_db'])):
    rows,offs,n=run(d,keyf); out[name]=rows
    print('==',name,'gate pass',sum(r['passed'] for r in rows),'/',len(rows))
    by=collections.defaultdict(list)
    for r in rows: by[r['key']].append(r)
    for k,v in by.items():
        ce=[min(x for x in r['canary_excess_db'].values() if x is not None) for r in v if r['canary_excess_db'] and all(x is not None for x in r['canary_excess_db'].values())]
        print(' %-14s pass %d/%d  excess %s  std %.2f  clip %.4f  cands med %d'%(k,sum(r['passed'] for r in v),len(v),('%.1f/%.1f'%(min(ce),max(ce))) if ce else '-',np.median([r['std'] for r in v]),max(r['clip_pct'] for r in v),np.median([r['n_cands'] for r in v])))
    if name=='gains':
        for k in sorted(offs):
            fixed=[(b*24,c/n[k]) for b,c in offs[k].items() if c/n[k]>=0.90 and abs(b*24)>=20000]
            print(' fixed offsets (>=90%% of %s visits, excess>=8 dB) at %s:'%(n[k],k),sorted((int(b),round(float(f),2)) for b,f in fixed))
json.dump(out,open('/home/spectrcyde/slope_load_gains_20261115/visits_load.json','w'),indent=1)
