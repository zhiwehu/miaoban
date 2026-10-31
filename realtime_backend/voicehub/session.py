"""Connection-independent turn ownership and a bounded streaming speech pipeline."""
import asyncio
import base64
import contextlib
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from . import phrasing
from .recording import (CONFIRM_SECONDS, TRANSCRIPT_JOIN_PAUSE_MS,
                        command as recording_command, stamp_ms)
from .speaker import SpeakerTracker
from .tools import ToolNotice, ToolStatus


@dataclass
class Turn:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    cancelled: bool = False
    task: asyncio.Task | None = None
    played: asyncio.Event = field(default_factory=asyncio.Event)
    text: str = ''
    recorded: bool = False
    delivery: str = ''
    notification_id: str = ''
    audio_started: bool = False
    system: bool = False  # recording prompts are never journalled as a reply
    started: float = field(default_factory=time.monotonic)


# 单轮播报的硬上限（秒）。它只用来拦住失控的合成，正常的长故事不能被截断：
# 30 秒和 90 秒都曾把正常播报拦腰砍断。
MAX_REPLY_AUDIO_SECONDS = 180


class Session:
    def __init__(self, sid, device_id, store, pipeline, send, *, publish=None, wake_mode='manual',
                 wake_phrase='小宝同学', playback_ack=True, idle_seconds=30, recording_manager=None):
        self.id, self.device_id = sid, device_id
        self.store, self.pipeline, self.send = store, pipeline, send
        self.pipeline.session_id=sid
        self.pipeline.device_id=device_id
        self.publish = publish or (lambda event: None)
        self.wake_mode, self.wake_phrase = wake_mode, wake_phrase
        self.playback_ack, self.idle_seconds = playback_ack, idle_seconds
        self.state, self.turn, self.seq = 'standby', None, 0
        self.tasks, self.closed = set(), False
        self.last_activity = time.monotonic()
        self.store.session(sid, device_id)
        self.input_active=False
        self.streaming=False  # audio.start 到 audio.end 之间为 True，播报期间不算空闲
        self.recording_manager=recording_manager
        self.recording=None  # active WAV capture; while set, ordinary speech is never answered
        self.recording_queue=None
        self.recording_worker=None
        self.recording_confirm_timer=None
        self.recording_listen_after=0.0
        self.last_recording_packet_at=0.0
        self.recording_asr_notified=False
        self.recording_speakers=None
        self.recording_speaker_failed=False
        self.recording_speaker_failures=0
        self.recording_speaker_retry_at=0.0
        self.recording_speaker_notified=False
        self.rediarize_tasks=set()
        self.speaker_engine=getattr(getattr(pipeline,'local',None),'speakers',None)
        self.minutes=getattr(getattr(pipeline,'local',None),'minutes',None)
        self.operations=getattr(getattr(pipeline,'local',None),'operations',None)
        self.notifications=asyncio.Queue(maxsize=30);self.notification_ids=set()
        self.notification_callback=self.queue_notification
        self.announcer=None
        if self.operations:
            self.operations.bind(sid,self.notification_callback)
            self.announcer=asyncio.create_task(self.announce_results())

    async def emit(self, kind, turn=None, **data):
        if self.closed or (turn and (turn.cancelled or self.turn is not turn)):
            return
        self.seq += 1
        event = dict(type=kind, session_id=self.id, turn_id=turn.id if turn else None,
                     seq=self.seq, timestamp=time.time(), **data)
        # Background wake transcripts are sent only to their own client, never journalled.
        # Recording text already lives in its own .txt; do not double-store each line.
        if kind not in ('audio.chunk', 'reply.delta', 'wake.check', 'recording.transcript'):
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
        if self.recording is not None:
            return
        await self.transition('listening')
        await self.emit('session.activated', mode=self.wake_mode)

    def playback_done(self, tid):
        if self.turn and self.turn.id == tid:
            self.turn.played.set()

    def record_reply(self, turn, status):
        if turn.system:
            return
        if not turn.recorded:
            turn.recorded = True
            turn.delivery = status
            if turn.text:
                self.store.message(self.id, turn.id, 'assistant', turn.text, status)

    def recording_listen_allowed(self):
        """Voice-stop detection is muted while a recording prompt is audible."""
        return (bool(self.recording) and not self.recording.stopping and self.turn is None
                and time.monotonic() >= self.recording_listen_after)

    def recording_local_asr_allowed(self):
        """Only local ASR may hear a recording; never stream it to an HTTP provider."""
        config = getattr(self.pipeline, 'config', None)
        asr = config.get('asr') if isinstance(config, dict) else None
        return not isinstance(asr, dict) or asr.get('provider', 'builtin') == 'builtin'

    async def start_recording(self, trigger='voice', current_turn=None):
        if self.closed:
            raise ValueError('session_closed')
        if self.recording is not None:
            return self.recording
        if self.recording_manager is None:
            raise ValueError('recording_unavailable')
        if self.turn is not None and self.turn is not current_turn:
            # Any start entry point must cut an in-flight reply and playback.
            # The voice command's own turn is excluded: it is about to return.
            await self.interrupt('recording')
        recording = self.recording_manager.start(self.id, self.device_id, trigger)
        self.recording = recording
        self.recording_queue = asyncio.Queue(maxsize=8)
        self.recording_listen_after = time.monotonic()+.2
        self.last_recording_packet_at = time.monotonic()
        self.recording_asr_notified = False
        try:
            live_threshold = float(os.environ.get('VOICEHUB_LIVE_SPEAKER_THRESHOLD', '.42'))
        except ValueError:
            live_threshold = .42
        self.recording_speakers = (SpeakerTracker(threshold=live_threshold,
                                                  min_seconds=.8)
                                   if self.speaker_engine else None)
        self.recording_speaker_failed = False
        self.recording_speaker_failures = 0
        self.recording_speaker_retry_at = 0.0
        self.recording_speaker_notified = False
        recording.speaker_count = 0
        worker = self.recording_worker = asyncio.create_task(self._recording_worker())
        self.tasks.add(worker)
        worker.add_done_callback(self.tasks.discard)
        await self.transition('recording')
        await self.emit('recording.started', recording=recording.metadata())
        return recording

    async def stop_recording(self, reason='manual', *, speak=False, notify=True):
        """Finalize the WAV once; safe when a disconnect races a web stop."""
        recording = self.recording
        if recording is None or recording.stopping:
            return None
        recording.stopping = True
        self.recording_listen_after = 0
        # Stop is a mode switch, not a new conversational turn. Cut any reply
        # or mode prompt before waiting for the final audio.
        if self.turn is not None:
            with contextlib.suppress(Exception):
                await self.interrupt('recording_stop')
        timer = self.recording_confirm_timer
        self.recording_confirm_timer = None
        if timer and timer is not asyncio.current_task():
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        worker = self.recording_worker
        self.recording_worker = None
        # Let the last recognized segment reach the .txt before finalizing.
        if self.recording_queue is not None:
            with contextlib.suppress(asyncio.QueueFull):
                self.recording_queue.put_nowait(None)
        if worker and worker is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(worker), 3)
            except (TimeoutError, asyncio.CancelledError):
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
        if self.recording_speakers is not None:
            try:
                mapping, count = self.recording_speakers.refine()
                recording.relabel_transcript(mapping)
                recording.speaker_count = count
            except Exception:
                pass
            self.recording_speakers = None
        self.recording = None
        metadata = self.recording_manager.stop(recording.id, reason) if self.recording_manager else None
        metadata = metadata or recording.metadata()
        self._maybe_rediarize(recording, metadata)
        if notify and not self.closed:
            with contextlib.suppress(Exception):
                await self.emit('recording.stopped', recording=metadata)
                if self.state != 'standby':
                    await self.transition('standby')
        return metadata

    def recording_heard(self, pcm, elapsed_ms=None, pause_ms=None):
        """Queue one VAD segment for stop detection and live transcription."""
        if not self.recording or self.recording.stopping or self.recording_queue is None:
            return
        if self.recording_queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.recording_queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self.recording_queue.put_nowait((pcm, elapsed_ms, pause_ms))

    async def recording_transcription_unavailable(self, message):
        if not self.recording or self.recording_asr_notified:
            return
        self.recording_asr_notified = True
        self.recording.transcription_error = message
        with contextlib.suppress(Exception):
            await self.emit('recording.transcription_unavailable', message=message)

    async def recording_limit_reached(self):
        if self.recording is not None:
            await self.stop_recording('limit', speak=True)

    async def recording_storage_failed(self):
        if self.recording is None:
            return
        with contextlib.suppress(Exception):
            await self.emit('error', code='recording_storage_failed',
                            message='录音写入失败，已停止并保存已录到的部分。')
        await self.stop_recording('storage_error', speak=False)

    async def recording_tick(self):
        recording = self.recording
        if recording is None or self.recording_manager is None:
            return
        if recording.duration >= self.recording_manager.max_seconds:
            await self.stop_recording('limit', speak=True)
            return
        # A half-open socket can stay "connected" after a device loses power.
        # Continuous clients send audio even during silence; five minutes with
        # no packet means the source is gone, so finalize what we have.
        if (self.last_recording_packet_at
                and time.monotonic()-self.last_recording_packet_at > 300):
            await self.stop_recording('input_timeout', notify=True)

    async def request_recording_stop(self):
        recording = self.recording
        if recording is None:
            return
        if recording.confirming:
            await self.stop_recording('voice', speak=True)
            return
        recording.confirming = True
        recording.confirm_deadline = time.monotonic()+CONFIRM_SECONDS
        await self.emit('recording.confirming', recording=recording.metadata(),
                        expires_in=CONFIRM_SECONDS)
        timer = self.recording_confirm_timer = asyncio.create_task(
            self._recording_confirm_timeout(recording))
        self.tasks.add(timer)
        timer.add_done_callback(self.tasks.discard)
        prompt = asyncio.create_task(self._speak_recording_prompt(
            '要停止录音吗？请再说一次“确认停止”，或者点击网页上的停止。'))
        self.tasks.add(prompt)
        prompt.add_done_callback(self.tasks.discard)

    async def _recording_confirm_timeout(self, recording):
        try:
            await asyncio.sleep(CONFIRM_SECONDS)
        except asyncio.CancelledError:
            return
        if self.recording is recording and recording.confirming:
            recording.confirming = False
            recording.confirm_deadline = None
            self.recording_confirm_timer = None
            with contextlib.suppress(Exception):
                await self.emit('recording.confirm_expired', recording=recording.metadata())

    async def _recording_worker(self):
        try:
            while self.recording is not None:
                item = await self.recording_queue.get()
                if item is None or self.recording is None:
                    return
                pcm, started_ms, pause_ms = item
                if not self.recording_listen_allowed() and not self.recording.stopping:
                    continue
                try:
                    async with asyncio.timeout(20):
                        text = await self.pipeline.transcribe(pcm)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self.recording_transcription_unavailable(
                        '实时转写暂时不可用，录音仍在继续；请用网页或开关键停止。')
                    continue
                if self.recording is None:
                    return
                recording = self.recording
                text = (text or '').strip()
                if not text:
                    continue
                action = recording_command(text)
                handled = False
                if action == 'stop':
                    handled = True
                    if recording.confirming:
                        await self.stop_recording('voice', speak=True)
                    else:
                        await self.request_recording_stop()
                elif action == 'confirm' and recording.confirming:
                    handled = True
                    await self.stop_recording('voice', speak=True)
                if not handled and self.recording is recording:
                    elapsed_ms = (started_ms if started_ms is not None
                                  else round(recording.duration*1000))
                    speaker_runs = None
                    if (self.speaker_engine and self.recording_speakers
                            and time.monotonic() >= self.recording_speaker_retry_at):
                        try:
                            speaker_runs = await self._live_speaker_runs(
                                pcm, text, elapsed_ms)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            speaker_runs = None
                        failed_this_segment = False
                        if speaker_runs is None:
                            if len(pcm) < 800:
                                # Too short for ECAPA; reuse the last speaker
                                # rather than declaring the whole model broken.
                                speaker_runs = [{
                                    'start_ms': 0, 'end_ms': len(pcm)/16000*1000,
                                    'speaker_id': recording.transcript_last_speaker_id,
                                    'speaker': recording.transcript_last_speaker,
                                    'text': text}]
                            else:
                                try:
                                    embedding = await asyncio.wait_for(
                                        asyncio.to_thread(self.speaker_engine.embed, pcm), 12)
                                    speaker_id, speaker = self.recording_speakers.assign(
                                        embedding, len(pcm)/16000)
                                    speaker_runs = [{
                                        'start_ms': 0, 'end_ms': len(pcm)/16000*1000,
                                        'speaker_id': speaker_id, 'speaker': speaker,
                                        'text': text}]
                                except asyncio.CancelledError:
                                    raise
                                except Exception:
                                    failed_this_segment = True
                        if failed_this_segment:
                            self.recording_speaker_failures += 1
                            # A busy local model can time out once; do not give up
                            # for the rest of the recording. Retry after a cooldown.
                            self.recording_speaker_retry_at = time.monotonic()+30
                            if (self.recording_speaker_failures >= 1
                                    and not self.recording_speaker_notified):
                                self.recording_speaker_notified = True
                                with contextlib.suppress(Exception):
                                    await self.emit(
                                        'recording.speaker_unavailable',
                                        recording_id=recording.id,
                                        message='实时说话人暂时不稳定，转写继续；停止后会自动重新分析说话人。')
                        else:
                            self.recording_speaker_failures = 0
                            self.recording_speaker_retry_at = 0.0
                        recording.speaker_count = self.recording_speakers.count
                    if speaker_runs is None:
                        speaker_runs = [{
                            'start_ms': 0, 'end_ms': len(pcm)/16000*1000,
                            'speaker_id': None, 'speaker': None, 'text': text}]
                    try:
                        for run_index, run in enumerate(speaker_runs):
                            run_text = run.get('text', '').strip()
                            if not run_text:
                                continue
                            speaker = run.get('speaker')
                            speaker_id = run.get('speaker_id')
                            run_elapsed = elapsed_ms+run.get('start_ms', 0)
                            same_speaker = (speaker_id ==
                                            recording.transcript_last_speaker_id)
                            can_merge = (run_index == 0 and pause_ms is not None
                                         and pause_ms < TRANSCRIPT_JOIN_PAUSE_MS
                                         and recording.transcript_lines > 0
                                         and same_speaker)
                            merged = (recording.merge_transcript(run_text, speaker,
                                                                 speaker_id)
                                      if can_merge else None)
                            appended = (None if merged else
                                        recording.append_transcript(
                                            run_text, round(run_elapsed), speaker,
                                            speaker_id))
                            result = merged or appended
                            if not result:
                                continue
                            line, stamp, clean = result
                            await self.emit('recording.transcript',
                                            recording_id=recording.id, line=line,
                                            text=clean, stamp=stamp,
                                            lines=recording.transcript_lines,
                                            elapsed_ms=round(run_elapsed),
                                            speaker=speaker, speaker_id=speaker_id,
                                            merged=bool(merged))
                    except OSError:
                        await self.recording_transcription_unavailable(
                            '转写写入失败，录音仍在继续；请检查磁盘空间。')
                        continue
        except asyncio.CancelledError:
            pass

    @staticmethod
    def _sentence_parts(text):
        parts = [part.strip() for part in re.split(r'(?<=[。！？!?；;])', text or '')
                 if part.strip()]
        output = []
        for part in parts:
            while len(part) > 140:
                cut = max(part.rfind('，', 0, 140), part.rfind(',', 0, 140),
                          part.rfind('、', 0, 140))
                cut = cut+1 if cut > 20 else 140
                output.append(part[:cut].strip())
                part = part[cut:].strip()
            if part:
                output.append(part)
        return output or [text]

    async def _live_speaker_runs(self, pcm, text, elapsed_ms):
        """Split one ASR segment into speaker runs using short windows."""
        if (not self.speaker_engine or not self.recording_speakers
                or len(pcm) < 16000):
            return None
        win, hop = int(1.6*16000), int(.8*16000)
        rows = []
        for start in range(0, len(pcm)-win+1, hop):
            frame = pcm[start:start+win]
            if float((frame*frame).mean()) >= .006**2:
                rows.append((start, frame))
        if len(rows) < 2:
            return None
        try:
            embeddings = await asyncio.wait_for(
                asyncio.to_thread(self.speaker_engine.embed_batch,
                                  [frame for _, frame in rows]), 12)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        labels = []
        for embedding in embeddings:
            speaker_id, _ = self.recording_speakers.assign(embedding, 1.6)
            labels.append(speaker_id)
        # Median smooth then merge sub-second flips.
        smooth = list(labels)
        for index in range(len(labels)):
            window = labels[max(0, index-1):index+2]
            smooth[index] = max(set(window), key=window.count)
        runs = []
        start_index = 0
        for index in range(1, len(smooth)+1):
            if index == len(smooth) or smooth[index] != smooth[start_index]:
                run_start = rows[start_index][0]/16000*1000
                run_end = ((rows[index-1][0]+win)/16000*1000
                           if index <= len(rows) else len(pcm)/16000*1000)
                if runs and run_end-run_start < 900:
                    runs[-1][1] = run_end
                else:
                    runs.append([run_start, run_end, smooth[start_index]])
                start_index = index
        if len(runs) <= 1:
            return None
        duration_ms = len(pcm)/16000*1000
        sentences = self._sentence_parts(text)
        total = sum(max(1, len(item)) for item in sentences) or 1
        cursor = 0.0
        grouped = []
        for sentence in sentences:
            span = duration_ms*max(1, len(sentence))/total
            midpoint = min(duration_ms-.001, cursor+span/2)
            speaker = next((run[2] for run in runs
                            if run[0] <= midpoint < run[1]), runs[-1][2])
            if grouped and grouped[-1]['speaker_id'] == speaker:
                grouped[-1]['text'] += sentence
                grouped[-1]['end_ms'] = cursor+span
            else:
                grouped.append({'start_ms': round(cursor), 'end_ms': cursor+span,
                                'speaker_id': speaker,
                                'speaker': f'说话人{speaker}', 'text': sentence})
            cursor += span
        return grouped or None

    def _maybe_rediarize(self, recording, metadata):
        if self.closed:
            return
        entries = list(getattr(recording, 'transcript_entries', []) or [])
        if len(entries) < 2:
            return
        starts = []
        for entry in entries:
            try:
                starts.append(stamp_ms(entry.get('stamp', '')))
            except (TypeError, ValueError):
                return
        starts = [value for value in starts if isinstance(value, (int, float))]
        if len(starts) != len(entries):
            return
        duration = metadata.get('duration_ms') or 0
        gaps = [b-a for a, b in zip(starts, starts[1:])]
        gaps.append(max(0, duration-starts[-1]))
        if max(gaps or [0]) < 20000:
            return
        data_root = getattr(getattr(self.pipeline, 'local', None), 'data_root', None)
        if not data_root:
            return
        recording_id = recording.id

        async def run():
            try:
                from .rediarize import rediarize
                await rediarize(recording_id, data_root, speakers=2, mode='text')
                if not self.closed:
                    await self.emit('recording.diarized', recording_id=recording_id)
                minutes = self.minutes or getattr(
                    getattr(self.pipeline, 'local', None), 'minutes', None)
                if minutes is not None and not self.closed:
                    try:
                        existing = minutes.list_jobs(recording_id)
                        if not any(job.get('template_id') == 'general'
                                   for job in existing):
                            await minutes.start_job(recording_id, 'general')
                            await self.emit('recording.minutes_started',
                                            recording_id=recording_id)
                    except Exception:
                        pass
            except Exception:
                pass
        task = asyncio.create_task(run())
        self.rediarize_tasks.add(task)
        task.add_done_callback(self.rediarize_tasks.discard)

    async def _speak_recording_prompt(self, text):
        while self.turn is not None and not self.turn.cancelled:
            if self.recording is None or self.closed:
                return
            await asyncio.sleep(.1)
        if self.recording is None or self.closed or self.turn is not None:
            return
        await self._speak_system(text)

    async def _speak_system(self, text):
        if self.closed:
            return
        while self.turn is not None:
            await asyncio.sleep(.1)
            if self.closed:
                return
        turn = self.turn = Turn(system=True)
        turn.task = asyncio.create_task(self._run(turn, None, None, announcement=text))
        self.tasks.add(turn.task)
        turn.task.add_done_callback(self.tasks.discard)
        await asyncio.gather(turn.task, return_exceptions=True)

    async def interrupt(self, reason='manual'):
        turn = self.turn
        if turn and not turn.cancelled:
            if self.operations and turn.notification_id and reason!='disconnect':
                record=self.operations.get(turn.notification_id)
                if (record.get('calendar_question') or record.get('everyday_question') or turn.audio_started) and record['delivery']=='pending':
                    record['delivery']='superseded';self.operations.save(record)
            turn.cancelled = True  # Invalidate BEFORE cancelling provider or awaiting network I/O.
            if turn.task:
                turn.task.cancel()
            self.record_reply(turn, 'interrupted')
        self.turn = None
        await self.emit('playback.clear', interrupted_turn_id=turn.id if turn else None, reason=reason)
        if not self.closed:
            await self.transition('recording' if self.recording is not None else 'listening')

    async def submit(self, text=None, pcm=None):
        if self.closed or self.recording is not None:
            return
        if self.turn:
            await self.interrupt('new_input')
        turn = self.turn = Turn()
        turn.task = asyncio.create_task(self._run(turn, text, pcm))
        self.tasks.add(turn.task)
        turn.task.add_done_callback(self.tasks.discard)

    async def _run(self, turn, text, pcm, announcement=None):
        was_standby = self.state == 'standby'
        activated = not was_standby or self.wake_mode != 'backend'
        transcribed = text is None
        try:
            async with asyncio.timeout(120):
                await self.transition('thinking', turn)
                if announcement is not None:text=announcement
                if text is None:
                    text = (await self.pipeline.transcribe(pcm)).strip()
                if announcement is None and was_standby and self.wake_mode == 'backend':
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
                        announcement = text = '我在，请说。'
                if not text:
                    return
                if self.recording is None and announcement is None:
                    action = recording_command(text)
                    if action == 'start':
                        try:
                            await self.start_recording('voice' if transcribed else 'text',
                                                       current_turn=turn)
                        except ValueError as exc:
                            await self.emit('error', turn, code=str(exc),
                                            message='录音模式启动失败，请检查磁盘空间后重试。')
                            return
                        turn.system = True
                        return
                if transcribed and announcement is None:
                    await self.emit('transcript.final', turn, text=text,
                                    elapsed_ms=round((time.monotonic()-turn.started)*1000))
                if announcement is None:
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
                    async def notification():yield announcement
                    async def cancel_operation():
                        running=[r for r in self.operations.recent(self.id) if r['status'] in ('queued','running')]
                        if running:
                            await self.operations.cancel(running[0]['id']);yield '已请求停止最近一项后台任务，请查看任务记录核实已执行的操作。'
                        else:yield '当前没有运行中的后台任务。'
                    cancelling=announcement is None and self.operations and re.sub(r'[\s，。！？,.!?]','',text) in ('取消任务','取消后台任务') and bool(self.operations.tasks)
                    source=notification() if announcement is not None else cancel_operation() if cancelling else self.pipeline.chat(history)
                    detached=False
                    try:
                        async for delta in source:
                            if turn.cancelled:
                                return
                            if isinstance(delta, ToolStatus):
                                metadata={}
                                if delta.arguments is not None:metadata['arguments']=delta.arguments
                                if delta.planner:metadata['planner']=delta.planner
                                await self.emit('tool.'+delta.phase, turn, tool=delta.name,
                                                elapsed_ms=delta.elapsed_ms, code=delta.code, **metadata)
                                continue
                            if isinstance(delta, ToolNotice):
                                if pending.strip():
                                    await sentences.put(pending)
                                    pending = ''
                                if delta.text:
                                    turn.text += delta.text
                                    await self.emit('reply.delta', turn, text=delta.text)
                                    await sentences.put(delta.text)
                                delivered = asyncio.Event()
                                gate=asyncio.Event()
                                if self.operations and announcement is None:
                                    try:
                                        feedback=None
                                        if not turn.text.strip():
                                            feedback=phrasing.progress(text)
                                        operation=self.operations.start(self.id,turn.id,text,source,gate,self.emit_operation,progress_text=feedback)
                                        manager=getattr(getattr(self.pipeline,'local',None),'workflows',None)
                                        if manager:manager.transfer_owner(asyncio.current_task(),self.operations.tasks[operation['id']])
                                    except ValueError:
                                        message='后台任务数量已满，请等待已有任务完成。'
                                        turn.text+=message;await self.emit('reply.delta',turn,text=message)
                                        await sentences.put(message);break
                                    detached=True
                                if not turn.text.strip():
                                    gate.set()
                                    if detached:break
                                    continue
                                try:
                                    await sentences.put(delivered)
                                    await delivered.wait()
                                finally:gate.set()
                                if detached:break
                                continue
                            turn.text += delta
                            pending += delta
                            if not turn.system:
                                await self.emit('reply.delta', turn, text=delta)
                            if re.search(r'[。！？!?\n]', pending) or len(pending) >= 70:
                                await sentences.put(pending)
                                pending = ''
                    finally:
                        if not detached:await source.aclose()
                    if not turn.text.strip() and not detached:
                        raise RuntimeError('empty_reply')
                    if pending.strip():
                        await sentences.put(pending)
                    await sentences.put(None)

                audio = asyncio.Queue(maxsize=12)  # At most 480 ms of prefetched PCM.

                async def synthesize():
                    audio_samples = 0
                    while (sentence := await sentences.get()) is not None:
                        if isinstance(sentence, asyncio.Event):
                            await audio.put(sentence)
                            continue
                        async with contextlib.aclosing(self.pipeline.speech(sentence)) as source:
                            async for pcm_chunk in source:
                                for offset in range(0, len(pcm_chunk), 1280):  # <=40 ms @ 16 kHz s16le
                                    block = pcm_chunk[offset:offset+1280]
                                    if len(block) % 2:
                                        raise ValueError('TTS returned unaligned PCM')
                                    audio_samples += len(block)//2
                                    if audio_samples > 16000 * MAX_REPLY_AUDIO_SECONDS:
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
                        if isinstance(block, asyncio.Event):
                            if deadline is None:
                                raise RuntimeError('tool_preamble_unavailable')
                            # Complete the last 60ms Opus frame and cover codec lookahead.
                            # Keep one stream/turn: an interim audio.end would incorrectly
                            # count its playback acknowledgement as the final reply's ACK.
                            padding = (-audio_samples) % 960 + 960
                            for size in range(0, padding, 640):
                                samples = min(640, padding-size)
                                await asyncio.sleep(max(0, deadline-time.monotonic()))
                                await self.emit('audio.chunk', turn,
                                                data=base64.b64encode(b'\0\0'*samples).decode())
                                audio_samples += samples
                                deadline += samples/16000
                            await asyncio.sleep(max(0, deadline + lead - time.monotonic()))
                            block.set()
                            await self.transition('thinking', turn)
                            continue
                        if deadline is None:
                            turn.audio_started = True
                            self.streaming = True
                            await self.transition('speaking', turn)
                            await self.emit('audio.start', turn, sample_rate=16000,
                                            format='pcm_s16le', channels=1,
                                            elapsed_ms=round((time.monotonic()-turn.started)*1000))
                            deadline = time.monotonic() - lead
                        elif self.state != 'speaking':
                            await self.transition('speaking', turn)
                        deadline = max(deadline, time.monotonic() - lead)
                        await asyncio.sleep(max(0, deadline-time.monotonic()))
                        self.last_activity = time.monotonic()  # 播报本身就是活动
                        await self.emit('audio.chunk', turn, data=base64.b64encode(block).decode())
                        audio_samples += len(block)//2
                        deadline += len(block)/32000
                    # Flush the codec while the receiver still has buffered audio, then allow
                    # that audio to drain before legacy clients return to microphone input.
                    finish = deadline + lead if deadline is not None else time.monotonic()
                    drain_ms = max(0, round((finish-time.monotonic())*1000))
                    await self.emit('audio.end', turn, samples=audio_samples, drain_ms=drain_ms)
                    await asyncio.sleep(max(0, finish-time.monotonic()))
                    self.streaming = False
                    self.last_activity = time.monotonic()

                async with asyncio.TaskGroup() as group:
                    group.create_task(produce())
                    group.create_task(synthesize())
                    group.create_task(speak())
                if not turn.system:
                    await self.emit('reply.final', turn, text=turn.text)
                status = 'generated'
                if self.playback_ack:
                    try:
                        await asyncio.wait_for(turn.played.wait(), 5)
                        status = 'played'
                    except TimeoutError:
                        await self.emit('playback.unconfirmed', turn)
                self.record_reply(turn, status)
                if not turn.system:
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
                if self.recording is not None:
                    self.recording_listen_after = time.monotonic()+.6
                    with contextlib.suppress(Exception):
                        await self.transition('recording')
                elif self.state != 'standby':
                    with contextlib.suppress(Exception):
                        await self.transition('listening' if activated else 'standby')

    async def emit_operation(self,event):
        # Tool execution belongs to the original request, even after its audio is cleared.
        self.store.event(event);self.publish(event)
        if not self.closed:
            with contextlib.suppress(Exception):await asyncio.wait_for(self.send(event),3)

    def queue_notification(self,record):
        if record['id'] not in self.notification_ids and not self.notifications.full():
            self.notification_ids.add(record['id']);self.notifications.put_nowait(record)

    async def announce_results(self):
        try:
            while True:
                record=await self.notifications.get()
                while self.turn or self.input_active or self.recording is not None or time.monotonic()-self.last_activity<.5:
                    await asyncio.sleep(.1)
                if self.closed:return
                calendar=getattr(getattr(self.pipeline,'local',None),'calendar_reminders',None)
                if record.get('kind')=='calendar' and calendar:
                    event=calendar.store.get(record['event_id'])
                    if not calendar.enabled():calendar.store.mark(event['event_id'],'muted');calendar.cancel_notification(event['event_id'])
                    elif event['status']=='cancelled':calendar.cancel_notification(record['event_id'])
                    elif time.time()-event['remind_at']>calendar.store.settings()['late_minutes']*60:
                        calendar.store.mark(event['event_id'],'missed');calendar.cancel_notification(event['event_id'])
                timers=getattr(getattr(self.pipeline,'local',None),'timer_reminders',None)
                if record.get('kind')=='timer' and (not timers or not timers.valid(record)):
                    fresh=self.operations.get(record['id']);fresh['delivery']='cancelled';self.operations.save(fresh)
                if self.operations.get(record['id'])['delivery']!='pending':
                    self.notification_ids.discard(record['id']);continue
                kind=record.get('kind')
                if kind=='progress':
                    # 进度提示本身就是一句话，不能再套一层前缀。
                    text=record['text']
                elif kind in ('calendar','timer'):
                    text=record['text'] if len(record['text'])<=120 else record['text'][:120]+'。完整结果可在网页查看。'
                else:
                    text=phrasing.announcement(record['text'],record.get('status','completed'))
                turn=self.turn=Turn(notification_id=record['id'])
                turn.task=asyncio.create_task(self._run(turn,None,None,announcement=text))
                self.tasks.add(turn.task);turn.task.add_done_callback(self.tasks.discard)
                await asyncio.gather(turn.task,return_exceptions=True)
                self.notification_ids.discard(record['id'])
                if turn.delivery in ('generated','played') and not turn.cancelled:self.operations.announced(record['id'])
                elif not self.closed and turn.delivery!='failed':
                    # An interrupted announcement is retried only after the next quiet turn.
                    self.last_activity=time.monotonic();self.queue_notification(record)
        except asyncio.CancelledError:pass

    async def idle(self):
        if self.recording is not None:
            return  # 录音模式由用户明确停止，不能因空闲自动回到待机。
        timers=getattr(getattr(self.pipeline,'local',None),'timer_reminders',None)
        if timers and timers.enabled() and timers.store.running(self.device_id):return
        if self.operations and (self.notification_ids or any(self.operations.get(identity)['session_id']==self.id for identity in self.operations.tasks)):
            return  # Keep the voice channel available for long-running completion announcements.
        # 30 秒只针对「双方都没有任何动静」。正在说话、正在播报、正在收音都不算空闲，
        # 否则一段超过 30 秒的播报或一段很长的话会被误判成待机而中断。
        if self.turn or self.input_active or self.streaming:
            return
        if self.state != 'listening':
            return
        if time.monotonic()-self.last_activity > self.idle_seconds:
            await self.transition('standby')

    async def close(self):
        if self.closed:
            return
        # A device key press or network loss closes the channel: finalize the WAV
        # quietly before the task set is torn down.
        if self.recording is not None:
            with contextlib.suppress(Exception):
                await self.stop_recording('disconnect', notify=False)
        await self.interrupt('disconnect')
        self.closed = True
        self.state = 'disconnected'
        if self.operations:self.operations.unbind(self.id,self.notification_callback)
        if self.announcer:
            self.announcer.cancel();await asyncio.gather(self.announcer,return_exceptions=True)
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.event(dict(type='session.disconnected', session_id=self.id, turn_id=None,
                              timestamp=time.time()))
