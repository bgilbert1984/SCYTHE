import sys,json,glob,numpy as np
sys.path.insert(0,'/home/spectrcyde/SCYTHE')
import rf_spur_catalogue_sequencer as seq
W=seq.WINDOW_BYTES
def visit(fn):
    raw=open(fn,'rb').read(); a=np.frombuffer(raw,dtype=np.uint8)
    ws=[seq._decode_iq(raw[i*W:(i+1)*W]) for i in range(len(raw)//W)]
    cs=seq.detect_candidates(ws)
    out={}
    for side,s in (('neg',-1),('pos',1)):
        ex=[c.excess_db for c in cs if abs(c.baseband_hz-s*256000)<60]
        out[side]=max(ex) if ex else None
    out['std']=float((a.astype(np.float32)-127.5).std()); out['clip_pct']=float(np.mean((a==0)|(a==255))*100)
    out['n_cands']=len(cs)
    return out
H='/home/spectrcyde/'
sets={}
sets['terminated_rerun1']=[(f,{}) for f in sorted(glob.glob(H+'catalogue_xtr_20261114_e4k_rerun1/spool/visit_*.iq'))]
for name,d in (('quiet_520',H+'slope_feasibility_20261114'),('hwy99_gain29',H+'slope_strong_20261114'),
               ('hwy99_strong_bands',H+'slope_saturation_20261114'),('mukilteo_intermediate',H+'slope_intermediate_20261115')):
    log=json.load(open(d+'/capture_log.json'))['captures']
    sets[name]=[(d+'/'+c['file'],{k:c[k] for k in c if k in('gain_db','anchor_hz','centre_hz')}) for c in log]
res={}
for name,items in sets.items():
    rows=[]
    for fn,meta in items:
        r=visit(fn); r.update(meta); r['file']=fn.split('/')[-1]; rows.append(r)
    res[name]=rows; print(name,len(rows),flush=True)
json.dump(res,open('visits.json','w'))
print('DONE')
