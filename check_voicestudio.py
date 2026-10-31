"""Discover VoiceStudio and optionally synthesize/transcribe a benchmark clip."""
import argparse
import json
import os
import time
from pathlib import Path

import numpy as np
from voice_brain import write_wav
from voicestudio_client import VoiceStudioClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--text', help='Generate then transcribe this text (requires installed models)')
    parser.add_argument('--output', default='/tmp/miaoban-voicestudio.wav')
    args = parser.parse_args()
    client = VoiceStudioClient(os.environ.get('VOICESTUDIO_URL', 'http://127.0.0.1:3900'),
                               os.environ.get('OMNIVOICE_API_KEY', ''), timeout=15)
    info = client.discover()
    paths = info['schema'].get('paths', {})
    required = ['/v1/audio/speech', '/v1/audio/transcriptions']
    missing = [path for path in required if 'post' not in paths.get(path, {})]
    if missing:
        raise RuntimeError(f'Service lacks required POST endpoints: {missing}')
    print(json.dumps({'health': info['health'], 'voices': info['voices']}, ensure_ascii=False))
    if not args.text:
        return
    client.timeout = 60
    started = time.monotonic()
    chunks = list(client.speech(args.text, os.environ.get('VOICESTUDIO_VOICE', 'alloy')))
    synth_seconds = time.monotonic() - started
    if not chunks or any(rate != chunks[0][1] for _, rate in chunks):
        raise RuntimeError('Missing audio or inconsistent sample rates')
    pcm = np.concatenate([pcm for pcm, _ in chunks])
    rate = chunks[0][1]
    output = Path(args.output).resolve()
    write_wav(str(output), pcm, rate)
    started = time.monotonic()
    transcript = client.transcribe(pcm, rate)
    print(json.dumps({'output': str(output), 'audio_seconds': len(pcm) / rate,
                      'tts_seconds': synth_seconds, 'asr_seconds': time.monotonic() - started,
                      'transcript': transcript}, ensure_ascii=False))


if __name__ == '__main__':
    main()
