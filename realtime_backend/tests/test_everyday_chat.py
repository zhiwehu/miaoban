import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from voicehub.config import ConfigFile
from voicehub.everyday_chat import grounded,explicit_duration,execute
from voicehub.tools import ToolNotice,ToolStatus

class EverydaySlots(unittest.TestCase):
 def test_duration(self):
  for text,expected in [('五分钟',300),('两分半',150),('半小时',1800),('一小时二十分钟',4800),('0.5分钟',30)]:self.assertEqual(explicit_duration(text),expected,text)
 def test_hallucinated_slots_rejected(self):
  for name,args,text in [('item_memory',{'action':'remember','item':'护照','location':'抽屉'},'记住护照位置'),('timer',{'action':'start','duration_seconds':60},'倒计时五分钟'),('shopping_list',{'action':'add','items':['牛奶']},'加入鸡蛋')]:
   with self.assertRaises(ValueError):grounded(name,args,text)

class EverydayChat(unittest.IsolatedAsyncioTestCase):
 async def test_notice_before_extraction_and_mcp(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cf=ConfigFile(root/'settings.json');cfg=cf.get();cfg['tools']['shopping_list']=True;cf.save(cfg)
   calls=[]
   class Pipeline:
    local=SimpleNamespace(data_root=root)
    async def raw_chat(self,*args,**kwargs):calls.append('extract');yield '{"items":["两盒牛奶"]}'
   source=execute(Pipeline(),[{'role':'user','content':'把两盒牛奶加到购物清单'}],{'tool':'shopping_list','arguments':{'action':'add'}})
   self.assertIsInstance(await anext(source),ToolNotice);self.assertEqual(calls,[])
   values=[v async for v in source];self.assertTrue(any(isinstance(v,ToolStatus) and v.phase=='completed' for v in values));self.assertIn('已加入',values[-1])
 async def test_missing_location_does_not_write(self):
  with tempfile.TemporaryDirectory() as d:
   class Pipeline:
    local=SimpleNamespace(data_root=Path(d))
    async def raw_chat(self,*args,**kwargs):yield '{"item":"护照"}'
   values=[v async for v in execute(Pipeline(),[{'role':'user','content':'帮我记住护照'}],{'tool':'item_memory','arguments':{'action':'remember'}})]
   self.assertIn('位置',values[-1]);self.assertFalse((Path(d)/'everyday/memory.json').exists())

class MemoryGrounding(unittest.TestCase):
 def test_placeholder_is_not_a_location(self):
  with self.assertRaises(ValueError):grounded('item_memory',{'action':'remember','item':'雨伞','location':'的位置'},'记一下雨伞的位置')

class EverydayRouting(unittest.IsolatedAsyncioTestCase):
 async def test_new_rex_action_precedes_document_workflow(self):
  from voicehub.tool_chat import chat
  from voicehub.config import DEFAULT
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cf=ConfigFile(root/'settings.json');cfg=cf.get();cfg['tools']['timer']=True;cfg['tools']['planner']='rex';cf.save(cfg)
   class Planner:
    async def plan(self,*args):return {'status':'extract','tool':'timer','arguments':{}}
   class Pipeline:
    config=cfg;local=SimpleNamespace(data_root=root,planner=Planner());session_id='test';device_id='robot'
    async def raw_chat(self,*args,**kwargs):yield '{"action":"start","name":"煮蛋"}'
   values=[v async for v in chat(Pipeline(),[{'role':'user','content':'创建一个五分钟的煮蛋计时器'}])]
   self.assertTrue(any(isinstance(v,ToolStatus) and v.name=='timer' and v.phase=='completed' for v in values))
   from voicehub.everyday_store import EverydayStore
   self.assertEqual(EverydayStore(root).timer('status',device='robot')['items'][0]['name'],'煮蛋')

class DurationEdges(unittest.TestCase):
 def test_no_silent_range_negative_or_future_conversion(self):
  for text in ('倒计时负三分钟','设置-5分钟倒计时','倒计时五到十分钟','下午三点开始计时五分钟','计时五分钟再加十分钟'):
   self.assertIsNone(explicit_duration(text),text)
  self.assertEqual(explicit_duration('一个半小时'),5400)
  self.assertEqual(explicit_duration('两分钟半'),150)

class ExistingToolPriority(unittest.IsolatedAsyncioTestCase):
 async def test_explicit_music_command_keeps_its_existing_route(self):
  from voicehub.tool_chat import chat
  from voicehub.config import DEFAULT
  cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(planner='rex',timer=True,music=True)
  class Planner:
   async def plan(self,*args):raise AssertionError('Explicit music command must keep its route')
  p=SimpleNamespace(config=cfg,local=SimpleNamespace(planner=Planner()))
  source=chat(p,[{'role':'user','content':'暂停音乐'}])
  try:self.assertEqual((await anext(source)).name,'music')
  finally:await source.aclose()

class MutationAuthority(unittest.TestCase):
 def test_refusal_is_not_permission_to_delete(self):
  from voicehub.everyday_chat import refused
  self.assertTrue(refused('item_memory','forget','先别记录车钥匙的位置'))
  self.assertTrue(refused('item_memory','forget','不要删除护照的位置记录'))
  self.assertFalse(refused('item_memory','forget','删除护照的位置记录'))
  self.assertFalse(refused('timer','resume','不要再暂停了，继续计时'))
  self.assertFalse(refused('shopping_list','remove','牛奶不买了，从购物清单移除'))

class OrdinaryChatIsForeground(unittest.IsolatedAsyncioTestCase):
 async def test_new_tools_do_not_background_unrelated_chat(self):
  from voicehub.tool_chat import chat
  from voicehub.config import DEFAULT
  cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(planner='rex',timer=True)
  class Planner:
   async def plan(self,*args):return {'status':'chat','tool':None,'arguments':{}}
  class P:
   config=cfg;local=SimpleNamespace(planner=Planner())
   async def raw_chat(self,*args,**kwargs):yield '八乘九等于七十二。'
  values=[v async for v in chat(P(),[{'role':'user','content':'八乘九等于多少'}])]
  self.assertTrue(all(isinstance(v,str) for v in values))

class BoundedFallback(unittest.IsolatedAsyncioTestCase):
 async def test_rex_abstention_uses_one_bounded_model_call_and_host_duration(self):
  from voicehub.tool_chat import chat
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cf=ConfigFile(root/'settings.json');cfg=cf.get();cfg['tools'].update(timer=True,planner='rex');cf.save(cfg)
   class Planner:
    async def plan(self,*args):return {'status':'chat','tool':None,'arguments':{}}
   class P:
    config=cfg;local=SimpleNamespace(planner=Planner(),data_root=root);count=0;device_id='test';session_id='test'
    async def raw_chat(self,*args,**kwargs):self.count+=1;yield '{"tool":"timer","arguments":{"action":"start","name":"咖啡","duration_seconds":600}}'
   p=P();values=[v async for v in chat(p,[{'role':'user','content':'给咖啡这件事倒数五分钟'}])]
   call=next(v for v in values if isinstance(v,ToolStatus) and v.phase=='completed')
   self.assertEqual(call.planner,'rex_llm_fallback');self.assertEqual(call.arguments['duration_seconds'],300);self.assertEqual(p.count,1)

class FollowupSlots(unittest.IsolatedAsyncioTestCase):
 async def test_location_reply_uses_pending_tool_context_after_rex_abstains(self):
  from voicehub.tool_chat import chat
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);cf=ConfigFile(root/'settings.json');cfg=cf.get();cfg['tools'].update(item_memory=True,planner='rex');cf.save(cfg)
   class Planner:
    async def plan(self,*args):return {'status':'chat','tool':None,'arguments':{}}
   class P:
    config=cfg;local=SimpleNamespace(planner=Planner(),data_root=root);count=0
    async def raw_chat(self,*args,**kwargs):
     self.count+=1;yield '{"tool":"item_memory","arguments":{"action":"remember","item":"雨伞"'+(',"location":"玄关"' if self.count>1 else '')+'}}'
   p=P();history=[{'role':'user','content':'记一下雨伞的位置'}];[v async for v in chat(p,history)]
   history += [{'role':'assistant','content':'请说明位置。'},{'role':'user','content':'就在玄关'}]
   values=[v async for v in chat(p,history)]
   self.assertTrue(any(isinstance(v,ToolStatus) and v.phase=='completed' for v in values))
