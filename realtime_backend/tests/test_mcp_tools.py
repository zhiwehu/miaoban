import asyncio
import copy
import json
import unittest

from voicehub.config import DEFAULT, validate


class ToolConfigTests(unittest.TestCase):
    def test_old_settings_receive_tool_defaults(self):
        old = copy.deepcopy(DEFAULT)
        old.pop('tools', None)
        self.assertEqual(validate(old)['tools']['news_limit'], 3)

    def test_invalid_tool_settings_rejected(self):
        for key, value in [('timeout_seconds', 0), ('news_limit', 50),
                           ('news_source', 'http://127.0.0.1/'), ('weather', 'true')]:
            cfg = copy.deepcopy(DEFAULT)
            cfg['tools'][key] = value
            with self.assertRaises(ValueError):
                validate(cfg)


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_system_sections_are_transmitted_and_filtered(self):
        from voicehub.mcp_client import CommonToolsClient
        async with CommonToolsClient() as client:
            result=await client.call('system_info',{'sections':['memory']})
        value=json.loads(result['content'][0]['text'])
        self.assertEqual(set(value),{'fetched_at','memory'})
    async def test_stdio_handshake_list_and_real_system_info(self):
        from voicehub.mcp_client import CommonToolsClient
        async with CommonToolsClient() as client:
            tools = await client.list_tools()
            self.assertEqual({t['name'] for t in tools}, {'weather', 'news', 'system_info','music','file_organizer'})
            result = await client.call('system_info', {})
            self.assertFalse(result.get('isError', False))
            value = json.loads(result['content'][0]['text'])
            self.assertIn('memory', value)
            self.assertNotIn('hostname', value)

    async def test_tool_rejects_unknown_name_and_parameters(self):
        from voicehub.mcp_client import CommonToolsClient
        async with CommonToolsClient() as client:
            for name, args in [('shell', {}), ('system_info', {'command':'whoami'}),
                               ('weather', {'city':''}), ('news', {'limit':100})]:
                result = await client.call(name, args)
                self.assertTrue(result['isError'])


class ToolPlanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_inflight_tool_closes_client_and_never_yields_result(self):
        from unittest.mock import patch
        from voicehub.tool_chat import chat
        closed, called = asyncio.Event(), asyncio.Event()
        class Pipeline:
            config = copy.deepcopy(DEFAULT)
            async def raw_chat(self, history, **kwargs):
                yield '{"tool":"news","arguments":{}}'
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): closed.set()
            async def list_tools(self): return [{'name':'news'}]
            async def call(self, *args):
                called.set()
                await asyncio.Event().wait()
        with patch('voicehub.tool_chat.CommonToolsClient', Client):
            source = chat(Pipeline(), [{'role':'user', 'content':'新闻'}])
            await anext(source)  # Spoken acknowledgement barrier.
            await anext(source)  # started event.
            task = asyncio.create_task(anext(source))
            await asyncio.wait_for(called.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(closed.is_set())
            with self.assertRaises(StopAsyncIteration):
                await anext(source)

    async def test_timeout_has_failed_event_and_spoken_feedback(self):
        from unittest.mock import patch
        from voicehub.tool_chat import chat
        class Pipeline:
            config = copy.deepcopy(DEFAULT)
            async def raw_chat(self, history, **kwargs):
                yield '{"tool":"news","arguments":{}}'
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): raise TimeoutError()
            async def __aexit__(self, *args): pass
        with patch('voicehub.tool_chat.CommonToolsClient', Client):
            items = [i async for i in chat(Pipeline(), [])]
        self.assertEqual(items[-2].phase, 'failed')
        self.assertEqual(items[-2].code, 'tool_timeout')
        self.assertIn('查询超时', items[-1])

    async def test_plan_ack_call_result_and_failure_are_ordered(self):
        from unittest.mock import patch
        from voicehub.tool_chat import chat
        from voicehub.tools import ToolNotice, ToolStatus
        calls = []
        class Pipeline:
            config = copy.deepcopy(DEFAULT)
            async def raw_chat(self, history, **kwargs):
                if '路由器' in kwargs['system_prompt']:
                    yield '{"tool":"system_info","arguments":{}}'
                else:
                    self.history = history
                    yield '内存使用正常。'
        class Client:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def list_tools(self): return [{'name':'system_info'}]
            async def call(self, name, arguments):
                calls.append(name)
                return {'content':[{'type':'text','text':'{"memory":{}}'}]}
        pipeline = Pipeline()
        with patch('voicehub.tool_chat.CommonToolsClient', Client):
            source = chat(pipeline, [{'role':'user', 'content':'内存用了多少'}])
            self.assertIsInstance(await anext(source), ToolNotice)
            self.assertEqual(calls, [])
            self.assertEqual((await anext(source)).phase, 'started')
            self.assertEqual(calls, [])
            self.assertEqual((await anext(source)).phase, 'completed')
            self.assertEqual(calls, ['system_info'])
            self.assertEqual(await anext(source), '内存使用正常。')
            await source.aclose()
            self.assertIn('memory', pipeline.history[-1]['content'])

    async def test_plan_is_allowlisted_and_arguments_are_not_executed_as_code(self):
        from voicehub.tools import parse_plan
        cfg = copy.deepcopy(DEFAULT)['tools']
        self.assertEqual(parse_plan('{"tool":"system_info","arguments":{}}', cfg),
                         ('system_info', {}))
        for raw in ['{"tool":"shell","arguments":{}}',
                    '{"tool":"weather","arguments":{"url":"http://localhost"}}']:
            with self.assertRaises(ValueError):
                parse_plan(raw, cfg)
        cfg['system_info'] = False
        with self.assertRaises(ValueError):
            parse_plan('{"tool":"system_info","arguments":{}}', cfg)


class CommonToolDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_weather_uses_fixed_endpoints_and_returns_dated_forecast(self):
        from unittest.mock import AsyncMock, patch
        from voicehub.mcp_tools.weather import execute as weather
        forecast = {'current':{'temperature_2m':20}, 'timezone':'Asia/Shanghai',
                    'daily':{'time':['2026-10-28'], 'weather_code':[3],
                    'temperature_2m_max':[22], 'temperature_2m_min':[14],
                    'precipitation_probability_max':[10]}}
        fetch = AsyncMock(side_effect=[{'results':[{'name':'上海','latitude':31.2,'longitude':121.4}]}, forecast])
        with patch('voicehub.mcp_tools.weather.fetch', fetch):
            result = await weather(None, '上海')
        self.assertEqual(result['daily'][0]['date'], '2026-10-28')
        self.assertEqual(result['location']['name'], '上海')
        self.assertEqual(fetch.call_args_list[1].args[1], 'https://api.open-meteo.com/v1/forecast')

    async def test_tomorrow_excludes_today_and_decodes_rain_conditions(self):
        from unittest.mock import AsyncMock, patch
        from voicehub.mcp_tools.weather import execute as weather
        data = {'current':{'temperature_2m':20}, 'daily':{
            'time':['2026-10-28','2026-10-29','2026-10-30'], 'weather_code':[3,51,3],
            'temperature_2m_max':[19,22,23], 'temperature_2m_min':[12,14,15],
            'precipitation_probability_max':[0,45,10]}}
        fetch = AsyncMock(side_effect=[{'results':[{'name':'上海','latitude':31.2,'longitude':121.4}]},data])
        with patch('voicehub.mcp_tools.weather.fetch', fetch):
            result = await weather(None, '上海', day_offset=1)
        self.assertEqual(result['requested_day'], '明天')
        self.assertEqual(result['forecast']['date'], '2026-10-29')
        self.assertEqual(result['forecast']['conditions'], '毛毛雨')
        self.assertEqual(result['forecast']['precipitation_probability_max'], 45)
        self.assertNotIn('current', result)
        self.assertNotIn('daily', result)

    async def test_news_filters_and_bounds_feed_without_following_article_links(self):
        from unittest.mock import AsyncMock, patch
        from voicehub.mcp_tools.news import execute as news
        raw = b'<rss><channel><item><title>AI news</title><description>&lt;b&gt;hello&lt;/b&gt;</description><link>javascript:alert(1)</link></item><item><title>Other</title></item></channel></rss>'
        fetch = AsyncMock(return_value=raw)
        with patch('voicehub.mcp_tools.news.fetch', fetch):
            result = await news(None, source='bbc', limit=1, keyword='AI')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['summary'], 'hello')
        self.assertEqual(result['items'][0]['url'], '')
        self.assertEqual(fetch.await_count, 1)

    async def test_unsafe_feed_and_upstream_errors_are_tool_errors(self):
        from unittest.mock import AsyncMock, patch
        from voicehub.common_tools import call_tool
        for fetch in (AsyncMock(return_value=b'<!DOCTYPE rss><rss/>'),
                      AsyncMock(side_effect=TimeoutError)):
            with patch('voicehub.mcp_tools.news.fetch', fetch):
                result = await call_tool(None, 'news', {'source':'bbc'})
            self.assertTrue(result['isError'])
