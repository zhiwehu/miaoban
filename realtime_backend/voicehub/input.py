"""Bounded audio ingress; protocol control messages never wait for ASR or VAD."""
import asyncio
import contextlib
import time
import numpy as np
from .audio import AudioCodec, Segmenter, Silero, vad_path


class AudioInput:
    def __init__(self, session, format, echo_cancellation, config):
        self.session, self.format = session, format
        self.echo_cancellation, self.config = echo_cancellation, config
        self.queue = asyncio.Queue(maxsize=64)
        self.codec, self.vad = None, None
        self.segmenter = Segmenter(config['speech_threshold'], config['onset_ms'],
                                   config['silence_ms'], config['max_utterance_seconds'])
        self.buffer = b''
        self.task = asyncio.create_task(self.run())
        self.window, self.packets = time.monotonic(), 0

    def put(self, packet):
        now = time.monotonic()
        if now-self.window >= 1:
            self.window, self.packets = now, 0
        self.packets += 1
        if self.packets > 100 or self.queue.full():
            raise ValueError('audio_overload')
        if len(packet) > (8192 if self.format == 'pcm_s16le' else 2048):
            raise ValueError('audio_packet_limit')
        self.queue.put_nowait(packet)

    async def run(self):
        session = self.session
        disabled = False
        try:
            while True:
                packet = await self.queue.get()
                if disabled:
                    continue
                if session.state == 'standby' and session.wake_mode != 'backend':
                    self.segmenter.reset()
                    continue
                if session.turn and not self.echo_cancellation:
                    self.segmenter.reset()
                    self.buffer = b''
                    continue
                if self.vad is None:
                    try:
                        path = vad_path(self.config['vad_model'])
                        self.vad = await asyncio.to_thread(Silero, path)
                        self.codec = AudioCodec(self.format)
                    except Exception:
                        await session.emit('error', code='vad_unavailable',
                                           message='语音检测模型不可用；请配置 Silero VAD 路径，文字对话仍可使用。')
                        disabled = True
                        continue
                decoded = await asyncio.to_thread(self.codec.decode, packet)
                self.buffer += decoded
                while len(self.buffer) >= 1024:
                    pcm = np.frombuffer(self.buffer[:1024], '<i2').astype(np.float32)/32768
                    self.buffer = self.buffer[1024:]
                    probability = await asyncio.to_thread(self.vad.probability, pcm)
                    onset, segment = self.segmenter.feed(pcm, probability)
                    if onset:
                        session.last_activity = time.monotonic()
                        if session.turn:
                            if self.config['barge_in']:
                                await session.interrupt('speech')
                            else:
                                self.segmenter.reset()
                                continue
                        await session.emit('speech.started', probability=round(probability, 3))
                    if segment is not None:
                        if not session.turn:
                            await session.submit(pcm=segment)
                        else:
                            self.segmenter.reset()
        except asyncio.CancelledError:
            pass
        except Exception:
            with contextlib.suppress(Exception):
                await session.emit('error', code='audio_input_failed', message='音频输入失败，请重新连接。')

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
