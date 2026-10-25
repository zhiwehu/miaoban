"""Loopback-only, cached Qwen3-TTS with bounded, cancellable PCM streaming."""
import argparse
import asyncio
import contextlib
import logging
import time
from pathlib import Path

import numpy as np
from aiohttp import web
from .app import local_admin
from .models import LocalModels, ProviderError
from .speech_text import spoken_text

MODEL = 'mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit'
RATE = 24000
VOICES = ('Serena','Vivian','Uncle_Fu','Dylan','Eric','Ryan','Aiden','Ono_Anna','Sohee')
log = logging.getLogger('voicehub.qwen_tts')


class QwenVoice:
    def __init__(self):
        self.worker = LocalModels()
        self.model = None

    def load(self):
        if self.model is None:
            from huggingface_hub import snapshot_download
            from mlx_audio.tts.utils import load_model
            path = snapshot_download(MODEL, local_files_only=True)
            self.model = load_model(Path(path))
        return self.model

    async def speech(self, text, voice):
        def generate(stop):
            model = self.load()
            samples = 0
            started = time.monotonic()
            buffered = None
            pending = []
            for result in model.generate(text, voice=voice, lang_code='Chinese', stream=True,
                    streaming_interval=.8, temperature=.5, top_p=.9, top_k=40,
                    repetition_penalty=1.05, max_tokens=min(600,max(48,len(text)*8)), verbose=False):
                if stop.is_set():
                    return
                if result.sample_rate != RATE:
                    raise ProviderError('tts_sample_rate_mismatch')
                audio = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                samples += len(audio)
                if samples > RATE*30 or not np.all(np.isfinite(audio)):
                    raise ProviderError('tts_audio_limit_or_invalid')
                pcm = (np.clip(audio,-1,1)*32767).astype('<i2').tobytes()
                if buffered is None:
                    # If synthesis starts slower than playback, avoid knowingly starving
                    # the receiver. Bound the retained sentence by the 30-second limit.
                    buffered = time.monotonic()-started > len(audio)/RATE * .9
                    log.info('tts.delivery mode=%s', 'sentence' if buffered else 'stream')
                if buffered:
                    pending.append(pcm)
                else:
                    yield pcm
            if not stop.is_set():
                yield from pending
        async with contextlib.aclosing(self.worker.stream(generate)) as source:
            async for pcm in source:
                if pcm:
                    yield pcm

    async def warm(self):
        async with contextlib.aclosing(self.speech('你好。','Serena')) as source:
            async for _ in source:
                pass

    async def close(self):
        self.worker.close()


def create_tts_app(voice=None):
    voice = voice or QwenVoice()
    active, ready = 0, False

    @web.middleware
    async def guard(request, handler):
        if not local_admin(request):
            raise web.HTTPForbidden()
        origin = request.headers.get('Origin')
        if origin and origin != f'{request.scheme}://{request.host}':
            raise web.HTTPForbidden()
        return await handler(request)

    app = web.Application(middlewares=[guard],client_max_size=16384)

    async def lifetime(app):
        nonlocal ready
        try:
            async with asyncio.timeout(180):
                await voice.warm()
            ready = True
            log.info('tts.ready model=%s sample_rate=%s',MODEL,RATE)
            yield
        finally:
            ready = False
            await voice.close()
    app.cleanup_ctx.append(lifetime)

    async def health(request):
        return web.json_response(dict(ready=ready,model=MODEL,sample_rate=RATE,voices=VOICES,active=active))

    async def speech(request):
        nonlocal active
        if request.content_type != 'application/json':
            raise web.HTTPBadRequest(text='JSON required')
        try:
            data = await request.json()
        except (ValueError,UnicodeError):
            raise web.HTTPBadRequest(text='Invalid JSON') from None
        if (not isinstance(data,dict) or set(data) != {'model','input','voice','response_format'} or
            data.get('model') != MODEL or data.get('response_format') != 'pcm' or
            data.get('voice') not in VOICES or not isinstance(data.get('input'),str) or
            not 1 <= len(data['input']) <= 2000):
            raise web.HTTPBadRequest(text='Unsupported model, voice, text or format')
        text = spoken_text(data['input'])
        if not text:
            raise web.HTTPBadRequest(text='Empty spoken text')
        if active >= 2:
            raise web.HTTPServiceUnavailable(text='TTS busy')
        active += 1
        start, first_ms, samples = time.monotonic(), None, 0
        try:
            async with asyncio.timeout(90), contextlib.aclosing(voice.speech(text,data['voice'])) as source:
                response = web.StreamResponse(headers={'Content-Type':'audio/pcm','X-Audio-Sample-Rate':str(RATE)})
                prepared = False
                async for pcm in source:
                    if not prepared:
                        await response.prepare(request)
                        prepared = True
                        first_ms = round((time.monotonic()-start)*1000)
                    await response.write(pcm)
                    samples += len(pcm)//2
                if not prepared:
                    raise web.HTTPServiceUnavailable(text='No speech generated')
                await response.write_eof()
                log.info('tts.completed voice=%s first_ms=%s samples=%s elapsed_ms=%s',
                         data['voice'],first_ms,samples,round((time.monotonic()-start)*1000))
                return response
        finally:
            active -= 1

    app.router.add_get('/health',health)
    app.router.add_post('/v1/audio/speech',speech)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8767)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(name)s %(message)s')
    web.run_app(create_tts_app(),host='127.0.0.1',port=args.port,handler_cancellation=True,
                access_log=None,shutdown_timeout=5)

if __name__ == '__main__':
    main()
