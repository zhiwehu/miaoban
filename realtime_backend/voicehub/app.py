"""HTTP management and versioned WebSocket gateways."""
import asyncio
import base64
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web, WSMsgType
from .audio import AudioCodec, vad_path
from .config import ConfigFile
from .input import AudioInput
from .models import Pipeline, LocalModels, ProviderError
from .session import Session
from .store import Store
from .services import ServiceManager, ServiceError

HUB = web.AppKey('hub', object)
DEVICE_ONLY = web.AppKey('device_only', bool)
STATIC = Path(__file__).parent/'static'
PLAYBACK_READY_TIMEOUT = 2


def local_admin(request):
    try:
        address = ipaddress.ip_address(request.remote or '')
        host = urlsplit('//'+request.host).hostname
        return address.is_loopback and (host == 'localhost' or ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def admin(request):
    hub = request.app[HUB]
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    token = token or request.cookies.get('voicehub_admin', '')
    return local_admin(request) or secrets.compare_digest(token, hub.auth['admin_token'])


@web.middleware
async def guard(request, handler):
    origin = request.headers.get('Origin')
    if origin and (request.method not in ('GET', 'HEAD') or request.path in ('/ws', '/api/events')):
        if origin != f'{request.scheme}://{request.host}':
            raise web.HTTPForbidden(text='Cross-origin request denied')
    if request.path.startswith('/api/') and request.path != '/api/login' and not admin(request):
        raise web.HTTPUnauthorized(text='Administrator authentication required')
    try:
        response = await handler(request)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        response = web.json_response({'error':{'code':'invalid_request', 'message':'请求格式或配置无效'}}, status=400)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


class Hub:
    def __init__(self, root, pipeline_factory):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.config = ConfigFile(self.root/'settings.json')
        self.services = ServiceManager(self.root, self.config.get)
        auth_path = self.root/'auth.json'
        if auth_path.exists():
            self.auth = json.loads(auth_path.read_text())
        else:
            self.auth = {name:secrets.token_urlsafe(32) for name in ('device_token','admin_token')}
            fd = os.open(auth_path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(self.auth, f)
        self.store = Store(self.root/'history.sqlite3')
        self.store.db.execute('CREATE TABLE IF NOT EXISTS resumes (session_id TEXT PRIMARY KEY, digest TEXT)')
        self.sessions, self.sockets, self.watchers = {}, set(), set()
        self.cleanups = set()
        self.http = self.local = None
        self.factory = pipeline_factory
        self.maintenance = None
        self.mqtt = None

    def publish(self, event):
        for watcher in list(self.watchers):
            if watcher.full():
                # A slow dashboard reconnects and refetches persisted data instead of dropping silently.
                while not watcher.empty():
                    watcher.get_nowait()
                watcher.put_nowait({'type':'events.resync'})
            else:
                watcher.put_nowait(event)

    async def start(self):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90, connect=10, sock_read=30))
        self.local = LocalModels()
        self.local.services = self.services
        self.store.prune(self.config.get()['retention_days'])
        self.maintenance = asyncio.create_task(self.maintain())

    async def maintain(self):
        tick = 0
        while True:
            await asyncio.sleep(1)
            for session in list(self.sessions.values()):
                with contextlib.suppress(Exception):
                    await session.idle()
            tick += 1
            if tick % 3600 == 0:
                self.store.prune(self.config.get()['retention_days'])

    async def close(self):
        if self.maintenance:
            self.maintenance.cancel()
            await asyncio.gather(self.maintenance, return_exceptions=True)
        for ws in list(self.sockets):
            await ws.close(code=1001, message=b'server shutdown')
        for session in list(self.sessions.values()):
            await session.close()
        await asyncio.gather(*self.cleanups, return_exceptions=True)
        for watcher in self.watchers:
            if not watcher.full():
                watcher.put_nowait(None)
        if self.http:
            await self.http.close()
        if self.local:
            self.local.close()
        await self.services.shutdown()
        self.store.close()

    def resume(self, hello):
        sid = hello.get('session_id')
        if sid:
            if not isinstance(sid, str) or not re.fullmatch('[a-f0-9]{32}', sid):
                raise ValueError('invalid_session')
            row = self.store.db.execute('SELECT digest FROM resumes WHERE session_id=?', (sid,)).fetchone()
            supplied = hashlib.sha256(str(hello.get('resume_token','')).encode()).hexdigest()
            if not row or not secrets.compare_digest(row[0], supplied):
                raise ValueError('invalid_resume')
            if sid in self.sessions:
                raise ValueError('session_already_connected')
            return sid, hello['resume_token']
        sid, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        self.store.db.execute('INSERT INTO resumes VALUES (?,?)', (sid, hashlib.sha256(token.encode()).hexdigest()))
        self.store.db.commit()
        return sid, token


