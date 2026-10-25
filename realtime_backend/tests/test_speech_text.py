import copy
import unittest
from voicehub.config import DEFAULT
from voicehub.models import Pipeline
from voicehub.speech_text import spoken_text


class SpeechTextTests(unittest.TestCase):
    def test_removes_emoji_clusters_without_removing_chinese_or_numbers(self):
        self.assertEqual(spoken_text('你好呀！😊👨‍👩‍👧‍👦👍🏽🇨🇳❤️'), '你好呀！')
        self.assertEqual(spoken_text('第1️⃣题，答案是2。'), '第1题，答案是2。')

    def test_keeps_intended_descriptions_as_normal_language(self):
        self.assertEqual(spoken_text('这个符号表示脸颊泛红的笑脸。'), '这个符号表示脸颊泛红的笑脸。')
        self.assertEqual(spoken_text('温度是26℃，价格是¥50。'), '温度是26℃，价格是¥50。')

    def test_emoji_only_has_no_spoken_content(self):
        self.assertEqual(spoken_text(' 😊\u200d\ufe0f '), '')


class SpeechBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_tts_never_receives_emoji_or_synthesizes_an_emoji_only_sentence(self):
        p=Pipeline(copy.deepcopy(DEFAULT),None,None)
        received=[]
        async def native(text,voice):
            received.append(text)
            yield b'\0\0'
        p.native_speech=native
        self.assertEqual(b''.join([b async for b in p.speech('你好！😊')]),b'\0\0')
        self.assertEqual([b async for b in p.speech('😊')],[])
        self.assertEqual(received,['你好！'])
