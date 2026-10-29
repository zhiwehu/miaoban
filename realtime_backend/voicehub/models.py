"""HTTP adapters and lazy local inference. No model code runs on the asyncio thread."""
import asyncio
import contextlib
import io
import json
import os
import queue
import tempfile
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import aiohttp
import numpy as np
from .audio import to_pcm16
from .speech_text import spoken_text


class ProviderError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class LocalModels:
    """One GPU worker, at most one queued call. Cancellation never blocks the event loop."""
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='voicehub-model')
        self.slots = asyncio.Semaphore(2)
        self.cache = {}
        self.active_stops = set()

    def model(self, kind, name):
        key = (kind, name)
        if key not in self.cache:
            # Release the previous model of this kind before a configuration-driven replacement.
            for old in list(self.cache):
                if old[0] == kind:
                    del self.cache[old]
            if not Path(name).is_dir():
                from huggingface_hub import snapshot_download
                try:
                    name = snapshot_download(name, local_files_only=True,
                        allow_patterns=['*.json','*.safetensors','*.model','*.tiktoken','*.txt'])
                except Exception:
                    raise ProviderError('local_model_not_cached') from None
            if kind == 'asr':
                from mlx_audio.stt.utils import load_model
                self.cache[key] = load_model(name)
            else:
                import mlx_lm
                self.cache[key] = mlx_lm.load(name)
        return self.cache[key]

    async def stream(self, factory):
        try:
            await asyncio.wait_for(self.slots.acquire(), 5)
        except TimeoutError:
            raise ProviderError('local_model_busy') from None
        stop, output = threading.Event(), queue.Queue(maxsize=16)
        self.active_stops.add(stop)
        def put(value):
            while not stop.is_set():
                try:
                    output.put(value, timeout=.05)
                    return
                except queue.Full:
                    continue
        def work():
            try:
                if not stop.is_set():
                    for value in factory(stop):
                        if stop.is_set():
                            break
                        put(('value', value))
            except Exception as exc:
                put(('error', exc.code if isinstance(exc, ProviderError) else 'local_model_failed'))
            finally:
                put(('done', None))
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.pool, work)
        def release(_):
            self.active_stops.discard(stop)
            self.slots.release()
        future.add_done_callback(release)
        try:
            while True:
                try:
                    kind, value = output.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.01)
                    continue
                if kind == 'done':
                    break
                if kind == 'error':
                    raise ProviderError(value)
                yield value
        finally:
            stop.set()

    def close(self):
        for stop in self.active_stops:
            stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)


async def sse(response):
    data = []
    while line := await response.content.readline():
        if len(line) > 65536:
            raise ProviderError('provider_event_too_large')
        line = line.decode('utf-8').strip()
        if not line:
            if data:
                value = '\n'.join(data)
                data = []
                if value == '[DONE]':
                    return
                yield json.loads(value)
        elif line.startswith('data:'):
            data.append(line[5:].lstrip())
            if sum(map(len, data)) > 65536:
                raise ProviderError('provider_event_too_large')
    if data:
        value = '\n'.join(data)
        if value != '[DONE]':
            yield json.loads(value)


