import hashlib
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from voicehub.audio_cleanup import CleanupError, clean_wav

FFMPEG = shutil.which('ffmpeg')


@unittest.skipUnless(FFMPEG, 'ffmpeg is required for cleanup tests')
class AudioCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root/'source.wav'
        self.target = self.root/'clean.wav'
        sr = 16000
        t = np.arange(sr, dtype=np.float32)/sr
        signal = 0.95*np.sin(2*np.pi*440*t)
        signal = np.clip(signal*2.0, -1, 1)
        pcm = (signal*32767).astype('<i2').tobytes()
        with wave.open(str(self.source), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sr)
            handle.writeframes(pcm)

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_wav_preserves_length_and_removes_clipping(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        info = clean_wav(self.source, self.target, 'mild')
        self.assertEqual(info['duration_ms'], 1000)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        with wave.open(str(self.target), 'rb') as handle:
            self.assertEqual(handle.getnchannels(), 1)
            self.assertEqual(handle.getframerate(), 16000)
            self.assertEqual(handle.getnframes(), 16000)
            data = np.frombuffer(handle.readframes(handle.getnframes()), '<i2')
        self.assertLessEqual(float(np.abs(data).max())/32768, .96)
        self.assertFalse(bool((np.abs(data) >= 32767).any()))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(CleanupError) as context:
            clean_wav(self.source, self.target, 'extreme')
        self.assertEqual(context.exception.code, 'invalid_cleanup_profile')
        self.assertFalse(self.target.exists())
