"""16k mono PCM boundary; per-connection codecs, VAD state and utterance buffers."""
from collections import deque
from pathlib import Path
import importlib.util
import math
import os
import numpy as np


def vad_path(configured=''):
    path = configured or os.environ.get('VOICE_VAD_MODEL', '')
    if path:
        if not Path(path).is_file():
            raise ValueError('VAD 模型文件不存在')
        return path
    package = importlib.util.find_spec('faster_whisper')
    if package and package.origin:
        candidate = Path(package.origin).parent/'assets/silero_vad_v6.onnx'
        if candidate.exists():
            return str(candidate)
    # Reuse an existing downloaded model without importing another project's code.
    for candidate in (Path.home()/'.cache/uv').glob('archive-v0/*/faster_whisper/assets/silero_vad_v6.onnx'):
        return str(candidate)
    raise ValueError('请在音频配置中指定 Silero VAD v6 ONNX 模型路径')


class Silero:
    def __init__(self, path):
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.model = ort.InferenceSession(path, options, providers=['CPUExecutionProvider'])
        names = {v.name for v in self.model.get_inputs()}
        if names == {'input', 'h', 'c'}:
            self.schema = 'v6'
        elif names == {'input', 'state', 'sr'}:
            self.schema = 'standard'
        else:
            raise ValueError('Unsupported Silero ONNX schema')
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, frame):
        samples = np.concatenate([self.context, frame.reshape(1, 512)], axis=1)
        if self.schema == 'v6':
            result, self.h, self.c = self.model.run(None, {'input': samples, 'h': self.h, 'c': self.c})
        else:
            result, self.state = self.model.run(None, {'input': samples, 'state': self.state,
                                                      'sr': np.array(16000, dtype=np.int64)})
        self.context = samples[:, -64:].copy()
        return float(result.squeeze())


class Segmenter:
    def __init__(self, threshold=.65, onset_ms=160, silence_ms=640, max_seconds=15):
        self.threshold, self.onset_frames = threshold, math.ceil(onset_ms/32)
        self.silence_frames, self.max_samples = math.ceil(silence_ms/32), max_seconds*16000
        self.last_pre_trim_frames = 0
        self.last_trailing_silence = 0
        self.last_ended_by_silence = False
        self.segment_trim_frames = 0
        self.reset()

    def reset(self):
        self.pre = deque(maxlen=10)
        self.frames = []
        self.streak = self.silence = self.count = 0
        self.speaking = False
        self.segment_trim_frames = 0

    def feed(self, frame, probability):
        onset, segment = False, None
        self.pre.append(frame.copy())
        voice = probability >= self.threshold
        if not self.speaking:
            self.streak = self.streak+1 if voice else 0
            if self.streak >= self.onset_frames:
                onset = self.speaking = True
                self.frames = list(self.pre)
                self.count = sum(len(f) for f in self.frames)
                # Keep the onset frames; drop only the silence before them.
                self.segment_trim_frames = max(0, len(self.pre)-self.onset_frames)
        else:
            self.frames.append(frame.copy())
            self.count += len(frame)
            self.silence = 0 if voice else self.silence+1
            if self.silence >= self.silence_frames or self.count >= self.max_samples:
                segment = np.concatenate(self.frames)
                self.last_pre_trim_frames = self.segment_trim_frames
                self.last_trailing_silence = self.silence
                self.last_ended_by_silence = self.silence >= self.silence_frames
                self.reset()
        return onset, segment


class AudioCodec:
    def __init__(self, format, *, bitrate=64000, application='audio'):
        if format not in ('pcm_s16le', 'opus'):
            raise ValueError('支持 pcm_s16le / opus')
        self.format = format
        self.buffer = b''
        self.pts = 0
        if format == 'opus':
            import av
            self.av = av
            self.decoder = av.CodecContext.create('opus', 'r')
            self.resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
            self.encoder = av.CodecContext.create('libopus', 'w')
            self.encoder.sample_rate = 16000
            self.encoder.layout = 'mono'
            self.encoder.format = 's16'
            self.encoder.bit_rate = bitrate
            self.encoder.options = {'frame_duration': '60', 'application': application}
            self.encoder.open()

    def decode(self, packet):
        if len(packet) > 16384:
            raise ValueError('音频包过大')
        if self.format == 'pcm_s16le':
            if len(packet) % 2:
                raise ValueError('PCM 长度必须为偶数')
            return packet
        frames = []
        for frame in self.decoder.decode(self.av.Packet(packet)):
            frames.extend(self.resampler.resample(frame))
        return b''.join(f.to_ndarray().astype('<i2').tobytes() for f in frames)

    def encode(self, pcm, final=False):
        if self.format == 'pcm_s16le':
            return [pcm] if pcm else []
        self.buffer += pcm
        if final and self.buffer:
            self.buffer += b'\0' * ((1920-len(self.buffer)%1920)%1920)
        packets = []
        while len(self.buffer) >= 1920:
            frame = self.av.AudioFrame.from_ndarray(np.frombuffer(self.buffer[:1920], '<i2').reshape(1,-1),
                                                    format='s16', layout='mono')
            self.buffer = self.buffer[1920:]
            frame.sample_rate, frame.pts = 16000, self.pts
            from fractions import Fraction
            frame.time_base = Fraction(1, 16000)
            self.pts += 960
            packets.extend(bytes(p) for p in self.encoder.encode(frame))
        if final:
            packets.extend(bytes(p) for p in self.encoder.encode(None))
        return packets


def to_pcm16(audio, source_rate):
    if isinstance(audio, bytes):
        audio = np.frombuffer(audio, '<i2').astype(np.float32)/32768
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if source_rate != 16000 and len(audio):
        import av
        frame = av.AudioFrame.from_ndarray(audio.reshape(1,-1), format='flt', layout='mono')
        frame.sample_rate = source_rate
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        return b''.join(f.to_ndarray().astype('<i2').tobytes()
                        for f in resampler.resample(frame) + resampler.resample(None))
    return (np.clip(audio, -1, 1)*32767).astype('<i2').tobytes()
