"""VoiceStudio file transcription adapter; does not install or download models."""
import io
import json
import uuid
import urllib.request
import wave

import numpy as np


class VoiceStudioClient:
    def __init__(self, url='http://127.0.0.1:3900', api_key='', timeout=60):
        self.url = url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout

    def transcribe(self, pcm, sample_rate=16000):
        pcm = np.asarray(pcm, dtype=np.float32)
        if pcm.size == 0:
            return ''
        if pcm.ndim != 1 or sample_rate <= 0:
            raise ValueError('Expected mono PCM and a positive sample rate')
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes((np.clip(pcm, -1, 1) * 32767).astype('<i2').tobytes())
        boundary = uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            'filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'
        ).encode() + audio.getvalue() + f'\r\n--{boundary}--\r\n'.encode()
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary}'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        request = urllib.request.Request(
            self.url + '/v1/audio/transcriptions', data=body, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict) or not isinstance(result.get('text'), str):
            raise ValueError('VoiceStudio response missing transcription text')
        return result['text'].strip()

    def discover(self):
        """Return installed service contracts and voices without installing models."""
        result = {}
        for name, path in [('health', '/health'), ('schema', '/openapi.json'),
                           ('voices', '/v1/audio/voices')]:
            headers = {'Authorization': f'Bearer {self.api_key}'} if self.api_key else {}
            request = urllib.request.Request(self.url + path, headers=headers)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result[name] = json.load(response)
        return result

    def speech(self, text, voice='alloy', stop_event=None):
        """Buffered WAV synthesis; cancellation suppresses playback, not server work.

        The HTTP request can remain in flight until the configured timeout.
        This endpoint does not promise incremental model generation.
        """
        def stopped():
            return stop_event is not None and stop_event.is_set()
        if not text.strip() or stopped():
            return
        payload = json.dumps({'model': 'tts-1', 'voice': voice, 'input': text,
                              'response_format': 'wav'}).encode()
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        request = urllib.request.Request(self.url + '/v1/audio/speech',
                                         data=payload, headers=headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            raw = response.read(64 * 1024 * 1024 + 1)
        if stopped():
            return
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError('VoiceStudio audio exceeds 64 MiB limit')
        with wave.open(io.BytesIO(raw), 'rb') as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                raise ValueError('Expected mono 16-bit PCM WAV from VoiceStudio')
            rate = wav.getframerate()
            while not stopped():
                data = wav.readframes(max(1, rate // 10))
                if not data:
                    break
                yield np.frombuffer(data, dtype='<i2').astype(np.float32) / 32768, rate
