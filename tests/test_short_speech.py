import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import xiaozhi_server as server

class ShortSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_vad_confirmed_short_utterance_starts_a_turn(self):
        # A short '你好' plus endpoint silence is less than one second.
        decoder=SimpleNamespace(decode=lambda _:np.zeros(960,dtype=np.float32))
        vad=SimpleNamespace(feed=lambda _:np.zeros(12800,dtype=np.float32))
        with patch.object(server,'DECODER',decoder),patch.object(server,'VAD_TURN',vad),patch.object(server,'TTS_TASK',None),patch.object(server,'start_turn') as start:
            old=server.STATE['voice_enabled'];server.STATE['voice_enabled']=True
            try:await server._on_mic_audio(b'frame')
            finally:server.STATE['voice_enabled']=old
        self.assertEqual(start.call_count,1)
