import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from voicehub.config import DEFAULT,validate
from voicehub.tool_chat import chat
from voicehub.tools import ToolNotice,ToolStatus


class RexIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def pipeline(self,result):
        class Planner:
            async def plan(self,*args):return copy.deepcopy(result)
        class Pipeline:
            config=copy.deepcopy(DEFAULT)
            local=SimpleNamespace(planner=Planner())
            async def raw_chat(self,history,**kwargs):
                if result.get('tool'):
                    self.asserted_grounded='已执行工具' in history[-1]['content']
                yield '回答。'
        p=Pipeline();p.config['tools']['planner']='rex';return p

    async def test_rex_ready_passes_arguments_to_mcp_without_llm_planning(self):
        p=self.pipeline({'status':'ready','tool':'weather','arguments':{'city':'上海','day_offset':1}})
        calls=[]
        class Client:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            async def list_tools(self):return [{'name':'weather'}]
            async def call(self,name,args):
                calls.append((name,args));return {'content':[{'type':'text','text':'天气结果'}]}
        with patch('voicehub.tool_chat.CommonToolsClient',Client):
            source=chat(p,[{'role':'user','content':'上海明天天气'}])
            self.assertIsInstance(await anext(source),ToolNotice)
            self.assertFalse(calls)
            self.assertEqual((await anext(source)).phase,'started')
            self.assertFalse(calls)
            self.assertEqual((await anext(source)).phase,'completed')
            self.assertEqual(calls,[('weather',{'city':'上海','day_offset':1})])
            self.assertEqual(await anext(source),'回答。')
            self.assertTrue(p.asserted_grounded)
            await source.aclose()

    async def test_clarify_or_disabled_tool_never_executes(self):
        for result,disabled in [({'status':'clarify','tool':'weather','arguments':{},'message':'哪个城市？'},False),
                                ({'status':'ready','tool':'weather','arguments':{'city':'上海'}},True)]:
            p=self.pipeline(result)
            if disabled:p.config['tools']['weather']=False
            with patch('voicehub.tool_chat.CommonToolsClient',side_effect=AssertionError('Must not execute')):
                items=[item async for item in chat(p,[{'role':'user','content':'查询天气'}])]
            self.assertEqual(len(items),1);self.assertIsInstance(items[0],str)

    async def test_null_is_normal_chat_not_a_tool_call(self):
        p=self.pipeline({'status':'chat','tool':None,'arguments':{}})
        with patch('voicehub.tool_chat.CommonToolsClient',side_effect=AssertionError('Must not execute')):
            self.assertEqual([item async for item in chat(p,[{'role':'user','content':'你好'}])],['回答。'])

    async def test_cancellation_during_model_plan_stays_cancelled(self):
        p=self.pipeline({})
        started=asyncio.Event()
        async def waiting(*args):started.set();await asyncio.Event().wait()
        p.local.planner.plan=waiting
        source=chat(p,[{'role':'user','content':'查天气'}])
        task=asyncio.create_task(anext(source));await started.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task


class PlannerConfigTests(unittest.TestCase):
    def test_old_tool_settings_migrate_without_changing_provider(self):
        cfg=copy.deepcopy(DEFAULT);cfg['tools'].pop('planner')
        self.assertEqual(validate(cfg)['tools']['planner'],'llm')
        cfg=copy.deepcopy(DEFAULT);cfg['tools']['planner']='rex'
        self.assertEqual(validate(cfg)['tools']['planner'],'rex')
