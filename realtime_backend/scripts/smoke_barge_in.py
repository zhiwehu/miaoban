"""Inject clean synthetic user audio during playback; this does not validate acoustic AEC."""
import asyncio
import json
import time
import sys
from pathlib import Path
import aiohttp
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from voicehub.models import Pipeline
from voicehub.config import DEFAULT

async def main():
 async with aiohttp.ClientSession() as http:
  pipeline=Pipeline(DEFAULT,http,None)
  pcm=b''.join([p async for p in pipeline.native_speech('等一下，请只回答你好。','Tingting')])+b'\0'*32000
  async with http.ws_connect('http://127.0.0.1:8765/ws') as ws:
   await ws.send_json(dict(type='hello',version=1,device_id='smoke-interruption',wake_mode='manual',
         capabilities=dict(echo_cancellation=True)))
   assert (await ws.receive_json())['type']=='hello'
   await ws.send_json(dict(type='input.text',text='请用一百个字介绍太阳系中的行星。'))
   old=None;cleared=False;injected=None;upload=None
   async def feed():
    for start in range(0,len(pcm),1280):
     await ws.send_bytes(pcm[start:start+1280]);await asyncio.sleep(.04)
   async with asyncio.timeout(90):
    async for message in ws:
     e=json.loads(message.data);kind=e['type']
     if kind=='audio.start' and old is None:
      old=e['turn_id'];injected=time.monotonic();upload=asyncio.create_task(feed())
      print('Text-to-first-audio:',e['elapsed_ms'],'ms',flush=True)
     elif kind=='playback.clear' and e.get('reason')=='speech':
      cleared=True;print('Synthetic audio onset to clear:',round((time.monotonic()-injected)*1000),'ms',flush=True)
     elif kind in ('audio.chunk','reply.delta') and cleared:
      assert e['turn_id']!=old,'stale output after interruption'
     elif kind=='transcript.final':
      print('New ASR:',e['text'],flush=True)
     elif kind=='audio.end':
      await ws.send_json(dict(type='playback.done',turn_id=e['turn_id']))
     elif kind=='turn.completed' and cleared:
      print('New turn completed; no stale output after clear.',flush=True);break
     elif kind=='error':
      raise RuntimeError(e)
   assert cleared,'no automatic interruption'
   if upload:
    upload.cancel();await asyncio.gather(upload,return_exceptions=True)

asyncio.run(main())
