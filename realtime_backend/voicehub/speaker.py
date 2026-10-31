"""Local, anonymous speaker labels for recording transcripts.

The ASR already produces one segment per pause.  This module turns each
segment into a speaker embedding with the cached SpeechBrain ECAPA model and
clusters the embeddings online.  Only cluster labels (说话人1/2/…) are kept;
embeddings never leave memory and are never written to disk.  There is no
cross-recording identity and no name recognition.

The clusterer is deliberately simple and can be tuned through environment
variables:

* ``VOICEHUB_SPEAKER_THRESHOLD`` (default 0.25) — cosine similarity needed to
  join an existing speaker.
* ``VOICEHUB_SPEAKER_MAX`` (default 6) — maximum speakers per recording.
* ``VOICEHUB_SPEAKER_MIN_SECONDS`` (default 1.2) — shorter segments never
  create a new speaker.
* ``VOICEHUB_SPEAKER_MODEL`` — optional local SpeechBrain model directory.

On stop, clusters with a single segment are folded into the nearest remaining
speaker.  A lone short utterance therefore does not become a spurious person,
while a conversation with several segments per participant keeps its labels.
"""
import os
import threading
from pathlib import Path

import numpy as np


class SpeakerUnavailable(RuntimeError):
    def __init__(self, code='speaker_unavailable'):
        self.code = code
        super().__init__(code)


def _normalize(vector):
    vector = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 1e-8:
        raise SpeakerUnavailable('speaker_embedding_invalid')
    return vector/norm


def _env_float(name, default, low, high):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


def _env_int(name, default, low, high):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return min(high, max(low, value))


class SpeakerTracker:
    """Online cosine clustering of one recording's segment embeddings."""
    def __init__(self, threshold=None, max_speakers=None, min_seconds=None):
        self.threshold = (_env_float('VOICEHUB_SPEAKER_THRESHOLD', .25, .05, .9)
                          if threshold is None
                          else min(.9, max(.05, float(threshold))))
        self.max_speakers = (_env_int('VOICEHUB_SPEAKER_MAX', 6, 1, 12)
                             if max_speakers is None
                             else min(12, max(1, int(max_speakers))))
        self.min_seconds = (_env_float('VOICEHUB_SPEAKER_MIN_SECONDS', 1.2, .2, 10)
                            if min_seconds is None
                            else min(10, max(.2, float(min_seconds))))
        self.centroids = []
        self.counts = []
        self.first = []
        self.durations = []

    @property
    def count(self):
        return len(self.centroids)

    def _add(self, embedding, duration):
        self.centroids.append(embedding.copy())
        self.counts.append(1)
        self.first.append(len(self.first))
        self.durations.append(duration)
        identity = len(self.centroids)
        return identity, f'说话人{identity}'

    def _join(self, identity, embedding, duration):
        index = identity-1
        total = self.counts[index]
        centroid = (self.centroids[index]*total + embedding)/(total+1)
        self.centroids[index] = _normalize(centroid)
        self.counts[index] = total+1
        self.durations[index] += duration
        return identity, f'说话人{identity}'

    def assign(self, embedding, duration):
        """Return ``(speaker_id, '说话人N')`` for one segment."""
        embedding = _normalize(embedding)
        duration = max(0.0, float(duration))
        if not self.centroids:
            return self._add(embedding, duration)
        similarities = np.stack(self.centroids) @ embedding
        best = int(np.argmax(similarities))
        if (float(similarities[best]) >= self.threshold
                or duration < self.min_seconds
                or len(self.centroids) >= self.max_speakers):
            return self._join(best+1, embedding, duration)
        return self._add(embedding, duration)

    def refine(self):
        """Fold single-segment clusters, then return an old→new id map.

        Called once after the recording stops, when all segments are known.
        A participant represented by only one segment cannot be verified, so
        that segment joins the nearest remaining speaker.  Clusters with two
        or more segments survive.  New ids follow first appearance.
        """
        alias = {identity: identity for identity in range(1, len(self.centroids)+1)}
        counts = list(self.counts)
        centroids = [c.copy() for c in self.centroids]
        first = list(self.first)
        durations = list(self.durations)
        active = set(alias)

        changed = True
        while len(active) > 1 and changed:
            changed = False
            singletons = sorted(
                (identity for identity in active if counts[identity-1] == 1),
                key=lambda identity: (durations[identity-1], identity),
            )
            if not singletons:
                break
            source = singletons[0]
            source_centroid = centroids[source-1]
            targets = [
                (float(np.dot(source_centroid, centroids[target-1])), target)
                for target in active if target != source
            ]
            if not targets:
                break
            target = max(targets)[1]
            total = counts[target-1]
            centroids[target-1] = _normalize(
                (centroids[target-1]*total + centroids[source-1])/(total+1))
            counts[target-1] += counts[source-1]
            durations[target-1] += durations[source-1]
            first[target-1] = min(first[target-1], first[source-1])
            alias[source] = target
            for old, value in list(alias.items()):
                if value == source:
                    alias[old] = target
            active.discard(source)
            changed = True

        ordered = sorted(active, key=lambda identity: first[identity-1])
        renumber = {identity: position+1 for position, identity in enumerate(ordered)}
        mapping = {}
        for old in range(1, len(self.centroids)+1):
            resolved = old
            while alias.get(resolved) != resolved:
                resolved = alias[resolved]
            mapping[old] = renumber[resolved]
        return mapping, len(ordered)


