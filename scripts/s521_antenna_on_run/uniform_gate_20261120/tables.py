import json, glob, os, datetime, numpy as np, collections
H='/home/spectrcyde'; R=json.load(open(H+'/analysis_uniform_gate/uniform_results.json'))
SITE=lambda s:('not stated' if s=='feasibility_20261114' else 'Hwy99' if 'hwy99' in s or s=='strong_20261114' else 'Lynnwood' if 'lynn' in s else 'Muk12426' if '12426' in s else 'Muk12502' if 'mukilteo' in s else 'Phinney' if 'phinney' in s else '?')
# 1. identity with stored per-capture results
tot=mism=cmp_n=0; diffs=[]
for sid,v in R.items():
    st=None
    for g in glob.glob(f'{H}/slope_{sid}/visits*.json'):
        r=json.load(open(g))
        if isinstance(r,list): st={x['file']:x for x in r}
    if not st: print('no stored gate results:',sid); continue
    for x in v['rows']:
        s=st.get(x['file']); cmp_n+=1
        if s is None: diffs.append((sid,x['file'],'missing')); continue
        same=(s['passed']==x['passed'] and s['reasons']==x['reasons'] and s['n_cands']==x['n_cands'] and
              all((s[k] is None and x[k] is None) or (s[k] is not None and x[k] is not None and abs(s[k]-x[k])<1e-6) for k in('neg','pos','std','clip_pct')))
        if not same: diffs.append((sid,x['file'],{k:(s[k],x[k]) for k in('passed','neg','pos','n_cands') if s[k]!=x[k]}))
print('stored-vs-recomputed: compared',cmp_n,'captures; differing',len(diffs)); print(diffs[:5])
# 2. floor reference: pooled load-fitted median floor per tuning
ref={t:float(np.median([r['floor_db'] for v in R.values() if v['condition']=='load' for r in v['rows'] if r['tuning_index']==t])) for t in (0,8,16,24,32,40,48,56)}
print('pooled load floor dB',{k:round(v,2) for k,v in ref.items()})
spread={t:(min(float(np.median([r['floor_db'] for r in v['rows'] if r['tuning_index']==t])) for v in R.values() if v['condition']=='load'),max(float(np.median([r['floor_db'] for r in v['rows'] if r['tuning_index']==t])) for v in R.values() if v['condition']=='load')) for t in ref}
print('load-set median floor min/max per tuning',{k:(round(a,2),round(b,2)) for k,(a,b) in spread.items()})
rows=[]
for sid,v in sorted(R.items(),key=lambda kv:kv[1]['start_unix']):
    rr=v['rows']; pt={t:float(np.median([r['floor_db'] for r in rr if r['tuning_index']==t]))-ref[t] for t in ref}
    lo=float(np.mean([pt[t] for t in (0,8,16,24,32)])); up=float(np.mean([pt[t] for t in (40,48,56)]))
    both=[min(r['neg'],r['pos']) for r in rr if r['neg'] is not None and r['pos'] is not None]
    rows.append(dict(set=sid,site=SITE(sid),cond=v['condition'],t=datetime.datetime.fromtimestamp(v['start_unix']).strftime('%m-%d %H:%M'),
      passed=sum(r['passed'] for r in rr),lo_pass=sum(r['passed'] for r in rr if r['tuning_index']<=32),up_pass=sum(r['passed'] for r in rr if r['tuning_index']>=40),
      excess=float(np.median(both)) if both else None,std=float(np.median([r['std'] for r in rr])),lo_rise=lo,up_rise=up,pt=pt))
json.dump(rows,open('floor_rise_table.json','w'),indent=1)
print('%-30s %-8s %-4s %-11s %5s %6s %6s %7s %6s %7s %7s'%('set','site','cond','start','pass','lo/20','up/12','excess','std','lo rise','up rise'))
for r in rows:
    print('%-30s %-8s %-4s %-11s %2d/32 %3d %5d %7s %6.2f %+7.1f %+7.1f'%(r['set'],r['site'],r['cond'][:4],r['t'],r['passed'],r['lo_pass'],r['up_pass'],'%.1f'%r['excess'] if r['excess'] else '  -',r['std'],r['lo_rise'],r['up_rise']))
a=[r for r in rows if r['cond']=='antenna']
import numpy as np
x=np.array([r['lo_rise'] for r in a]); y=np.array([r['lo_pass'] for r in a]); print('antenna sets: corr(lower-band rise, lower-band passes) =',round(float(np.corrcoef(x,y)[0,1]),2),'n',len(a))
print('load sets lower-band rise range',round(min(r['lo_rise'] for r in rows if r['cond']=='load'),2),round(max(r['lo_rise'] for r in rows if r['cond']=='load'),2))
print('pass vs rise (antenna, sorted by lo_rise):',[(round(r['lo_rise'],1),r['lo_pass']) for r in sorted(a,key=lambda r:r['lo_rise'])])
