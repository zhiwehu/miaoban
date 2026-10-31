"""Recording mode: continuous WAV capture with explicit, confirmed stopping.

Recording mode is deliberately outside the turn pipeline.  While a session is
recording, decoded PCM is appended to one WAV file and ordinary speech never
reaches ASR, the LLM or TTS.  The only optional voice interaction is the
bounded stop command: the first phrase asks for a second confirmation, and the
recording keeps running until the web client, the robot key or that second
confirmation stops it.

Recordings are private local files under ``<data>/recordings``.  The metadata
sidecar never contains audio content or transcripts.  A WAV without sidecar is
repaired at startup so an unclean shutdown still leaves a playable file.
"""
import json
import os
import re
import shutil
import struct
import time
import uuid
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
HEADER_BYTES = 44
DEFAULT_MAX_SECONDS = 4 * 60 * 60
MIN_FREE_BYTES = 256 * 1024 * 1024
CONFIRM_SECONDS = 20
TRANSCRIPT_JOIN_PAUSE_MS = 1000

_PUNCTUATION = re.compile(r'[\s\W_]+', re.UNICODE)
_START = re.compile(
    r'^(?:请|麻烦|帮我|现在|我们|我想|我要|可以|能不能)?'
    r'(?:开始|进入|打开|开启|启动)(?:一下)?(?:录音|录制)(?:模式)?(?:吧|了|呀)?$')
_START_MODE = re.compile(r'^(?:请|麻烦|帮我|现在|我们|我想|我要)?(?:录音|录制)模式(?:吧|了|呀)?$')
_STOP = re.compile(
    r'^(?:请|麻烦|帮我|现在|我们)?(?:停止|结束|关闭|退出|终止)'
    r'(?:一下)?(?:录音|录制)(?:模式)?(?:吧|了|呀)?(?:并)?(?:保存|存下来|存起来)?$')
_STOP_ALT = re.compile(r'^(?:别|不要)(?:再)?录了?$')
_CONFIRM = re.compile(
    r'^(?:(?:确认|确定|是的|对)(?:停止|结束|关闭)(?:录音|录制)?(?:模式)?(?:吧|了|呀)?'
    r'(?:并)?(?:保存|存下来|存起来)?'
    r'|(?:确认|确定))$')


def normalize(text):
    """Drop punctuation/spaces so ASR punctuation choices cannot hide a command."""
    return _PUNCTUATION.sub('', text or '').lower()


def command(text):
    """Return ``start``, ``stop``, ``confirm`` or ``None`` for one utterance.

    Only a short, dedicated utterance matches.  A sentence that merely mentions
    recording ("录音功能怎么用") therefore stays ordinary conversation.
    """
    value = normalize(text)
    if not value:
        return None
    if _START.fullmatch(value) or _START_MODE.fullmatch(value):
        return 'start'
    if _STOP.fullmatch(value) or _STOP_ALT.fullmatch(value):
        return 'stop'
    if _CONFIRM.fullmatch(value):
        return 'confirm'
    return None


def duration_text(seconds):
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f'{seconds}秒'
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes}分{seconds}秒' if seconds else f'{minutes}分钟'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}小时{minutes}分钟' if minutes else f'{hours}小时'


_STAMP = re.compile(r'^\[(\d{1,3}:\d{2}(?::\d{2})?)\]\s?(.*)$')
_SPEAKER = re.compile(r'^(说话人\s*(\d+))[：:]\s?(.*)$')


def stamp_text(elapsed_ms):
    total = max(0, int(elapsed_ms))//1000
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


def stamp_ms(value):
    parts = [int(part) for part in value.split(':')]
    if len(parts) == 2:
        minutes, seconds = parts
        return (minutes*60+seconds)*1000
    hours, minutes, seconds = parts
    return ((hours*60+minutes)*60+seconds)*1000


