import socket, struct, sys, time, numpy as np
RATE=2048000; N=RATE*2*2
CFG=[("A tuner FM 98.000 MHz",0,98000000),("B Q-dir AM 1.000 MHz",2,1000000),("C I-dir AM 1.000 MHz",1,1000000),("D Q-dir  7.074 MHz",2,7074000),("E Q-dir 10.000 MHz",2,10000000),("F Q-dir 5.000 MHz",2,5000000)]
s=socket.create_connection(("127.0.0.1",1234),timeout=15); s.recv(12)
def cmd(c,p): s.sendall(struct.pack(">BI",c,p))
def rd(n):
    b=bytearray()
    while len(b)<n:
        x=s.recv(min(262144,n-len(b)))
        if not x: raise EOFError
        b+=x
    return bytes(b)
def psd(raw, f0):
    u=np.frombuffer(raw,dtype=np.uint8).astype(np.float32)-127.5
    z=(u[0::2]+1j*u[1::2]); nfft=4096
    seg=z[:len(z)//nfft*nfft].reshape(-1,nfft)*np.hanning(nfft)
    p=(np.abs(np.fft.fftshift(np.fft.fft(seg,axis=1),axes=1))**2).mean(0)
    db=10*np.log10(p+1e-12); fr=(np.arange(nfft)-nfft//2)*RATE/nfft
    med=np.median(db)
    idx=np.argsort(db)[::-1]; peaks=[]
    for i in idx:
        if abs(fr[i])>6e3 and abs(abs(fr[i])-RATE/8)>6e3 and all(abs(fr[i]-q)>20e3 for q,_ in peaks): peaks.append((fr[i],db[i]-med))
        if len(peaks)==4: break
    clip=float(np.mean((raw_arr:=np.frombuffer(raw,dtype=np.uint8))==255)+np.mean(raw_arr==0))
    return dict(std=float(u.std()),floor_db=float(med),dc_i=float(u[0::2].mean()),dc_q=float(u[1::2].mean()),clip=clip,
        peaks=[(round(float(f0+f)/1e6,4),round(float(d),1)) for f,d in peaks], p95_minus_med=float(np.percentile(db,95)-med))
try:
    for name,ds,f in CFG:
        cmd(0x09,ds); cmd(0x01,f); time.sleep(0.3); rd(RATE*2)
        r=psd(rd(N),f); print(name, {k:(round(v,3) if isinstance(v,float) else v) for k,v in r.items()}, flush=True)
finally:
    cmd(0x09,0); cmd(0x01,100000000); time.sleep(0.3); s.close(); print("restored: direct off, 100 MHz", flush=True)
