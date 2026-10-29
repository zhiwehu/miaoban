import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from aiohttp.test_utils import TestClient,TestServer
from voicehub.app import create_app,HUB
from voicehub.skills import PRESETS
from tests.test_sessions import Pipeline
class SkillsPausedTests(unittest.IsolatedAsyncioTestCase):
 async def test_default_platform_disables_skill_endpoints_without_touching_saved_definitions(self):
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'skills/quick_note.json';path.parent.mkdir();definition=copy.deepcopy(PRESETS[1]);definition['name']='保留的技能';original=json.dumps(definition,ensure_ascii=False);path.write_text(original)
   app=create_app(root,pipeline_factory=lambda *args:Pipeline())
   async with TestClient(TestServer(app)) as client:
    for method,url in [('GET','/api/skills'),('POST','/api/skills'),('PUT','/api/skills/quick_note'),('POST','/api/skills/quick_note/run')]:
     response=await client.request(method,url,json={} if method!='GET' else None);self.assertEqual(response.status,404)
    self.assertEqual((await client.get('/api/operations')).status,200)
    self.assertEqual((await client.get('/api/calendar/settings')).status,200)
    self.assertEqual(path.read_text(),original);self.assertIsNone(app[HUB].local.skills)
 async def test_explicit_skill_request_does_not_reach_model_or_tools(self):
  from voicehub.tool_chat import chat
  class P:
   local=SimpleNamespace(skills=None)
   async def raw_chat(self,*args,**kwargs):raise AssertionError('paused skill must not call model');yield ''
  result=[v async for v in chat(P(),[{'role':'user','content':'执行技能 保存笔记'}])]
  self.assertIn('暂停',''.join(result))
