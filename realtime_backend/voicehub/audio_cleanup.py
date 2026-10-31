"""Conservative cleanup for clipped / harsh-sounding device recordings.

The source WAV is never modified.  The cleaned copy keeps the same sample rate,
mono channel and duration, so transcript timestamps and the player stay valid.
"""
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

PROFILES = {
    'mild': 'highpass=f=90,adeclick,adeclip,highshelf=f=3200:g=-4,lowpass=f=6800,'
            'alimiter=level=false:limit=0.95:latency=true',
    'strong': 'highpass=f=90,adeclick,adeclip,highshelf=f=2800:g=-7,lowpass=f=5200,'
              'alimiter=level=false:limit=0.95:latency=true',
}


class CleanupError(RuntimeError):
    def __init__(self, code, message=None):
        self.code = code
        self.message = message or code
        super().__init__(code)


def clean_wav(source, destination, profile='mild', timeout=300):
    """Write a cleaned mono 16 kHz 16-bit WAV and return its stats."""
    if profile not in PROFILES:
        raise CleanupError('invalid_cleanup_profile', '未知的降噪档位。')
    source, destination = Path(source), Path(destination)
    if not source.is_file():
        raise CleanupError('cleanup_source_missing', '找不到原始音频。')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise CleanupError('cleanup_unavailable', '本机没有可用的 ffmpeg，无法降噪。')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f'.{destination.stem}-', suffix='.wav')
    os.close(fd)
    temporary = Path(temporary)
    command = [
        ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(source), '-af', PROFILES[profile],
        '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', '-f', 'wav',
        str(temporary),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
        if result.returncode != 0 or not temporary.is_file():
            raise CleanupError('cleanup_failed', '降噪处理失败，请检查原始音频。')
        with wave.open(str(temporary), 'rb') as check:
            if (check.getnchannels(), check.getsampwidth(),
                    check.getframerate()) != (1, 2, 16000):
                raise CleanupError('cleanup_failed', '降噪输出格式不正确。')
            frames = check.getnframes()
        try:
            with wave.open(str(source), 'rb') as original:
                original_frames = original.getnframes()
        except (OSError, wave.Error):
            original_frames = frames
        if original_frames and abs(frames-original_frames) > 16:
            raise CleanupError('cleanup_failed', '降噪改变了音频长度，已取消。')
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        return {'profile': profile, 'bytes': destination.stat().st_size,
                'duration_ms': round(frames/16000*1000)}
    except subprocess.TimeoutExpired:
        raise CleanupError('cleanup_timeout', '降噪处理超时。') from None
    finally:
        temporary.unlink(missing_ok=True)
