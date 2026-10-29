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
        self.assertEqual(set(tools), {'weather','news','system_info','music','file_organizer','calendar','shopping_list','timer','item_memory'})
        self.assertFalse(tools['music']['enabled'])
        self.assertFalse(tools['news']['enabled'])
        self.assertEqual(tools['weather']['inputSchema']['required'], ['city'])
        self.assertEqual(data['servers'][0]['startup'], 'on_demand')
        self.assertTrue(data['external_import_supported'])
        self.assertNotIn(self.app[HUB].auth['admin_token'], str(data))

    async def test_upload_example_then_enable_call_disable_delete_over_http(self):
        response=await self.client.get('/api/mcp/example');raw=await response.read()
        response=await self.client.post('/api/mcp/import',data=raw)
        self.assertEqual(response.status,201)
        response=await self.client.post('/api/mcp/servers/local-echo/enable',json={'trust':False})
        self.assertEqual(response.status,400)
        response=await self.client.post('/api/mcp/servers/local-echo/enable',json={'trust':True})
        self.assertEqual(response.status,200)
        response=await self.client.get('/api/mcp');data=await response.json()
        tool=next(t for t in data['tools'] if t['server_id']=='local-echo')
        response=await self.client.post('/api/mcp/tools/'+tool['name']+'/test',json={'text':'HTTP上传验证'})
        self.assertEqual(response.status,200)
        self.assertIn('HTTP上传验证',str(await response.json()))
        response=await self.client.post('/api/mcp/tools/'+tool['name']+'/test',json={'text':123})
        self.assertEqual(response.status,400)
        await self.client.post('/api/mcp/servers/local-echo/disable')
        self.assertFalse(self.app[HUB].extensions.catalogue())
        await self.client.post('/api/mcp/servers/local-echo/delete')
        self.assertFalse(self.app[HUB].extensions.records)

    async def test_music_search_uses_configured_directory_through_real_stdio(self):
        from pathlib import Path
        directory=Path(self.tmp.name)/'songs';directory.mkdir()
        (directory/'测试音乐.wav').write_bytes(b'not used for playback')
        cfg=self.app[HUB].config.get();cfg['tools'].update(music=True,music_directory=str(directory))
        self.app[HUB].config.save(cfg)
        response=await self.client.post('/api/mcp/tools/music/test',json={'action':'search','query':'测试'})
        self.assertEqual(response.status,200)
        self.assertIn('测试音乐',str(await response.json()))

    async def test_upload_route_accepts_bounded_packages_larger_than_normal_api_limit(self):
        from tests.test_mcp_extensions import example_zip
        raw=example_zip(('notes.txt','x'*80000))
        self.assertGreater(len(raw),65536)
        response=await self.client.post('/api/mcp/import',data=raw)
        self.assertEqual(response.status,201)
        self.assertFalse(self.app[HUB].extensions.public_servers()[0]['enabled'])

    async def test_older_settings_form_does_not_reset_music_configuration(self):
        cfg=self.app[HUB].config.get();cfg['tools'].update(music=True,music_directory=self.tmp.name)
        self.app[HUB].config.save(cfg)
        del cfg['tools']['music'];del cfg['tools']['music_directory']
        response=await self.client.put('/api/config',json=cfg)
        self.assertEqual(response.status,200)
        tools=(await response.json())['config']['tools']
        self.assertTrue(tools['music']);self.assertEqual(tools['music_directory'],self.tmp.name)

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

    async def test_remote_save_is_disabled_and_credentials_never_return(self):
        body={'id':'remote-demo','name':'远程测试','url':'https://example.com/mcp','headers':{'Authorization':'Bearer private-fixture'}}
        response=await self.client.post('/api/mcp/remote',json=body)
        self.assertEqual(response.status,201);record=(await response.json())['server']
        self.assertEqual(record['name'],'远程测试');self.assertFalse(record['enabled'])
        self.assertEqual(record['transport'],'streamable_http');self.assertNotIn('private-fixture',str(record))
        response=await self.client.get('/api/mcp');self.assertNotIn('private-fixture',str(await response.json()))
        response=await self.client.post('/api/mcp/servers/remote-demo/enable',json={'trust':False})
        self.assertEqual(response.status,400)
        response=await self.client.post('/api/mcp/remote',json={**body,'id':'invalid-name','name':[]})
        self.assertEqual(response.status,400);self.assertNotIn('invalid-name',self.app[HUB].extensions.records)
