import numpy as np
D="/tmp/exp_e_stage0b"; W=409600
POP=np.array([bin(i).count("1") for i in range(256)],dtype=np.uint8)
rng=np.random.default_rng(11)
def rank(x): r=np.empty(len(x)); r[np.argsort(x)]=np.arange(len(x)); return r
def sp(x,y):
    a,b=rank(x),rank(y); a-=a.mean(); b-=b.mean(); return float((a*b).sum()/np.sqrt((a*a).sum()*(b*b).sum()))
def resid(y,cols):
    X=np.column_stack([np.ones(len(y))]+cols); b,*_=np.linalg.lstsq(X,y,rcond=None); return y-X@b
def test(x,y,n):
    r=sp(x,y); null=[abs(sp(x,np.roll(y,int(k)))) for k in rng.integers(100,n-100,1000)]
    return r,np.percentile(null,95),(1+sum(v>=abs(r) for v in null))/1001
for name,path in (("am (320 mm)","am.u8"),("am584 (584 mm)","am584.u8")):
    a=np.fromfile(f"{D}/{path}",dtype=np.uint8); n=len(a)//W; w=a[:n*W].reshape(n,W)
    I,Q=w[:,0::2],w[:,1::2]
    z=I.astype(np.float32); zq=Q.astype(np.float32)
    mean=w.mean(1); sd=w.astype(np.float32).std(1)
    P=((z-z.mean(1,keepdims=True))**2+(zq-zq.mean(1,keepdims=True))**2).mean(1); lp=np.log10(P)
    t=np.arange(n,dtype=float)
    y={"tog_qi":POP[Q[:,:-1]^I[:,1:]].mean(1)/8,"tog_ii":POP[I[:,:-1]^I[:,1:]].mean(1)/8}
    print(f"\n{name}: {n} windows; corr(logP, log sd)={np.corrcoef(lp,np.log10(sd))[0,1]:.4f}; corr(logP, mean)={np.corrcoef(lp,mean)[0,1]:+.3f}")
    base=[t]
    for lab,cov in (("trend only",base),("+mean,mean^2",base+[mean,mean**2]),("+mean+sd",base+[mean,mean**2,np.log10(sd)])):
        xr=resid(lp,cov); sdx=xr.std()
        line=[f"resid sd(logP)={sdx:.1e}"]
        for k,v in y.items():
            r,n95,p=test(xr,resid(v,cov),n); line.append(f"{k}: r={r:+.3f} (null95 {n95:.3f}, p={p:.3f})")
        print(f"  {lab:14s} "+" | ".join(line))
