"""Restart only this project's backend, keeping models offline."""
import os
from pathlib import Path
import signal
import sys
import subprocess
import time

root=Path(__file__).resolve().parent
pidfile=root/'server.pid'
if pidfile.exists():
    pid=int(pidfile.read_text())
    check=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
    if check.returncode==0:
        if str(root/'xiaozhi_server.py') not in check.stdout:
            raise RuntimeError('PID belongs to another process; refusing to stop it')
        os.kill(pid,signal.SIGTERM)
        for _ in range(30):
            try:os.kill(pid,0)
            except ProcessLookupError:break
            time.sleep(.1)
        else:raise RuntimeError('Old backend did not stop')
env=os.environ.copy();env['HF_HUB_OFFLINE']='1'
with (root/'server.log').open('a') as log:
    log.write('\n--- backend restart ---\n');log.flush()
    process=subprocess.Popen(['/Users/huzhiwei/ai/mac_voice_agent/.venv/bin/python',str(root/'xiaozhi_server.py'),'--bind','0.0.0.0'] + sys.argv[1:],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
pidfile.write_text(str(process.pid))
print('Backend started:',process.pid)
