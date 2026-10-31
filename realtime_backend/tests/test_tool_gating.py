"""Ordinary conversation must not reach the tool planners."""
import unittest
from voicehub.config import DEFAULT
from voicehub.extension_routing import (is_conversational_turn, is_followup,
                                        is_small_talk, is_wake_only)
from voicehub.rex_planner import build_request

DEFAULTS={'default_city':'合肥','news_source':'chinanews','news_limit':3}


class FollowupTests(unittest.TestCase):
    def test_only_continuations_may_carry_previous_context(self):
        for text in ('明天呢？','那后天呢','换成上海吧','再来一条','继续','还有吗','也一样'):
            self.assertTrue(is_followup(text),text)

    def test_ordinary_chitchat_never_carries_previous_context(self):
        # 上一句查过新闻时，这些句子曾被带成新闻查询。
        for text in ('小宝同学。','小宝。','还可以啊。','今天过得好吗？','怎么了？','哈喽。','你好呀。'):
            self.assertFalse(is_followup(text),text)


class WakeAndSmallTalkTests(unittest.TestCase):
    def test_wake_word_alone_is_an_activation(self):
        for text in ('小宝。','小宝同学。','小宝，小宝，小宝，小宝。','你好喵伴','喵伴！'):
            self.assertTrue(is_wake_only(text),text)

    def test_wake_word_inside_a_request_stays_routable(self):
        for text in ('小宝，播放音乐。','小宝同学，今天天气怎么样？','搜小宝','小宝帮我记一下明天买牛奶'):
            self.assertFalse(is_wake_only(text),text)

    def test_asking_about_someones_day_is_not_a_weather_query(self):
        for text in ('今天过得好吗？','今天过得怎么样？','最近日子过得怎么样？','你今天过得开心吗？'):
            self.assertTrue(is_small_talk(text),text)

    def test_small_talk_detection_does_not_swallow_requests(self):
        for text in ('帮我记一下今天过得怎么样','查一下今天过得怎么样','提醒我今天过得怎么样'):
            self.assertFalse(is_small_talk(text),text)


class ConversationalTurnTests(unittest.TestCase):
    def test_conversational_lines_are_kept_away_from_tools(self):
        # 这几句在实测里会被分类器判成工具；「怎么了？」这类由模型正确判成 chat，
        # 不在这里固化，避免把正则变成越写越长的例外表。
        for text in ('小宝同学。','今天过得好吗？','还可以啊。','嗯，可以。','还行吧','挺好的'):
            self.assertTrue(is_conversational_turn(text),text)

    def test_real_requests_stay_routable(self):
        for text in ('今天天气怎么样？','明天呢？','最近有什么新闻？','查一下新闻，谢谢','这台电脑内存还剩多少？'):
            self.assertFalse(is_conversational_turn(text),text)


class RequestBuildingTests(unittest.TestCase):
    def test_context_is_withheld_from_plain_turns(self):
        request=build_request(1,['最近有什么新闻？','小宝同学。'],DEFAULTS)
        self.assertEqual(request['text'],'小宝同学。')
        self.assertEqual(request['previous_user'],'')
        self.assertEqual(request['defaults'],DEFAULTS)

    def test_context_is_sent_for_follow_ups(self):
        request=build_request(2,['今天天气怎么样？','明天呢？'],DEFAULTS)
        self.assertEqual(request['previous_user'],'今天天气怎么样？')

    def test_single_turn_has_no_context(self):
        self.assertEqual(build_request(3,['今天天气怎么样？'],DEFAULTS)['previous_user'],'')


if __name__=='__main__':
    unittest.main()
