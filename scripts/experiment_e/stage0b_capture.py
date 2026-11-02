import socket, struct, sys, time, os
RATE=2048000; SECS=60; D="/tmp/exp_e_stage0b"; os.makedirs(D,exist_ok=True)
CFG=[("q1",12500000),("am",1000000),("hf7",7074000),("hf5",5000000),("q2",12500000)]
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
    for name,f in CFG:
        cmd(0x09,2); cmd(0x01,f); time.sleep(0.5); rd(RATE*2*2)
        t=time.strftime("%H:%M:%S")
        with open(f"{D}/{name}.u8","wb") as fh: rd(RATE*2*SECS,fh)
        print(name,f,"started",t,flush=True)
finally:
    cmd(0x09,0); cmd(0x01,100000000); time.sleep(0.3); s.close(); print("restored: direct off, 100 MHz",flush=True)
