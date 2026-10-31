"""Offline speaker re-diarization and re-transcription for a finished recording.

VAD segments can be tens of seconds long when people talk continuously, and a
single embedding per segment collapses a whole conversation into one speaker.
This pass clusters short sliding windows, smooths speaker turns, re-runs the
local ASR per turn and rewrites the transcript.  The WAV is never modified and
the old transcript is backed up first.
"""
import asyncio
import json
import os
import re
import wave
from pathlib import Path

import numpy as np

from .audio import to_pcm16
from .config import ConfigFile
from .models import LocalModels, Pipeline
from .recording import stamp_ms, stamp_text
from .services import ServiceManager
from .speaker import SpeakerEngine

CONTROL = re.compile(r'^(开始录音模式|开始录音|进入录音模式|停止录音模式|确认停止)[。！!，,、\s]*$')


def _read_wav(path):
    with wave.open(str(path), 'rb') as source:
        if (source.getnchannels(), source.getsampwidth(),
                source.getframerate()) != (1, 2, 16000):
            raise ValueError('unsupported_wav')
        pcm = np.frombuffer(source.readframes(source.getnframes()), '<i2')
        return pcm.astype(np.float32)/32768


def windows(pcm, sample_rate=16000, size=1.6, hop=.8):
    length, step = int(size*sample_rate), int(hop*sample_rate)
    rows = []
    for start in range(0, max(0, len(pcm)-length+1), step):
        frame = pcm[start:start+length]
        rms = float(np.sqrt((frame*frame).mean()))
        if rms >= .006:
            rows.append((start, frame))
    return rows


def _normalize(vector):
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    return vector/norm if norm > 1e-8 else vector


def cluster(embeddings, count=2):
    embeddings = np.stack([_normalize(e) for e in embeddings])
    count = max(1, min(count, len(embeddings)))
    best = None
    for seed in range(4):
        rng = np.random.default_rng(seed)
        centers = embeddings[rng.choice(len(embeddings), count, replace=False)].copy()
        labels = np.zeros(len(embeddings), dtype=int)
        for _ in range(40):
            labels = (embeddings @ centers.T).argmax(1)
            updated = []
            for index in range(count):
                members = embeddings[labels == index]
                centers[index] = _normalize(members.mean(0)) if len(members) else centers[index]
                updated.append(centers[index])
            centers = np.stack(updated)
        inertia = float(sum(1-float(np.dot(embeddings[i], centers[labels[i]]))
                            for i in range(len(embeddings))))
        if best is None or inertia < best[0]:
            best = (inertia, labels)
    return best[1]


def smooth_labels(labels, radius=2):
    labels = np.asarray(labels, dtype=int)
    output = labels.copy()
    for index in range(len(labels)):
        window = labels[max(0, index-radius):index+radius+1]
        counts = np.bincount(window)
        output[index] = int(counts.argmax())
    return output


def turns_from_windows(labels, hop=.8, duration=None, min_turn=1.2):
    groups = []
    start, current = 0, int(labels[0])
    for index, value in enumerate(labels[1:], 1):
        if int(value) != current:
            groups.append([start, index, current])
            start, current = index, int(value)
    groups.append([start, len(labels), current])
    changed = True
    while changed and len(groups) > 1:
        changed = False
        for index, (start, end, label) in enumerate(groups):
            if (end-start)*hop >= min_turn:
                continue
            left = groups[index-1] if index > 0 else None
            right = groups[index+1] if index+1 < len(groups) else None
            if left and right:
                target = left if (left[1]-left[0]) >= (right[1]-right[0]) else right
            else:
                target = left or right
            target[0] = min(target[0], start)
            target[1] = max(target[1], end)
            groups.pop(index)
            changed = True
            break
    # Renumber by first appearance so the result is stable and readable.
    mapping, next_id, result = {}, 1, []
    for start, end, label in groups:
        if label not in mapping:
            mapping[label] = next_id
            next_id += 1
        result.append((start, end, mapping[label]))
    return result


