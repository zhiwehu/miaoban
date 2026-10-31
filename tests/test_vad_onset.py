import unittest
import numpy as np
from voice_loop import EnergyVAD
class OnsetTests(unittest.TestCase):
    def test_first_syllable_is_retained_while_confirming_speech(self):
        vad=EnergyVAD(threshold=.01,min_speech_ms=160,silence_ms=600,pre_roll_ms=400)
        chunks=[np.full(960, v,np.float32) for v in [.021,.022,.023]]
        for chunk in chunks:self.assertIsNone(vad.feed(chunk))
        result=None
        for _ in range(10):result=vad.feed(np.zeros(960,np.float32))
        self.assertIsNotNone(result)
        self.assertTrue(np.array_equal(result[:2880],np.concatenate(chunks)))

    def test_continuous_noise_cannot_grow_buffer_without_bound(self):
        vad=EnergyVAD(threshold=.01,min_speech_ms=160,silence_ms=600,pre_roll_ms=400)
        segments=[]
        for _ in range(550):
            result=vad.feed(np.full(960,.02,np.float32))
            if result is not None:segments.append(result)
        self.assertGreaterEqual(len(segments),2)
        self.assertTrue(all(len(x)<=16000*15+960 for x in segments))

    def test_high_energy_non_speech_still_ends_utterance(self):
        vad=EnergyVAD(threshold=.01,min_speech_ms=160,silence_ms=600,pre_roll_ms=400)
        for _ in range(5):vad.feed(np.full(960,.1,np.float32),speech=True)
        result=None
        for _ in range(10):result=vad.feed(np.full(960,.03,np.float32),speech=False)
        self.assertIsNotNone(result)
        self.assertLess(len(result),16000*2)
