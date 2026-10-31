#!/usr/bin/env python3
"""
语音回路 —— 把喵伴的麦克风接到本地 ASR/LLM/TTS
================================================

数据流：

    设备 ──二进制 Opus 帧(16k mono, 60ms)──▶
        PyAV 解码 → 48k 立体声 → 重采样 → 16k 单声道 float32
        → 能量 VAD 切分「用户说话轮次」
        → ASR (Qwen3-ASR-0.6B / MLX)  → 文本
        → LLM (Qwen3-0.6B / MLX)        → 回复文本
        → TTS (Qwen3-TTS / MLX)         → 24k PCM
        → 重采样 16k → ffmpeg 编 Opus 60ms 帧
        → 设备（按实时速率推，队列上限 20 帧，不能快推）

打断：推 TTS 期间 VAD 一直在跑，检测到用户插话就立刻停推。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections import deque
from typing import Callable, Optional

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 60
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000        # 960


# ------------------------------------------------------------------ Opus 解码

class OpusDecoder:
    """裸 Opus 包 -> 16kHz 单声道 float32。

    小智协议是 version=1，二进制帧里就是**没有包头的裸 Opus 包**。
    libopus 内部一律解到 48kHz，所以这里要自己下混 + 重采样。
    """

    def __init__(self, target_rate: int = SAMPLE_RATE):
        import av
        self._av = av
        self._target = target_rate
        self._dec = av.CodecContext.create("opus", "r")
        self._resampler = av.audio.resampler.AudioResampler(
            format="fltp", layout="mono", rate=target_rate)

    def decode(self, packet: bytes) -> np.ndarray:
        """解一个 Opus 包。**不要**在这里 flush 重采样器（它是流式的）。"""
        out = []
        try:
            for frame in self._dec.decode(self._av.Packet(packet)):
                out.extend(self._resampler.resample(frame))
        except Exception:
            return np.zeros(0, dtype=np.float32)
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(
            [f.to_ndarray().reshape(-1).astype(np.float32) for f in out])

    def flush(self) -> np.ndarray:
        """把解码器和重采样器里剩下的取出来（会话结束时调用）。"""
        out = []
        try:
            for frame in self._dec.decode(None):
                out.extend(self._resampler.resample(frame))
        except Exception:
            pass
        try:
            for frame in self._resampler.resample(None):
                out.append(frame)
        except Exception:
            pass
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(
            [f.to_ndarray().reshape(-1).astype(np.float32) for f in out])


# ------------------------------------------------------------------ 重采样

def resample(pcm: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """任意采样率 -> 16k 单声道。"""
    pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if pcm.size == 0 or src_rate == dst_rate:
        return pcm
    import av
    res = av.audio.resampler.AudioResampler(
        format="fltp", layout="mono", rate=dst_rate)
    frame = av.AudioFrame.from_ndarray(
        pcm.reshape(1, -1).copy(), format="fltp", layout="mono")
    frame.sample_rate = src_rate
    out = []
    for f in res.resample(frame):
        out.append(f.to_ndarray().reshape(-1).astype(np.float32))
    for f in res.resample(None):
        out.append(f.to_ndarray().reshape(-1).astype(np.float32))
    return np.concatenate(out) if out else pcm


# ------------------------------------------------------------------ PCM -> Opus

def pcm_to_opus_packets(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> list[bytes]:
    """PCM -> 裸 Opus 包（60ms 一帧）。走 ffmpeg 编码 + 自己解 Ogg 容器。"""
    pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if pcm.size == 0:
        return []
    with tempfile.TemporaryDirectory(prefix="miaoban-tts-") as d:
        wav = os.path.join(d, "a.wav")
        ogg = os.path.join(d, "a.opus")
        import wave
        pcm16 = (np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm16.tobytes())
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
             "-ar", str(sample_rate), "-ac", "1", "-c:a", "libopus",
             "-b:a", "24k", "-frame_duration", str(FRAME_MS), "-f", "ogg", ogg],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg opus 编码失败: " + r.stderr[-300:])
        data = open(ogg, "rb").read()
    return parse_ogg_opus(data)


def parse_ogg_opus(data: bytes) -> list[bytes]:
    """从 Ogg 容器里抽出裸 Opus 包（跳过 OpusHead / OpusTags）。"""
    packets = []
    pos = 0
    while pos + 27 <= len(data):
        if data[pos:pos + 4] != b"OggS":
            break
        nseg = data[pos + 26]
        seg_table = data[pos + 27:pos + 27 + nseg]
        body_start = pos + 27 + nseg
        body_len = sum(seg_table)
        body = data[body_start:body_start + body_len]
        off = 0
        cur = b""
        for slen in seg_table:
            cur += body[off:off + slen]
            off += slen
            if slen < 255:
                packets.append(cur)
                cur = b""
        if cur:
            packets.append(cur)
        pos = body_start + body_len
    return [p for p in packets
            if not p.startswith(b"OpusHead") and not p.startswith(b"OpusTags")]


# ------------------------------------------------------------------ VAD

class EnergyVAD:
    """能量 VAD：切分「用户说话」轮次。

    不依赖模型，延迟极低；阈值按 RMS 定。宁可略保守（少触发），
    也不要在 TTS 播放时把自己的回声当成用户插话。
    """

    def __init__(self, *, threshold: float = 0.012, min_speech_ms: int = 160,
                 silence_ms: int = 700, pre_roll_ms: int = 300):
        self.threshold = threshold
        self.min_speech_ms = min_speech_ms
        self.silence_ms = silence_ms
        self.pre_roll_ms = pre_roll_ms
        self.reset()

    def reset(self):
        self._speech_ms = 0
        self._silence_ms = 0
        self._speaking = False
        self._buffer = []            # 本轮累积的 PCM
        self._pre = deque(maxlen=8)  # pre-roll
        self._last_rms = 0.0

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def rms(self) -> float:
        return self._last_rms

    def feed(self, pcm: np.ndarray, *, speech=None) -> Optional[np.ndarray]:
        """喂一段 PCM。返回非 None 表示「这一轮说完了」，值是完整的说话音频。"""
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if pcm.size == 0:
            return None
        rms = float(np.sqrt(np.mean(pcm ** 2)))
        self._last_rms = rms
        ms = len(pcm) * 1000.0 / SAMPLE_RATE
        voiced = rms >= self.threshold if speech is None else bool(speech)

        if not self._speaking:
            self._pre.append(pcm)
            if voiced:
                self._speech_ms += ms
                if self._speech_ms >= self.min_speech_ms:
                    self._speaking = True
                    self._silence_ms = 0
                    self._buffer = list(self._pre)
                    self._pre.clear()
            else:
                self._speech_ms = 0
        else:
            self._buffer.append(pcm)
            # Bound latency and memory even when room noise never falls quiet.
            if sum(len(chunk) for chunk in self._buffer) >= 15 * SAMPLE_RATE:
                audio = np.concatenate(self._buffer)
                self.reset()
                return audio
            if voiced:
                self._silence_ms = 0
            else:
                self._silence_ms += ms
                if self._silence_ms >= self.silence_ms:
                    audio = np.concatenate(self._buffer) if self._buffer else np.zeros(0, np.float32)
                    self.reset()
                    return audio
        return None

    def abort(self):
        """放弃当前这一轮（例如被打断）。"""
        self.reset()


# ------------------------------------------------------------------ 持续编码器

class StreamResampler:
    """Continuous PCM conversion, draining the old converter on rate changes."""
    def __init__(self, dst_rate: int = SAMPLE_RATE):
        import av
        self._av = av
        self._dst = dst_rate
        self._src = None
        self._res = None

    def push(self, pcm, src_rate: int):
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if not pcm.size:
            return np.zeros(0, np.float32)
        out = []
        if src_rate != self._src:
            if self._res is not None:
                out.extend(f.to_ndarray().reshape(-1).astype(np.float32)
                           for f in self._res.resample(None))
            self._src = src_rate
            self._res = (self._av.audio.resampler.AudioResampler(
                format="fltp", layout="mono", rate=self._dst)
                if src_rate != self._dst else None)
        if self._res is None:
            out.append(pcm)
        else:
            frame = self._av.AudioFrame.from_ndarray(
                pcm.reshape(1, -1).copy(), format="fltp", layout="mono")
            frame.sample_rate = src_rate
            out.extend(f.to_ndarray().reshape(-1).astype(np.float32)
                       for f in self._res.resample(frame))
        return np.concatenate(out) if out else np.zeros(0, np.float32)


class OpusEncoder:
    """持续的 Opus 编码器：一个上下文编一整段回复。

    ⚠️ 之前是每 0.5 秒起一个 ffmpeg 进程编码，每批都是**独立的 Opus 流**，
    拼起来在边界处不连续（听感就是"卡顿/毛刺"）。用一个持续上下文就没这问题，
    而且省掉了 ffmpeg 进程开销（编码从 ~1.2x 实时变成远快于实时）。
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, frame_ms: int = FRAME_MS,
                 bit_rate: int = 24000):
        import av
        import numpy as np
        self._av = av
        self._np = np
        self._sr = sample_rate
        self._frame = sample_rate * frame_ms // 1000
        self._buf = np.zeros(0, dtype=np.float32)
        self._pts = 0
        # ⚠️ 必须强制 frame_duration=60，否则 libopus 默认按 20ms 一包输出：
        # 喂 960 样本会返回 3 个 20ms 包。而设备是按 server hello 里的
        # frame_duration=60 建解码器、并按 60ms/包消费 —— 收到 20ms 包时
        # 我这边 26.5 秒才喂完 8.8 秒音频，设备队列被抽干 → 听感严重卡顿。
        # frame_duration 必须在 open() 之前通过 options 设。
        enc = av.CodecContext.create("libopus", "w")
        enc.options = {"frame_duration": str(frame_ms), "application": "voip"}
        enc.sample_rate = sample_rate
        enc.format = "s16"
        enc.layout = "mono"
        enc.bit_rate = bit_rate
        enc.open()
        if enc.frame_size not in (0, self._frame):
            raise RuntimeError(
                f"Opus 帧长 {enc.frame_size} != 期望 {self._frame}，"
                f"会让设备按 60ms 消费 20ms 包而严重欠载")
        self._enc = enc

    def _one(self, chunk) -> list[bytes]:
        np = self._np
        pcm16 = (np.clip(chunk, -1.0, 1.0) * 32767.0).astype("<i2")
        frame = self._av.AudioFrame.from_ndarray(
            pcm16.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = self._sr
        frame.pts = self._pts
        self._pts += self._frame
        return [bytes(p) for p in self._enc.encode(frame)]

    def encode(self, pcm) -> list[bytes]:
        np = self._np
        pcm = np.asarray(pcm, dtype=np.float32).reshape(-1)
        if pcm.size:
            self._buf = np.concatenate([self._buf, pcm])
        out = []
        while len(self._buf) >= self._frame:
            chunk = self._buf[:self._frame]
            self._buf = self._buf[self._frame:]
            out.extend(self._one(chunk))
        return out

    def flush(self) -> list[bytes]:
        np = self._np
        out = []
        if len(self._buf):
            pad = np.pad(self._buf, (0, self._frame - len(self._buf)))
            out.extend(self._one(pad))
            self._buf = np.zeros(0, dtype=np.float32)
        try:
            for p in self._enc.encode(None):
                out.append(bytes(p))
        except Exception:
            pass
        return out


class SpeechVAD:
    """Silero streaming probabilities drive endpointing, independent of loud noise."""
    def __init__(self):
        from mlx_audio.vad import load
        from voice_brain import resolve_model
        self.model = load(resolve_model("mlx-community/silero-vad"))
        self.gate = EnergyVAD(min_speech_ms=160, silence_ms=600, pre_roll_ms=400)
        self.reset()

    def reset(self):
        self.state = None
        self.pending = np.zeros(0, np.float32)
        self.gate.reset()

    def feed(self, pcm):
        self.pending = np.concatenate((self.pending, np.asarray(pcm, np.float32)))
        result = None
        while len(self.pending) >= 512:
            frame, self.pending = self.pending[:512], self.pending[512:]
            probability, self.state = self.model.feed(frame, self.state, sample_rate=16000)
            voiced = float(probability.item()) >= .5
            segment = self.gate.feed(frame, speech=voiced)
            if segment is not None:
                result = segment
        return result
