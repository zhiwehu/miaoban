import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from voicehub.config import DEFAULT
from voicehub.tool_chat import chat
from voicehub.tools import ToolNotice

class WeatherRoutingFeedbackTests(unittest.IsolatedAsyncioTestCase):
 def pipeline(self):
  class Pipeline:
   config=copy.deepcopy(DEFAULT)
   local=SimpleNamespace(operations=object())
   async def raw_chat(self,history,**kwargs):
    yield '真实天气回答。' if '已执行工具' in history[-1]['content'] else '{"tool":null,"arguments":{}}'
  p=Pipeline();p.config['tools'].update(default_city='上海',planner='llm',weather=True);return p
 async def test_colloquial_weather_calls_mcp(self):
  for phrase in ('今天天气怎么样','查询今天天气','明天天气如何？'):
   p=self.pipeline();calls=[]
   class Client:
    def __init__(self,**kwargs):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def list_tools(self):return [{'name':'weather'}]
    async def call(self,name,args):calls.append((name,args));return {'content':[{'type':'text','text':'晴'}]}
   with patch('voicehub.tool_chat.CommonToolsClient',Client):
    source=chat(p,[{'role':'user','content':phrase}])
    rest=[item async for item in source]
    self.assertFalse(any(isinstance(item,ToolNotice) and item.text for item in rest if isinstance(item,ToolNotice) and item.name=='tool_planning'))
   self.assertEqual(calls,[('weather',{'city':'上海','day_offset':1 if '明天' in phrase else 0})]);self.assertEqual(rest[-1],'真实天气回答。')
 async def test_missing_city_asks(self):
  p=self.pipeline();p.config['tools']['default_city']=''
  items=[item async for item in chat(p,[{'role':'user','content':'今天天气怎么样'}])]
  self.assertIn('哪个城市',items[-1])
 async def test_feedback_precedes_slow_rex(self):
  p=self.pipeline();p.config['tools']['planner']='rex'
  async def plan(*args):await asyncio.sleep(10);return {'status':'chat','tool':None,'arguments':{}}
  p.local.planner=SimpleNamespace(plan=plan)
  source=chat(p,[{'role':'user','content':'北京今天会下雨吗'}]);first=await anext(source)
  self.assertIsInstance(first,ToolNotice);self.assertFalse(first.text);await source.aclose()
