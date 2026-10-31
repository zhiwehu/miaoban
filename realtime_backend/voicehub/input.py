"""Bounded audio ingress; protocol control messages never wait for ASR or VAD."""
import asyncio
import contextlib
import time
import numpy as np
from .audio import AudioCodec, Segmenter, Silero, to_pcm16, vad_path


class AudioInput:
    def __init__(self, session, format, echo_cancellation, config):
        self.session, self.format = session, format
        self.echo_cancellation, self.config = echo_cancellation, config
        self.queue = asyncio.Queue(maxsize=64)
        self.codec, self.vad = None, None
        self.vad_task = None
        self.recording_vad_failed = False
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
        recording_seen = None
        recording_onset = False
        recording_between = False
        recording_pause_frames = 0
        pending_pause_ms = None
        try:
            while True:
                packet = await self.queue.get()
                if packet is None:
                    break
                recording = session.recording
                if recording is not None:
                    session.last_recording_packet_at = time.monotonic()
                    # Recording mode owns every packet: no ASR turn, no replies.
                    if recording_seen is not recording:
                        recording_seen = recording
                        self.segmenter.reset()
                        self.buffer = b''
                        recording_onset = False
                        recording_between = False
                        recording_pause_frames = 0
                        pending_pause_ms = None
                        self.recording_vad_failed = False
                        disabled = False
                        if not session.recording_local_asr_allowed():
                            self.recording_vad_failed = True
                            await session.recording_transcription_unavailable(
                                '当前 ASR 不是本机模型，无法实时转写；录音不会上传，仍在继续。请用网页或开关键停止。')
                    if self.codec is None:
                        try:
                            self.codec = AudioCodec(self.format)
                        except Exception:
                            await session.recording_transcription_unavailable(
                                '音频解码不可用，录音无法继续；请检查运行环境的音频依赖。')
                            continue
                    try:
                        decoded = await asyncio.to_thread(self.codec.decode, packet)
                    except (ValueError, TypeError):
                        continue
                    except Exception:
                        with contextlib.suppress(Exception):
                            await session.emit('error', code='audio_input_failed',
                                               message='音频输入失败，请重新连接。')
                        continue
                    if session.recording is not recording:
                        continue
                    try:
                        if recording.write(decoded) == 'limit':
                            await session.recording_limit_reached()
                    except OSError:
                        await session.recording_storage_failed()
                        continue
                    if self.recording_vad_failed:
                        continue
                    if self.vad is None:
                        # Load the VAD beside the capture loop, never in its path:
                        # a slow model load must not overflow the audio queue.
                        if self.vad_task is None or self.vad_task.done():
                            self.vad_task = asyncio.create_task(
                                self._load_recording_vad(session))
                        continue
                    self.buffer += decoded
                    while len(self.buffer) >= 1024:
                        pcm = np.frombuffer(self.buffer[:1024], '<i2').astype(np.float32)/32768
                        self.buffer = self.buffer[1024:]
                        probability = await asyncio.to_thread(self.vad.probability, pcm)
                        onset, segment = self.segmenter.feed(pcm, probability)
                        if onset:
                            if recording_between:
                                pending_pause_ms = (self.config['silence_ms']
                                                    + recording_pause_frames*32)
                            recording_between = False
                            recording_pause_frames = 0
                            # Drop any utterance that began while a prompt was audible,
                            # so the assistant cannot confirm its own stop prompt.
                            recording_onset = session.recording_listen_allowed()
                        if segment is not None:
                            if (recording_onset and session.recording is recording
                                    and session.recording_listen_allowed()):
                                lead = self.segmenter.last_pre_trim_frames*512
                                tail = self.segmenter.last_trailing_silence*512
                                end = len(segment)-tail
                                start_ms = max(0, round(
                                    (recording.samples-len(segment))/16000*1000))
                                if end > lead:
                                    speech = segment[lead:end]
                                    if speech.size:
                                        recording.write_speech(
                                            to_pcm16(speech, 16000),
                                            start_ms+lead/16000*1000,
                                            start_ms+end/16000*1000)
                                session.recording_heard(segment, start_ms, pending_pause_ms)
                            recording_onset = False
                            pending_pause_ms = None
                            recording_between = True
                            recording_pause_frames = 0
                        elif (recording_between
                              and probability < self.config['speech_threshold']):
                            recording_pause_frames += 1
                    continue
                if recording_seen is not None:
                    # Do not carry a half-utterance from recording mode into chat.
                    self.segmenter.reset()
                    self.buffer = b''
                    recording_seen = None
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
                        session.input_active=True
                        session.last_activity = time.monotonic()
                        if session.turn:
                            if self.config['barge_in']:
                                await session.interrupt('speech')
                            else:
                                self.segmenter.reset()
                                session.input_active=False
                                continue
                        await session.emit('speech.started', probability=round(probability, 3))
                    if segment is not None:
                        session.input_active=False
                        if not session.turn:
                            await session.submit(pcm=segment)
                        else:
                            self.segmenter.reset()
        except asyncio.CancelledError:
            pass
        except Exception:
            with contextlib.suppress(Exception):
                await session.emit('error', code='audio_input_failed', message='音频输入失败，请重新连接。')

    async def _load_recording_vad(self, session):
        try:
            path = vad_path(self.config['vad_model'])
            vad = await asyncio.to_thread(Silero, path)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.recording_vad_failed = True
            with contextlib.suppress(Exception):
                await session.recording_transcription_unavailable(
                    '语音检测模型不可用，无法实时转写；录音仍在继续，请用网页或开关键停止。')
        else:
            self.vad = vad

    async def close(self):
        self.session.input_active=False
        # On a device key press the channel closes immediately; drain already
        # queued packets into the WAV instead of dropping the tail of the memo.
        if self.session.recording is not None and not self.task.done():
            try:
                await asyncio.wait_for(self.queue.put(None), 2)
                await asyncio.wait_for(self.task, 2)
            except (TimeoutError, asyncio.QueueFull):
                self.task.cancel()
        else:
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        if self.vad_task and not self.vad_task.done():
            self.vad_task.cancel()
            await asyncio.gather(self.vad_task, return_exceptions=True)
