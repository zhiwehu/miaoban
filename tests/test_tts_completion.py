import unittest
from types import SimpleNamespace
import numpy as np
from voice_brain import VoiceBrain

class CompletionTests(unittest.TestCase):
    def test_short_text_slow_voice_keeps_its_last_audio_chunk(self):
        class Model:
            def generate(self, **kwargs):
                for i in range(30):
                    # A voiced sound naturally has high short-lag autocorrelation.
                    t=np.arange(9600)/24000
                    yield SimpleNamespace(audio=(.1*np.sin(2*np.pi*180*t)).astype(np.float32), sample_rate=24000)
                yield SimpleNamespace(audio=np.full(1200,.023,dtype=np.float32), sample_rate=24000)
        brain=VoiceBrain();brain._tts=Model()
        chunks=list(brain.tts_stream('你好呀。'))
        self.assertEqual(len(chunks),31)
        self.assertTrue(np.allclose(chunks[-1][0],.023))
    def test_failed_consumer_closes_model_stream(self):
        closed=[]
        class Model:
            def generate(self,**kwargs):
                try:
                    while True:yield SimpleNamespace(audio=np.full(9600,.1,dtype=np.float32),sample_rate=24000)
                finally:closed.append(True)
        brain=VoiceBrain();brain._tts=Model();stream=brain.tts_stream('你好')
        next(stream);stream.close()
        self.assertEqual(closed,[True])

    def test_speech_followed_by_endless_silence_finishes_promptly(self):
        closed=[]
        class Model:
            def generate(self, **kwargs):
                try:
                    yield SimpleNamespace(audio=np.full(9600,.1,dtype=np.float32), sample_rate=24000)
                    for _ in range(100):
                        yield SimpleNamespace(audio=np.zeros(9600,dtype=np.float32), sample_rate=24000)
                finally:closed.append(True)
        brain=VoiceBrain();brain._tts=Model()
        chunks=list(brain.tts_stream('你好。'))
        self.assertLess(sum(len(x)/r for x,r in chunks),2)
        self.assertTrue(np.any(chunks[0][0]))
        self.assertEqual(closed,[True])

    def test_silent_attempt_retries_before_emitting_speech(self):
        calls=[]
        class Model:
            def generate(self, **kwargs):
                calls.append(kwargs['temperature'])
                if len(calls)==1:
                    for _ in range(10):
                        yield SimpleNamespace(audio=np.zeros(19200,dtype=np.float32),sample_rate=24000)
                else:
                    yield SimpleNamespace(audio=np.full(19200,.1,dtype=np.float32),sample_rate=24000)
        brain=VoiceBrain();brain._tts=Model()
        result=list(brain.tts_stream('你好，我是小宝。'))
        self.assertEqual(calls,[.5,.85])
        self.assertEqual(len(result),1)
        self.assertTrue(np.allclose(result[0][0],.1))

    def test_repeated_silence_uses_native_fallback(self):
        from unittest.mock import patch
        from voice_brain import SilentSpeechError
        brain=VoiceBrain()
        with patch.object(brain,'_tts_attempt',side_effect=SilentSpeechError('silence')), \
             patch.object(brain,'_macos_tts_stream',side_effect=lambda *a: ((np.ones(10),24000) for _ in range(1))) as fallback:
            chunks=list(brain.tts_stream('我在，请说。'))
        self.assertEqual(len(chunks),1)
        fallback.assert_called_once()

    def test_runaway_generation_stops_before_twenty_seconds(self):
        closed=[]
        class Model:
            def generate(self,**kwargs):
                try:
                    for _ in range(200):
                        yield SimpleNamespace(audio=np.full(19200,.1,dtype=np.float32),sample_rate=24000)
                finally:closed.append(True)
        brain=VoiceBrain();brain._tts=Model();seconds=0
        with self.assertRaisesRegex(RuntimeError,'audio budget'):
            for pcm,rate in brain.tts_stream('你好，这是一次测试。'):
                seconds+=len(pcm)/rate
        self.assertLessEqual(seconds,15)
        self.assertEqual(closed,[True])
