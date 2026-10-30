import unittest
import numpy as np
from aiohttp.test_utils import TestClient, TestServer
from voicehub.model_worker import create_worker_app


class FakeEngine:
    def __init__(self): self.closed = False
    async def warm(self): pass
    async def close(self): self.closed = True
    async def chat(self, messages, max_tokens):
        yield '你好'
        yield '。'
    async def transcribe(self, pcm, language): return '识别成功'
    async def speech(self, text, voice): yield b'\x01\x00'*160


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def open(self, kind, engine=None):
        self.engine = engine or FakeEngine()
        self.client = TestClient(TestServer(create_worker_app(kind, 'test-model', 'instance', 'token', self.engine)))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        import asyncio
        for _ in range(100):
            r = await self.client.get('/health', headers={'Authorization': 'Bearer token'})
            if (await r.json())['ready']: break
            await asyncio.sleep(.01)
        return {'Authorization': 'Bearer token'}

    async def test_auth_and_llm_stream(self):
        h = await self.open('llm')
        self.assertEqual((await self.client.get('/health')).status, 401)
        response = await self.client.post('/v1/chat/completions', headers=h,
            json={'model':'test-model','messages':[{'role':'user','content':'你好'}], 'max_tokens':16,'stream':True})
        self.assertEqual(response.status, 200)
        text = await response.text()
        self.assertIn('你好', text)
        self.assertIn('[DONE]', text)
        r = await self.client.get('/health', headers=h)
        self.assertEqual((await r.json())['active'], 0)

    async def test_asr_upload_and_tts_pcm(self):
        import io, wave, aiohttp
        h = await self.open('asr')
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as w:
            w.setparams((1,2,16000,0,'NONE',''))
            w.writeframes(np.zeros(1600, dtype='<i2').tobytes())
        form = aiohttp.FormData()
        form.add_field('file',buf.getvalue(),filename='speech.wav',content_type='audio/wav')
        form.add_field('model','test-model')
        form.add_field('language','zh')
        r = await self.client.post('/v1/audio/transcriptions',headers=h,data=form)
        self.assertEqual(await r.json(), {'text':'识别成功'})
        await self.client.close()
        h = await self.open('tts')
        r = await self.client.post('/v1/audio/speech',headers=h,json={
            'model':'test-model','input':'你好','voice':'Serena','response_format':'pcm'})
        self.assertEqual(r.status,200)
        self.assertEqual(r.content_type,'audio/pcm')
        self.assertEqual(len(await r.read()),320)

    async def test_load_failure_is_visible(self):
        class Broken(FakeEngine):
            async def warm(self): raise RuntimeError('private path')
        h = await self.open('llm',Broken())
        r = await self.client.get('/health',headers=h)
        data = await r.json()
        self.assertFalse(data['ready'])
        self.assertEqual(data['error'],'local_model_load_failed')
        r = await self.client.post('/v1/chat/completions',headers=h,json={})
        self.assertEqual(r.status,503)

    async def test_invalid_request_shapes_are_rejected(self):
        h = await self.open('llm')
        for body in ([], {}, {'model':'test-model','messages':[], 'max_tokens':16,'stream':True}):
            r = await self.client.post('/v1/chat/completions',headers=h,json=body)
            self.assertEqual(r.status,400)
