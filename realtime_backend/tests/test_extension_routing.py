import copy
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from voicehub.config import DEFAULT
from voicehub.extension_routing import music_plan
from voicehub.mcp_extensions import ExtensionManager
from voicehub.tool_chat import chat
from voicehub.tools import ToolNotice,ToolStatus
from tests.test_mcp_extensions import example_zip


class ExtensionRoutingTests(unittest.IsolatedAsyncioTestCase):
    def test_music_commands_preserve_keywords_and_do_not_catch_negation_or_other_media(self):
        for text,expected in [('播放音乐',{'action':'play'}),('请暂停音乐',{'action':'pause'}),
                              ('继续播放',{'action':'resume'}),('停止音乐',{'action':'stop'}),
                              ('播放《稻香》',{'action':'play','query':'稻香'}),
                              ('播放《Love Story》',{'action':'play','query':'Love Story'}),
                              ('搜索歌曲稻香',{'action':'search','query':'稻香'})]:
            self.assertEqual(music_plan(text),expected)
        for text in ('不要播放音乐','你会播放音乐吗','播放两条新闻','解释播放音乐是什么意思'):
            self.assertIsNone(music_plan(text))

    async def test_explicit_music_refusal_never_reaches_a_model_or_mcp(self):
        cfg=copy.deepcopy(DEFAULT);cfg['tools']['music']=True
        pipeline=SimpleNamespace(config=cfg)
        with patch('voicehub.tool_chat.CommonToolsClient',side_effect=AssertionError('Must not play')):
            result=[item async for item in chat(pipeline,[{'role':'user','content':'先别播放音乐'}])]
        self.assertEqual(result,['好的，我不会开始播放音乐。'])

    async def test_disabled_music_explains_setup_without_claiming_to_play(self):
        pipeline=SimpleNamespace(config=copy.deepcopy(DEFAULT))
        result=[item async for item in chat(pipeline,[{'role':'user','content':'播放音乐'}])]
        self.assertEqual(result,['本地音乐尚未启用，请在 MCP 工具页设置音乐目录并启用。'])

    async def test_uploaded_tool_overrides_fixed_model_candidate_and_carries_actual_parameters(self):
        with tempfile.TemporaryDirectory() as root:
            manager=ExtensionManager(root);manager.import_bytes(example_zip());await manager.enable('local-echo',True)
            name=manager.catalogue()[0]['name']
            class Pipeline:
                config=copy.deepcopy(DEFAULT)
                local=SimpleNamespace(extensions=manager)
                async def raw_chat(self,history,**kwargs):
                    if kwargs.get('system_prompt','').startswith('根据用户本轮'):
                        import json
                        yield json.dumps({'tool':name,'arguments':{'text':'这是实际参数'}})
                    else:yield '回显完成。'
            p=Pipeline();p.config['tools']['planner']='rex'
            with patch('voicehub.tool_chat.rex_plan',side_effect=AssertionError('Unknown tools must not depend on Rex labels')):
                result=[r async for r in chat(p,[{'role':'user','content':'调用回显工具返回这句话'}])]
            self.assertIsInstance(result[0],ToolNotice)
            started=next(r for r in result if isinstance(r,ToolStatus) and r.phase=='started')
            self.assertEqual(started.arguments,{'text':'这是实际参数'});self.assertEqual(started.planner,'dynamic_llm')
            self.assertTrue(any(isinstance(r,ToolStatus) and r.phase=='completed' for r in result))
            await manager.close()
