import subprocess, time, sys, re
PY="/Users/huzhiwei/.espressif/python_env/idf5.3_py3.9_env/bin/python3"
print("=== 监控猫头连接 (最多 180 秒) ===", flush=True)
# 监控服务端日志
p = subprocess.Popen(["tail","-F","-n","0","/tmp/xz_server.log"], stdout=subprocess.PIPE, text=True)
# 同时抓串口
s = subprocess.Popen([PY,"/tmp/cap2.py","/dev/cu.usbmodem101","180"], stdout=subprocess.PIPE, text=True)
start=time.time()
import threading
def rd(proc, tag):
    for line in proc.stdout:
        print(f"{tag} {line.rstrip()}", flush=True)
t1=threading.Thread(target=rd,args=(p,"[SRV]"),daemon=True); t1.start()
t2=threading.Thread(target=rd,args=(s,"[DEV]"),daemon=True); t2.start()
while time.time()-start < 185:
    time.sleep(2)
    try:
        cur=open("/tmp/xz_server.log").read()
        if "设备已连接" in cur and cur.count("设备已连接")>1:
            print("=== 检测到猫头连入！===", flush=True); break
    except Exception: pass