async def gateway(request):
    hub = request.app[HUB]
    if len(hub.sockets) >= 16 or len(hub.sessions) >= 8:
        raise web.HTTPServiceUnavailable(text='Connection capacity reached')
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=32768, receive_timeout=90)
    await ws.prepare(request)
    return await device_gateway(request, ws)


async def device_gateway(request, ws):
    """Shared device session; transport implements the bounded socket methods below."""
    hub = request.app[HUB]
    hub.sockets.add(ws)
    session = ingress = None
    legacy = request.path == '/xiaozhi/v1/'
    try:
        hello = await asyncio.wait_for(ws.receive_json(), 5)
        if not isinstance(hello, dict) or hello.get('type') != 'hello':
            raise ValueError('expected_hello')
        token = request.headers.get('Authorization', '').removeprefix('Bearer ') or hello.get('token','')
        require_device_token = request.app.get(DEVICE_ONLY, False) or not admin(request)
        if require_device_token and (not isinstance(token, str) or not secrets.compare_digest(token, hub.auth['device_token'])):
            raise ValueError('unauthorized')
        if not legacy and (type(hello.get('version')) is not int or hello['version'] != 1):
            raise ValueError('unsupported_version')
        device = hello.get('device_id') or request.headers.get('Device-Id') or 'esp32'
        if not isinstance(device, str) or not re.fullmatch(r'[\w.:-]{1,80}', device):
            raise ValueError('invalid_device_id')
        audio = hello.get('audio', hello.get('audio_params', {}))
        if not isinstance(audio, dict):
            raise ValueError('invalid_audio')
        format = audio.get('format', 'opus' if legacy else 'pcm_s16le')
        if format not in ('pcm_s16le','opus') or audio.get('sample_rate',16000) != 16000:
            raise ValueError('unsupported_audio')
        capabilities = hello.get('capabilities', {})
        if not isinstance(capabilities, dict):
            raise ValueError('invalid_capabilities')
        aec = capabilities.get('echo_cancellation') is True
        cfg = hub.config.get()
        mode = hello.get('wake_mode', cfg['wake']['mode'])
        if mode not in ('device','backend','manual'):
            raise ValueError('invalid_wake_mode')
        if mode == 'backend' and capabilities.get('continuous_audio') is not True:
            raise ValueError('continuous_audio_required')
        sid, resume = hub.resume(hello)
        if len(hub.sessions) >= 8:
            raise ValueError('capacity_reached')
        encoder, encoding_turn = None, None
        first_packet_turn = None
        features = hello.get('features', {})
        if not isinstance(features, dict):
            raise ValueError('invalid_capabilities')
        ready_supported = legacy and features.get('playback_ready') is True
        prepared = asyncio.Event()
        ready_state = None
        async def send_audio_packet(packet):
            nonlocal first_packet_turn
            await ws.send_bytes(packet)
            if first_packet_turn != encoding_turn:
                first_packet_turn = encoding_turn
                await session.emit('transport.first_audio', session.turn,
                    transport=getattr(request, 'protocol_name', 'xiaozhi'), packet_bytes=len(packet))

        async def send(event):
            nonlocal encoder, encoding_turn
            if ws.closed:
                return
            if not legacy:
                await ws.send_json(event)
                return
            kind = event['type']
            if kind == 'session.state' and event.get('state') == 'standby':
                # Legacy clients have no standby message. Channel closure restores their
                # idle state and wake-word detector. Own the task without waiting for the
                # receive loop (which may itself be emitting this transition) to finish.
                closing = asyncio.create_task(ws.close(code=1000, message=b'idle timeout'))
                hub.cleanups.add(closing)
                closing.add_done_callback(hub.cleanups.discard)
            elif kind == 'audio.start':
                encoder = AudioCodec('opus')
                encoding_turn = event['turn_id']
                prepared.clear()
                await ws.send_json({'type':'tts','state':'start','session_id':sid,
                                    'turn_id':encoding_turn})
                if ready_supported:
                    try:
                        await asyncio.wait_for(prepared.wait(), PLAYBACK_READY_TIMEOUT)
                    except TimeoutError:
                        raise ProviderError('playback_not_ready') from None
                    if ready_state != (6, True):
                        raise ProviderError('playback_not_ready')
                else:
                    await asyncio.sleep(.1)
            elif kind == 'audio.chunk' and encoder and encoding_turn == event['turn_id']:
                for packet in encoder.encode(base64.b64decode(event['data'])):
                    await send_audio_packet(packet)
            elif kind == 'audio.end':
                if encoder:
                    for packet in encoder.encode(b'', final=True):
                        await send_audio_packet(packet)
                encoder = None
                await asyncio.sleep(event.get('drain_ms', 0)/1000)
                await ws.send_json({'type':'tts','state':'stop','session_id':sid,
                                    'turn_id':encoding_turn})
            elif kind == 'playback.clear':
                encoder = None
                await ws.send_json({'type':'tts','state':'stop','session_id':sid,
                                    'turn_id':encoding_turn})
                encoding_turn = None
            elif kind == 'message.user':
                await ws.send_json({'type':'stt','text':event['text'],'session_id':sid})
            elif kind == 'reply.final':
                await ws.send_json({'type':'llm','text':event['text'],'emotion':'neutral','session_id':sid})
            elif kind == 'error':
                await ws.send_json(event)
        pipeline = hub.factory(cfg, hub.http, hub.local)
        session = Session(sid, device, hub.store, pipeline, send, publish=hub.publish,
                          wake_mode=mode, wake_phrase=cfg['wake']['phrase'], playback_ack=not legacy,
                          idle_seconds=cfg['wake']['idle_seconds'])
        hub.sessions[sid] = session
        if legacy:
            await ws.send_json(dict(type='hello', transport='websocket', version=1, session_id=sid,
                audio_params=dict(format='opus', sample_rate=16000, channels=1, frame_duration=60)))
        else:
            await ws.send_json(dict(type='hello', version=1, session_id=sid, resume_token=resume,
                wake_mode=mode, audio=dict(format='pcm_s16le', sample_rate=16000, channels=1),
                capabilities=dict(barge_in=cfg['audio']['barge_in'] and aec,
                                  aec_verification='client_declared', playback_ack=True)))
        await session.emit('session.connected', device_id=device, protocol=getattr(request, 'protocol_name', 'xiaozhi' if legacy else 'v1'),
                           wake_mode=mode, barge_in=cfg['audio']['barge_in'] and aec)
        ingress = AudioInput(session, format, aec, cfg['audio'])
        controls, window = 0, time.monotonic()
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                ingress.put(message.data)
            elif message.type == WSMsgType.TEXT:
                if time.monotonic()-window > 1:
                    controls, window = 0, time.monotonic()
                controls += 1
                if controls > 40:
                    raise ValueError('control_rate_limit')
                value = json.loads(message.data)
                if not isinstance(value, dict):
                    raise ValueError('invalid_message')
                kind = value.get('type')
                if kind == 'tts' and ready_supported and value.get('state') in ('ready','stopped'):
                    if value.get('turn_id') != encoding_turn:
                        continue
                    if value['state'] == 'ready' and (not session.turn or session.turn.id != encoding_turn):
                        continue
                    fields = {k:value.get(k) for k in ('device_state','rx_audio_packets','dropped_audio_packets',
                                                      'decoded_audio_packets','played_audio_packets')}
                    if any(type(v) is not int or not 0 <= v <= 0xffffffff for v in fields.values()):
                        raise ValueError('invalid_message')
                    if type(value.get('output_enabled')) is not bool:
                        raise ValueError('invalid_message')
                    owner = session.turn if session.turn and session.turn.id == encoding_turn else None
                    await session.emit('device.playback_state', owner, state=value['state'],
                                       reported_turn_id=encoding_turn, output_enabled=value['output_enabled'], **fields)
                    if value['state'] == 'ready':
                        ready_state = fields['device_state'], value['output_enabled']
                        prepared.set()
                elif kind == 'input.text':
                    text = value.get('text')
                    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000:
                        raise ValueError('invalid_text')
                    if session.state == 'standby':
                        await session.activate()
                    await session.submit(text.strip())
                elif kind in ('session.activate','wake') or (kind == 'listen' and value.get('state') in ('detect','start')):
                    await session.activate()
                elif kind in ('interrupt','abort'):
                    await session.interrupt('client')
                elif kind == 'session.standby':
                    await session.interrupt('standby')
                    await session.transition('standby')
                elif kind == 'playback.done':
                    session.playback_done(value.get('turn_id'))
                elif kind == 'ping':
                    await ws.send_json({'type':'pong'})
                else:
                    await session.emit('error', code='unknown_message', message='不支持该消息类型')
            elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    except (ValueError, TypeError, KeyError, TimeoutError) as exc:
        codes = {'expected_hello','unauthorized','unsupported_version','invalid_device_id',
                 'invalid_audio','unsupported_audio','invalid_capabilities','invalid_wake_mode',
                 'continuous_audio_required','invalid_session','invalid_resume','session_already_connected',
                 'capacity_reached','audio_overload','audio_packet_limit','control_rate_limit','invalid_message','invalid_text'}
        code = str(exc) if str(exc) in codes else 'invalid_connection_or_input'
        with contextlib.suppress(Exception):
            await ws.send_json({'type':'error','code':code,
                               'message':'请检查版本、认证、恢复凭证、音频格式或发送速率。'})
    finally:
        # aiohttp can cancel a disconnected request while it is awaiting cleanup.
        # Detach synchronously, then let an independently owned task finish cleanup.
        if session:
            hub.sessions.pop(session.id, None)
        hub.sockets.discard(ws)
        async def cleanup():
            if ingress:
                await ingress.close()
            if session:
                await session.close()
                hub.publish(dict(type='session.disconnected', session_id=session.id, timestamp=time.time()))
            await ws.close()
        task = asyncio.create_task(cleanup())
        hub.cleanups.add(task)
        task.add_done_callback(hub.cleanups.discard)
        await asyncio.shield(task)
    return ws


