import json
from pathlib import Path
import tempfile
import unittest
from aiohttp.test_utils import TestClient,TestServer
from voicehub.app import HUB,create_app
from voicehub.tools import catalogue,validate_arguments
from tests.test_sessions import Pipeline

class FileApiTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.directory=self.root/'selected';self.directory.mkdir()
  (self.directory/'report.pdf').write_bytes(b'original')
  self.app=create_app(self.root/'data',pipeline_factory=lambda c,h,l:Pipeline());self.client=TestClient(TestServer(self.app));await self.client.start_server()
  cfg=self.app[HUB].config.get();cfg['tools'].update(file_organizer=True,file_directory=str(self.directory));self.app[HUB].config.save(cfg)
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def test_api_preview_confirmation_execute_undo_and_no_model_write_tool(self):
  tool=next(t for t in catalogue() if t['name']=='file_organizer');self.assertTrue(tool['annotations']['readOnlyHint'])
  with self.assertRaises(ValueError):validate_arguments('file_organizer',{'action':'execute'})
  with self.assertRaises(ValueError):validate_arguments('file_organizer',{'action':'preview','directory':'/'})
  r=await self.client.post('/api/files/preview',json={});self.assertEqual(r.status,200);plan=(await r.json())['result']
  self.assertTrue((self.directory/'report.pdf').exists());self.assertEqual(plan['files'][0]['destination'],'文档/report.pdf')
  r=await self.client.post('/api/files/execute',json={'plan_id':plan['id'],'confirm':False});self.assertEqual(r.status,400)
  r=await self.client.post('/api/files/execute',json={'plan_id':plan['id'],'confirm':True});self.assertEqual(r.status,200)
  self.assertEqual((await r.json())['result']['status'],'completed');self.assertTrue((self.directory/'文档/report.pdf').exists())
  r=await self.client.post('/api/files/undo',json={'plan_id':plan['id'],'confirm':True});self.assertEqual(r.status,200)
  self.assertEqual((self.directory/'report.pdf').read_bytes(),b'original')
 async def test_real_mcp_only_returns_summary_and_plan_without_names_or_moving(self):
  r=await self.client.post('/api/mcp/tools/file_organizer/test',json={'action':'preview'});self.assertEqual(r.status,200)
  raw=await r.json();self.assertNotIn('report.pdf',str(raw));self.assertTrue((self.directory/'report.pdf').exists())
  result=json.loads(raw['result']['content'][0]['text']);self.assertTrue(result['requires_confirmation'])
  r=await self.client.get('/api/files');self.assertEqual((await r.json())['pending']['id'],result['plan_id'])
 async def test_disabled_mutation_denied_and_old_config_preserves_selection(self):
  cfg=self.app[HUB].config.get();del cfg['tools']['file_organizer'];del cfg['tools']['file_directory']
  r=await self.client.put('/api/config',json=cfg);self.assertEqual(r.status,200)
  saved=(await r.json())['config'];self.assertTrue(saved['tools']['file_organizer']);self.assertEqual(saved['tools']['file_directory'],str(self.directory))
  saved['tools']['file_organizer']=False;self.app[HUB].config.save(saved)
  r=await self.client.post('/api/files/preview',json={});self.assertEqual(r.status,400)
 async def test_cross_origin_and_unknown_plan_cannot_move(self):
  r=await self.client.post('/api/files/preview',json={},headers={'Origin':'https://other.invalid'});self.assertEqual(r.status,403)
  r=await self.client.post('/api/files/execute',json={'plan_id':'x'*32,'confirm':True});self.assertEqual(r.status,400)
  self.assertTrue((self.directory/'report.pdf').exists())
