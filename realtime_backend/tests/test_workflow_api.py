import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from aiohttp.test_utils import TestClient,TestServer
from voicehub.app import create_app,HUB
from tests.test_workflow_manager import PLAN
class WorkflowApiTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory()
  class P:
   def __init__(self,*args):pass
   async def raw_chat(self,*args,**kwargs):yield json.dumps(PLAN)
  self.app=create_app(self.tmp.name,pipeline_factory=P);self.client=TestClient(TestServer(self.app));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def wait(self,identity,status):
  async with asyncio.timeout(4):
   while self.app[HUB].workflows.get(identity)['status']!=status:await asyncio.sleep(.01)
 async def test_api_goal_generates_artifact_and_draft_but_waits_for_smtp(self):
  r=await self.client.post('/api/workflows',json={'goal':'生成文档并准备发送'});self.assertEqual(r.status,202);task=(await r.json())['task']
  await self.wait(task['id'],'waiting_configuration');task=self.app[HUB].workflows.get(task['id'])
  identity=task['steps'][0]['result']['artifact_id'];r=await self.client.get('/api/artifacts/'+identity);self.assertEqual(r.status,200);self.assertIn('真实内容',await r.text())
  r=await self.client.post('/api/workflows/'+task['id']+'/actions',json={'action':'confirm','revision':task['revision'],'confirm':True});self.assertEqual(r.status,400)
  r=await self.client.get('/api/workflow-settings/mcp-config');data=await r.json();self.assertIn('voicehub-email',data['mcpServers']);self.assertNotIn('password',str(data))
 async def test_smtp_password_is_preserved_but_never_returned_and_origin_is_protected(self):
  r=await self.client.put('/api/workflow-settings',json={'password':'fixture-private-secret','host':'smtp.example.com'});self.assertEqual(r.status,200);self.assertNotIn('fixture-private-secret',str(await r.json()))
  r=await self.client.put('/api/workflow-settings',json={'password':''});self.assertTrue((await r.json())['settings']['password_set'])
  r=await self.client.post('/api/workflows',json={'goal':'生成文档'},headers={'Origin':'https://outside.invalid'});self.assertEqual(r.status,403)