async def api(request):
    hub, name = request.app[HUB], request.match_info.get('name', request.path.rsplit('/', 1)[-1])
    if name == 'services':
        return web.json_response(await hub.services.all_status())
    if name == 'status':
        try:
            vad_path(hub.config.get()['audio']['vad_model'])
            vad = True
        except ValueError:
            vad = False
        return web.json_response(dict(version='0.1.0', online=len(hub.sessions), vad_available=vad,
            mqtt_ready=bool(hub.mqtt and hub.mqtt.ready.is_set()),
            model_validation='not_probed', sessions=[dict(id=s.id, device_id=s.device_id, state=s.state,
            wake_mode=s.wake_mode) for s in hub.sessions.values()]))
    if name == 'config':
        if request.method == 'PUT':
            value = await request.json()
            from .config import validate
            value = validate(value)
            for kind in ('asr', 'llm', 'tts'):
                status = await hub.services.status(kind)
                if status.get('pid') and value[kind]['provider'] != 'builtin':
                    return web.json_response({'error': {'code':'service_still_running',
                        'message':'请先停止内置服务，再切换提供商'}}, status=409)
            hub.config.save(value)
            hub.store.event(dict(type='config.saved', session_id=None, turn_id=None))
            hub.publish(dict(type='config.saved', timestamp=time.time()))
        return web.json_response({'config':hub.config.get(), 'applies_to':'new_connections'})
    if name == 'sessions':
        return web.json_response(hub.store.sessions(float(request.query['before']) if 'before' in request.query else None))
    if name == 'logs':
        return web.json_response(hub.store.events(request.query.get('session_id'),
            int(request.query['before']) if 'before' in request.query else None))
    if name == 'login' and request.method == 'POST':
        value = await request.json()
        token = value.get('token','') if isinstance(value, dict) else ''
        if not isinstance(token, str) or not secrets.compare_digest(token, hub.auth['admin_token']):
            raise web.HTTPUnauthorized()
        response = web.json_response({'ok':True})
        response.set_cookie('voicehub_admin', token, httponly=True, samesite='Strict',
                            secure=request.secure, max_age=86400)
        return response
    raise web.HTTPNotFound()


