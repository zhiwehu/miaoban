import tempfile
import unittest
from pathlib import Path
import numpy as np
from voicehub.config import ConfigFile, validate
from voicehub.audio import Segmenter, AudioCodec


class ConfigTests(unittest.TestCase):
    def test_config_is_validated_before_atomic_save(self):
        with tempfile.TemporaryDirectory() as root:
            f = ConfigFile(Path(root)/'settings.json')
            original = f.get()
            bad = f.get()
            bad['wake']['mode'] = 'guess'
            with self.assertRaises(ValueError):
                f.save(bad)
            self.assertEqual(f.get(), original)
            self.assertEqual(ConfigFile(f.path).get(), original)

    def test_rejects_embedded_keys_and_invalid_urls(self):
        with tempfile.TemporaryDirectory() as root:
            c = ConfigFile(Path(root)/'settings.json').get()
            c['llm']['api_key'] = 'secret'
            with self.assertRaises(ValueError):
                validate(c)
            c['llm'].pop('api_key')
            c['llm']['url'] = 'file:///etc/passwd'
            with self.assertRaises(ValueError):
                validate(c)


class AudioTests(unittest.TestCase):
    def test_onset_uses_speech_probability_and_preserves_preroll(self):
        v = Segmenter()
        quiet = np.full(512, .01, np.float32)
        loud = np.full(512, .5, np.float32)
        for _ in range(10):
            start, segment = v.feed(loud, .01)
            self.assertFalse(start)  # Loud noise alone cannot trigger interruption.
        starts = []
        for _ in range(6):
            start, segment = v.feed(loud, .99)
            starts.append(start)
        self.assertEqual(sum(starts), 1)
        for _ in range(25):
            start, segment = v.feed(quiet, .01)
            if segment is not None:
                self.assertGreaterEqual(len(segment), 512*6)
                break
        else:
            self.fail('Utterance did not end after silence')

    def test_opus_roundtrip_and_pcm_packet_limits(self):
        c = AudioCodec('opus')
        tone = (np.sin(np.arange(960)*.1)*6000).astype('<i2').tobytes()
        packets = c.encode(tone, final=True)
        self.assertTrue(packets)
        out = b''.join(c.decode(p) for p in packets)
        self.assertGreater(len(out), 0)
        with self.assertRaises(ValueError):
            AudioCodec('pcm_s16le').decode(b'\0')

    def test_opus_roundtrip_preserves_speech_band_detail(self):
        rng = np.random.default_rng(73)
        rate = 16000
        t = np.arange(rate * 2) / rate
        noise = rng.normal(size=len(t))
        spectrum = np.fft.rfft(noise)
        frequencies = np.fft.rfftfreq(len(t), 1 / rate)
        spectrum[(frequencies < 3000) | (frequencies > 6500)] = 0
        breath = np.fft.irfft(spectrum, len(t))
        breath /= np.std(breath)
        voice = np.sin(2*np.pi*180*t) + .35*np.sin(2*np.pi*720*t) + .13*breath
        voice *= .2 + .8*np.sin(np.pi*t)**2
        voice *= .65 / np.max(np.abs(voice))
        pcm = (voice * 32767).astype('<i2').tobytes()
        codec = AudioCodec('opus')
        packets = []
        for start in range(0, len(pcm), 1280):
            packets.extend(codec.encode(pcm[start:start+1280]))
        packets.extend(codec.encode(b'', final=True))
        decoded = np.frombuffer(b''.join(codec.decode(p) for p in packets), '<i2').astype(float)
        source = np.frombuffer(pcm, '<i2').astype(float)
        errors = []
        for lag in range(80, 130):
            size = min(len(source), len(decoded)-lag)
            errors.append(np.mean((source[:size] - decoded[lag:lag+size])**2))
        snr = 10*np.log10(np.mean(source**2) / min(errors))
        self.assertGreater(snr, 20, f'Opus speech-band SNR was {snr:.2f} dB')

class SileroSchemaTests(unittest.TestCase):
    def test_v6_hidden_and_cell_states_are_kept_per_stream(self):
        from voicehub.audio import Silero
        class Engine:
            def run(self, names, values):
                self.values = values
                return np.array([.8]), values['h']+1, values['c']+2
        detector = Silero.__new__(Silero)
        detector.model = Engine()
        detector.schema = 'v6'
        detector.reset()
        self.assertAlmostEqual(detector.probability(np.zeros(512, np.float32)), .8)
        self.assertEqual(set(detector.model.values), {'input','h','c'})
        detector.probability(np.zeros(512, np.float32))
        self.assertEqual(float(detector.h[0,0,0]), 2)
        self.assertEqual(float(detector.c[0,0,0]), 4)
