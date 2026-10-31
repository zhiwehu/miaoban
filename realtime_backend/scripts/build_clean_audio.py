"""Build cleaned (-clean.wav) copies for existing stopped recordings.

Usage:
    python scripts/build_clean_audio.py --all [--profile mild|strong]
    python scripts/build_clean_audio.py --id <32-hex-id> [--force]
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicehub.audio_cleanup import clean_wav


def build(identity, data, profile, force=False):
    root = data/'recordings'
    source = root/f'{identity}.wav'
    target = root/f'{identity}-clean.wav'
    metadata_path = root/f'{identity}.json'
    if not source.is_file() or not metadata_path.is_file():
        return 'missing'
    try:
        metadata = json.loads(metadata_path.read_text())
    except (OSError, ValueError):
        return 'invalid'
    if metadata.get('status') == 'active':
        return 'active'
    if target.is_file() and not force:
        return 'exists'
    try:
        info = clean_wav(source, target, profile)
    except Exception as exc:
        return f'failed:{getattr(exc, "code", type(exc).__name__)}'
    metadata.update({
        'clean_available': True,
        'clean_audio_url': f'/api/recordings/{identity}/clean',
        'clean_bytes': info['bytes'],
        'clean_duration_ms': info['duration_ms'],
        'clean_profile': profile,
    })
    temporary = metadata_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    os.chmod(temporary, 0o600)
    os.replace(temporary, metadata_path)
    return f"built:{info['duration_ms']}ms"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default=str(Path(__file__).resolve().parents[1]/'data'))
    parser.add_argument('--id')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--profile', choices=('mild', 'strong'), default='mild')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    data = Path(args.data)
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
        print(identity, build(identity, data, args.profile, args.force))


if __name__ == '__main__':
    main()
