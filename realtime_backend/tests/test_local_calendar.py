import copy
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from voicehub.config import DEFAULT
from voicehub.local_calendar import calendar_answer
from voicehub.tool_chat import chat


class CalendarTests(unittest.TestCase):
    def test_weekday_uses_clock_and_handles_day_boundaries(self):
        now=datetime(2026,10,29,0,5,tzinfo=timezone(timedelta(hours=8)))
        for text in ('今天星期几？','你知道今天星期几吗？','请问今天是周几啊','上海今天星期几？'):
            self.assertEqual(calendar_answer(text,now),'今天是2026年10月29日，星期四。')
        self.assertIn('星期三',calendar_answer('昨天星期几',now))
        self.assertIn('2026年10月30日，星期五',calendar_answer('明天是周几',now))
        self.assertIn('2026年10月29日',calendar_answer('今天几月几号',now))

    def test_does_not_swallow_weather_or_calendar_discussions(self):
        for text in ('上海今天天气怎么样','今天星期几，顺便查上海天气','解释星期几是什么意思',
                     '把今天星期几翻译成英文','写首关于星期一的诗','明天会下雨吗','伦敦今天星期几'):
            self.assertIsNone(calendar_answer(text))


class CalendarPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_planners_bypass_weather_and_clear_pending_city(self):
        for planner in ('rex','llm'):
            cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(planner=planner,default_city='上海')
            p=SimpleNamespace(config=cfg,_previous_tool_plan={'tool':'weather','arguments':{'city':'上海'}})
            with patch('voicehub.tool_chat.calendar_answer',return_value='今天是2026年10月29日，星期四。'), \
                 patch('voicehub.tool_chat.CommonToolsClient',side_effect=AssertionError('No MCP')):
                reply=[item async for item in chat(p,[{'role':'user','content':'今天星期几？'}])]
            self.assertEqual(reply,['今天是2026年10月29日，星期四。'])
            self.assertIsNone(p._previous_tool_plan)
