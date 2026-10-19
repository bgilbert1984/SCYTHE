import json,math,sys
FS=2048000;N=4096;MARGIN=6.0;NSEG=8;MEDW=129
DIR='/tmp/claude-1000/-home-spectrcyde/4e8369e3-0103-4876-8d11-c8ce3cdcfece/scratchpad/xtr_repeat_2026-10-12'
def db(p):return 10*math.log10(p+1e-30)
def median(v):
    s=sorted(v);n=len(s);return s[n//2] if n%2 else 0.5*(s[n//2-1]+s[n//2])
def locmed(v):
    h=MEDW//2;pad=[v[0]]*h+list(v)+[v[-1]]*h
    return [median(pad[i:i+MEDW]) for i in range(len(v))]
def run(m):
    d=json.load(open('%s/r%d.json'%(DIR,m)));fc=m*10**6
    P=d['P'];ns=d['bytes']//2
    full=[sum(P[s][i] for s in range(NSEG))/NSEG for i in range(N)]
    fdb=[db(x) for x in full];lm=locmed(fdb);c=N//2
    lo,hi=int(N*.125),int(N*.875)
    mI=d['sumI']/ns;mQ=d['sumQ']/ns
    sI=math.sqrt(d['sumsqI']/ns-mI*mI);sQ=math.sqrt(d['sumsqQ']/ns-mQ*mQ)
    hit=[0]*N
    for s in range(NSEG):
        r=[db(x) for x in P[s]];l=locmed(r)
        for b in range(N):
            if r[b]-l[b]>=MARGIN:hit[b]+=1
    feats=[]
    for b in range(N):
        if hit[b]>=7 and abs(b-c)>1:
            feats.append([round((b-c)*FS/N),round(fdb[b]-lm[b],1),hit[b],int(lo<=b<hi)])
    mg=[]
    for f in feats:
        if mg and f[0]-mg[-1][0]<=2*FS/N:
            if f[1]>mg[-1][1]:mg[-1][:4]=f
        else:mg.append(f)
    return dict(c=m,B=d['bytes'],lo=d['clip0'],hi=d['clip255'],mI=round(mI,3),mQ=round(mQ,3),sI=round(sI,3),sQ=round(sQ,3),
      rI=[d['minI'],d['maxI']],rQ=[d['minQ'],d['maxQ']],fl=round(median(fdb[lo:hi]),2),
      dc=round(fdb[c],2),dcx=round(fdb[c]-lm[c],2),nb=[round(fdb[c-1],2),round(fdb[c+1],2)],ns=d['nseg'],F=mg)
json.dump([run(m) for m in (100,400,1500)],open('%s/final.json'%DIR,'w'),separators=(',',':'))
print('ok')
