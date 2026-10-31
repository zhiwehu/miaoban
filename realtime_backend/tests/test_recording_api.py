import asyncio
import io
import tempfile
import unittest
import wave

from aiohttp.test_utils import TestClient, TestServer

from tests.test_sessions import Pipeline
from voicehub.app import HUB, create_app


async def wait_message(ws, kind, timeout=2):
    async with asyncio.timeout(timeout):
        while True:
            event = await ws.receive_json()
            if event.get('type') == kind:
                return event


class RecordingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, pipeline_factory=lambda c, h, l: Pipeline())
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.ws = await self.client.ws_connect('/ws')
        await self.ws.send_json({'type': 'hello', 'version': 1, 'device_id': 'recording-web',
                                 'audio': {'format': 'pcm_s16le', 'sample_rate': 16000},
                                 'capabilities': {'echo_cancellation': True}})
        self.hello = await self.ws.receive_json()
        self.sid = self.hello['session_id']
        self.session = self.app[HUB].sessions[self.sid]

    async def asyncTearDown(self):
        await self.ws.close()
        await self.client.close()
        self.tmp.cleanup()

    async def start(self):
        response = await self.client.post('/api/recordings', json={'session_id': self.sid})
        self.assertEqual(response.status, 201)
        data = await response.json()
        await wait_message(self.ws, 'recording.started')
        return data['recording']

    async def test_start_ignores_chat_and_stop_saves_a_downloadable_wav(self):
        recording = await self.start()
        self.assertEqual(self.session.state, 'recording')
        self.assertFalse(self.session.pipeline.started.is_set())

        await self.ws.send_json({'type': 'input.text', 'text': '录音时不应该回复'})
        ignored = await wait_message(self.ws, 'recording.input_ignored')
        self.assertIn('录音模式', ignored['message'])
        self.assertFalse(self.session.pipeline.started.is_set())
        self.assertEqual(self.app[HUB].store.messages(self.sid), [])

        self.session.recording.write(b'\x01\x00'*16000)
        response = await self.client.post(f'/api/recordings/{recording["id"]}/stop')
        self.assertEqual(response.status, 200)
        stopped = (await response.json())['recording']
        self.assertEqual(stopped['status'], 'stopped')
        self.assertEqual(stopped['samples'], 16000)
        self.assertEqual(stopped['duration_ms'], 1000)
        await wait_message(self.ws, 'recording.stopped')
        self.assertEqual(self.session.state, 'listening')

        listing = await (await self.client.get('/api/recordings')).json()
        self.assertIn(recording['id'], [item['id'] for item in listing['recordings']])

        audio = await self.client.get(f'/api/recordings/{recording["id"]}/audio')
        self.assertEqual(audio.status, 200)
        self.assertEqual(audio.headers['Content-Type'], 'audio/wav')
        data = await audio.read()
        with wave.open(io.BytesIO(data), 'rb') as wav:
            self.assertEqual(wav.getframerate(), 16000)
            self.assertEqual(wav.getnframes(), 16000)
            self.assertEqual(wav.readframes(16000), b'\x01\x00'*16000)

        deleted = await self.client.delete(f'/api/recordings/{recording["id"]}')
        self.assertEqual(deleted.status, 200)
        self.assertEqual((await self.client.get(f'/api/recordings/{recording["id"]}/audio')).status, 404)
        self.assertEqual((await self.client.delete(f'/api/recordings/{recording["id"]}')).status, 404)

    async def test_active_recording_cannot_be_downloaded_or_started_twice(self):
        recording = await self.start()
        self.assertEqual((await self.client.get(f'/api/recordings/{recording["id"]}/audio')).status, 404)
        again = await self.client.post('/api/recordings', json={'session_id': self.sid})
        self.assertEqual(again.status, 409)
        self.assertEqual((await again.json())['error']['code'], 'recording_already_active')

    async def test_device_abort_stops_recording_without_a_voice_confirmation(self):
        recording = await self.start()
        self.session.recording.write(b'\0\0'*800)
        await self.ws.send_json({'type': 'abort'})
        async with asyncio.timeout(2):
            while self.session.recording is not None:
                await asyncio.sleep(.01)
        await wait_message(self.ws, 'recording.stopped')
        metadata = self.app[HUB].recordings.get(recording['id'])
        self.assertEqual(metadata['status'], 'stopped')
        self.assertEqual(metadata['reason'], 'client')

    async def test_standby_control_stops_recording_instead_of_hiding_it(self):
        recording = await self.start()
        await self.ws.send_json({'type': 'session.standby'})
        async with asyncio.timeout(2):
            while self.session.recording is not None:
                await asyncio.sleep(.01)
        await wait_message(self.ws, 'recording.stopped')
        self.assertEqual(self.session.state, 'standby')
        self.assertEqual(self.app[HUB].recordings.get(recording['id'])['status'], 'stopped')

    async def test_websocket_controls_start_and_stop_recording(self):
        await self.ws.send_json({'type': 'recording.start'})
        started = await wait_message(self.ws, 'recording.started')
        self.assertEqual(started['recording']['trigger'], 'client')
        self.assertIsNotNone(self.session.recording)
        await self.ws.send_json({'type': 'recording.stop'})
        async with asyncio.timeout(2):
            while self.session.recording is not None:
                await asyncio.sleep(.01)
        await wait_message(self.ws, 'recording.stopped')
        self.assertEqual(self.session.state, 'listening')

    async def test_voice_start_command_over_the_session_enters_recording(self):
        # A typed command uses the same session-level detector as a transcript.
        await self.ws.send_json({'type': 'input.text', 'text': '开始录音模式'})
        started = await wait_message(self.ws, 'recording.started')
        self.assertEqual(started['recording']['trigger'], 'text')
        self.assertIsNotNone(self.session.recording)
        self.assertFalse(self.session.pipeline.started.is_set())
        self.assertEqual(self.app[HUB].store.messages(self.sid), [])
        await self.session.stop_recording('web')
