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

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
HEADER_BYTES = 44
DEFAULT_MAX_SECONDS = 4 * 60 * 60
MIN_FREE_BYTES = 256 * 1024 * 1024
CONFIRM_SECONDS = 20

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


def _wav_header(data_bytes=0):
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 36+data_bytes, b'WAVE', b'fmt ',
                       16, 1, CHANNELS, SAMPLE_RATE, SAMPLE_RATE*CHANNELS*SAMPLE_WIDTH,
                       CHANNELS*SAMPLE_WIDTH, SAMPLE_WIDTH*8, b'data', data_bytes)


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
        self.file = open(self.path, 'wb', buffering=0)
        os.chmod(self.path, 0o600)
        self.file.write(_wav_header())

    @property
    def duration(self):
        return self.samples/SAMPLE_RATE

    @property
    def max_samples(self):
        return int(self.manager.max_seconds*SAMPLE_RATE)

    def write(self, pcm):
        """Append complete int16 samples; returns ``limit`` when the cap is hit."""
        if self.status != 'active' or not pcm:
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
        return 'limit' if over or self.samples >= self.max_samples else None

    def metadata(self):
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
            'available': True,
        }

    def finalize(self, reason):
        if self.status != 'active':
            return self.metadata()
        self.status = 'stopped'
        self.reason = reason
        self.ended_at = time.time()
        try:
            try:
                self.file.flush()
                os.fsync(self.file.fileno())
                data_bytes = self.samples*SAMPLE_WIDTH
                self.file.seek(4)
                self.file.write(struct.pack('<I', 36+data_bytes))
                self.file.seek(40)
                self.file.write(struct.pack('<I', data_bytes))
            except OSError:
                # The sidecar and partial audio are still preferable to losing the
                # session because a full disk rejected the header update.
                pass
        finally:
            self.file.close()
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
            return recording.metadata()
        path = self.metadata_path(identity)
        if not path.is_file():
            return None
        try:
            metadata = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(metadata, dict) or metadata.get('id') != identity:
            return None
        return metadata

    def list(self):
        items = {r.id: r.metadata() for r in self.active.values()}
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
            items[identity] = metadata
        return sorted(items.values(), key=lambda item: item.get('started_at') or 0, reverse=True)

    def delete(self, identity):
        if identity in self.active:
            raise ValueError('recording_active')
        if not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            raise ValueError('recording_not_found')
        audio, metadata = self.root/f'{identity}.wav', self.metadata_path(identity)
        if not audio.is_file() and not metadata.is_file():
            raise ValueError('recording_not_found')
        for path in (audio, metadata):
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
            if metadata_path.is_file():
                try:
                    metadata = json.loads(metadata_path.read_text())
                    if isinstance(metadata, dict) and metadata.get('status') != 'active':
                        continue
                except (OSError, ValueError):
                    pass
            try:
                size = audio.stat().st_size
                data_bytes = max(0, size-HEADER_BYTES)
                with open(audio, 'r+b') as handle:
                    handle.seek(4)
                    handle.write(struct.pack('<I', 36+data_bytes))
                    handle.seek(40)
                    handle.write(struct.pack('<I', data_bytes))
                os.chmod(audio, 0o600)
                samples = data_bytes//SAMPLE_WIDTH
                started_at = audio.stat().st_mtime-(samples/SAMPLE_RATE)
                metadata = {
                    'id': identity,
                    'session_id': None,
                    'device_id': None,
                    'status': 'recovered',
                    'trigger': 'unknown',
                    'reason': 'server_restart',
                    'started_at': round(started_at, 3),
                    'ended_at': round(audio.stat().st_mtime, 3),
                    'duration_ms': round((samples/SAMPLE_RATE)*1000),
                    'samples': samples,
                    'bytes': samples*SAMPLE_WIDTH,
                    'format': 'wav',
                    'sample_rate': SAMPLE_RATE,
                    'channels': CHANNELS,
                    'download_url': f'/api/recordings/{identity}/audio',
                    'available': True,
                }
                self.write_metadata(metadata)
            except OSError:
                continue