async def service_action(request):
    hub = request.app[HUB]
    try:
        result = await hub.services.action(request.match_info['kind'], request.match_info['action'])
    except ServiceError as exc:
        messages = {'service_port_in_use': '服务端口被其他进程占用，请先检查端口',
                    'external_service_unmanaged': '请先保存为内置服务；外部服务不能由后台控制',
                    'invalid_service_action': '不支持的服务或操作',
                    'service_start_failed': '服务启动失败，请检查服务日志'}
        return web.json_response({'error': {'code': exc.code, 'message': messages.get(exc.code, exc.code)}}, status=409)
    event = dict(type='service.changed', kind=request.match_info['kind'],
                 action=request.match_info['action'], state=result['state'], timestamp=time.time())
    hub.store.event(dict(event, session_id=None, turn_id=None))
    hub.publish(event)
    return web.json_response(result)


async def messages(request):
    return web.json_response(request.app[HUB].store.messages(request.match_info['sid'],
                             int(request.query['before']) if 'before' in request.query else None))


async def interrupt(request):
    session = request.app[HUB].sessions.get(request.match_info['sid'])
    if not session:
        raise web.HTTPNotFound()
    await session.interrupt('console')
    return web.json_response({'ok':True})


async def events(request):
    hub = request.app[HUB]
    if len(hub.watchers) >= 8:
        raise web.HTTPServiceUnavailable()
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    queue = asyncio.Queue(maxsize=256)
    hub.watchers.add(queue)
    async def writer():
        while True:
            event = await queue.get()
            if event is None:
                await ws.close()
                return
            await asyncio.wait_for(ws.send_json(event), 3)
    task = asyncio.create_task(writer())
    try:
        async for _ in ws:
            pass
    finally:
        hub.watchers.discard(queue)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return ws


