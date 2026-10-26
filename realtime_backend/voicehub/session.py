"""Connection-independent turn ownership and a bounded streaming speech pipeline."""
import asyncio
import base64
import contextlib
import re
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Turn:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancelled: bool = False
    task: asyncio.Task | None = None
    played: asyncio.Event = field(default_factory=asyncio.Event)
    text: str = ''
    recorded: bool = False
    started: float = field(default_factory=time.monotonic)


class Session:
    def __init__(self, sid, device_id, store, pipeline, send, *, publish=None, wake_mode='manual',
                 wake_phrase='小宝同学', playback_ack=True, idle_seconds=30):
        self.id, self.device_id = sid, device_id
        self.store, self.pipeline, self.send = store, pipeline, send
        self.publish = publish or (lambda event: None)
        self.wake_mode, self.wake_phrase = wake_mode, wake_phrase
        self.playback_ack, self.idle_seconds = playback_ack, idle_seconds
        self.state, self.turn, self.seq = 'standby', None, 0
        self.tasks, self.closed = set(), False
        self.last_activity = time.monotonic()
        self.store.session(sid, device_id)

    async def emit(self, kind, turn=None, **data):
        if self.closed or (turn and (turn.cancelled or self.turn is not turn)):
            return
        self.seq += 1
        event = dict(type=kind, session_id=self.id, turn_id=turn.id if turn else None,
                     seq=self.seq, timestamp=time.time(), **data)
        # Background wake transcripts are sent only to their own client, never journalled.
        if kind not in ('audio.chunk', 'reply.delta', 'wake.check'):
            self.store.event(event)
        if kind not in ('audio.chunk', 'wake.check'):
            self.publish(event)
        await asyncio.wait_for(self.send(event), 3)

    async def transition(self, state, turn=None):
        if turn and (turn.cancelled or self.turn is not turn):
            return
        self.state = state
        self.last_activity = time.monotonic()
        await self.emit('session.state', turn, state=state)

    async def activate(self):
        await self.transition('listening')
        await self.emit('session.activated', mode=self.wake_mode)

    def playback_done(self, tid):
        if self.turn and self.turn.id == tid:
            self.turn.played.set()

    def record_reply(self, turn, status):
        if not turn.recorded:
            turn.recorded = True
            if turn.text:
                self.store.message(self.id, turn.id, 'assistant', turn.text, status)

    async def interrupt(self, reason='manual'):
        turn = self.turn
        if turn and not turn.cancelled:
            turn.cancelled = True  # Invalidate BEFORE cancelling provider or awaiting network I/O.
            if turn.task:
                turn.task.cancel()
            self.record_reply(turn, 'interrupted')
        self.turn = None
        await self.emit('playback.clear', interrupted_turn_id=turn.id if turn else None, reason=reason)
        if not self.closed:
            await self.transition('listening')

    async def submit(self, text=None, pcm=None):
        if self.closed:
            return
        if self.turn:
            await self.interrupt('new_input')
        turn = self.turn = Turn()
        turn.task = asyncio.create_task(self._run(turn, text, pcm))
        self.tasks.add(turn.task)
        turn.task.add_done_callback(self.tasks.discard)

    async def _run(self, turn, text, pcm):
        was_standby = self.state == 'standby'
        activated = not was_standby or self.wake_mode != 'backend'
        transcribed = text is None
        try:
            async with asyncio.timeout(120):
                await self.transition('thinking', turn)
                if text is None:
                    text = (await self.pipeline.transcribe(pcm)).strip()
                if was_standby and self.wake_mode == 'backend':
                    phrase = re.sub(r'[\W_]+', '', self.wake_phrase)
                    pattern = r'[\W_]*'.join(re.escape(c) for c in phrase)
                    match = re.search(pattern, text, flags=re.IGNORECASE)
                    await self.emit('wake.check', turn, matched=bool(match),
                                    phrase=self.wake_phrase, heard=text[:500])
                    if not match:
                        await self.transition('standby', turn)
                        return
                    activated = True
                    await self.emit('session.activated', turn, mode='backend')
                    text = text[match.end():].lstrip(' ，。！？,.:;!?')
                    if not text:
                        return
                if not text:
                    return
                if transcribed:
                    await self.emit('transcript.final', turn, text=text,
                                    elapsed_ms=round((time.monotonic()-turn.started)*1000))
                self.store.message(self.id, turn.id, 'user', text, 'confirmed')
                await self.emit('message.user', turn, text=text)
                history = [{'role': m['role'], 'content': m['content'] +
                            ('（回复被打断）' if m['status'] == 'interrupted' else '')}
                           for m in self.store.messages(self.id, limit=20)]
                # Keep model context bounded even when the journal contains long messages.
                budget, trimmed = 12000, []
                for message in reversed(history):
                    if len(message['content']) > budget:
                        break
                    trimmed.append(message)
                    budget -= len(message['content'])
                history = list(reversed(trimmed))
                sentences = asyncio.Queue(maxsize=4)
                async def produce():
                    pending = ''
                    async with contextlib.aclosing(self.pipeline.chat(history)) as source:
                        async for delta in source:
                            if turn.cancelled:
                                return
                            turn.text += delta
                            pending += delta
                            await self.emit('reply.delta', turn, text=delta)
                            if re.search(r'[。！？!?\n]', pending) or len(pending) >= 70:
                                await sentences.put(pending)
                                pending = ''
                    if not turn.text.strip():
                        raise RuntimeError('empty_reply')
                    if pending.strip():
                        await sentences.put(pending)
                    await sentences.put(None)

                audio = asyncio.Queue(maxsize=12)  # At most 480 ms of prefetched PCM.

                async def synthesize():
                    audio_samples = 0
                    while (sentence := await sentences.get()) is not None:
                        async with contextlib.aclosing(self.pipeline.speech(sentence)) as source:
                            async for pcm_chunk in source:
                                for offset in range(0, len(pcm_chunk), 1280):  # <=40 ms @ 16 kHz s16le
                                    block = pcm_chunk[offset:offset+1280]
                                    if len(block) % 2:
                                        raise ValueError('TTS returned unaligned PCM')
                                    audio_samples += len(block)//2
                                    if audio_samples > 16000 * 90:
                                        raise TimeoutError('reply_audio_limit')
                                    await audio.put(block)
                    await audio.put(None)

                async def speak():
                    # Keep a small playback lead to cover packetization and scheduling jitter.
                    # A fixed sample clock includes encoding/send time instead of adding it.
                    lead = .12
                    deadline = None
                    audio_samples = 0
                    while (block := await audio.get()) is not None:
                        if deadline is None:
                            await self.transition('speaking', turn)
                            await self.emit('audio.start', turn, sample_rate=16000,
                                            format='pcm_s16le', channels=1,
                                            elapsed_ms=round((time.monotonic()-turn.started)*1000))
                            deadline = time.monotonic() - lead
                        deadline = max(deadline, time.monotonic() - lead)
                        await asyncio.sleep(max(0, deadline-time.monotonic()))
                        await self.emit('audio.chunk', turn, data=base64.b64encode(block).decode())
                        audio_samples += len(block)//2
                        deadline += len(block)/32000
                    # Flush the codec while the receiver still has buffered audio, then allow
                    # that audio to drain before legacy clients return to microphone input.
                    finish = deadline + lead if deadline is not None else time.monotonic()
                    drain_ms = max(0, round((finish-time.monotonic())*1000))
                    await self.emit('audio.end', turn, samples=audio_samples, drain_ms=drain_ms)
                    await asyncio.sleep(max(0, finish-time.monotonic()))

                async with asyncio.TaskGroup() as group:
                    group.create_task(produce())
                    group.create_task(synthesize())
                    group.create_task(speak())
                await self.emit('reply.final', turn, text=turn.text)
                status = 'generated'
                if self.playback_ack:
                    try:
                        await asyncio.wait_for(turn.played.wait(), 5)
                        status = 'played'
                    except TimeoutError:
                        await self.emit('playback.unconfirmed', turn)
                self.record_reply(turn, status)
                await self.emit('turn.completed', turn, delivery=status,
                                elapsed_ms=round((time.monotonic()-turn.started)*1000))
        except asyncio.CancelledError:
            self.record_reply(turn, 'interrupted')
        except Exception as exc:
            while isinstance(exc, BaseExceptionGroup):
                exc = exc.exceptions[0]
            self.record_reply(turn, 'failed')
            with contextlib.suppress(Exception):
                # Provider details may contain URLs/credentials. Emit only a safe class/code.
                code = getattr(exc, 'code', 'turn_failed')
                message = ('设备未准备好播放，本轮未发送语音。请重新唤醒后再试。'
                           if code == 'playback_not_ready' else '本轮处理失败，请检查模型配置及服务状态。')
                await self.emit('error', turn, code=code, message=message, error_class=type(exc).__name__)
                await self.emit('playback.clear', reason='error', interrupted_turn_id=turn.id)
        finally:
            if self.turn is turn and not self.closed:
                self.turn = None
                if self.state != 'standby':
                    with contextlib.suppress(Exception):
                        await self.transition('listening' if activated else 'standby')

    async def idle(self):
        if not self.turn and self.state == 'listening' and time.monotonic()-self.last_activity > self.idle_seconds:
            await self.transition('standby')

    async def close(self):
        if self.closed:
            return
        await self.interrupt('disconnect')
        self.closed = True
        self.state = 'disconnected'
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.event(dict(type='session.disconnected', session_id=self.id, turn_id=None,
                              timestamp=time.time()))