class SpeakerEngine:
    """Lazy, process-wide ECAPA model; CPU inference guarded by a lock."""
    def __init__(self, cache_dir=None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._model = None
        self._lock = threading.Lock()

    def _snapshot(self):
        override = os.environ.get('VOICEHUB_SPEAKER_MODEL')
        if override:
            path = Path(override).expanduser()
            if (path/'hyperparams.yaml').is_file():
                return path
            raise SpeakerUnavailable('speaker_model_missing')
        base = Path.home()/'.cache/huggingface/hub/models--speechbrain--spkrec-ecapa-voxceleb/snapshots'
        for candidate in sorted(base.glob('*')):
            if (candidate/'hyperparams.yaml').is_file():
                return candidate
        raise SpeakerUnavailable('speaker_model_missing')

    def _load(self):
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                import torch
                from speechbrain.inference.speaker import SpeakerRecognition
            except Exception:
                raise SpeakerUnavailable('speaker_runtime_unavailable') from None
            snapshot = self._snapshot()
            cached = self.cache_dir or (Path.home()/'.cache/voicehub-speaker')
            cached.mkdir(parents=True, exist_ok=True)
            os.environ.setdefault('HF_HUB_OFFLINE', '1')
            torch.set_num_threads(1)
            try:
                model = SpeakerRecognition.from_hparams(
                    source=str(snapshot), savedir=str(cached),
                    run_opts={'device': 'cpu'})
            except Exception:
                raise SpeakerUnavailable('speaker_model_load_failed') from None
            self._model = model
            return model

    def embed_batch(self, frames):
        """Embed equal-length 16 kHz frames in one model call."""
        if not isinstance(frames, (list, tuple)) or not frames:
            return np.zeros((0, 192), dtype=np.float32)
        lengths = {len(frame) for frame in frames}
        if len(lengths) != 1:
            raise SpeakerUnavailable('speaker_batch_invalid')
        audio = np.stack([np.asarray(frame, dtype=np.float32).reshape(-1)
                          for frame in frames])
        if audio.shape[1] < 800:
            raise SpeakerUnavailable('speaker_segment_too_short')
        model = self._load()
        with self._lock:
            try:
                import torch
                with torch.no_grad():
                    tensor = torch.from_numpy(audio)
                    embeddings = model.encode_batch(tensor).reshape(len(frames), -1)
                    embeddings = embeddings.cpu().numpy()
            except SpeakerUnavailable:
                raise
            except Exception:
                raise SpeakerUnavailable('speaker_embedding_failed') from None
        output = []
        for embedding in embeddings:
            output.append(_normalize(embedding))
        return np.stack(output)

    def embed(self, pcm):
        """Return one normalized 192-d embedding for 16 kHz mono PCM."""
        audio = np.asarray(pcm)
        if audio.dtype.kind in 'iu':
            audio = audio.astype(np.float32)/32768.0
        else:
            audio = audio.astype(np.float32, copy=False)
        audio = audio.reshape(-1)
        if audio.size < 800:
            raise SpeakerUnavailable('speaker_segment_too_short')
        model = self._load()
        with self._lock:
            try:
                import torch
                with torch.no_grad():
                    tensor = torch.from_numpy(audio).unsqueeze(0)
                    embedding = model.encode_batch(tensor).reshape(-1)
                    embedding = embedding.cpu().numpy()
            except SpeakerUnavailable:
                raise
            except Exception:
                raise SpeakerUnavailable('speaker_embedding_failed') from None
        return _normalize(embedding)
