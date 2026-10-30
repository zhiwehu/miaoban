import tempfile
import unittest
from aiohttp.test_utils import TestClient,TestServer
from voicehub.app import create_app,HUB
from tests.test_sessions import Pipeline
class SkillsApiTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,skills_enabled=True,pipeline_factory=lambda *args:Pipeline());self.client=TestClient(TestServer(self.app));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def test_parameter_validation_snapshot_and_background_run(self):
  response=await self.client.get('/api/skills');self.assertEqual(len((await response.json())['skills']),3)
  response=await self.client.post('/api/skills/quick_note/run',json={'arguments':{'title':'测试'}});self.assertEqual(response.status,400)
  response=await self.client.post('/api/skills/quick_note/run',json={'arguments':{'title':'测试','content':'真实正文'}});self.assertEqual(response.status,202)
  value=await response.json();import asyncio
  async with asyncio.timeout(2):
   while self.app[HUB].operations.tasks:await asyncio.sleep(.01)
  task=self.app[HUB].workflows.get(value['task']['id']);self.assertEqual(task['status'],'completed')
  response=await self.client.get('/api/operations');self.assertEqual((await response.json())['operations'][0]['status'],'completed')
  response=await self.client.post('/api/skills/quick_note/run',json={'arguments':{}},headers={'Origin':'https://outside.invalid'});self.assertEqual(response.status,403)
