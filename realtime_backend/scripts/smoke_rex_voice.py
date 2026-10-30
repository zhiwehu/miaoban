"""Exercise the configured planner -> real MCP -> local LLM/TTS over WebSocket.

Receives generated PCM without playing it on speakers. Run after enabling Rex.
"""
import asyncio
import base64
import json
import time
from pathlib import Path

import aiohttp


async def main():
    started=time.monotonic()
    events=[]
    samples=0
    async with aiohttp.ClientSession() as http:
        async with http.get('http://127.0.0.1:8765/api/config') as response:
            assert (await response.json())['config']['tools']['planner']=='rex'
        async with http.ws_connect('http://127.0.0.1:8765/ws') as ws:
            await ws.send_json(dict(type='hello',version=1,device_id='rex-voice-smoke',
                wake_mode='manual',audio=dict(format='pcm_s16le',sample_rate=16000),
                capabilities=dict(echo_cancellation=False)))
            assert (await ws.receive_json())['type']=='hello'
            await ws.send_json(dict(type='input.text',text='查询本机内存'))
            async with asyncio.timeout(180):
                async for message in ws:
                    event=json.loads(message.data)
                    kind=event['type']
                    if kind=='audio.chunk':
                        samples+=len(base64.b64decode(event['data']))//2
                        continue
                    if kind=='audio.end':
                        await ws.send_json(dict(type='playback.done',turn_id=event['turn_id']))
                    if kind in ('audio.start','tool.started','tool.completed','tool.failed',
                                'reply.final','turn.completed','error'):
                        event['received_ms']=round((time.monotonic()-started)*1000)
                        event['pcm_samples_so_far']=samples
                        events.append(event)
                        print(json.dumps(event,ensure_ascii=False),flush=True)
                    if kind=='error':raise RuntimeError(event['code'])
                    if kind=='turn.completed':break
                else:raise RuntimeError('WebSocket closed before turn completion')
    first=next(e for e in events if e['type']=='audio.start')
    tool=next(e for e in events if e['type']=='tool.started')
    reply=next(e for e in events if e['type']=='reply.final')
    assert first['received_ms']<tool['received_ms'] and tool['pcm_samples_so_far']>0
    assert tool['planner']=='rex' and tool['arguments']=={'sections':['memory']}
    assert any(e['type']=='tool.completed' for e in events)
    assert reply['text'] and samples>tool['pcm_samples_so_far']
    report={'input':'查询本机内存','events':events,'pcm_seconds':samples/16000,
            'ack_pcm_seconds':tool['pcm_samples_so_far']/16000,
            'audio_before_mcp':True,'physical_playback_tested':False}
    output=Path(__file__).resolve().parents[2]/'diagnostics/2026-10-28/rex-slots-v2/voice-smoke.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':asyncio.run(main())