def _split_for_asr(pcm, sample_rate=16000, max_seconds=16):
    chunks = []
    while len(pcm) > max_seconds*sample_rate:
        low = int(6*sample_rate)
        high = min(len(pcm)-int(1*sample_rate), int(max_seconds*sample_rate))
        best, best_energy = high, None
        for start in range(low, max(low+1, high), sample_rate//2):
            frame = pcm[start:start+sample_rate//2]
            energy = float(np.sqrt((frame*frame).mean()))
            if best_energy is None or energy < best_energy:
                best, best_energy = start, energy
        chunks.append(pcm[:best])
        pcm = pcm[best:]
    if len(pcm):
        chunks.append(pcm)
    return chunks


_STAMP = re.compile(r'^\[(\d{1,3}:\d{2}(?::\d{2})?)\]\s?(?:说话人\s*\d+[：:]\s?)?(.*)$')


def _sentences(text, limit=160):
    parts = [part.strip() for part in re.split(r'(?<=[。！？!?；;])', text) if part.strip()]
    output = []
    for part in parts:
        while len(part) > limit:
            cut = max(part.rfind('，', 0, limit), part.rfind(',', 0, limit),
                      part.rfind('、', 0, limit))
            if cut < 20:
                cut = limit
            else:
                cut += 1
            output.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            output.append(part)
    return output


def _speaker_at(groups, seconds):
    for start, end, speaker in groups:
        if start*.8 <= seconds < end*.8:
            return speaker
    if not groups:
        return 1
    return min(groups, key=lambda group: abs(group[0]*.8-seconds))[2]


def label_existing_text(text, groups, duration, hop=.8):
    source = []
    for raw in (text or '').splitlines():
        match = _STAMP.match(raw.strip())
        if match:
            source.append({'ms': stamp_ms(match.group(1)), 'text': match.group(2).strip()})
    if not source:
        raise ValueError('transcript_empty')
    output = []
    for index, line in enumerate(source):
        end = source[index+1]['ms'] if index+1 < len(source) else duration*1000
        end = max(line['ms']+500, end)
        sentences = _sentences(line['text'])
        total = sum(max(1, len(sentence)) for sentence in sentences) or 1
        cursor = line['ms']
        span = end-line['ms']
        for sentence in sentences:
            share = max(1, len(sentence))/total
            sentence_end = min(end, cursor+span*share)
            midpoint = (cursor+sentence_end)/2/1000
            speaker = _speaker_at(groups, min(max(midpoint, 0), duration))
            if (output and output[-1]['speaker'] == speaker
                    and cursor/1000-output[-1]['end'] <= .9):
                output[-1]['text'] += sentence
                output[-1]['end'] = sentence_end
            else:
                output.append({'ms': round(cursor), 'end': sentence_end,
                               'speaker': f'说话人{speaker}', 'speaker_id': speaker,
                               'text': sentence})
            cursor = sentence_end
    return output


async def _transcribe_turns(pcm, groups, data_root, hop=.8):
    config = ConfigFile(data_root/'settings.json')
    services = ServiceManager(data_root, config.get)
    local = LocalModels()
    local.services = services
    import aiohttp
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120, connect=5))
    pipeline = Pipeline(config.get(), http, local)
    lines = []
    try:
        for start, end, speaker in groups:
            begin = int(start*hop*16000)
            finish = min(len(pcm), int(end*hop*16000))
            if finish-begin < 1600:
                continue
            for chunk in _split_for_asr(pcm[begin:finish]):
                text = ''
                for attempt in range(2):
                    try:
                        text = await pipeline.transcribe(chunk)
                        break
                    except Exception:
                        if attempt == 0:
                            await asyncio.sleep(1)
                text = re.sub(r'\s+', ' ', text or '').strip()
                if not text or CONTROL.match(text):
                    continue
                lines.append({
                    'stamp': stamp_text(round(begin/16000*1000)),
                    'speaker': f'说话人{speaker}',
                    'speaker_id': speaker,
                    'text': text[:4000],
                })
                begin += len(chunk)
    finally:
        await http.close()
        local.close()
    return lines


async def rediarize(recording_id, data_root, speakers=2, wait=False, mode='text'):
    root = Path(data_root)
    audio = root/'recordings'/f'{recording_id}.wav'
    metadata_path = root/'recordings'/f'{recording_id}.json'
    transcript_path = root/'recordings'/f'{recording_id}.txt'
    if wait:
        while True:
            if metadata_path.is_file():
                try:
                    metadata = json.loads(metadata_path.read_text())
                except (OSError, ValueError):
                    metadata = None
                if metadata and metadata.get('status') != 'active':
                    break
            await asyncio.sleep(5)
    if not audio.is_file() or not metadata_path.is_file():
        raise ValueError('recording_not_found')
    metadata = json.loads(metadata_path.read_text())
    if metadata.get('status') == 'active':
        raise ValueError('recording_active')
    pcm = _read_wav(audio)
    rows = windows(pcm)
    if len(rows) < 4:
        raise ValueError('too_little_speech')
    def _embed_all():
        engine = SpeakerEngine(root/'speaker-cache')
        return [engine.embed(frame) for _, frame in rows]
    embeddings = await asyncio.to_thread(_embed_all)
    labels = smooth_labels(cluster(embeddings, speakers))
    groups = turns_from_windows(labels, duration=len(pcm)/16000)
    if mode == 'text':
        source = transcript_path
        backup = root/'recordings'/f'{recording_id}.txt.before-diarization'
        if not source.is_file() and backup.is_file():
            source = backup
        lines = label_existing_text(source.read_text(encoding='utf-8', errors='replace'),
                                    groups, len(pcm)/16000)
    else:
        lines = await _transcribe_turns(pcm, groups, root)
    if not lines:
        raise ValueError('transcript_empty')
    if transcript_path.is_file():
        backup = root/'recordings'/f'{recording_id}.txt.before-diarization'
        if not backup.is_file():
            os.replace(transcript_path, backup)
            os.chmod(backup, 0o600)
    temporary = transcript_path.with_suffix('.txt.tmp')
    rendered = []
    for line in lines:
        stamp = line.get('stamp') or stamp_text(round(line.get('ms', 0)))
        speaker = line.get('speaker') or f"说话人{line.get('speaker_id', 1)}"
        rendered.append(f"[{stamp}] {speaker}：{line['text']}")
    text = '\n'.join(rendered)+'\n'
    temporary.write_text(text, encoding='utf-8')
    os.chmod(temporary, 0o600)
    os.replace(temporary, transcript_path)
    metadata.update({
        'transcript_lines': len(lines),
        'speaker_count': len({line.get('speaker_id') or int(re.search(r'说话人(\d+)', line.get('speaker','说话人1')).group(1))
                              for line in lines}),
        'diarization_revision': int(metadata.get('diarization_revision') or 0)+1,
        'diarization_method': ('ecapa-window-text-align' if mode == 'text'
                               else 'ecapa-window-kmeans-asr'),
        'diarization_speakers': speakers,
        'transcription_error': None,
    })
    tmp = metadata_path.with_suffix('.tmp')
    tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, metadata_path)
    return {'lines': len(lines), 'speakers': metadata['speaker_count']}
