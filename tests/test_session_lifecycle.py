import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import xiaozhi_server as server

class FakeWS:
    def __init__(self):
        self.end = asyncio.Event()
        self.audio = []
        self.messages = []
    async def prepare(self, request): pass
    async def receive(self): return SimpleNamespace(data=json.dumps({'type': 'hello'}))
    async def send_str(self, text): self.messages.append(json.loads(text))
    async def send_bytes(self, packet): self.audio.append(packet)
    def __aiter__(self): return self
    async def __anext__(self):
        await self.end.wait()
        raise StopAsyncIteration

class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_state = server.STATE.copy()
        server.WS = None
        server.ACTIVE_TURN = server.TTS_TASK = server.VOICE_LOCK = None
        server.STATE.update(tunnel_mode=False, history=[], tts_cancel=False)
    async def asyncTearDown(self):
        server.cancel_turn()
        if server.ACTIVE_TURN:
            await asyncio.gather(server.ACTIVE_TURN.task, return_exceptions=True)
        server.WS = None
        server.ACTIVE_TURN = server.TTS_TASK = server.VOICE_LOCK = None
        server.STATE.clear(); server.STATE.update(self.old_state)
    async def test_old_connection_cannot_clear_replacement(self):
        first, second = FakeWS(), FakeWS()
        request = SimpleNamespace(remote='test', path='/xiaozhi/v1/', headers={})
        with patch.object(server.web, 'WebSocketResponse', side_effect=[first, second]):
            a = asyncio.create_task(server.device_handler(request)); await asyncio.sleep(.01)
            b = asyncio.create_task(server.device_handler(request)); await asyncio.sleep(.01)
            first.end.set(); await a
            self.assertIs(server.WS, second)
            self.assertIsNotNone(server.STATE['device'])
            second.end.set(); await b
            self.assertIsNone(server.WS)
    async def test_abort_during_llm_cannot_revive_speech(self):
        entered, release = threading.Event(), threading.Event()
        class Brain:
            def transcribe(self, *args): return '一加一等于几'
            def chat(self, *args): entered.set(); release.wait(2); return '等于二'
            def tts_stream(self, *args, **kwargs):
                raise AssertionError('Cancelled reply must not enter TTS')
        ws = FakeWS(); server.WS = ws
        with patch.object(server, 'brain', return_value=Brain()):
            task = server.start_turn(lambda turn: server.handle_turn(np.zeros(32000), turn))
            try:
                async with asyncio.timeout(2):
                    while not entered.is_set(): await asyncio.sleep(.001)
                server.cancel_turn()
            finally: release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(ws.audio, [])
        self.assertEqual(server.STATE['history'], [])
    async def test_replacement_waits_for_cancelled_inference_worker(self):
        entered, release = threading.Event(), threading.Event()
        active = 0; peak = 0
        def inference():
            nonlocal active, peak
            active += 1; peak = max(peak, active)
            entered.set(); release.wait(2)
            active -= 1
        async def call(turn): await server.run_model(inference)
        server.WS = FakeWS()
        first = server.start_turn(call)
        async with asyncio.timeout(2):
            while not entered.is_set(): await asyncio.sleep(.001)
        second = server.start_turn(call)
        await asyncio.sleep(.02)
        self.assertEqual(peak, 1)
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
        self.assertEqual(peak, 1)

    async def test_current_turn_delivers_audio_and_finishes(self):
        class Brain:
            def transcribe(self, *args): return '一加一等于几'
            def chat(self, *args): return '[emotion:neutral]\n等于二'
            def tts_stream(self, *args, stop_event=None):
                yield np.zeros(1920, dtype=np.float32), 16000
        ws = FakeWS(); server.WS = ws
        with patch.object(server, 'brain', return_value=Brain()):
            task = server.start_turn(lambda turn: server.handle_turn(np.zeros(32000), turn))
            await asyncio.wait_for(task, 3)
        self.assertTrue(ws.audio)
        self.assertEqual([m['emotion'] for m in ws.messages if m['type']=='llm'], ['thinking','neutral'])
        self.assertEqual(server.STATE['last_reply'], '等于二')
        self.assertEqual(server.STATE['history'][-1]['content'], '等于二')
        self.assertFalse(any('[emotion:' in m.get('text','') for m in ws.messages))
        self.assertEqual(ws.messages[-1], {'type': 'tts', 'state': 'stop'})
        self.assertIsNone(server.ACTIVE_TURN)
        self.assertIsNone(server.TTS_TASK)

    async def test_abort_stops_audio_producer_before_turn_exits(self):
        exited = threading.Event()
        class Brain:
            def transcribe(self, *args): return '讲句话'
            def chat(self, *args): return '你好'
            def tts_stream(self, *args, stop_event=None):
                try:
                    yield np.zeros(1920, dtype=np.float32), 16000
                    stop_event.wait(2)
                finally: exited.set()
        ws = FakeWS(); server.WS = ws
        with patch.object(server, 'brain', return_value=Brain()):
            task = server.start_turn(lambda turn: server.handle_turn(np.zeros(32000), turn))
            async with asyncio.timeout(2):
                while not ws.audio: await asyncio.sleep(.001)
            count = len(ws.audio)
            server.cancel_turn()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(exited.is_set())
        self.assertEqual(len(ws.audio), count)
        self.assertIsNone(server.ACTIVE_TURN)

    async def test_first_audio_precedes_llm_completion_and_tail_is_spoken(self):
        release = threading.Event()
        spoken = []
        class Brain:
            def transcribe(self, *args): return '介绍一下你'
            def chat_stream(self, history, stop_event=None):
                yield '[emotion:happy]\n你好！'
                if not release.wait(2): raise RuntimeError('First audio waited for full LLM')
                yield '我是你的喵伴。'
            def tts_stream(self, sentence, stop_event=None):
                spoken.append(sentence)
                yield np.zeros(1920, dtype=np.float32), 16000
        ws = FakeWS(); server.WS = ws
        with patch.object(server, 'brain', return_value=Brain()):
            task = server.start_turn(lambda turn: server.handle_turn(np.zeros(32000), turn))
            try:
                async with asyncio.timeout(1):
                    while not ws.audio: await asyncio.sleep(.001)
                self.assertFalse(task.done())
            finally: release.set()
            await asyncio.wait_for(task, 3)
        self.assertEqual(spoken, ['你好！', '我是你的喵伴。'])
        self.assertEqual(server.STATE['history'][-1]['content'], '你好！我是你的喵伴。')
        self.assertEqual(ws.messages[-1], {'type':'tts', 'state':'stop'})

    async def test_late_hello_cannot_overwrite_replacement_session(self):
        release = asyncio.Event()
        class SlowWS(FakeWS):
            async def receive(self):
                await release.wait()
                return SimpleNamespace(data=json.dumps({'type': 'hello'}))
        first, second = SlowWS(), FakeWS()
        old_request = SimpleNamespace(remote='old', path='/xiaozhi/v1/', headers={})
        new_request = SimpleNamespace(remote='new', path='/xiaozhi/v1/', headers={})
        with patch.object(server.web, 'WebSocketResponse', side_effect=[first, second]):
            a = asyncio.create_task(server.device_handler(old_request)); await asyncio.sleep(.01)
            b = asyncio.create_task(server.device_handler(new_request)); await asyncio.sleep(.01)
            release.set(); await a
            self.assertIs(server.WS, second)
            self.assertEqual(server.STATE['device']['peer'], 'new')
            second.end.set(); await b

    async def test_tts_failure_stops_playback_and_releases_turn(self):
        class Brain:
            def transcribe(self,*args):return '请说一句话'
            def chat(self,*args):return '你好。'
            def tts_stream(self,*args,**kwargs):
                raise RuntimeError('Synthetic failure')
                yield
        ws=FakeWS();server.WS=ws
        with patch.object(server,'brain',return_value=Brain()):
            task=server.start_turn(lambda turn:server.handle_turn(np.zeros(32000),turn))
            await asyncio.wait_for(task,2)
        self.assertEqual(ws.messages[-1],{'type':'tts','state':'stop'})
        self.assertEqual(server.STATE['history'],[])
        self.assertIsNone(server.ACTIVE_TURN)

    async def test_device_wake_queues_audible_acknowledgement(self):
        from aiohttp import WSMsgType
        class WakeWS(FakeWS):
            def __init__(self):
                super().__init__()
                self.inputs=iter([
                    {'type':'listen','state':'detect','text':'小宝同学'},
                    {'type':'listen','state':'start','mode':'auto'}])
            async def __anext__(self):
                try: value=next(self.inputs)
                except StopIteration: raise StopAsyncIteration
                return SimpleNamespace(type=WSMsgType.TEXT,data=json.dumps(value))
        ws=WakeWS()
        request=SimpleNamespace(remote='test',path='/xiaozhi/v1/',headers={})
        server.STATE.pop('pending_reply',None)
        with patch.object(server.web,'WebSocketResponse',return_value=ws), \
             patch.object(server,'start_turn') as start:
            await server.device_handler(request)
            start.assert_called_once()
            self.assertNotIn('pending_reply',server.STATE)

    async def test_trailing_emotion_reaches_device_without_becoming_speech(self):
        spoken=[]
        class Brain:
            def transcribe(self,*args):return '我今天很开心'
            def chat_stream(self,*args,**kwargs):
                yield '太棒了！'
                yield '[emo'
                yield 'tion:happy]'
            def tts_stream(self,text,**kwargs):
                spoken.append(text)
                yield np.zeros(1920,dtype=np.float32),16000
        ws=FakeWS();server.WS=ws
        with patch.object(server,'brain',return_value=Brain()):
            task=server.start_turn(lambda turn:server.handle_turn(np.zeros(32000),turn))
            await asyncio.wait_for(task,3)
        self.assertEqual(spoken,['太棒了！'])
        self.assertIn({'type':'llm','emotion':'happy'},ws.messages)
        self.assertEqual(server.STATE['last_emotion'],'happy')
        self.assertEqual(server.STATE['last_reply'],'太棒了！')
