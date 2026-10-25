import asyncio
import unittest
import threading
import time
from types import SimpleNamespace
import numpy as np
from aiohttp.test_utils import TestClient, TestServer
from voicehub.qwen_tts import create_tts_app, MODEL, QwenVoice

class FakeVoice:
    def __init__(self):
        self.calls=[]
        self.release=asyncio.Event()
    async def warm(self):pass
    async def close(self):pass
    async def speech(self,text,voice):
        self.calls.append((text,voice))
        yield b'\x01\x00'*240
        await self.release.wait()
        yield b'\x02\x00'*240

class QwenTTS(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.voice=FakeVoice()
        self.client=TestClient(TestServer(create_tts_app(self.voice)))
        await self.client.start_server()
    async def asyncTearDown(self):
        self.voice.release.set()
        await self.client.close()
    def payload(self,**kwargs):
        return dict(model=MODEL,input='你好😊',voice='Serena',response_format='pcm',**kwargs)
    async def test_streams_raw_pcm_before_synthesis_finishes(self):
        async with self.client.post('/v1/audio/speech',json=self.payload()) as response:
            self.assertEqual(response.status,200)
            self.assertEqual(response.content_type,'audio/pcm')
            first=await asyncio.wait_for(response.content.readexactly(480),.3)
            self.assertEqual(first,b'\x01\x00'*240)
            self.assertEqual(self.voice.calls,[('你好','Serena')])
            self.voice.release.set()
            self.assertEqual(await response.read(),b'\x02\x00'*240)
    async def test_rejects_other_models_or_formats_before_inference(self):
        for key,value in [('model','https://untrusted/model'),('voice','unknown'),('response_format','wav'),('input','x'*2001)]:
            payload=self.payload();payload[key]=value
            response=await self.client.post('/v1/audio/speech',json=payload)
            self.assertEqual(response.status,400)
        self.assertEqual(self.voice.calls,[])
    async def test_cross_origin_or_rebinding_host_cannot_run_local_inference(self):
        for headers in [{'Origin':'https://untrusted.example'},{'Host':'untrusted.example'}]:
            response=await self.client.post('/v1/audio/speech',json=self.payload(),headers=headers)
            self.assertEqual(response.status,403)
        self.assertEqual(self.voice.calls,[])
    async def test_health_identifies_loaded_model_and_sample_rate(self):
        response=await self.client.get('/health')
        data=await response.json()
        self.assertTrue(data['ready'])
        self.assertEqual(data['model'],MODEL)
        self.assertEqual(data['sample_rate'],24000)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def check_delivery(self, delay, buffered):
        release = threading.Event()
        first_generated = threading.Event()
        class Model:
            def generate(self,*args,**kwargs):
                time.sleep(delay)
                first_generated.set()
                yield SimpleNamespace(sample_rate=24000,audio=np.full(2400,.1,np.float32))
                release.wait(1)
                yield SimpleNamespace(sample_rate=24000,audio=np.full(2400,.2,np.float32))
        voice=QwenVoice();voice.model=Model()
        stream=voice.speech('测试。','Serena')
        pending=asyncio.create_task(anext(stream))
        try:
            self.assertTrue(await asyncio.to_thread(first_generated.wait,1))
            if buffered:
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(pending),.03)
            else:
                self.assertEqual(len(await asyncio.wait_for(asyncio.shield(pending),.2)),4800)
            release.set()
            if buffered:self.assertEqual(len(await pending),4800)
            self.assertEqual(sum([len(b) async for b in stream]),4800)
        finally:
            release.set()
            await asyncio.gather(pending,return_exceptions=True)
            await stream.aclose();await voice.close()

    async def test_slow_generation_waits_for_sentence_before_playback(self):
        await self.check_delivery(.15,True)

    async def test_fast_generation_keeps_first_chunk_streaming(self):
        await self.check_delivery(.001,False)
