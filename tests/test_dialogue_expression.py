import unittest
from dialogue_expression import parse_reply

class ExpressionTests(unittest.TestCase):
    def test_model_selects_emotion_without_speaking_metadata(self):
        self.assertEqual(parse_reply('[emotion:happy]\n太棒了，恭喜你！'), ('太棒了，恭喜你！','happy'))
    def test_comfort_uses_soft_sad_eyes(self):
        self.assertEqual(parse_reply('[emotion:sad]\n我在呢，慢慢说。'), ('我在呢，慢慢说。','sad'))
    def test_unknown_emotion_falls_back_without_reading_tag(self):
        self.assertEqual(parse_reply('[emotion:evil]\n你好'), ('你好','neutral'))
    def test_model_shorthand_is_metadata_too(self):
        self.assertEqual(parse_reply('[sleepy] 晚安。'), ('晚安。','sleepy'))
    def test_normal_brackets_are_not_metadata(self):
        self.assertEqual(parse_reply('[注意] 记得关灯。'), ('[注意] 记得关灯。','neutral'))
    def test_plain_text_keeps_compatible(self):
        self.assertEqual(parse_reply('你好呀'), ('你好呀','neutral'))
    def test_only_metadata_is_not_a_spoken_reply(self):
        self.assertEqual(parse_reply('[emotion:happy]'), ('','happy'))
    def test_tag_in_actual_quote_is_not_removed(self):
        self.assertEqual(parse_reply('代码里可以写 [emotion:happy]。'), ('代码里可以写 [emotion:happy]。','neutral'))