class Pipeline:
    def __init__(self, config, http, local):
        self.config, self.http, self.local = config, http, local

    def headers(self, kind):
        if self.config[kind]['provider'] == 'builtin':
            from .services import ServiceError
            try:
                return self.local.services.headers(kind)
            except ServiceError as exc:
                raise ProviderError(exc.code) from None
        value = os.environ.get(self.config[kind]['key_env'], '')
        return {'Authorization': 'Bearer '+value} if value else {}

    def endpoint(self, kind, path):
        if self.config[kind]['provider'] == 'builtin':
            return self.local.services.endpoint(kind) + '/' + path
        return self.config[kind]['url'].rstrip('/') + '/' + path

    def check(self, response):
        if response.status >= 400:
            raise ProviderError(f'provider_http_{response.status}')

    async def transcribe(self, pcm):
        c = self.config['asr']
        if c['provider'] == 'mlx':
            def generate(stop):
                model = self.local.model('asr', c['model'])
                if not stop.is_set():
                    result = model.generate(pcm, language=c['language'] if c['language'] != 'auto' else None,
                                            max_tokens=256, verbose=False)
                    yield result.text
            async with contextlib.aclosing(self.local.stream(generate)) as source:
                async for text in source:
                    return text
            return ''
        wav = io.BytesIO()
        with wave.open(wav, 'wb') as f:
            f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            f.writeframes(to_pcm16(pcm, 16000))
        form = aiohttp.FormData()
        form.add_field('file', wav.getvalue(), filename='utterance.wav', content_type='audio/wav')
        form.add_field('model', c['model'])
        if c['language'] != 'auto':
            form.add_field('language', {'Chinese':'zh', 'English':'en'}[c['language']])
        async with self.http.post(self.endpoint('asr', 'audio/transcriptions'), data=form,
                                  headers=self.headers('asr')) as response:
            self.check(response)
            data = await self.read_json(response)
            if not isinstance(data.get('text'), str):
                raise ProviderError('invalid_transcript')
            return data['text'][:8000]

    async def read_json(self, response):
        raw = bytearray()
        async for chunk in response.content.iter_chunked(8192):
            raw.extend(chunk)
            if len(raw) > 1024*1024:
                raise ProviderError('provider_response_too_large')
        return json.loads(raw)

    async def chat(self, history):
        from .tool_chat import chat
        async with contextlib.aclosing(chat(self, history)) as source:
            async for item in source:
                yield item

    async def raw_chat(self, history, *, system_prompt=None, max_tokens=None):
        c = self.config['llm']
        token_limit=c['max_tokens'] if max_tokens is None else max_tokens
        if type(token_limit) is not int or not 16<=token_limit<=2048:raise ProviderError('invalid_token_limit')
        messages = [{'role':'system', 'content':system_prompt if system_prompt is not None else self.config['system_prompt']}] + history
        if c['provider'] == 'mlx':
            def generate(stop):
                import mlx_lm
                model, tokenizer = self.local.model('llm', c['model'])
                prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
                for result in mlx_lm.stream_generate(model, tokenizer, prompt, max_tokens=token_limit):
                    if stop.is_set():
                        break
                    yield result.text
            async with contextlib.aclosing(self.local.stream(generate)) as source:
                async for text in source:
                    yield text
        else:
            async with self.http.post(self.endpoint('llm', 'chat/completions'),
                    headers=self.headers('llm'), json=dict(model=c['model'], messages=messages,
                                                         max_tokens=token_limit, stream=True)) as response:
                self.check(response)
                count = 0
                async for event in sse(response):
                    choices = event.get('choices') or []
                    if event.get('error'):
                        raise ProviderError('provider_stream_error')
                    text = choices[0].get('delta', {}).get('content', '') if choices else ''
                    if text:
                        count += len(text)
                        if count > 16000:
                            raise ProviderError('reply_text_limit')
                        yield text

    async def speech(self, text):
        text = spoken_text(text)
        if not text:
            return
        c = self.config['tts']
        if c['provider'] == 'macos':
            async with contextlib.aclosing(self.native_speech(text, c['voice'])) as source:
                async for block in source:
                    yield block
        else:
            # Compatible providers return raw signed little-endian PCM at the configured rate.
            import av
            rate = c['sample_rate']
            resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
            pending, total = b'', 0
            async with self.http.post(self.endpoint('tts', 'audio/speech'), headers=self.headers('tts'),
                    json=dict(model=c['model'], input=text, voice=c['voice'], response_format='pcm')) as response:
                self.check(response)
                content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
                if content_type not in ('application/octet-stream', 'audio/pcm', 'audio/raw', 'audio/x-pcm'):
                    raise ProviderError('tts_format_mismatch')
                async for raw in response.content.iter_chunked(8192):
                    pending += raw
                    total += len(raw)
                    if total > rate*2*30:
                        raise ProviderError('tts_audio_limit')
                    size = len(pending)//2*2
                    if size:
                        frame = av.AudioFrame.from_ndarray(np.frombuffer(pending[:size], '<i2').reshape(1,-1),
                                                          format='s16', layout='mono')
                        frame.sample_rate = rate
                        pending = pending[size:]
                        for out in resampler.resample(frame):
                            yield out.to_ndarray().astype('<i2').tobytes()
                if pending:
                    raise ProviderError('invalid_pcm')
                for out in resampler.resample(None):
                    yield out.to_ndarray().astype('<i2').tobytes()

    async def native_speech(self, text, voice):
        processes = []
        with tempfile.TemporaryDirectory(prefix='voicehub-') as root:
            try:
                destination = str(Path(root)/'speech.aiff')
                say = await asyncio.create_subprocess_exec('/usr/bin/say', '-v', voice, '-o', destination,
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL)
                processes.append(say)
                await asyncio.wait_for(say.communicate(text.encode()), 30)
                if say.returncode:
                    raise ProviderError('native_voice_failed')
                ffmpeg = await asyncio.create_subprocess_exec('ffmpeg', '-v', 'error', '-i', destination,
                        '-f', 's16le', '-ar', '16000', '-ac', '1', 'pipe:1',
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                processes.append(ffmpeg)
                while block := await ffmpeg.stdout.read(8192):
                    yield block
                if await ffmpeg.wait():
                    raise ProviderError('audio_conversion_failed')
            finally:
                for process in processes:
                    if process.returncode is None:
                        with contextlib.suppress(ProcessLookupError):
                            process.kill()
                        await process.wait()
