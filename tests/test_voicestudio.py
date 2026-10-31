import io
import json
import unittest
import wave
from unittest.mock import patch

import numpy as np
from voicestudio_client import VoiceStudioClient


class VoiceStudioTests(unittest.TestCase):
    def test_transcribes_pcm_as_wav_with_auth(self):
        client = VoiceStudioClient('http://127.0.0.1:3900', 'secret')
        response = io.BytesIO(json.dumps({'text': '你好喵伴'}).encode())
        with patch('urllib.request.urlopen', return_value=response) as send:
            self.assertEqual(client.transcribe(np.zeros(160), 16000), '你好喵伴')
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:3900/v1/audio/transcriptions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret')
        wav = request.data.split(b'RIFF', 1)[1].split(b'\r\n--', 1)[0]
        with wave.open(io.BytesIO(b'RIFF' + wav)) as audio:
            self.assertEqual(audio.getframerate(), 16000)
            self.assertEqual(audio.getnframes(), 160)

    def test_empty_audio_does_not_contact_service(self):
        with patch('urllib.request.urlopen') as send:
            self.assertEqual(VoiceStudioClient().transcribe(np.array([]), 16000), '')
            send.assert_not_called()

    def test_malformed_response_is_not_silence(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{}')):
            with self.assertRaises(ValueError):
                VoiceStudioClient().transcribe(np.zeros(160), 16000)

    def test_speech_decodes_wav_and_uses_selected_voice(self):
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(np.array([0, 16384, -16384], dtype='<i2').tobytes())
        with patch('urllib.request.urlopen', return_value=io.BytesIO(audio.getvalue())) as send:
            chunks = list(VoiceStudioClient().speech('你好', 'saved-voice'))
        np.testing.assert_allclose(chunks[0][0], [0, .5, -.5])
        self.assertEqual(chunks[0][1], 24000)
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload['voice'], 'saved-voice')
        self.assertEqual(payload['response_format'], 'wav')

    def test_cancelled_speech_does_not_make_request(self):
        import threading
        stop = threading.Event()
        stop.set()
        with patch('urllib.request.urlopen') as send:
            self.assertEqual(list(VoiceStudioClient().speech('你好', stop_event=stop)), [])
            send.assert_not_called()

    def test_invalid_audio_is_reported(self):
        with patch('urllib.request.urlopen', return_value=io.BytesIO(b'{"error":"missing model"}')):
            with self.assertRaises((ValueError, wave.Error, EOFError)):
                list(VoiceStudioClient().speech('你好'))

    def test_brain_routes_tts_without_loading_mlx(self):
        from voice_brain import VoiceBrain
        with patch.dict('os.environ', {'MIAOBAN_ASR_ENGINE': 'mlx', 'VOICESTUDIO_VOICE': 'cat'}):
            brain = VoiceBrain(tts_engine='voicestudio')
            try:
                with patch.object(brain._voicestudio, 'speech', side_effect=lambda *args: ((np.ones(2), 24000) for _ in range(1))) as speech:
                    self.assertEqual(len(list(brain.tts_stream('你好'))), 1)
                    self.assertEqual(speech.call_args.args[:2], ('你好', 'cat'))
                    self.assertIsNone(brain._tts)
            finally:
                for pool in (brain._asr_pool, brain._tts_pool, brain._llm_pool):
                    pool.shutdown()