def _wav_header(data_bytes=0):
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36+data_bytes, b'WAVE', b'fmt ',
                       16, 1, CHANNELS, SAMPLE_RATE, SAMPLE_RATE*CHANNELS*SAMPLE_WIDTH,
                       CHANNELS*SAMPLE_WIDTH, SAMPLE_WIDTH*8, b'data', data_bytes)


def _fade_edges(pcm, milliseconds=8):
    """Short fades prevent a click where two VAD segments are concatenated."""
    data = np.frombuffer(pcm, '<i2').copy()
    fade = min(len(data)//2, int(SAMPLE_RATE*milliseconds/1000))
    if fade <= 0:
        return pcm
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    data[:fade] = (data[:fade].astype(np.float32)*ramp).astype(np.int16)
    data[-fade:] = (data[-fade:].astype(np.float32)*ramp[::-1]).astype(np.int16)
    return data.tobytes()


class ActiveRecording:
    """One in-flight WAV file.  Only the event loop writes it."""
    def __init__(self, manager, session_id, device_id, trigger):
        self.manager = manager
        self.id = uuid.uuid4().hex
        self.session_id = session_id
        self.device_id = device_id
        self.trigger = trigger
        self.path = manager.root/f'{self.id}.wav'
        self.started_at = time.time()
        self.started_mono = time.monotonic()
        self.samples = 0
        self.status = 'active'
        self.reason = None
        self.ended_at = None
        self.confirming = False
        self.confirm_deadline = None
        self.stopping = False
        self.transcript_path = manager.root/f'{self.id}.txt'
        self.transcript_file = None
        self.transcript_lines = 0
        self.transcript_last_offset = 0
        self.transcript_last_stamp = ''
        self.transcript_last_text = ''
        self.transcript_last_speaker = None
        self.transcript_last_speaker_id = None
        self.transcript_entries = []
        self.speaker_count = 0
        self.transcription_error = None
        self.speech_path = manager.root/f'{self.id}-speech.wav'
        self.speech_map_path = manager.root/f'{self.id}-speech.json'
        self.speech_samples = 0
        self.speech_segments = []
        self.last_data_sync = time.monotonic()
        self.last_checkpoint = time.monotonic()
        self.file = open(self.path, 'wb', buffering=0)
        os.chmod(self.path, 0o600)
        self.file.write(_wav_header())
        self.speech_file = open(self.speech_path, 'wb', buffering=0)
        os.chmod(self.speech_path, 0o600)
        self.speech_file.write(_wav_header())

    @property
    def duration(self):
        return self.samples/SAMPLE_RATE

    @property
    def max_samples(self):
        return int(self.manager.max_seconds*SAMPLE_RATE)

    def write(self, pcm):
        """Append complete int16 samples; returns ``limit`` when the cap is hit."""
        if self.status != 'active' or self.stopping or not pcm:
            return None
        if len(pcm) % SAMPLE_WIDTH:
            pcm = pcm[:-1]
        if not pcm:
            return None
        remaining = self.max_samples-self.samples
        if remaining <= 0:
            return 'limit'
        limit_bytes = remaining*SAMPLE_WIDTH
        over = len(pcm) > limit_bytes
        if over:
            pcm = pcm[:limit_bytes]
        self.file.write(pcm)
        self.samples += len(pcm)//SAMPLE_WIDTH
        now = time.monotonic()
        if now-self.last_data_sync >= 2:
            try:
                os.fsync(self.file.fileno())
            except OSError:
                pass
            self.last_data_sync = now
        if now-self.last_checkpoint >= 10:
            self.checkpoint()
        return 'limit' if over or self.samples >= self.max_samples else None

    def _sync_header(self, handle, samples):
        try:
            data_bytes = samples*SAMPLE_WIDTH
            handle.seek(4)
            handle.write(struct.pack('<I', 36+data_bytes))
            handle.seek(40)
            handle.write(struct.pack('<I', data_bytes))
            handle.seek(0, 2)
            handle.flush()
            os.fsync(handle.fileno())
        except OSError:
            pass

    def checkpoint(self):
        """Persist header sizes and an active sidecar while recording."""
        self.last_checkpoint = time.monotonic()
        self._sync_header(self.file, self.samples)
        self._sync_header(self.speech_file, self.speech_samples)
        try:
            self.manager.write_metadata(self.metadata())
        except OSError:
            pass

    def write_speech(self, pcm, full_start_ms=None, full_end_ms=None):
        """Append one VAD-trimmed utterance and its full-timeline mapping."""
        if self.status != 'active' or not pcm:
            return
        if len(pcm) % SAMPLE_WIDTH:
            pcm = pcm[:-1]
        pcm = _fade_edges(pcm)
        remaining = self.max_samples-self.speech_samples
        if remaining <= 0 or not pcm:
            return
        if len(pcm) > remaining*SAMPLE_WIDTH:
            pcm = pcm[:remaining*SAMPLE_WIDTH]
        ready_start = round(self.speech_samples/SAMPLE_RATE*1000)
        self.speech_file.write(pcm)
        self.speech_samples += len(pcm)//SAMPLE_WIDTH
        now = time.monotonic()
        if now-self.last_data_sync >= 2:
            try:
                os.fsync(self.speech_file.fileno())
            except OSError:
                pass
            self.last_data_sync = now
        if now-self.last_checkpoint >= 10:
            self.checkpoint()
        if isinstance(full_start_ms, (int, float)) and isinstance(full_end_ms, (int, float)):
            self.speech_segments.append({
                'full_start_ms': round(full_start_ms),
                'full_end_ms': round(full_end_ms),
                'ready_start_ms': ready_start,
                'ready_end_ms': round(self.speech_samples/SAMPLE_RATE*1000),
            })

    def append_transcript(self, text, elapsed_ms=0, speaker=None, speaker_id=None):
        """Append one VAD segment as one [mm:ss] line; returns (line, stamp, text)."""
        if self.status != 'active':
            return None
        clean = re.sub(r'\s+', ' ', text or '').strip()
        if not clean:
            return None
        if len(clean) > 4000:
            clean = clean[:4000]
        stamp = stamp_text(elapsed_ms)
        if self.transcript_file is None:
            self.transcript_file = open(self.transcript_path, 'ab')
            os.chmod(self.transcript_path, 0o600)
        prefix = f'{speaker}：' if speaker else ''
        self.transcript_last_offset = self.transcript_file.tell()
        self.transcript_last_stamp = stamp
        self.transcript_last_text = clean
        self.transcript_last_speaker = speaker
        self.transcript_last_speaker_id = speaker_id
        entry = {'stamp': stamp, 'speaker': speaker, 'speaker_id': speaker_id,
                 'text': clean}
        self.transcript_entries.append(entry)
        self.transcript_file.write(f'[{stamp}] {prefix}{clean}\n'.encode('utf-8'))
        self.transcript_file.flush()
        try:
            os.fsync(self.transcript_file.fileno())
        except OSError:
            pass
        self.transcript_lines += 1
        return self.transcript_lines, stamp, clean

    def merge_transcript(self, text, speaker=None, speaker_id=None):
        """Join a short-pause continuation to the previous line with a full stop."""
        if self.status != 'active' or self.transcript_lines == 0:
            return None
        if speaker_id != self.transcript_last_speaker_id:
            return None
        clean = re.sub(r'\s+', ' ', text or '').strip()
        clean = clean.lstrip('，,。.；;：:、 ')
        if not clean:
            return None
        if len(clean) > 4000:
            clean = clean[:4000]
        previous = self.transcript_last_text
        if previous and previous[-1] in '，,、；;：:':
            previous = previous[:-1] + '。'
        elif previous and previous[-1] not in '。！？!?；;':
            previous += '。'
        merged = previous + clean
        if self.transcript_file is not None:
            self.transcript_file.flush()
            self.transcript_file.close()
            self.transcript_file = None
        prefix = f'{self.transcript_last_speaker}：' if self.transcript_last_speaker else ''
        line = f'[{self.transcript_last_stamp}] {prefix}{merged}\n'.encode('utf-8')
        with open(self.transcript_path, 'r+b') as handle:
            handle.seek(self.transcript_last_offset)
            handle.truncate()
            handle.write(line)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        self.transcript_file = open(self.transcript_path, 'ab')
        try:
            self.transcript_file.flush()
            os.fsync(self.transcript_file.fileno())
        except OSError:
            pass
        self.transcript_last_text = merged
        if self.transcript_entries:
            self.transcript_entries[-1]['text'] = merged
        return self.transcript_lines, self.transcript_last_stamp, merged

    def relabel_transcript(self, mapping):
        """Rewrite speaker labels after the tracker's final small-cluster merge."""
        if not mapping or not self.transcript_entries:
            return
        changed = False
        for entry in self.transcript_entries:
            old = entry.get('speaker_id')
            if old is None:
                continue
            new = mapping.get(old, old)
            label = f'说话人{new}'
            if new != old or entry.get('speaker') != label:
                changed = True
            entry['speaker_id'] = new
            entry['speaker'] = label
        if not changed:
            return
        self.close_transcript()
        last_offset = 0
        with open(self.transcript_path, 'wb') as handle:
            for entry in self.transcript_entries:
                last_offset = handle.tell()
                prefix = f"{entry['speaker']}：" if entry.get('speaker') else ''
                handle.write(f"[{entry['stamp']}] {prefix}{entry['text']}\n".encode('utf-8'))
        os.chmod(self.transcript_path, 0o600)
        self.transcript_file = open(self.transcript_path, 'ab')
        self.transcript_last_offset = last_offset
        last = self.transcript_entries[-1]
        self.transcript_last_stamp = last['stamp']
        self.transcript_last_text = last['text']
        self.transcript_last_speaker = last.get('speaker')
        self.transcript_last_speaker_id = last.get('speaker_id')
        self.transcript_lines = len(self.transcript_entries)
        self.speaker_count = len({entry.get('speaker_id') for entry in self.transcript_entries
                                  if entry.get('speaker_id') is not None})

    def close_transcript(self):
        if self.transcript_file is None:
            return
        try:
            self.transcript_file.flush()
            os.fsync(self.transcript_file.fileno())
        except OSError:
            pass
        finally:
            try:
                self.transcript_file.close()
            except OSError:
                pass
            self.transcript_file = None

    def metadata(self):
        transcript_available = self.transcript_file is not None or self.transcript_path.is_file()
        return {
            'id': self.id,
            'session_id': self.session_id,
            'device_id': self.device_id,
            'status': self.status,
            'trigger': self.trigger,
            'reason': self.reason,
            'started_at': round(self.started_at, 3),
            'ended_at': round(self.ended_at, 3) if self.ended_at else None,
            'duration_ms': round((self.samples/SAMPLE_RATE)*1000),
            'samples': self.samples,
            'bytes': self.samples*SAMPLE_WIDTH,
            'format': 'wav',
            'sample_rate': SAMPLE_RATE,
            'channels': CHANNELS,
            'download_url': None if self.status == 'active' else f'/api/recordings/{self.id}/audio',
            'transcript_available': transcript_available,
            'transcript_url': f'/api/recordings/{self.id}/transcript' if transcript_available else None,
            'transcript_lines': self.transcript_lines,
            'speaker_count': self.speaker_count,
            'speech_available': (self.status != 'active' and self.speech_samples > 0
                                 and self.speech_path.is_file()),
            'speech_audio_url': (f'/api/recordings/{self.id}/speech'
                                 if self.status != 'active' and self.speech_samples > 0
                                 and self.speech_path.is_file() else None),
            'speech_duration_ms': round((self.speech_samples/SAMPLE_RATE)*1000),
            'speech_bytes': self.speech_samples*SAMPLE_WIDTH,
            'transcription_error': self.transcription_error,
            'available': True,
        }

    def finalize(self, reason):
        if self.status != 'active':
            return self.metadata()
        self.status = 'stopped'
        self.reason = reason
        self.ended_at = time.time()
        for handle, samples in ((self.file, self.samples),
                                (self.speech_file, self.speech_samples)):
            try:
                handle.flush()
                os.fsync(handle.fileno())
                data_bytes = samples*SAMPLE_WIDTH
                handle.seek(4)
                handle.write(struct.pack('<I', 36+data_bytes))
                handle.seek(40)
                handle.write(struct.pack('<I', data_bytes))
            except OSError:
                pass
            finally:
                try:
                    handle.close()
                except OSError:
                    pass
        if self.speech_samples <= 0:
            try:
                self.speech_path.unlink()
            except OSError:
                pass
        else:
            map_path = self.speech_map_path
            data = {
                'recording_id': self.id,
                'full_duration_ms': round(self.samples/SAMPLE_RATE*1000),
                'ready_duration_ms': round(self.speech_samples/SAMPLE_RATE*1000),
                'segments': self.speech_segments,
            }
            temporary = map_path.with_suffix('.tmp')
            try:
                temporary.write_text(json.dumps(data, ensure_ascii=False))
                os.chmod(temporary, 0o600)
                os.replace(temporary, map_path)
            except OSError:
                temporary.unlink(missing_ok=True)
        self.close_transcript()
        metadata = self.metadata()
        try:
            self.manager.write_metadata(metadata)
        except OSError:
            pass
        return metadata


class Recordings:
    """Owns active recordings and durable metadata; never keeps audio in SQLite."""
    def __init__(self, root, *, max_seconds=DEFAULT_MAX_SECONDS):
        self.root = Path(root)/'recordings'
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.max_seconds = int(max_seconds)
        if self.max_seconds < 1:
            raise ValueError('invalid_recording_limit')
        self.active = {}
        self.recover()

    def active_for_session(self, session_id):
        return next((r for r in self.active.values() if r.session_id == session_id), None)

    def start(self, session_id, device_id, trigger='voice'):
        if self.active_for_session(session_id):
            raise ValueError('recording_already_active')
        try:
            free = shutil.disk_usage(self.root).free
        except OSError:
            raise ValueError('recording_storage_unavailable') from None
        if free < MIN_FREE_BYTES:
            raise ValueError('recording_disk_full')
        recording = ActiveRecording(self, session_id, device_id, trigger)
        self.active[recording.id] = recording
        return recording

    def stop(self, identity, reason='manual'):
        recording = self.active.pop(identity, None)
        if recording is None:
            return None
        return recording.finalize(reason)

    def metadata_path(self, identity):
        return self.root/f'{identity}.json'

    def audio_path(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            return None
        if identity in self.active:
            return None  # the WAV header is only finalized on stop
        path = self.root/f'{identity}.wav'
        return path if path.is_file() else None

    def speech_path(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            return None
        if identity in self.active:
            return None
        path = self.root/f'{identity}-speech.wav'
        return path if path.is_file() else None

    def ready_path(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            return None
        if identity in self.active:
            return None
        path = self.root/f'{identity}-ready.wav'
        return path if path.is_file() else None

    def speech_map_path(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            return None
        path = self.root/f'{identity}-speech.json'
        return path if path.is_file() else None

    def clean_path(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            return None
        if identity in self.active:
            return None
        path = self.root/f'{identity}-clean.wav'
        return path if path.is_file() else None

    def transcript_path(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            return None
        path = self.root/f'{identity}.txt'
        return path if path.is_file() else None

    def transcript(self, identity):
        metadata = self.get(identity)
        if metadata is None:
            return None
        path = self.transcript_path(identity)
        if path is None:
            return {'id': identity, 'status': metadata.get('status'), 'available': False,
                    'text': '', 'count': 0}
        try:
            data = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            data = ''
        if len(data) > 4_000_000:
            data = data[-4_000_000:]
        if data and not data.endswith('\n'):
            # A reader can race the next append; drop only the incomplete tail.
            cut = data.rfind('\n')
            data = data[:cut+1] if cut >= 0 else ''
        raw_lines = data.splitlines()
        lines = []
        for raw in raw_lines:
            match = _STAMP.match(raw)
            if match:
                stamp, rest = match.group(1), match.group(2)
                speaker_match = _SPEAKER.match(rest)
                if speaker_match:
                    speaker, speaker_id, text = (speaker_match.group(1),
                                                 int(speaker_match.group(2)),
                                                 speaker_match.group(3))
                else:
                    speaker, speaker_id, text = None, None, rest
                lines.append({'stamp': stamp, 'elapsed_ms': stamp_ms(stamp),
                              'speaker': speaker, 'speaker_id': speaker_id,
                              'text': text})
            else:
                lines.append({'stamp': '', 'elapsed_ms': None, 'speaker': None,
                              'speaker_id': None, 'text': raw})
        mapping = None
        map_path = self.speech_map_path(identity)
        if map_path:
            try:
                mapping = json.loads(map_path.read_text())
            except (OSError, ValueError):
                mapping = None
        return {'id': identity, 'status': metadata.get('status'), 'available': True,
                'text': '\n'.join(raw_lines), 'lines': lines, 'count': len(lines),
                'speech_map': mapping}

    def decorate(self, metadata):
        """Keep older sidecars working without a schema migration."""
        identity = metadata.get('id')
        path = self.transcript_path(identity)
        metadata['transcript_available'] = bool(path)
        metadata['transcript_url'] = (f'/api/recordings/{identity}/transcript'
                                      if path else None)
        current = metadata.get('transcript_lines')
        if path and (not isinstance(current, int) or current == 0):
            try:
                text = path.read_text(encoding='utf-8', errors='replace')
                metadata['transcript_lines'] = len(text.splitlines())
            except OSError:
                metadata['transcript_lines'] = 0
        metadata.setdefault('transcript_lines', 0)
        metadata.setdefault('speaker_count', 0)
        metadata.setdefault('transcription_error', None)
        speech = self.speech_path(identity)
        metadata['speech_available'] = bool(speech)
        metadata['speech_audio_url'] = (f'/api/recordings/{identity}/speech'
                                        if speech else None)
        if speech:
            metadata.setdefault('speech_bytes', speech.stat().st_size-HEADER_BYTES)
            metadata.setdefault('speech_duration_ms',
                                round((speech.stat().st_size-HEADER_BYTES)
                                      /SAMPLE_WIDTH/SAMPLE_RATE*1000))
        else:
            metadata.setdefault('speech_bytes', 0)
            metadata.setdefault('speech_duration_ms', 0)
        ready = self.ready_path(identity)
        speech = self.speech_path(identity)
        speech_map = self.speech_map_path(identity)
        metadata['ready_available'] = bool(speech and speech_map)
        metadata['ready_audio_url'] = (f'/api/recordings/{identity}/ready'
                                       if speech and speech_map else None)
        metadata['speech_map_url'] = (f'/api/recordings/{identity}/speech-map'
                                      if speech_map else None)
        if ready:
            data_bytes = max(0, ready.stat().st_size-HEADER_BYTES)
            metadata['ready_bytes'] = data_bytes
            metadata['ready_duration_ms'] = round(data_bytes/SAMPLE_WIDTH/SAMPLE_RATE*1000)
        else:
            metadata['ready_bytes'] = 0
            metadata['ready_duration_ms'] = 0
        clean = self.clean_path(identity)
        metadata['clean_available'] = bool(clean)
        metadata['clean_audio_url'] = (f'/api/recordings/{identity}/clean'
                                       if clean else None)
        if clean:
            data_bytes = max(0, clean.stat().st_size-HEADER_BYTES)
            metadata.setdefault('clean_bytes', data_bytes)
            metadata.setdefault('clean_duration_ms',
                                round(data_bytes/SAMPLE_WIDTH/SAMPLE_RATE*1000))
            metadata.setdefault('clean_profile', None)
        else:
            metadata.setdefault('clean_bytes', 0)
            metadata.setdefault('clean_duration_ms', 0)
        return metadata

    def write_metadata(self, metadata, *, file=None):
        if file is None:
            file = self.metadata_path(metadata['id'])
        temporary = file.with_suffix('.tmp')
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
        os.chmod(temporary, 0o600)
        os.replace(temporary, file)

    def get(self, identity):
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            return None
        recording = self.active.get(identity)
        if recording is not None:
            return self.decorate(recording.metadata())
        path = self.metadata_path(identity)
        if not path.is_file():
            return None
        try:
            metadata = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(metadata, dict) or metadata.get('id') != identity:
            return None
        return self.decorate(metadata)

    def list(self):
        items = {r.id: self.decorate(r.metadata()) for r in self.active.values()}
        try:
            paths = sorted(self.root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            paths = []
        for path in paths[:500]:
            try:
                metadata = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(metadata, dict) or not re.fullmatch(r'[a-f0-9]{32}', str(metadata.get('id', ''))):
                continue
            if not isinstance(metadata.get('started_at'), (int, float)):
                continue
            identity = metadata['id']
            if identity in items or not (self.root/f'{identity}.wav').is_file():
                continue
            items[identity] = self.decorate(metadata)
        return sorted(items.values(), key=lambda item: item.get('started_at') or 0, reverse=True)

    def delete(self, identity):
        if identity in self.active:
            raise ValueError('recording_active')
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            raise ValueError('recording_not_found')
        audio, metadata = self.root/f'{identity}.wav', self.metadata_path(identity)
        transcript = self.root/f'{identity}.txt'
        speech = self.root/f'{identity}-speech.wav'
        speech_map = self.root/f'{identity}-speech.json'
        ready = self.root/f'{identity}-ready.wav'
        clean = self.root/f'{identity}-clean.wav'
        if not audio.is_file() and not metadata.is_file():
            raise ValueError('recording_not_found')
        for path in (audio, metadata, transcript, speech, speech_map, ready, clean):
            if path.exists():
                path.unlink()

    def over_limit(self):
        return [r for r in self.active.values() if r.duration >= self.max_seconds]

    def close(self):
        for identity in list(self.active):
            self.stop(identity, 'server_shutdown')

    def recover(self):
        """Repair WAVs left behind by a crash and rebuild missing sidecars."""
        for audio in list(self.root.glob('*.wav')):
            identity = audio.stem
            if not re.fullmatch(r'[a-f0-9]{32}', identity):
                continue
            metadata_path = self.metadata_path(identity)
            existing = {}
            if metadata_path.is_file():
                try:
                    loaded = json.loads(metadata_path.read_text())
                    if isinstance(loaded, dict) and loaded.get('id') == identity:
                        existing = loaded
                        if loaded.get('status') != 'active':
                            continue
                except (OSError, ValueError):
                    existing = {}
            try:
                size = audio.stat().st_size
                data_bytes = max(0, size-HEADER_BYTES)
                data_bytes -= data_bytes % SAMPLE_WIDTH
                with open(audio, 'r+b') as handle:
                    handle.seek(4)
                    handle.write(struct.pack('<I', 36+data_bytes))
                    handle.seek(40)
                    handle.write(struct.pack('<I', data_bytes))
                    handle.flush()
                    try:
                        os.fsync(handle.fileno())
                    except OSError:
                        pass
                os.chmod(audio, 0o600)
                speech = self.root/f'{identity}-speech.wav'
                clean = self.root/f'{identity}-clean.wav'
                ready = self.root/f'{identity}-ready.wav'
                for extra in (speech, clean, ready):
                    if not extra.is_file():
                        continue
                    extra_bytes = max(0, extra.stat().st_size-HEADER_BYTES)
                    extra_bytes -= extra_bytes % SAMPLE_WIDTH
                    with open(extra, 'r+b') as handle:
                        handle.seek(4)
                        handle.write(struct.pack('<I', 36+extra_bytes))
                        handle.seek(40)
                        handle.write(struct.pack('<I', extra_bytes))
                        handle.flush()
                        try:
                            os.fsync(handle.fileno())
                        except OSError:
                            pass
                    os.chmod(extra, 0o600)
                samples = data_bytes//SAMPLE_WIDTH
                transcript = self.root/f'{identity}.txt'
                transcript_available = transcript.is_file()
                transcript_text = ''
                transcript_lines = 0
                speaker_count = existing.get('speaker_count', 0)
                if transcript_available:
                    try:
                        transcript_text = transcript.read_text(
                            encoding='utf-8', errors='replace')
                        transcript_lines = len(transcript_text.splitlines())
                        speakers = {int(number) for number in re.findall(
                            r']\s*说话人\s*(\d+)[：:]', transcript_text)}
                        if speakers:
                            speaker_count = max(speaker_count or 0, len(speakers))
                    except OSError:
                        transcript_available, transcript_lines, transcript_text = False, 0, ''
                duration_ms = round((samples/SAMPLE_RATE)*1000)
                started_at = existing.get('started_at')
                if not isinstance(started_at, (int, float)):
                    started_at = audio.stat().st_mtime-(samples/SAMPLE_RATE)
                ended_at = existing.get('ended_at')
                if not isinstance(ended_at, (int, float)):
                    ended_at = audio.stat().st_mtime
                map_path = self.speech_map_path(identity)
                speech_file = speech if speech.is_file() else None
                metadata = {
                    **existing,
                    'id': identity,
                    'session_id': existing.get('session_id'),
                    'device_id': existing.get('device_id'),
                    'status': 'recovered',
                    'trigger': existing.get('trigger', 'unknown'),
                    'reason': 'server_restart',
                    'started_at': round(started_at, 3),
                    'ended_at': round(ended_at, 3),
                    'duration_ms': duration_ms,
                    'samples': samples,
                    'bytes': samples*SAMPLE_WIDTH,
                    'format': 'wav',
                    'sample_rate': SAMPLE_RATE,
                    'channels': CHANNELS,
                    'download_url': f'/api/recordings/{identity}/audio',
                    'transcript_available': transcript_available,
                    'transcript_url': (f'/api/recordings/{identity}/transcript'
                                       if transcript_available else None),
                    'transcript_lines': transcript_lines,
                    'speaker_count': speaker_count,
                    'transcription_error': existing.get('transcription_error'),
                    'speech_available': bool(speech_file),
                    'speech_audio_url': (f'/api/recordings/{identity}/speech'
                                         if speech_file else None),
                    'speech_bytes': max(0, speech_file.stat().st_size-HEADER_BYTES)
                                    if speech_file else 0,
                    'speech_duration_ms': round(
                        max(0, speech_file.stat().st_size-HEADER_BYTES)
                        /SAMPLE_WIDTH/SAMPLE_RATE*1000) if speech_file else 0,
                    'speech_map_url': (f'/api/recordings/{identity}/speech-map'
                                       if map_path else None),
                    'ready_available': bool(speech_file and map_path),
                    'ready_audio_url': (f'/api/recordings/{identity}/ready'
                                        if speech_file and map_path else None),
                    'available': True,
                }
                self.write_metadata(metadata)
            except OSError:
                continue
