import socket, struct, time, os, numpy as np
RATE=2048000; W=RATE//10*2; D="/tmp/exp_e_stage0b"
# ---- capture 120 s at 1.000 MHz, Q-branch direct, new antenna
s=socket.create_connection(("127.0.0.1",1234),timeout=20); s.recv(12)
def cmd(c,p): s.sendall(struct.pack(">BI",c,p))
def rd(n,fh=None):
    left=n
    while left>0:
        x=s.recv(min(262144,left))
        if not x: raise EOFError
        left-=len(x)
        if fh: fh.write(x)
try:
    cmd(0x09,2); cmd(0x01,1000000); time.sleep(0.5); rd(RATE*4)
    with open(f"{D}/am584.u8","wb") as fh: rd(RATE*2*120,fh)
finally:
    cmd(0x09,0); cmd(0x01,100000000); time.sleep(0.3); s.close(); print("restored: direct off, 100 MHz",flush=True)
# ---- features
POP=np.array([bin(i).count("1") for i in range(256)],dtype=np.uint8)
def feats(path):
    a=np.fromfile(path,dtype=np.uint8); n=len(a)//W; a=a[:n*W].reshape(n,W)
    I,Q=a[:,0::2],a[:,1::2]
    z=I.astype(np.float32)-I.mean(1,keepdims=True); zq=Q.astype(np.float32)-Q.mean(1,keepdims=True)
    P=(z**2+zq**2).mean(1)
    f=dict(logP=np.log10(P+1e-9),
      hw=POP[a].mean(1),
      tog_iq=POP[I^Q].mean(1)/8,
      tog_qi=POP[Q[:,:-1]^I[:,1:]].mean(1)/8,
      tog_ii=POP[I[:,:-1]^I[:,1:]].mean(1)/8)
    return f
def rank(x): r=np.empty(len(x)); r[np.argsort(x)]=np.arange(len(x)); return r
def detr(x): t=np.arange(len(x)); c=np.polyfit(t,x,1); return x-np.polyval(c,t)
def spearman(x,y):
    a,b=rank(x),rank(y); a-=a.mean(); b-=b.mean(); return float((a*b).sum()/np.sqrt((a*a).sum()*(b*b).sum()))
rng=np.random.default_rng(7)
def run(name,path):
    f=feats(path); n=len(f["logP"]); x=f["logP"]
    print(f"\n{name}: {n} windows, logP mean={x.mean():.3f} sd={x.std():.3f}, P range x{10**(x.max()-x.min()):.2f}")
    for s_ in ("hw","tog_iq","tog_qi","tog_ii"):
        y=f[s_]; out=[]
        for tag,(xx,yy) in (("raw",(x,y)),("detrended",(detr(x),detr(y)))):
            r=spearman(xx,yy); sl=np.polyfit(xx,yy,1)[0]
            null=[abs(spearman(xx,np.roll(yy,int(k)))) for k in rng.integers(100,n-100,1000)]
            p=(1+sum(v>=abs(r) for v in null))/1001
            out.append(f"{tag}: r={r:+.3f} slope={sl:+.2e}/dec null95|r|={np.percentile(null,95):.3f} p={p:.3f}")
        print(f"  {s_:7s} "+" | ".join(out))
run("am.u8   (320 mm, 60 s)",f"{D}/am.u8")
run("am584   (584 mm, 120 s)",f"{D}/am584.u8")
