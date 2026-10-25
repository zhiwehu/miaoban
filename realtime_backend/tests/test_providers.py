import asyncio
import copy
import json
import unittest
from aiohttp import web, ClientSession
from aiohttp.test_utils import TestServer
from voicehub.config import DEFAULT
from voicehub.models import Pipeline, ProviderError


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app=web.Application()
        async def chat(request):
            self.request=await request.json()
            response=web.StreamResponse(headers={'Content-Type':'text/event-stream'})
            await response.prepare(request)
            for text in ['你','好。']:
                event='data: '+json.dumps({'choices':[{'delta':{'content':text}}]})+'\n\n'
                for offset in range(0,len(event),7):
                    await response.write(event[offset:offset+7].encode())
            await response.write(b'data: [DONE]\n\n')
            return response
        async def transcribe(request):
            reader=await request.multipart()
            first=await reader.next()
            self.wav=await first.read()
            response=web.StreamResponse(headers={'Content-Type':'application/json'})
            await response.prepare(request)
            for chunk in [b'{"text":', b'"hello"}']:
                await response.write(chunk)
                await asyncio.sleep(.01)
            return response
        async def speech(request):
            return web.Response(body=b'\0\0'*2400,content_type='application/octet-stream')
        app.router.add_post('/chat/completions',chat)
        app.router.add_post('/audio/transcriptions',transcribe)
        app.router.add_post('/audio/speech',speech)
        self.server=TestServer(app)
        await self.server.start_server()
        self.http=ClientSession()
        config=copy.deepcopy(DEFAULT)
        for key in ('asr','llm','tts'):
            config[key]['provider']='http'
            config[key]['url']=str(self.server.make_url('/')).rstrip('/')
        self.pipeline=Pipeline(config,self.http,None)

    async def asyncTearDown(self):
        await self.http.close()
        await self.server.close()

    async def test_fragmented_sse_reassembles_and_keeps_history(self):
        result=''.join([text async for text in self.pipeline.chat([{'role':'user','content':'问题'}])])
        self.assertEqual(result,'你好。')
        self.assertEqual(self.request['messages'][-1]['content'],'问题')

    async def test_asr_reads_fragmented_json_and_sends_wav(self):
        import numpy as np
        self.assertEqual(await self.pipeline.transcribe(np.zeros(16000,np.float32)),'hello')
        self.assertEqual(self.wav[:4],b'RIFF')

    async def test_tts_resamples_streaming_pcm(self):
        result=b''.join([block async for block in self.pipeline.speech('你好')])
        self.assertAlmostEqual(len(result)/32000,.1,places=2)

class LocalCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_noncooperative_work_does_not_block_cancellation_or_the_loop(self):
        import threading
        from voicehub.models import LocalModels
        local=LocalModels();release=threading.Event();finished=threading.Event()
        def generate(stop):
            try:
                yield 'first'
                release.wait(5)
                yield 'late'
            finally:
                finished.set()
        source=local.stream(generate)
        try:
            self.assertEqual(await anext(source),'first')
            await asyncio.wait_for(source.aclose(),.2)
            self.assertFalse(finished.is_set())
            # The actual worker remains tracked until it exits.
            self.assertTrue(local.active_stops)
            release.set()
            for _ in range(100):
                if not local.active_stops:
                    break
                await asyncio.sleep(.01)
            self.assertFalse(local.active_stops)
        finally:
            release.set();local.close()
