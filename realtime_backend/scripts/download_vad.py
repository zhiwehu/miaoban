"""Fetch a pinned official Silero ONNX file and verify its digest."""
import hashlib,urllib.request
from pathlib import Path
URL='https://raw.githubusercontent.com/snakers4/silero-vad/bfdc0193023f121ea5b3cc7b176dbed570a68a59/src/silero_vad/data/silero_vad.onnx'
DIGEST='1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3'
destination=Path(__file__).resolve().parents[1]/'models/silero_vad.onnx'
if destination.exists():
 if hashlib.sha256(destination.read_bytes()).hexdigest()!=DIGEST:raise SystemExit('Existing VAD file differs; refusing overwrite')
 print('VAD already verified');raise SystemExit(0)
with urllib.request.urlopen(urllib.request.Request(URL,headers={'User-Agent':'VoiceHub-setup'}),timeout=60) as response:data=response.read(5000000)
if hashlib.sha256(data).hexdigest()!=DIGEST:raise SystemExit('VAD digest mismatch')
destination.parent.mkdir(exist_ok=True)
temporary=destination.with_suffix('.download');temporary.write_bytes(data);temporary.replace(destination)
print('VAD verified:',destination)
