import numpy as np, os, sys, json
OLD=os.path.expanduser("~/exp_e_evidence/stage0b_recapture_20261102"); NEW=os.path.expanduser("~/exp_e_evidence/stage0b_dipole584_20261107")
NFFT=4096; RATE=2048000; SEC=60
win=np.hanning(NFFT).astype(np.float32); 
def load(p):
    a=np.fromfile(p,dtype=np.uint8,count=RATE*2*SEC); return a
def psd(a):
    z=(a[0::2].astype(np.float32)-127.5)+1j*(a[1::2].astype(np.float32)-127.5)
    n=len(z)//NFFT; z=z[:n*NFFT].reshape(n,NFFT); out=np.zeros(NFFT)
    for i in range(0,n,2000):
        out+=(np.abs(np.fft.fft(z[i:i+2000]*win,axis=1))**2).sum(0)
    return np.fft.fftshift(out/n)
def basic(a):
    I=a[0::2].astype(np.float64); Q=a[1::2].astype(np.float64)
    return dict(mean=a.mean(), sdI=I.std(), sdQ=Q.std(), ac1=float(np.corrcoef(I[:4_000_000-1],I[1:4_000_000])[0,1]))
def db(p): return 10*np.log10(p+1e-12)
def cmp(pa,pb):
    d=db(pa)-db(pb); lvl=d.mean(); shp=(d-lvl).std(); return lvl,shp
names=["q1","am","hf7","hf5","q2","am_120s"]
D={}
for s,dr in (("old",OLD),("new",NEW)):
    for n in names:
        a=load(f"{dr}/{n}.u8"); h=len(a)//2; h-=h%2
        D[(s,n)]=dict(b=basic(a),p=psd(a),p1=psd(a[:h]),p2=psd(a[h:]))
print("per-file basics (first 60 s):")
for k,v in D.items(): b=v["b"]; print(f"  {k[0]} {k[1]:8s} mean {b['mean']:.2f} sdI {b['sdI']:.2f} sdQ {b['sdQ']:.2f} lag1(I) {b['ac1']:+.2f}")
def line(tag,x,y):
    l,s=cmp(x,y); print(f"  {tag:46s} level {l:+6.2f} dB   shape rms {s:5.2f} dB")
print("\nwithin-file (first 30 s vs last 30 s) -- drift floor:")
for k,v in D.items(): line(f"{k[0]} {k[1]}",v["p1"],v["p2"])
print("\nwithin-session same-tuning pairs:")
for s in ("old","new"):
    line(f"{s}: q1 vs q2 (12.5 MHz)",D[(s,'q1')]["p"],D[(s,'q2')]["p"])
    line(f"{s}: am vs am_120s first 60 s (1.0 MHz)",D[(s,'am')]["p"],D[(s,'am_120s')]["p"])
print("\ncross-session (new minus old), same tuning:")
for n in names: line(f"{n}",D[("new",n)]["p"],D[("old",n)]["p"])
print("\nnew q1/q2 vs old q1/q2 (all four combos, 12.5 MHz):")
for a in ("q1","q2"):
    for b in ("q1","q2"): line(f"new {a} vs old {b}",D[("new",a)]["p"],D[("old",b)]["p"])
print("\nnew am vs old am_120s, new am_120s vs old am:")
line("new am vs old am_120s",D[("new","am")]["p"],D[("old","am_120s")]["p"]); line("new am_120s vs old am",D[("new","am_120s")]["p"],D[("old","am")]["p"])
