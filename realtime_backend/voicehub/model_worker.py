"""One authenticated loopback process per cached model, owned by ServiceManager."""
import argparse
import asyncio
import contextlib
import io
import json
import logging
import os
import secrets
import wave

import numpy as np
from aiohttp import web
from .config import DEFAULT
from .models import LocalModels, Pipeline, ProviderError
from .qwen_tts import QwenVoice, RATE, VOICES

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, kind, model):
        import copy
        self.kind, self.name = kind, model
        self.local = LocalModels()
        self.voice = QwenVoice(model) if kind == 'tts' else None
        self.cfg = copy.deepcopy(DEFAULT)
        self.cfg[kind].update(provider='mlx', model=model)

    async def warm(self):
        if self.voice:
            await self.voice.warm()
        else:
            def load(stop):
                self.local.model(self.kind, self.name)
                yield True
            async with contextlib.aclosing(self.local.stream(load)) as source:
                async for _ in source:
                    pass

    async def chat(self, messages, max_tokens):
        # The caller supplies the complete history, including its system message.
        def generate(stop):
            import mlx_lm
            model, tokenizer = self.local.model('llm', self.name)
            prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            for result in mlx_lm.stream_generate(model, tokenizer, prompt, max_tokens=max_tokens):
                if stop.is_set(): break
                yield result.text
        async with contextlib.aclosing(self.local.stream(generate)) as source:
            async for text in source: yield text

    async def transcribe(self, pcm, language):
        import copy
        cfg = copy.deepcopy(self.cfg)
        cfg['asr']['language'] = language
        return await Pipeline(cfg, None, self.local).transcribe(pcm)

    async def speech(self, text, voice):
        async with contextlib.aclosing(self.voice.speech(text, voice)) as source:
            async for pcm in source: yield pcm

    async def close(self):
        self.local.close()
        if self.voice: await self.voice.close()


def create_worker_app(kind, model, service_id, token, engine=None):
    engine = engine or Engine(kind, model)
    ready, error, active = False, None, 0

    @web.middleware
    async def guard(request, handler):
        nonlocal active
        if request.headers.get('Origin') or not secrets.compare_digest(
                request.headers.get('Authorization', ''), 'Bearer '+token):
            raise web.HTTPUnauthorized()
        if request.path == '/health': return await handler(request)
        if not ready: raise web.HTTPServiceUnavailable(text=error or 'Model loading')
        if active >= 2: raise web.HTTPServiceUnavailable(text='Model busy')
        active += 1
        try:
            return await handler(request)
        except (ValueError, KeyError, TypeError, wave.Error, EOFError):
            raise web.HTTPBadRequest(text='Invalid model request') from None
        except ProviderError as exc:
            return web.json_response({'error': {'code': exc.code}}, status=503)
        finally:
            active -= 1

    app = web.Application(middlewares=[guard], client_max_size=2*1024*1024)

    async def load():
        nonlocal ready, error
        try:
            async with asyncio.timeout(300): await engine.warm()
            ready = True
        except Exception as exc:
            error = exc.code if isinstance(exc, ProviderError) else 'local_model_load_failed'
            log.exception('Model load failed kind=%s model=%s', kind, model)

    async def lifetime(app):
        task = asyncio.create_task(load())
        try: yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await engine.close()
    app.cleanup_ctx.append(lifetime)

    async def health(request):
        return web.json_response(dict(service_id=service_id, model=model, ready=ready,
            error=error, active=active, sample_rate=RATE if kind == 'tts' else None))

    async def chat(request):
        data = await request.json()
        if not isinstance(data, dict): raise ValueError('invalid_chat')
        messages, limit = data['messages'], data['max_tokens']
        if (data.get('model') != model or data.get('stream') is not True or
                type(limit) is not int or not 16 <= limit <= 2048 or
                not isinstance(messages, list) or not 1 <= len(messages) <= 200 or
                any(not isinstance(m, dict) or m.get('role') not in ('system','user','assistant') or
                    not isinstance(m.get('content'), str) or len(m['content']) > 16000 for m in messages)):
            raise ValueError('invalid_chat')
        response = web.StreamResponse(headers={'Content-Type':'text/event-stream'})
        async with asyncio.timeout(90), contextlib.aclosing(engine.chat(messages, limit)) as source:
            prepared = False
            try:
                async for text in source:
                    if not prepared:
                        await response.prepare(request)
                        prepared = True
                    value = json.dumps({'choices':[{'delta':{'content':text}}]}, ensure_ascii=False)
                    await response.write(('data: '+value+'\n\n').encode())
            except ProviderError as exc:
                if not prepared: raise
                await response.write(('data: '+json.dumps({'error':{'code':exc.code}})+'\n\n').encode())
            if not prepared: await response.prepare(request)
            await response.write(b'data: [DONE]\n\n')
            await response.write_eof()
        return response

    async def transcribe(request):
        reader = await request.multipart()
        fields, audio, size = {}, None, 0
        while part := await reader.next():
            raw = bytearray()
            while chunk := await part.read_chunk():
                size += len(chunk)
                if size > 1024*1024: raise ValueError('audio_limit')
                raw.extend(chunk)
            if part.name == 'file': audio = bytes(raw)
            else: fields[part.name] = raw.decode()
        if fields.get('model') != model or audio is None: raise ValueError('invalid_asr')
        language = {'zh':'Chinese','en':'English',None:'auto'}.get(fields.get('language'))
        if language is None: raise ValueError('invalid_language')
        with wave.open(io.BytesIO(audio), 'rb') as f:
            if f.getparams()[:3] != (1,2,16000) or f.getnframes() > 16000*30:
                raise ValueError('invalid_audio')
            pcm = np.frombuffer(f.readframes(f.getnframes()), '<i2').astype(np.float32)/32768
        async with asyncio.timeout(90): text = await engine.transcribe(pcm, language)
        return web.json_response({'text':text})

    async def speech(request):
        data = await request.json()
        if not isinstance(data, dict): raise ValueError('invalid_tts')
        if (data.get('model') != model or data.get('response_format') != 'pcm' or
                data.get('voice') not in VOICES or not isinstance(data.get('input'), str) or
                not 1 <= len(data['input']) <= 2000): raise ValueError('invalid_tts')
        response = web.StreamResponse(headers={'Content-Type':'audio/pcm','X-Audio-Sample-Rate':str(RATE)})
        async with asyncio.timeout(90), contextlib.aclosing(engine.speech(data['input'], data['voice'])) as source:
            prepared = False
            async for pcm in source:
                if not prepared:
                    await response.prepare(request)
                    prepared = True
                await response.write(pcm)
            if not prepared: raise web.HTTPServiceUnavailable(text='No speech generated')
            await response.write_eof()
        return response

    app.router.add_get('/health', health)
    app.router.add_post({'asr':'/v1/audio/transcriptions', 'llm':'/v1/chat/completions',
                         'tts':'/v1/audio/speech'}[kind], {'asr':transcribe,'llm':chat,'tts':speech}[kind])
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=('asr','llm','tts'), required=True)
    for name in ('model','service-id'): parser.add_argument('--'+name, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    token = os.environ['VOICEHUB_WORKER_TOKEN']
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_worker_app(args.kind, args.model, args.service_id, token), host='127.0.0.1',
                port=args.port, handler_cancellation=True, access_log=None, shutdown_timeout=3)


if __name__ == '__main__': main()
