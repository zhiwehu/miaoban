"""Exercise real native TTS -> VAD -> ASR -> LLM -> TTS through a real WebSocket.
Run against a local VoiceHub with local models configured; no cloud requests.
"""
import asyncio
import json
import time
import aiohttp
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voicehub.models import Pipeline
from voicehub.config import DEFAULT


async def main():
    async with aiohttp.ClientSession() as http:
        pipeline = Pipeline(DEFAULT, http, None)
        pcm = b''.join([part async for part in pipeline.native_speech('你好，请告诉我一加一等于几。', 'Tingting')])
        pcm += b'\0' * 32000
        async with http.ws_connect('http://127.0.0.1:8765/ws') as ws:
            await ws.send_json(dict(type='hello',version=1,device_id='smoke-local',wake_mode='manual',
                audio=dict(format='pcm_s16le',sample_rate=16000),capabilities=dict(echo_cancellation=True)))
            hello = await ws.receive_json()
            assert hello['type']=='hello', hello.get('code')
            await ws.send_json(dict(type='session.activate'))
            started = time.monotonic()
            async def upload():
                for offset in range(0,len(pcm),1280):
                    await ws.send_bytes(pcm[offset:offset+1280])
                    await asyncio.sleep(.04)
            task = asyncio.create_task(upload())
            audio_bytes, transcript, reply = 0, '', ''
            try:
                async with asyncio.timeout(120):
                    async for message in ws:
                        event = json.loads(message.data)
                        kind=event['type']
                        if kind=='transcript.final':
                            transcript=event['text'];print('ASR:',transcript,flush=True)
                        elif kind=='audio.chunk':
                            import base64
                            audio_bytes+=len(base64.b64decode(event['data']))
                        elif kind=='audio.start':
                            print('First audio:',event['elapsed_ms'],'ms (includes initial model loading)',flush=True)
                        elif kind=='reply.final':
                            reply=event['text'];print('Reply:',reply,flush=True)
                        elif kind=='audio.end':
                            await ws.send_json(dict(type='playback.done',turn_id=event['turn_id']))
                        elif kind=='error':
                            raise RuntimeError(event)
                        elif kind=='turn.completed':
                            assert transcript and reply and audio_bytes
                            print('PCM seconds:',round(audio_bytes/32000,2),
                                  'wall seconds:',round(time.monotonic()-started,2),flush=True)
                            break
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)

asyncio.run(main())