async def ota(request):
    hub = request.app[HUB]
    if not secrets.compare_digest(request.match_info['token'], hub.auth['device_token']):
        raise web.HTTPNotFound()
    scheme = 'wss' if request.secure else 'ws'
    return web.json_response(dict(websocket=dict(url=f'{scheme}://{request.host}/xiaozhi/v1/',
               token=hub.auth['device_token'], version=1)))


async def mqtt_ota(request):
    """Separate per-device provisioning: shared WS tokens never grant MQTT credentials."""
    service = request.app[HUB].mqtt
    if service is None:
        raise web.HTTPNotFound()
    value = service.device_config(request.headers.get('Device-Id', ''),request.match_info['token'])
    if value is None:
        raise web.HTTPNotFound()
    return web.json_response({'mqtt': value})


def create_device_app(hub):
    """Device ingress only: no dashboard, management API or admin authentication bypass."""
    app = web.Application(middlewares=[guard], client_max_size=65536)
    app[HUB] = hub
    app[DEVICE_ONLY] = True
    app.router.add_get('/ws', gateway)
    app.router.add_get('/xiaozhi/v1/', gateway)
    app.router.add_route('*', '/xiaozhi/ota/{token}/', ota)
    app.router.add_post('/xiaozhi/mqtt-ota/{token}/', mqtt_ota)
    return app


def create_app(root, pipeline_factory=Pipeline, *, device_host=None, device_port=8766, mqtt_config=None):
    if device_host:
        address = ipaddress.ip_address(device_host)
        if not address.is_private or address.is_unspecified or address.is_multicast:
            raise ValueError('Device listener must bind a specific private interface address')
    app = web.Application(middlewares=[guard], client_max_size=65536)
    hub = app[HUB] = Hub(root, pipeline_factory)
    async def life(app):
        await hub.start()
        device_runner = None
        try:
            if mqtt_config:
                from .mqtt_service import MqttService, load_config
                hub.mqtt = MqttService(hub, load_config(mqtt_config))
                await hub.mqtt.start()
            if device_host:
                device_runner = web.AppRunner(create_device_app(hub), access_log=None, shutdown_timeout=3)
                await device_runner.setup()
                await web.TCPSite(device_runner, device_host, device_port).start()
            yield
        finally:
            if hub.mqtt:
                await hub.mqtt.close()
            await hub.close()
            if device_runner:
                await device_runner.cleanup()
    app.cleanup_ctx.append(life)
    app.router.add_get('/ws', gateway)
    app.router.add_get('/xiaozhi/v1/', gateway)
    app.router.add_route('*', '/xiaozhi/ota/{token}/', ota)
    app.router.add_get('/api/events', events)
    app.router.add_get('/api/sessions/{sid}/messages', messages)
    app.router.add_post('/api/sessions/{sid}/interrupt', interrupt)
    app.router.add_post('/api/services/{kind}/{action}', service_action)
    app.router.add_get('/api/{name}', api)
    app.router.add_put('/api/config', api)
    app.router.add_post('/api/login', api)
    async def index(request):
        return web.FileResponse(STATIC/'index.html')
    app.router.add_get('/', index)
    app.router.add_static('/static', STATIC, show_index=False)
    return app
