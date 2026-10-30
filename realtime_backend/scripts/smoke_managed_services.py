"""Offline real-model smoke: ASR/LLM/TTS inference, independent restart and stop.

Run from realtime_backend: PYTHONPATH=. python scripts/smoke_managed_services.py
Uses temporary data and ports 18781/18782/18767; always stops its own workers.
"""
import asyncio,copy,tempfile,time,json
from pathlib import Path
import aiohttp,numpy as np
from voicehub.services import ServiceManager
from voicehub.config import DEFAULT
from voicehub.models import LocalModels,Pipeline

async def main():
 cfg=copy.deepcopy(DEFAULT)
 for kind in ('asr','llm','tts'): cfg[kind]['provider']='builtin'
 cfg['tts'].update(model='mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit',voice='Serena',sample_rate=24000)
 with tempfile.TemporaryDirectory() as root:
  manager=ServiceManager(Path(root),lambda:cfg,ports={'asr':18781,'llm':18782,'tts':18767})
  local=LocalModels();local.services=manager
  try:
   for kind in ('asr','llm','tts'):
    await manager.action(kind,'start')
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
     state=await manager.status(kind)
     if state['state']=='running':break
     if state['state']=='failed':
      print((Path(root)/'services'/(kind+'.log')).read_text()[-4000:],flush=True)
      raise RuntimeError(state)
     await asyncio.sleep(.5)
    else:raise RuntimeError('startup timed out '+kind)
    print(json.dumps(state),flush=True)
   async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120)) as http:
    pipeline=Pipeline(cfg,http,local)
    text=''.join([t async for t in pipeline.chat([{'role':'user','content':'只回答：你好'}])])
    assert text;print('LLM:',text,flush=True)
    pcm=b''.join([block async for block in pipeline.speech('你好。')])
    assert pcm;print('TTS pcm bytes:',len(pcm),flush=True)
    # Feed the generated speech back into ASR (pipeline output is 16 kHz mono PCM).
    recognized=await pipeline.transcribe(np.frombuffer(pcm,'<i2').astype(np.float32)/32768)
    assert recognized;print('ASR:',recognized,flush=True)
   for kind in ('asr','llm','tts'):
    before=(await manager.status(kind))['pid']
    await manager.action(kind,'restart')
    async with asyncio.timeout(240):
     while True:
      state=await manager.status(kind)
      if state['state']=='running':break
      if state['state']=='failed':raise RuntimeError(state)
      await asyncio.sleep(.5)
    assert state['pid']!=before
    await manager.action(kind,'stop')
    assert (await manager.status(kind))['state']=='stopped'
    print(kind+' restart and stop verified',flush=True)
  finally:
   await manager.shutdown(stop_workers=True);local.close()
asyncio.run(main())
