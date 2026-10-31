import asyncio
import json
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from tests.test_sessions import Pipeline
from voicehub.app import HUB, create_app
from voicehub.meeting_minutes.inference import MinutesError
from voicehub.meeting_minutes.store import MinutesStore


class FakeMinutesEngine:
    model_id = 'fake/Qwen3.5-4B-local'

    def __init__(self):
        self.calls = 0

    async def check_available(self):
        return None

    async def generate(self, messages, *, max_tokens=1400, timeout=240):
        self.calls += 1
        evidence = [{'line': 1, 'quote': '甲说话'}]
        return json.dumps({
            'topics': [{'title': '测试议题', 'summary': '测试摘要', 'evidence': evidence}],
            'key_points': [{'text': '测试关键点', 'evidence': evidence}],
            'decisions': [{'text': '测试决定', 'status': 'agreed', 'evidence': evidence}],
            'actions': [{'task': '测试行动', 'owner': '小王', 'due_date': None,
                         'status': 'agreed', 'needs_confirmation': True,
                         'evidence': evidence}],
            'risks': [{'text': '测试风险', 'evidence': evidence}],
            'questions': [{'text': '测试问题', 'evidence': evidence}],
        }, ensure_ascii=False)

    def close(self):
        return None


class MissingModelEngine(FakeMinutesEngine):
    async def check_available(self):
        raise MinutesError('minutes_model_not_cached', '本地模型未缓存。')


async def wait_for(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.02)


class MeetingMinutesApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, pipeline_factory=lambda c, h, l: Pipeline())
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.hub = self.app[HUB]
        self.hub.minutes.engine = FakeMinutesEngine()
        self.ws = await self.client.ws_connect('/ws')
        await self.ws.send_json({'type': 'hello', 'version': 1, 'device_id': 'minutes-web',
                                 'audio': {'format': 'pcm_s16le', 'sample_rate': 16000},
                                 'capabilities': {'echo_cancellation': True}})
        self.hello = await self.ws.receive_json()
        self.sid = self.hello['session_id']
        self.session = self.hub.sessions[self.sid]

    async def asyncTearDown(self):
        await self.ws.close()
        await self.client.close()
        self.tmp.cleanup()

    async def recording(self, lines=('甲说话', '乙说话')):
        response = await self.client.post('/api/recordings', json={'session_id': self.sid})
        self.assertEqual(response.status, 201)
        recording = (await response.json())['recording']
        for index, text in enumerate(lines):
            self.session.recording.append_transcript(text, index*1000)
        response = await self.client.post(f'/api/recordings/{recording["id"]}/stop')
        self.assertEqual(response.status, 200)
        return recording['id']

    async def start_job(self, recording_id, template='general'):
        return await self.client.post(f'/api/recordings/{recording_id}/minutes',
                                      json={'template_id': template})

    async def wait_job(self, recording_id, job_id):
        async with asyncio.timeout(3):
            while True:
                response = await self.client.get(
                    f'/api/recordings/{recording_id}/minutes/{job_id}')
                data = await response.json()
                if data['job']['status'] in ('completed', 'failed', 'cancelled',
                                             'interrupted'):
                    return data['job']
                await asyncio.sleep(.02)

    async def test_templates_and_end_to_end_job_export_edit_delete(self):
        templates = await (await self.client.get('/api/meeting-templates')).json()
        self.assertEqual(templates['model_id'], 'fake/Qwen3.5-4B-local')
        self.assertEqual([item['name'] for item in templates['templates']][:2],
                         ['通用详细纪要', '项目周会'])
        recording_id = await self.recording()
        response = await self.start_job(recording_id)
        self.assertEqual(response.status, 202)
        job = (await response.json())['job']
        self.assertEqual(job['status'], 'queued')
        job = await self.wait_job(recording_id, job['id'])
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['result']['stats']['decisions'], 1)
        self.assertEqual(job['result']['stats']['actions'], 1)
        self.assertIn('测试决定', job['result']['markdown'])
        self.assertEqual(job['result']['sections'][2]['items'][0]['evidence'][0]['stamp'],
                         '00:00')
        listing = await (await self.client.get(
            f'/api/recordings/{recording_id}/minutes')).json()
        self.assertEqual(len(listing['jobs']), 1)
        export = await self.client.get(
            f'/api/recordings/{recording_id}/minutes/{job["id"]}/export?format=md')
        self.assertEqual(export.status, 200)
        self.assertIn('测试决定', await export.text())
        edited = '# 我的手改纪要\n\n只保留这一行。\n'
        response = await self.client.patch(
            f'/api/recordings/{recording_id}/minutes/{job["id"]}',
            json={'markdown': edited})
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['job']['revision'], 1)
        export = await self.client.get(
            f'/api/recordings/{recording_id}/minutes/{job["id"]}/export?format=txt')
        self.assertIn('我的手改纪要', await export.text())
        deleted = await self.client.delete(
            f'/api/recordings/{recording_id}/minutes/{job["id"]}')
        self.assertEqual(deleted.status, 200)
        self.assertEqual((await self.client.get(
            f'/api/recordings/{recording_id}/minutes/{job["id"]}')).status, 404)

    async def test_active_recording_and_empty_transcript_are_rejected(self):
        response = await self.client.post('/api/recordings', json={'session_id': self.sid})
        active = (await response.json())['recording']
        response = await self.start_job(active['id'])
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())['error']['code'], 'minutes_recording_active')
        await self.client.post(f'/api/recordings/{active["id"]}/stop')

        recording_id = await self.recording(lines=())
        response = await self.start_job(recording_id)
        self.assertEqual(response.status, 400)
        self.assertEqual((await response.json())['error']['code'], 'minutes_transcript_empty')

    async def test_recording_control_phrases_are_not_summarized(self):
        recording_id = await self.recording(lines=('甲说话', '确认停止。'))
        job = (await (await self.start_job(recording_id)).json())['job']
        job = await self.wait_job(recording_id, job['id'])
        self.assertEqual(job['transcript_lines'], 1)
        self.assertNotIn('确认停止', job['result']['markdown'])

    async def test_missing_model_returns_503(self):
        recording_id = await self.recording()
        self.hub.minutes.engine = MissingModelEngine()
        response = await self.start_job(recording_id)
        self.assertEqual(response.status, 503)
        self.assertEqual((await response.json())['error']['code'], 'minutes_model_not_cached')

    async def test_custom_template_crud_and_builtin_protection(self):
        response = await self.client.post('/api/meeting-templates', json={
            'name': '我的模板', 'instructions': '只保留重点。', 'sections': ['overview', 'topics']})
        self.assertEqual(response.status, 201)
        template = (await response.json())['template']
        self.assertFalse(template['builtin'])
        updated = await self.client.patch(f"/api/meeting-templates/{template['template_id']}",
                                          json={'name': '我的模板2', 'instructions': '更新。',
                                                'sections': ['overview']})
        self.assertEqual(updated.status, 200)
        self.assertEqual((await updated.json())['template']['version'], 2)
        denied = await self.client.patch('/api/meeting-templates/general',
                                         json={'name': 'x', 'instructions': 'x'})
        self.assertEqual(denied.status, 400)
        self.assertEqual((await self.client.delete(
            f"/api/meeting-templates/{template['template_id']}")).status, 200)

    async def test_recording_delete_removes_minutes_and_jobs(self):
        recording_id = await self.recording()
        job = (await (await self.start_job(recording_id)).json())['job']
        await self.wait_job(recording_id, job['id'])
        self.assertTrue((self.hub.minutes.store.recording_dir(recording_id)).is_dir())
        response = await self.client.delete(f'/api/recordings/{recording_id}')
        self.assertEqual(response.status, 200)
        self.assertFalse((self.hub.minutes.store.recording_dir(recording_id)).exists())


class MinutesStoreRecoveryTests(unittest.TestCase):
    def test_running_job_is_marked_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MinutesStore(tmp)
            job = store.create_job('a'*32, {
                'template_id': 'general', 'version': 1, 'name': '通用详细纪要',
                'sections': ['overview'], 'instructions': 'x'}, 'fake', 'b'*64, 2)
            job['status'] = 'running'
            store.save_job(job)
            MinutesStore(tmp).recover()
            recovered = store.get_job('a'*32, job['id'])
            self.assertEqual(recovered['status'], 'interrupted')
            self.assertEqual(recovered['error']['code'], 'minutes_interrupted')
