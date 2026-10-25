import asyncio
import time
import tempfile
import unittest
from pathlib import Path
from voicehub.store import Store
from voicehub.session import Session


class Pipeline:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, history):
        self.started.set()
        yield '你好。'
        await self.release.wait()
        yield '旧回复。'

    async def speech(self, text):
        yield b'\0\0' * 320

    async def transcribe(self, pcm):
        return '你好'


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.db')
        self.events = []
        self.pipeline = Pipeline()
        async def send(event):
            self.events.append(event)
        self.session = Session('one', 'browser', self.store, self.pipeline, send)

    async def asyncTearDown(self):
        await self.session.close()
        self.store.close()
        self.tmp.cleanup()

    async def test_interrupt_invalidates_old_output_and_does_not_wait_for_provider(self):
        await self.session.submit('第一句话')
        await self.pipeline.started.wait()
        old = self.session.turn.id
        await asyncio.wait_for(self.session.interrupt('speech'), .2)
        marker = len(self.events)
        self.pipeline.release.set()
        await asyncio.sleep(.03)
        self.assertTrue(any(e['type'] == 'playback.clear' for e in self.events))
        self.assertFalse(any(e.get('turn_id') == old and e['type'] in ('audio.chunk', 'reply.delta')
                             for e in self.events[marker:]))
        self.assertEqual(self.store.messages('one')[-1]['status'], 'interrupted')

    async def test_connections_keep_history_and_cancellation_separate(self):
        other_events = []
        async def send(event):
            other_events.append(event)
        p = Pipeline()
        other = Session('two', 'phone', self.store, p, send)
        try:
            await self.session.submit('甲')
            await other.submit('乙')
            await p.started.wait()
            await self.session.interrupt('manual')
            self.assertFalse(other.turn.cancelled)
            self.assertEqual(self.store.messages('one')[0]['content'], '甲')
            self.assertEqual(self.store.messages('two')[0]['content'], '乙')
            self.assertTrue(all(e['session_id'] == 'two' for e in other_events))
        finally:
            await other.close()

    async def test_close_cancels_turn_and_keeps_history(self):
        await self.session.submit('保留我')
        await self.pipeline.started.wait()
        await self.session.close()
        self.assertEqual(self.session.state, 'disconnected')
        self.assertEqual(self.store.messages('one')[0]['content'], '保留我')

    async def test_completion_waits_for_matching_playback_ack(self):
        self.pipeline.release.set()
        await self.session.submit('测试')
        for _ in range(100):
            if any(e['type'] == 'audio.end' for e in self.events):
                break
            await asyncio.sleep(.01)
        turn = self.session.turn
        self.assertEqual(self.session.state, 'speaking')
        self.session.playback_done('wrong')
        self.assertFalse(turn.played.is_set())
        self.session.playback_done(turn.id)
        await asyncio.wait_for(turn.task, 1)
        self.assertEqual(self.session.state, 'listening')
        self.assertEqual(self.store.messages('one')[-1]['status'], 'played')

    async def test_backend_wake_phrase_and_question_in_one_utterance(self):
        self.session.wake_mode='backend'
        self.session.wake_phrase='小宝同学'
        self.pipeline.release.set()
        async def transcribe(pcm):
            return '小宝同学，今天星期几？'
        self.pipeline.transcribe=transcribe
        await self.session.submit(pcm=b'input')
        for _ in range(50):
            if self.store.messages('one'):
                break
            await asyncio.sleep(.01)
        self.assertEqual(self.store.messages('one')[0]['content'],'今天星期几？')

    async def test_backend_wake_rejects_unrelated_speech_without_journalling_transcript(self):
        self.session.wake_mode='backend'
        async def transcribe(pcm):
            return '这是未唤醒时的背景谈话'
        self.pipeline.transcribe=transcribe
        await self.session.submit(pcm=b'input')
        await self.session.turn.task
        self.assertEqual(self.session.state,'standby')
        self.assertFalse(self.store.messages('one'))
        self.assertFalse(any(e['type']=='transcript.final' for e in self.events))

    async def test_next_sentence_is_synthesized_before_first_finishes_sending(self):
        sent = 0
        second_started_at = []
        first_size = 32000  # One second of PCM, longer than the bounded prefetch buffer.
        class Speech:
            async def chat(self, history):
                yield '第一句。'
                yield '第二句。'
            async def speech(self, text):
                if text.startswith('第二'):
                    second_started_at.append(sent)
                    await asyncio.sleep(.1)
                    yield b'\0\0' * 320
                else:
                    yield b'\0' * first_size
        async def send(event):
            nonlocal sent
            if event['type'] == 'audio.chunk':
                import base64
                sent += len(base64.b64decode(event['data']))
        self.session.pipeline = Speech()
        self.session.send = send
        self.session.playback_ack = False
        await self.session.submit('测试预取')
        await self.session.turn.task
        self.assertTrue(second_started_at)
        self.assertLess(second_started_at[0], first_size)
        self.assertEqual(sent, first_size + 640)

    async def test_send_overhead_does_not_accumulate_in_playback_clock(self):
        chunks = []
        ended = []
        class Speech:
            async def chat(self, history):
                yield '连续播放。'
            async def speech(self, text):
                for _ in range(20):
                    yield b'\0\0' * 640  # 800 ms total.
        async def send(event):
            if event['type'] == 'audio.chunk':
                chunks.append(time.monotonic())
                await asyncio.sleep(.015)  # Encoding/network overhead must fit inside the clock.
            elif event['type'] == 'audio.end':
                ended.append(time.monotonic())
        self.session.pipeline = Speech()
        self.session.send = send
        self.session.playback_ack = False
        await self.session.submit('测试连续播放')
        await self.session.turn.task
        self.assertEqual(len(chunks), 20)
        self.assertTrue(ended)
        self.assertLess(ended[0] - chunks[0], .95)
