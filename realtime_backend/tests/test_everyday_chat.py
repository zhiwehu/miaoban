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
    async def plan(self,*args):return {'status':'extract','tool':'timer','arguments':{'action':'start'}}
   class Pipeline:
    config=cfg;local=SimpleNamespace(data_root=root,planner=Planner());session_id='test';device_id='robot'
    async def raw_chat(self,*args,**kwargs):yield '{"name":"煮蛋"}'
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
