import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np

from voicehub.input import AudioInput
from voicehub.recording import Recordings
from voicehub.session import Session
from voicehub.store import Store


async def wait_for(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


class Pipeline:
    def __init__(self):
        self.chat_called = False
        self.spoken = []
        self.transcripts = []

    async def chat(self, history):
        self.chat_called = True
        yield '录音模式里不应该出现的回复。'

    async def speech(self, text):
        self.spoken.append(text)
        yield b'\0\0'*320

    async def transcribe(self, pcm):
        return self.transcripts.pop(0) if self.transcripts else '无关内容'


class RecordingSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root/'test.db')
        self.pipeline = Pipeline()
        self.manager = Recordings(root/'data')
        self.events = []

        async def send(event):
            self.events.append(event)

        self.session = Session('recording-one', 'browser', self.store, self.pipeline, send,
                               playback_ack=False, recording_manager=self.manager)

    async def asyncTearDown(self):
        await self.session.close()
        self.manager.close()
        self.store.close()
        self.tmp.cleanup()

    async def test_voice_start_switches_to_recording_without_chat_or_journal(self):
        await self.session.submit('开始录音模式')
        await asyncio.wait_for(self.session.turn.task, 2)
        self.assertIsNotNone(self.session.recording)
        self.assertEqual(self.session.state, 'recording')
        self.assertFalse(self.pipeline.chat_called)
        self.assertEqual(self.store.messages(self.session.id), [])
        self.assertIn('开始录音', self.pipeline.spoken[0])
        self.assertTrue(any(event['type'] == 'recording.started' for event in self.events))
        self.assertFalse(any(event['type'] in ('message.user', 'transcript.final', 'reply.final')
                             for event in self.events))

    async def test_transcribed_start_command_uses_the_voice_path(self):
        self.pipeline.transcripts = ['开始录音模式']
        await self.session.submit(pcm=np.zeros(1600, dtype=np.float32))
        await asyncio.wait_for(self.session.turn.task, 2)
        self.assertIsNotNone(self.session.recording)
        self.assertEqual(self.session.recording.trigger, 'voice')
        self.assertFalse(self.pipeline.chat_called)
        self.assertEqual(self.store.messages(self.session.id), [])

    async def test_duration_tick_stops_and_speaks_a_saved_notice(self):
        await self.session.start_recording('web')
        self.manager.max_seconds = .01
        self.session.recording.write(b'\0\0'*200)
        await self.session.recording_tick()
        self.assertIsNone(self.session.recording)
        self.assertEqual(self.session.state, 'listening')
        self.assertTrue(any('录音已停止' in text for text in self.pipeline.spoken))
        metadata = self.manager.list()[0]
        self.assertEqual(metadata['reason'], 'limit')

    async def test_web_stop_finalizes_the_file_and_returns_to_listening(self):
        recording = await self.session.start_recording('web')
        recording.write(b'\x01\x00'*1600)
        metadata = await self.session.stop_recording('web')
        self.assertEqual(metadata['id'], recording.id)
        self.assertIsNone(self.session.recording)
        self.assertEqual(self.session.state, 'listening')
        self.assertTrue(self.manager.audio_path(recording.id).is_file())
        self.assertTrue(any(event['type'] == 'recording.stopped' for event in self.events))
        self.assertFalse(self.pipeline.chat_called)

    async def test_voice_stop_needs_a_second_confirmation(self):
        await self.session.start_recording('web')
        self.session.recording_listen_after = 0
        with patch.object(self.session, '_speak_recording_prompt', new=AsyncMock()):
            self.pipeline.transcripts = ['停止录音模式']
            self.session.recording_heard(np.zeros(1600, dtype=np.float32))
            await wait_for(lambda: self.session.recording.confirming)
            self.assertIsNotNone(self.session.recording)
            self.assertTrue(any(event['type'] == 'recording.confirming' for event in self.events))

            self.pipeline.transcripts = ['确认停止']
            self.session.recording_heard(np.zeros(1600, dtype=np.float32))
            await wait_for(lambda: self.session.recording is None
                           and self.session.state == 'listening')
        self.assertEqual(self.session.state, 'listening')
        self.assertTrue(any(event['type'] == 'recording.stopped' for event in self.events))
        self.assertTrue(any('录音已停止' in text for text in self.pipeline.spoken))
        transcript = self.manager.transcript(self.manager.list()[0]['id'])
        self.assertEqual(transcript['count'], 0)  # control phrases are not dictation

    async def test_ordinary_speech_during_recording_is_not_transcribed_into_a_turn(self):
        await self.session.start_recording('web')
        self.session.recording_listen_after = 0
        self.pipeline.transcripts = ['把会议室灯关一下']
        self.session.recording_heard(np.zeros(1600, dtype=np.float32))
        await wait_for(lambda: any(event['type'] == 'recording.transcript'
                                   for event in self.events))
        self.assertIsNotNone(self.session.recording)
        self.assertIsNone(self.session.turn)
        self.assertFalse(self.pipeline.chat_called)
        self.assertFalse(any(event['type'] == 'message.user' for event in self.events))
        event = next(event for event in self.events if event['type'] == 'recording.transcript')
        self.assertEqual(event['line'], 1)
        self.assertEqual(event['text'], '把会议室灯关一下')
        transcript = self.manager.transcript(self.session.recording.id)
        self.assertEqual(transcript['text'], '把会议室灯关一下')
        self.assertEqual(transcript['count'], 1)

    async def test_each_pause_becomes_one_transcript_line(self):
        recording = await self.session.start_recording('web')
        self.session.recording_listen_after = 0
        self.pipeline.transcripts = ['第一段话。', '第二段话。']
        self.session.recording_heard(np.zeros(1600, dtype=np.float32))
        self.session.recording_heard(np.zeros(1600, dtype=np.float32))
        await wait_for(lambda: self.manager.transcript(recording.id)['count'] >= 2)
        transcript = self.manager.transcript(recording.id)
        self.assertEqual(transcript['text'], '第一段话。\n第二段话。')
        self.assertEqual(transcript['count'], 2)
        events = [event for event in self.events if event['type'] == 'recording.transcript']
        self.assertEqual([event['line'] for event in events], [1, 2])
        self.assertEqual([event['text'] for event in events], ['第一段话。', '第二段话。'])
        metadata = await self.session.stop_recording('web')
        self.assertTrue(metadata['transcript_available'])
        self.assertEqual(metadata['transcript_lines'], 2)
        self.assertEqual(self.manager.transcript(recording.id)['status'], 'stopped')

    async def test_stop_waits_for_the_last_transcript_line(self):
        recording = await self.session.start_recording('web')
        self.session.recording_listen_after = 0

        async def transcribe(pcm):
            await asyncio.sleep(.2)
            return '最后一句'

        self.pipeline.transcribe = transcribe
        self.session.recording_heard(np.zeros(1600, dtype=np.float32))
        await asyncio.sleep(.05)
        metadata = await self.session.stop_recording('web')
        transcript = self.manager.transcript(recording.id)
        self.assertEqual(transcript['text'], '最后一句')
        self.assertEqual(transcript['count'], 1)
        self.assertEqual(metadata['transcript_lines'], 1)

    async def test_idle_timeout_never_ends_recording_mode(self):
        await self.session.start_recording('web')
        self.session.state = 'recording'
        self.session.last_activity = time.monotonic()-self.session.idle_seconds-10
        await self.session.idle()
        self.assertEqual(self.session.state, 'recording')
        self.assertIsNotNone(self.session.recording)

    async def test_close_finalizes_an_active_recording(self):
        recording = await self.session.start_recording('device')
        recording.write(b'\0\0'*800)
        await self.session.close()
        self.assertIsNone(self.session.recording)
        metadata = self.manager.get(recording.id)
        self.assertEqual(metadata['status'], 'stopped')
        self.assertEqual(metadata['reason'], 'disconnect')
        self.assertFalse(self.manager.active)

    async def test_audio_input_keeps_recording_when_vad_is_unavailable(self):
        await self.session.start_recording('web')
        config = {'speech_threshold': .65, 'onset_ms': 160, 'silence_ms': 640,
                  'max_utterance_seconds': 15, 'vad_model': '', 'barge_in': True}
        with patch('voicehub.input.vad_path', side_effect=ValueError('no vad')):
            ingress = AudioInput(self.session, 'pcm_s16le', True, config)
            try:
                for _ in range(5):
                    ingress.put(b'\x01\x00'*640)
                await wait_for(lambda: self.session.recording.samples >= 3200)
                await wait_for(lambda: any(
                    event['type'] == 'recording.transcription_unavailable'
                    for event in self.events))
            finally:
                await ingress.close()
        metadata = await self.session.stop_recording('web')
        self.assertEqual(metadata['samples'], 3200)
        self.assertTrue(any(event['type'] == 'recording.transcription_unavailable'
                            for event in self.events))

    async def test_http_asr_never_receives_recording_audio_for_stop_detection(self):
        self.pipeline.config = {'asr': {'provider': 'http'}}
        self.assertFalse(self.session.recording_local_asr_allowed())
        await self.session.start_recording('web')
        config = {'speech_threshold': .65, 'onset_ms': 160, 'silence_ms': 640,
                  'max_utterance_seconds': 15, 'vad_model': '', 'barge_in': True}
        with patch('voicehub.input.vad_path') as vad:
            ingress = AudioInput(self.session, 'pcm_s16le', True, config)
            ingress.put(b'\x03\x00'*640)
            await wait_for(lambda: self.session.recording.samples >= 640)
            await ingress.close()
            vad.assert_not_called()
        self.assertTrue(any(event['type'] == 'recording.transcription_unavailable'
                            and '不会上传' in event.get('message', '')
                            for event in self.events))
        await self.session.stop_recording('web')

    async def test_channel_close_drains_queued_audio_before_finalizing(self):
        await self.session.start_recording('device')
        config = {'speech_threshold': .65, 'onset_ms': 160, 'silence_ms': 640,
                  'max_utterance_seconds': 15, 'vad_model': '', 'barge_in': True}
        with patch('voicehub.input.vad_path', side_effect=ValueError('no vad')):
            ingress = AudioInput(self.session, 'pcm_s16le', True, config)
            for _ in range(10):
                ingress.put(b'\x02\x00'*640)
            # Simulate the device key closing the WebSocket right after speech.
            await ingress.close()
        metadata = await self.session.stop_recording('disconnect')
        self.assertEqual(metadata['samples'], 6400)
        self.assertEqual(metadata['reason'], 'disconnect')
