import sys, json, numpy as np, collections
sys.path.insert(0,'/home/spectrcyde/SCYTHE-h5')
import rf_spur_catalogue_sequencer as seq
W=seq.WINDOW_BYTES; N=8192; FS=2.048e6; win=np.hanning(N)
H='/home/spectrcyde'
def stats(path):
    raw=np.fromfile(path,dtype=np.uint8); x=(raw.astype(np.float32)-127.5)
    clip=float(((raw==0)|(raw==255)).mean())*100
    z=x[0::2]+1j*x[1::2]; acc=np.zeros(N); k=0
    for s in range(0,len(z)-N+1,N*8):
        acc+=np.abs(np.fft.fftshift(np.fft.fft(z[s:s+N]*win)))**2; k+=1
    p=10*np.log10(acc/k+1e-9); f=np.fft.fftshift(np.fft.fftfreq(N,1/FS))
    med=float(np.median(p)); m=np.abs(f)>20e3
    pk=int(np.argmax(np.where(m,p,-1e9)))
    return dict(std=float(x.std()),clip_pct=clip,floor=med,peak_db_over_floor=float(p[pk]-med),peak_off_khz=float(f[pk]/1e3),p=p,f=f)
out={}
for name in ('saturation_20261114','load_strong_20261115','intermediate_20261115'):
    d=f'{H}/slope_{name}'; j=json.load(open(d+'/capture_log.json')); rows=[]
    for c in j['captures']:
        s=stats(d+'/'+c['file']); s.update(file=c['file'],gain=c['gain_db'],anchor=c['anchor_hz'],delta=c['delta_hz'],centre=c['centre_hz']); rows.append(s)
    out[name]=rows
import pickle; pickle.dump(out,open('strong_levels.pkl','wb'))
def agg(rows):
    g=collections.defaultdict(list)
    for r in rows: g[(r['gain'],r['anchor'])].append(r)
    return g
A=agg(out['saturation_20261114']); L=agg(out['load_strong_20261115']); I=agg(out['intermediate_20261115'])
print('antenna-on Hwy99 11-14 15:56 vs load-fitted 11-15 21:37 (same plan). median over 4 offsets')
print('%5s %7s | %7s %6s %8s | %7s %6s | %s'%('gain','MHz','ant std','clip%','pk/floor','ld std','clip%','power ratio ant/load (dB)'))
for k in sorted(A,key=lambda k:(k[1],-k[0])):
    a=A[k]; l=L.get(k)
    ms=lambda rs,key:float(np.median([r[key] for r in rs]))
    pr=10*np.log10(ms(a,'std')**2/ms(l,'std')**2)
    print('%5.1f %7.1f | %7.2f %6.3f %8.1f | %7.2f %6.3f | %+6.1f'%(k[0],k[1]/1e6,ms(a,'std'),ms(a,'clip_pct'),ms(a,'peak_db_over_floor'),ms(l,'std'),ms(l,'clip_pct'),pr))
print('\n12502 11-15 08:13 antenna-on at intermediate gains (no matching load; load control at 14.0 only)')
for k in sorted(I,key=lambda k:(k[1],-k[0])):
    a=I[k]; print('%5.1f %7.1f | std %6.2f clip%% %6.3f pk/floor %5.1f'%(k[0],k[1]/1e6,np.median([r['std'] for r in a]),np.median([r['clip_pct'] for r in a]),np.median([r['peak_db_over_floor'] for r in a])))
