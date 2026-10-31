import threading
import unittest
from unittest.mock import patch
from voice_brain import VoiceBrain

class TtsOwnershipTests(unittest.TestCase):
    def test_cancelled_turn_never_starts_generation(self):
        brain = VoiceBrain(); stop = threading.Event(); stop.set()
        with patch.object(brain, '_tts_stream_unlocked') as generate:
            self.assertEqual(list(brain.tts_stream('hello', stop_event=stop)), [])
            generate.assert_not_called()
    def test_generators_cannot_share_model_concurrently(self):
        brain = VoiceBrain(); entered_second = threading.Event()
        def generate(text, *args):
            if text == 'second': entered_second.set()
            yield ([], 24000)
        with patch.object(brain, '_tts_stream_unlocked', side_effect=generate):
            first = brain.tts_stream('first'); next(first)
            result = []
            worker = threading.Thread(target=lambda: result.extend(brain.tts_stream('second')))
            worker.start()
            try:
                self.assertFalse(entered_second.wait(.02))
            finally:
                first.close(); worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(entered_second.is_set())
