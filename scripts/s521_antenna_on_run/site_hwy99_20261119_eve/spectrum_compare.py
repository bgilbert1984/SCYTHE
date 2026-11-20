import sys, json, numpy as np
sys.path.insert(0,'/home/spectrcyde/SCYTHE')
import rf_spur_catalogue_sequencer as seq
W=seq.WINDOW_BYTES; N=4096; FS=2.048e6
def psd(path):
    raw=open(path,'rb').read(); acc=np.zeros(N); k=0; win=np.hanning(N)
    for i in range(len(raw)//W):
        x=np.asarray(seq._decode_iq(raw[i*W:(i+1)*W])).ravel()
        for s in range(0,len(x)-N+1,N*4):
            acc+=np.abs(np.fft.fftshift(np.fft.fft(x[s:s+N]*win)))**2; k+=1
    return 10*np.log10(acc/k+1e-9), np.fft.fftshift(np.fft.fftfreq(N,1/FS))
sets={'load':'/home/spectrcyde/slope_hwy99_load_20261119','ant':'/home/spectrcyde/slope_hwy99_antennaon_20261119_eve'}
res={}
for name,d in sets.items():
    for c in json.load(open(d+'/capture_log.json'))['captures']:
        p,f=psd(d+'/'+c['file']); res.setdefault((c['tuning_index'],c['delta_hz']),{})[name]=(c['centre_hz'],p,f)
out={}
print('tuning  centre   |  load-med  ant-med  rise  | in-band (+-0.8MHz excl +-12k) mean rise | ant bins>10dB  load bins>10dB')
for ti in sorted({k[0] for k in res}):
    c,pl,f=res[(ti,0.0)]['load']; _,pa,_=res[(ti,0.0)]['ant']
    ml,ma=np.median(pl),np.median(pa)
    mask=(np.abs(f)<0.8e6)&(np.abs(f)>12e3)
    rise=float(np.mean(pa[mask])-np.mean(pl[mask]))
    out[ti]=dict(centre_mhz=c/1e6,load_med=float(ml),ant_med=float(ma),median_rise_db=float(ma-ml),band_mean_rise_db=rise,ant_bins10=int((pa>ma+10).sum()),load_bins10=int((pl>ml+10).sum()))
    print('%3d  %7.2f   |  %6.1f  %6.1f  %+5.1f  | %+5.1f | %3d  %3d'%(ti,c/1e6,ml,ma,ma-ml,rise,out[ti]['ant_bins10'],out[ti]['load_bins10']))
# narrowband carriers present antenna-on, absent on load (all four offsets, +-0.9 MHz)
print('--- carriers: antenna-on peak > 12 dB over local median and > 10 dB above the load at the same frequency')
seen={}
for (ti,dl),r in res.items():
    c,pa,f=r['ant']; _,pl,_=r['load']
    ma=np.median(pa); ml=np.median(pl)
    for i in np.where((pa>ma+12)&(np.abs(f)>20e3)&(np.abs(f)<0.9e6))[0]:
        if pa[i]-ma-(pl[i]-ml)>10:
            fm=round((c+f[i])/1e6,2); seen[fm]=max(seen.get(fm,0),round(float(pa[i]-ma),1))
for k in sorted(seen): print('  %.2f MHz  +%.1f dB'%(k,seen[k]))
json.dump(dict(per_tuning=out,carriers=seen),open('/tmp/claude-1000/-home-spectrcyde/87d8d0b3-d7cf-4991-943d-ad8217cfff78/scratchpad/spechwy99eve.json','w'),indent=1)
