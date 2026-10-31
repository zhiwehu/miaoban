import asyncio
import unittest
from unittest.mock import Mock, patch
import numpy as np
import xiaozhi_server as server


class BargeInTests(unittest.IsolatedAsyncioTestCase):
    async def test_playback_audio_cannot_cancel_turn_or_feed_idle_vad(self):
        task = asyncio.create_task(asyncio.sleep(10))
        decoder = Mock()
        decoder.decode.return_value = np.full(960, .2, np.float32)
        turn_vad, barge_vad = Mock(), Mock()
        barge_vad.speaking = True
        try:
            with patch.dict(server.STATE, {'voice_enabled': True}), \
                 patch.object(server, 'DECODER', decoder), \
                 patch.object(server, 'VAD_TURN', turn_vad), \
                 patch.object(server, 'VAD_BARGE', barge_vad), \
                 patch.object(server, 'TTS_TASK', task), \
                 patch.object(server, 'cancel_turn') as cancel:
                await server._on_mic_audio(b'packet')
                cancel.assert_not_called()
                turn_vad.feed.assert_not_called()
                turn_vad.reset.assert_called_once()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_inference_turn_cannot_be_replaced_by_noise_before_first_audio(self):
        from types import SimpleNamespace
        decoder = Mock()
        decoder.decode.return_value = np.full(960, .2, np.float32)
        vad = Mock()
        with patch.dict(server.STATE, {'voice_enabled': True}), \
             patch.object(server, 'DECODER', decoder), \
             patch.object(server, 'VAD_TURN', vad), \
             patch.object(server, 'VAD_BARGE', Mock()), \
             patch.object(server, 'TTS_TASK', None), \
             patch.object(server, 'ACTIVE_TURN', SimpleNamespace(current=lambda: True)), \
             patch.object(server, 'start_turn') as start:
            await server._on_mic_audio(b'packet')
            start.assert_not_called()
            vad.feed.assert_not_called()
