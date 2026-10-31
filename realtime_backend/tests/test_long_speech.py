"""The 30-second idle timer must never cut speech in either direction."""
import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from voicehub import session as session_module
from voicehub.session import Session
from voicehub.store import Store


class _Pipeline:
    def __init__(self):
        self.speech_calls = []

    async def chat(self, history):
        yield '你好。'

    async def speech(self, text):
        self.speech_calls.append(text)
        yield b'\0\0' * 320

    async def transcribe(self, pcm):
        return '你好'


class IdleDuringSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.db')
        self.events = []
        self.pipeline = _Pipeline()

        async def send(event):
            self.events.append(event)

        self.session = Session('one', 'device', self.store, self.pipeline, send,
                               idle_seconds=30)

    async def asyncTearDown(self):
        await self.session.close()
        self.store.close()
        self.tmp.cleanup()

    def _standby(self):
        return any(e['type'] == 'session.state' and e.get('state') == 'standby'
                   for e in self.events)

    async def test_long_user_speech_is_not_mistaken_for_idle(self):
        self.session.state = 'listening'
        self.session.input_active = True      # 用户正在连续说话
        self.session.last_activity = time.monotonic() - 120
        await self.session.idle()
        self.assertFalse(self._standby())

    async def test_long_playback_is_not_mistaken_for_idle(self):
        self.session.state = 'speaking'
        self.session.streaming = True         # audio.start 到 audio.end 之间
        self.session.last_activity = time.monotonic() - 120
        await self.session.idle()
        self.assertFalse(self._standby())

    async def test_active_turn_is_not_mistaken_for_idle(self):
        self.session.state = 'thinking'
        self.session.turn = session_module.Turn()
        self.session.last_activity = time.monotonic() - 120
        await self.session.idle()
        self.assertFalse(self._standby())

    async def test_real_silence_still_reaches_standby(self):
        self.session.state = 'listening'
        self.session.input_active = False
        self.session.streaming = False
        self.session.last_activity = time.monotonic() - 120
        await self.session.idle()
        self.assertTrue(self._standby())


class ReplyAudioBudgetTests(unittest.TestCase):
    def test_reply_budget_allows_a_long_but_bounded_playback(self):
        # 30 秒和 90 秒都曾经把正常播报截断；这个上限只该拦住失控的合成。
        self.assertGreaterEqual(session_module.MAX_REPLY_AUDIO_SECONDS, 120)


if __name__ == '__main__':
    unittest.main()
