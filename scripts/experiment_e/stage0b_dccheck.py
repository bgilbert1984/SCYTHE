import numpy as np
D="/tmp/exp_e_stage0b"; W=409600
POP=np.array([bin(i).count("1") for i in range(256)],dtype=np.float64)
print(f"{'file':7s} {'meanI':>7s} {'meanQ':>7s} {'std':>6s} {'%127/128':>9s} {'hw raw':>8s} {'hw DCfix':>9s} {'r(hw,winmean)':>14s}")
rows=[]
for n in ("q1","q2","am","hf7","hf5","am584"):
    a=np.fromfile(f"{D}/{n}.u8",dtype=np.uint8)
    k=len(a)//W; a=a[:k*W]
    mI,mQ=a[0::2].mean(),a[1::2].mean()
    h=np.bincount(a,minlength=256)/len(a)
    hw=float((h*POP).sum())
    sh=int(round(a.mean()-127.5))
    b=np.clip(a.astype(np.int16)-sh,0,255).astype(np.uint8)
    hwf=float((np.bincount(b,minlength=256)/len(b)*POP).sum())
    w=a.reshape(k,W); wm=w.mean(1); wh=POP[w].mean(1)
    r=float(np.corrcoef(wm,wh)[0,1])
    print(f"{n:7s} {mI:7.3f} {mQ:7.3f} {a.std():6.2f} {100*(h[127]+h[128]):9.1f} {hw:8.4f} {hwf:9.4f} {r:14.3f}")
    rows.append((hw,hwf))
r=np.array(rows)
print(f"\nspread of hw across files: raw {r[:,0].max()-r[:,0].min():.4f}  after DC fix {r[:,1].max()-r[:,1].min():.4f}")
