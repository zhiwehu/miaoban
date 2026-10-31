import unittest
from xiaozhi_server import is_wake_word_only,strip_wake_word

class WakePhraseTests(unittest.TestCase):
    def test_only_custom_wake_waits_for_question(self):
        self.assertTrue(is_wake_word_only('小宝同学！'))
    def test_custom_prefix_is_removed(self):
        self.assertEqual(strip_wake_word('小宝同学，讲个故事'), '讲个故事')
    def test_short_question_is_not_lost(self):
        self.assertFalse(is_wake_word_only('同学是什么'))
