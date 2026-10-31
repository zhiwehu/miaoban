import unittest
from streaming_reply import SpeechSentences

class SentenceTests(unittest.TestCase):
    def test_split_metadata_and_first_sentence_without_waiting_for_tail(self):
        stream = SpeechSentences()
        self.assertEqual(stream.feed('[emo'), [])
        self.assertEqual(stream.feed('tion:happy]\n你'), [])
        self.assertEqual(stream.feed('好！后面'), ['你好！'])
        self.assertEqual(stream.emotion, 'happy')
        self.assertEqual(stream.feed('还有一句。'), ['后面还有一句。'])
        self.assertEqual(stream.finish(), [])
    def test_plain_text_tail_and_decimal_remain_intact(self):
        stream = SpeechSentences()
        self.assertEqual(stream.feed('温度是28.9度，不冷'), [])
        self.assertEqual(stream.finish(), ['温度是28.9度，不冷'])
    def test_long_unpunctuated_response_is_bounded(self):
        stream = SpeechSentences(max_chars=40)
        result=stream.feed('喵'*90)+stream.finish()
        self.assertEqual(''.join(result), '喵'*90)
        self.assertTrue(all(len(s)<=40 for s in result))
    def test_thought_and_metadata_are_never_spoken(self):
        stream=SpeechSentences()
        out=[]
        for part in ['<thi','nk>秘密。','</think>','[happy]','你好。']:
            out += stream.feed(part)
        out += stream.finish()
        self.assertEqual(out, ['你好。'])

    def test_decimal_split_across_model_tokens_is_not_a_sentence_end(self):
        stream=SpeechSentences()
        self.assertEqual(stream.feed('温度是28.'),[])
        self.assertEqual(stream.feed('9度。'),['温度是28.9度。'])

    def test_trailing_emotion_is_metadata_even_when_split_between_tokens(self):
        stream=SpeechSentences()
        output=[]
        for token in ['太棒了！',' [emo','tion:hap','py]']:
            output.extend(stream.feed(token))
        output.extend(stream.finish())
        self.assertEqual(''.join(output),'太棒了！')
        self.assertEqual(stream.emotion,'happy')

    def test_mid_sentence_emotion_is_not_spoken(self):
        stream=SpeechSentences()
        output=stream.feed('我在[emotion:sad]听你说。')+stream.finish()
        self.assertEqual(''.join(output),'我在听你说。')
        self.assertEqual(stream.emotion,'sad')
