"""Build speech-only WAVs for recordings that predate live VAD trimming.

New recordings write <id>-speech.wav while recording.  This script handles
existing stopped recordings by re-running the same local Silero VAD, trimming
the pre-roll/trailing silence per utterance, and concatenating only those
utterances.  The original <id>.wav is never modified.

Usage (from realtime_backend):
    python scripts/build_speech_only.py --all
    python scripts/build_speech_only.py --id <32-hex-id> [--force]
"""
import argparse
import json
import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from voicehub.audio import Segmenter, Silero, vad_path


def settings(data):
    path = data/'settings.json'
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    return {'audio': {'speech_threshold': .65, 'onset_ms': 160, 'silence_ms': 640,
                      'max_utterance_seconds': 15, 'vad_model': ''}}


def trim_segment(segment, segmenter):
    lead = segmenter.last_pre_trim_frames*512
    tail = segmenter.last_trailing_silence*512
    end = len(segment)-tail
    if end <= lead:
        return None
    return segment[lead:end]


def build(recording_id, data, config, force=False):
    root = data/'recordings'
    full = root/f'{recording_id}.wav'
    speech = root/f'{recording_id}-speech.wav'
    meta_path = root/f'{recording_id}.json'
    if not full.is_file() or not meta_path.is_file():
        return 'missing'
    try:
        metadata = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return 'invalid'
    if metadata.get('status') == 'active':
        return 'active'
    if speech.is_file() and not force:
        return 'exists'
    audio = config['audio']
    try:
        vad = Silero(vad_path(audio.get('vad_model', '')))
    except Exception as exc:
        print(f'VAD unavailable: {exc}', file=sys.stderr)
        return 'vad_unavailable'
    segmenter = Segmenter(audio.get('speech_threshold', .65),
                          audio.get('onset_ms', 160),
                          audio.get('silence_ms', 640),
                          audio.get('max_utterance_seconds', 15))
    with wave.open(str(full), 'rb') as source:
        if source.getnchannels() != 1 or source.getframerate() != 16000 \
                or source.getsampwidth() != 2:
            print(f'{recording_id}: unsupported WAV format', file=sys.stderr)
            return 'unsupported'
        output = tempfile.NamedTemporaryFile(
            dir=root, prefix=f'.{recording_id}-speech-', suffix='.wav', delete=False)
        output.close()
        output_path = Path(output.name)
        chunks = 0
        with wave.open(str(output_path), 'wb') as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(16000)
            while True:
                raw = source.readframes(512)
                if not raw:
                    break
                if len(raw) % 2:
                    raw = raw[:-1]
                if not raw:
                    continue
                frame = np.frombuffer(raw, '<i2').astype(np.float32)/32768
                probability = vad.probability(frame)
                _, segment = segmenter.feed(frame, probability)
                if segment is None:
                    continue
                trimmed = trim_segment(segment, segmenter)
                if trimmed is None or not len(trimmed):
                    continue
                target.writeframes((np.clip(trimmed, -1, 1)*32767)
                                   .astype('<i2').tobytes())
                chunks += 1
    if chunks <= 0:
        output_path.unlink(missing_ok=True)
        return 'no_speech'
    os.replace(output_path, speech)
    os.chmod(speech, 0o600)
    with wave.open(str(speech), 'rb') as result:
        frames = result.getnframes()
    metadata.update({
        'speech_available': True,
        'speech_audio_url': f'/api/recordings/{recording_id}/speech',
        'speech_bytes': frames*2,
        'speech_duration_ms': round(frames/16000*1000),
    })
    temporary = meta_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    os.chmod(temporary, 0o600)
    os.replace(temporary, meta_path)
    return f'built:{chunks}:{frames}'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default=str(Path(__file__).resolve().parents[1]/'data'))
    parser.add_argument('--id')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    data = Path(args.data)
    config = settings(data)
    if args.id:
        ids = [args.id]
    elif args.all:
        ids = sorted(path.stem for path in (data/'recordings').glob('*.json'))
    else:
        parser.error('use --id or --all')
    for identity in ids:
        if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            print(identity, 'invalid')
            continue
        print(identity, build(identity, data, config, args.force))


if __name__ == '__main__':
    main()
