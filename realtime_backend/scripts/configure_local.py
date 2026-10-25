"""Create a fresh local-model configuration without replacing an existing one."""
import argparse,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from voicehub.config import ConfigFile
from voicehub.qwen_tts import MODEL
parser=argparse.ArgumentParser()
parser.add_argument('--vad-model',required=True)
args=parser.parse_args()
vad=Path(args.vad_model).expanduser().resolve()
if not vad.is_file():raise SystemExit('VAD model file does not exist')
destination=root/'data/settings.json'
if destination.exists():raise SystemExit('Configuration already exists; update it in the console instead')
store=ConfigFile(destination)
cfg=store.get();cfg['audio']['vad_model']=str(vad)
cfg['tts'].update(provider='http',model=MODEL,voice='Serena',url='http://127.0.0.1:8767/v1',sample_rate=24000)
store.save(cfg)
print('Created data/settings.json; start local TTS before the backend')
