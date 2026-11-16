import sys, json, numpy as np
sys.path.insert(0,'/home/spectrcyde/SCYTHE')
import rf_spur_catalogue_sequencer as seq
W=seq.WINDOW_BYTES; N=4096; FS=2.048e6
def psd(path):
    raw=open(path,'rb').read()
    ws=[seq._decode_iq(raw[i*W:(i+1)*W]) for i in range(len(raw)//W)]
    acc=np.zeros(N); k=0
    win=np.hanning(N)
    for w in ws:
        x=np.asarray(w).ravel()
        for s in range(0,len(x)-N+1,N*4):
            f=np.fft.fftshift(np.fft.fft(x[s:s+N]*win)); acc+=np.abs(f)**2; k+=1
    return 10*np.log10(acc/k+1e-9), np.fft.fftshift(np.fft.fftfreq(N,1/FS))
sets={'earlier_1114':('/home/spectrcyde/slope_strong_20261114','hwy99_gain29'),'mukilteo':('/home/spectrcyde/slope_mukilteo_20261116',None),'antoff':('/home/spectrcyde/slope_hwy99_antoff_20261115',None)}
out={}
for name,(d,_) in sets.items():
    log=json.load(open(d+'/capture_log.json'))['captures']
    for c in log:
        if c.get('delta_hz',0)!=0 or 'tuning_index' not in c: continue
        p,f=psd(d+'/'+c['file']); med=np.median(p)
        top=np.argsort(p)[-3:][::-1]
        out.setdefault(c['tuning_index'],{})[name]=dict(centre_mhz=c['centre_hz']/1e6,median_db=float(med),peak_over_med_db=float(p.max()-med),
           top=[(round((c['centre_hz']+f[i])/1e6,3),round(float(p[i]-med),1)) for i in top], bins_gt10=int((p>med+10).sum()), total_db=float(10*np.log10(np.sum(10**(p/10)))))
for ti in sorted(out):
    print('tuning',ti)
    for n,r in out[ti].items(): print('  %-12s centre %.1f  median %.1f dB  total %.1f dB  peak+%.1f  bins>10dB %d  top %s'%(n,r['centre_mhz'],r['median_db'],r['total_db'],r['peak_over_med_db'],r['bins_gt10'],r['top']))
json.dump(out,open('/home/spectrcyde/slope_mukilteo_20261116/spectrum.json','w'),indent=1)
print('--- absolute dB at +-256 kHz bins vs median (d=0 captures)')
res={}
for name,(d,_) in sets.items():
    for c in json.load(open(d+'/capture_log.json'))['captures']:
        if c.get('delta_hz',0)!=0 or 'tuning_index' not in c: continue
        p,f=psd(d+'/'+c['file']); med=np.median(p)
        v=[]
        for s in (-256000,256000):
            i=np.argmin(abs(f-s)); v.append(float(p[i-1:i+2].max()))
        res.setdefault(c['tuning_index'],{})[name]=(v,float(med))
for ti in sorted(res):
    a=res[ti]['earlier_1114']; b=res[ti]['repeat_1115']
    print(ti,'earlier spur abs %.1f/%.1f floor %.1f | repeat spur abs %.1f/%.1f floor %.1f'%(a[0][0],a[0][1],a[1],b[0][0],b[0][1],b[1]))
