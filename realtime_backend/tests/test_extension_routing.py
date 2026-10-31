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

    async def test_volume_request_explains_limit_without_failing_planner(self):
        cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(music=True,planner='rex')
        pipeline=SimpleNamespace(config=cfg)
        for text in ['把音乐音量降低一点。','音量降低一点。','把音量调小一点']:
            result=[item async for item in chat(pipeline,[{'role':'user','content':text}])]
            self.assertEqual(result,['暂时还不能用语音调节音量，请先用播放设备上的音量键调整。'])

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

    async def test_social_turn_after_interrupted_news_skips_all_tool_planners(self):
        history=[{'role':'user','content':'查新闻'},
                 {'role':'assistant','content':'我看一下最新新闻，请稍等。（回复被打断）'},
                 {'role':'user','content':'那不要说了。'},
                 {'role':'assistant','content':'好的，不说了。'}]
        for mode in ('rex','llm'):
            for final in ('再见。','谢谢，再见！','拜拜','晚安','你好','谢谢你','那不要说了。'):
                with self.subTest(mode=mode,final=final):
                    cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(planner=mode,music=True)
                    manager=SimpleNamespace(catalogue=lambda:[{'name':'ext_demo__echo'}])
                    class P:
                        config=cfg;local=SimpleNamespace(extensions=manager)
                        _previous_tool_plan={'tool':'news','arguments':{'keyword':'AI'}}
                        prompts=[]
                        async def raw_chat(self,history,**kwargs):
                            self.prompts.append(kwargs.get('system_prompt',''))
                            yield '再见，有需要随时叫我。'
                    p=P()
                    with patch('voicehub.tool_chat.dynamic_plan',side_effect=AssertionError('No planning for a social turn')), \
                         patch('voicehub.tool_chat.rex_plan',side_effect=AssertionError('No stale Rex intent')), \
                         patch('voicehub.tool_chat.CommonToolsClient',side_effect=AssertionError('No MCP')):
                        result=[x async for x in chat(p,[*history,{'role':'user','content':final}])]
                    self.assertEqual(result,['再见，有需要随时叫我。'])
                    self.assertIsNone(p._previous_tool_plan)
                    self.assertNotIn('未执行外部查询',p.prompts[-1])

    async def test_unrelated_chat_does_not_depend_on_music_json_planning(self):
        from unittest.mock import AsyncMock
        cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(planner='rex',music=True)
        class P:
            config=cfg;local=SimpleNamespace(extensions=None)
            async def raw_chat(self,history,**kwargs):yield '可以，我们来聊聊。'
        with patch('voicehub.tool_chat.rex_plan',AsyncMock(return_value={'status':'chat','tool':None,'arguments':{}})), \
             patch('voicehub.tool_chat.dynamic_plan',side_effect=ValueError('invalid_plan')):
            result=[x async for x in chat(P(),[{'role':'user','content':'和我聊聊今天的心情'}])]
        self.assertEqual(result,['可以，我们来聊聊。'])

    def test_social_detection_does_not_swallow_tool_requests_or_quoted_words(self):
        from voicehub.extension_routing import is_conversational_turn,music_candidate
        for text in ('播放《再见》','查一下新闻，谢谢','把再见原样返回给回显工具','解释再见这个词','不要查天气，查新闻','谢谢你帮我查上海天气','晚安前查一下天气'):
            self.assertFalse(is_conversational_turn(text),text)
        self.assertTrue(music_candidate('我想听稻香'))
        self.assertTrue(music_candidate('Play a song'))
        self.assertTrue(music_candidate('放一首周杰伦的歌'))
        self.assertFalse(music_candidate('和我聊聊今天的心情'))

    def test_acknowledgements_and_praise_stay_conversational(self):
        from voicehub.extension_routing import is_conversational_turn
        for text in ('太好了','真棒','明白了','知道了','好的，谢谢','不错啊','嗯嗯','非常好'):
            self.assertTrue(is_conversational_turn(text),text)
        for text in ('好的帮我查天气','不错，播放音乐','明白了，整理文件'):
            self.assertFalse(is_conversational_turn(text),text)

class FileFollowupTests(unittest.TestCase):
    def test_explicit_and_contextual_organizing_route_to_preview(self):
        from voicehub.extension_routing import file_request
        for text in ['整理文件','帮我整理一下我的文件夹。','帮我把我的文件夹里那些文件分类整理好。','请把文件夹里的文件分类一下','帮我把我的目录里面的这些文件按类型整理好']:
            self.assertEqual(file_request([{'role':'user','content':text}]),{'action':'preview'})
        for text in ['那你帮我整理吧。','我知道了，你赶紧开始整理吧。','开始整理']:
            history=[{'role':'assistant','content':'指定目录第一层有438个可整理文件，没有移动文件。'},{'role':'user','content':text}]
            self.assertEqual(file_request(history),{'action':'preview'})
            self.assertIsNone(file_request([{'role':'user','content':text}]))
    def test_refusals_and_unrelated_context_never_become_a_file_request(self):
        from voicehub.extension_routing import file_request
        for text in ['不要整理文件','整理是什么意思','把整理翻译成英文','删除全部文件','文件夹','不要帮我把我的文件夹里那些文件分类整理好','解释帮我把文件夹里的文件分类整理好是什么意思','帮我把文件夹里那些文件删除整理好']:
            self.assertIsNone(file_request([{'role':'assistant','content':'已生成文件分类预览'},{'role':'user','content':text}]))


class PlannerResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_extension_planner_failure_degrades_to_chat(self):
        from unittest.mock import AsyncMock
        cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(planner='rex')
        class Extensions:
            def catalogue(self):return [{'name':'ext_demo__echo','description':'echo'}]
            async def call(self,*args,**kwargs):raise AssertionError('No MCP call after a failed plan')
        class P:
            config=cfg;local=SimpleNamespace(extensions=Extensions())
            async def raw_chat(self,history,**kwargs):yield '我们还是聊点别的吧。'
        with patch('voicehub.tool_chat.dynamic_plan',side_effect=ValueError('invalid_plan')), \
             patch('voicehub.tool_chat.rex_plan',AsyncMock(return_value={'status':'chat','tool':None,'arguments':{}})):
            result=[x async for x in chat(P(),[{'role':'user','content':'说点有意思的事'}])]
        self.assertEqual(result,['我们还是聊点别的吧。'])

    async def test_dynamic_plan_sends_only_the_current_utterance(self):
        from voicehub.extension_routing import dynamic_plan
        seen=[]
        class P:
            async def raw_chat(self,history,**kwargs):
                seen.append(history)
                yield '{"tool":null,"arguments":{}}'
        cfg=copy.deepcopy(DEFAULT)
        history=[{'role':'user','content':'查新闻'},
                 {'role':'assistant','content':'好的，我看一下。'},
                 {'role':'user','content':'那今天几号'}]
        await dynamic_plan(P(),history,[{'name':'ext_demo__echo','description':'echo'}],cfg['tools'])
        self.assertEqual(seen,[[{'role':'user','content':'那今天几号'}]])

