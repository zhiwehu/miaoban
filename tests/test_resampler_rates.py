import unittest
import numpy as np
from voice_loop import StreamResampler

class RateTests(unittest.TestCase):
    def test_fallback_sample_rate_can_change_between_sentences(self):
        res=StreamResampler()
        chunks=[]
        for rate in [24000,22050,16000,24000]:
            chunks.append(res.push(np.full(rate//5,.1,np.float32),rate))
        audio=np.concatenate(chunks)
        self.assertGreater(len(audio),12000)
        self.assertLess(len(audio),13000)
        self.assertTrue(np.isfinite(audio).all())
