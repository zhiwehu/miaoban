import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from voicehub.config import DEFAULT
from voicehub.input import AudioInput
from voicehub.session import Session
from voicehub.store import Store
from tests.test_sessions import Pipeline


class Speech:
    def __init__(self, path):
        pass
    def probability(self, frame):
        return .99


class IngressTests(unittest.IsolatedAsyncioTestCase):
    async def check_input(self, aec):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'test.db');events=[]
            async def send(event):
                events.append(event)
            pipeline=Pipeline()
            session=Session('one','device',store,pipeline,send)
            await session.submit('正在回复')
            await pipeline.started.wait()
            old=session.turn
            with patch('voicehub.input.Silero',Speech),patch('voicehub.input.vad_path',return_value='fixture'):
                ingress=AudioInput(session,'pcm_s16le',aec,DEFAULT['audio'])
                try:
                    for _ in range(8):
                        ingress.put(np.full(512,10000,dtype='<i2').tobytes())
                    for _ in range(100):
                        if ingress.queue.empty():
                            await asyncio.sleep(.02)
                            break
                        await asyncio.sleep(.01)
                    return old.cancelled,events
                finally:
                    await ingress.close()
                    await session.close()
                    store.close()

    async def test_confirmed_speech_interrupts_when_client_has_aec(self):
        cancelled,events=await self.check_input(True)
        self.assertTrue(cancelled)
        self.assertTrue(any(e['type']=='playback.clear' and e.get('reason')=='speech' for e in events))

    async def test_no_aec_never_turns_playback_echo_into_automatic_interruption(self):
        cancelled,events=await self.check_input(False)
        self.assertFalse(cancelled)
        self.assertFalse(any(e['type']=='playback.clear' and e.get('reason')=='speech' for e in events))

    async def test_audio_queue_is_bounded_and_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'test.db')
            async def send(event):
                pass
            session=Session('one','device',store,Pipeline(),send)
            ingress=AudioInput(session,'pcm_s16le',True,DEFAULT['audio'])
            try:
                with self.assertRaisesRegex(ValueError,'audio_overload'):
                    for _ in range(65):
                        ingress.put(b'\0'*1280)
            finally:
                await ingress.close();await session.close();store.close()
