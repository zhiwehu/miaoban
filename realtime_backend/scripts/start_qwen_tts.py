"""Start or reuse VoiceHub's private local Qwen TTS process."""
import json,os,subprocess,time,urllib.request,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
data=root/'data';data.mkdir(exist_ok=True)
pidfile=data/'qwen-tts.pid'
model='mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit'
def health():
 try:
  with urllib.request.urlopen('http://127.0.0.1:8767/health',timeout=1) as r:return json.load(r)
 except OSError:return None
status=health()
if status:
 if status.get('model')!=model or status.get('ready') is not True:raise RuntimeError('Port 8767 is occupied by an unexpected service')
 print(json.dumps(status));raise SystemExit(0)
if pidfile.exists():
 pid=int(pidfile.read_text())
 check=subprocess.run(['/bin/ps','-p',str(pid),'-o','command='],capture_output=True,text=True)
 if check.returncode==0:raise RuntimeError('Saved TTS process is still running but not ready; inspect log before retrying')
env=os.environ.copy();env['HF_HUB_OFFLINE']='1';env['TRANSFORMERS_OFFLINE']='1';env['TOKENIZERS_PARALLELISM']='false'
with (data/'qwen-tts.log').open('ab') as log:
 process=subprocess.Popen([sys.executable,'-u','-m','voicehub.qwen_tts','--port','8767'],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
pidfile.write_text(str(process.pid))
for _ in range(360):
 if process.poll() is not None:raise RuntimeError('TTS exited; inspect data/qwen-tts.log')
 status=health()
 if status and status.get('ready'):
  print(json.dumps(dict(pid=process.pid,**status)));break
 time.sleep(.5)
else:raise RuntimeError('TTS warmup did not complete')
