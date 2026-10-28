import tempfile
import unittest
from aiohttp.test_utils import TestClient, TestServer
from voicehub.app import HUB, create_app
from tests.test_sessions import Pipeline


class McpManagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, pipeline_factory=lambda c,h,l: Pipeline())
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_catalogue_tracks_saved_switches_and_exposes_no_credentials(self):
        cfg = self.app[HUB].config.get()
        cfg['tools']['news'] = False
        self.app[HUB].config.save(cfg)
        response = await self.client.get('/api/mcp')
        data = await response.json()
        tools = {t['name']:t for t in data['tools']}
        self.assertEqual(set(tools), {'weather','news','system_info'})
        self.assertFalse(tools['news']['enabled'])
        self.assertEqual(tools['weather']['inputSchema']['required'], ['city'])
        self.assertEqual(data['servers'][0]['startup'], 'on_demand')
        self.assertFalse(data['external_import_supported'])
        self.assertNotIn(self.app[HUB].auth['admin_token'], str(data))

    async def test_history_survives_noisy_events_and_scopes_to_session_and_turn(self):
        store = self.app[HUB].store
        store.session('one','test')
        store.message('one','turn-one','user','查内存','confirmed')
        store.event(dict(type='tool.started',session_id='one',turn_id='turn-one',tool='system_info',
                         arguments={'sections':['memory']},planner='rex'))
        for _ in range(250):
            store.event(dict(type='session.state',session_id='one',turn_id='turn-one',state='thinking'))
        store.event(dict(type='tool.completed',session_id='other',turn_id='turn-one',tool='system_info'))
        store.event(dict(type='tool.started',session_id='one',turn_id='another',tool='news'))
        store.event(dict(type='playback.clear',session_id='one',turn_id=None,interrupted_turn_id='turn-one',reason='client'))
        response = await self.client.get('/api/sessions/one/tool-calls?turn_id=turn-one')
        data = await response.json()
        self.assertEqual([e['type'] for e in data['events']], ['tool.started','playback.clear'])
        self.assertEqual(data['events'][0]['payload']['arguments'], {'sections':['memory']})
        self.assertIsNone(data['active_turn_id'])

    async def test_excessive_history_scope_rejected(self):
        response = await self.client.get('/api/sessions/one/tool-calls',params=[('turn_id',str(i)) for i in range(101)])
        self.assertEqual(response.status,400)
