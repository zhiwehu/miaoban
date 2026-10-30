import tempfile
import unittest
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient,TestServer
from voicehub.app import create_app,HUB

class ConfigScopes(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.client=TestClient(TestServer(create_app(self.tmp.name)));await self.client.start_server()
 async def asyncTearDown(self):
  await self.client.close();self.tmp.cleanup()
 async def test_model_form_cannot_read_or_write_tools_and_preserves_concurrent_tool_changes(self):
  cfg=(await (await self.client.get('/api/config/models')).json())['config'];self.assertNotIn('tools',cfg)
  hub=self.client.app[HUB]
  async def status(kind):
   value=hub.config.get();value['tools']['default_city']='上海';hub.config.save(value);return {}
  hub.services.status=AsyncMock(side_effect=status)
  cfg['system_prompt']='新的提示词';r=await self.client.put('/api/config/models',json=cfg);self.assertEqual(r.status,200)
  self.assertNotIn('tools',(await r.json())['config']);self.assertEqual(hub.config.get()['tools']['default_city'],'上海')
  self.assertEqual(hub.config.get()['system_prompt'],'新的提示词')
  self.assertEqual((await self.client.put('/api/config/models',json={**cfg,'tools':{}})).status,400)
 async def test_tool_scope_cannot_change_models(self):
  cfg=(await (await self.client.get('/api/config/tools')).json())['config'];self.assertEqual(set(cfg),{'tools'})
  cfg['tools']['default_city']='合肥';r=await self.client.put('/api/config/tools',json=cfg);self.assertEqual(r.status,200)
  self.assertEqual(self.client.app[HUB].config.get()['tools']['default_city'],'合肥')
  self.assertEqual((await self.client.put('/api/config/tools',json={**cfg,'system_prompt':'不允许'})).status,400)
