"""Authenticated local recording API; audio stays on this machine.

The web client uses these endpoints to stop a recording even when its session
WebSocket already dropped, and to list/download/delete the WAV files.  Starting
still requires the owning session because the recording is fed by that socket.
"""
import re
import time

from aiohttp import web

ERRORS = {
    'recording_already_active': '这个会话已经在录音了。',
    'recording_active': '正在录音的文件不能删除，请先停止。',
    'recording_disk_full': '磁盘剩余空间不足，无法开始录音。',
    'recording_storage_unavailable': '录音目录不可用，请检查数据目录权限。',
    'recording_not_found': '找不到这条录音。',
    'recording_unavailable': '后端录音功能未启用。',
    'session_closed': '会话已断开，无法开始录音。',
    'invalid_request': '请求格式无效。',
}


def error(code, status=400):
    return web.json_response({'error': {'code': code, 'message': ERRORS.get(code, code)}},
                             status=status)


async def recording_api(request):
    from .app import HUB
    hub = request.app[HUB]
    manager = hub.recordings
    try:
        if request.path == '/api/recordings':
            if request.method == 'GET':
                items = manager.list()
                session_id = request.query.get('session_id')
                if session_id:
                    items = [item for item in items if item.get('session_id') == session_id]
                return web.json_response({'recordings': items})
            body = await request.json()
            if not isinstance(body, dict) or set(body) != {'session_id'}:
                raise ValueError('invalid_request')
            session_id = body.get('session_id')
            if not isinstance(session_id, str) or not re.fullmatch(r'[a-f0-9]{32}', session_id):
                raise ValueError('invalid_request')
            session = hub.sessions.get(session_id)
            if session is None:
                raise web.HTTPNotFound()
            if session.recording is not None:
                raise ValueError('recording_already_active')
            recording = await session.start_recording('web')
            return web.json_response({'recording': recording.metadata()}, status=201)

        identity = request.match_info.get('identity', '')
        metadata = manager.get(identity)
        if request.path.endswith('/audio'):
            if metadata is None or metadata.get('status') == 'active':
                raise web.HTTPNotFound()
            path = manager.audio_path(identity)
            if path is None:
                raise web.HTTPNotFound()
            return web.FileResponse(path, headers={
                'Content-Type': 'audio/wav',
                'Content-Disposition': f'attachment; filename="voicehub-recording-{identity[:8]}.wav"',
                'Cache-Control': 'no-store',
            })
        if request.path.endswith('/stop'):
            if metadata is None:
                raise web.HTTPNotFound()
            if metadata.get('status') != 'active':
                return web.json_response({'recording': metadata})
            session = hub.sessions.get(metadata.get('session_id'))
            if session is not None and session.recording is not None and session.recording.id == identity:
                result = await session.stop_recording('web', speak=False)
            else:
                result = manager.stop(identity, 'web')
                if result is not None:
                    hub.publish({'type': 'recording.stopped', 'session_id': result.get('session_id'),
                                 'turn_id': None, 'recording': result, 'timestamp': time.time()})
            result = result or manager.get(identity)
            if result is None:
                raise web.HTTPNotFound()
            return web.json_response({'recording': result})
        if request.method == 'DELETE':
            manager.delete(identity)
            hub.publish({'type': 'recording.deleted', 'recording_id': identity,
                         'session_id': None, 'turn_id': None, 'timestamp': time.time()})
            return web.json_response({'ok': True})
        raise web.HTTPNotFound()
    except web.HTTPException:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        code = str(exc)
        if code not in ERRORS:
            code = 'invalid_request'
        status = 409 if code in ('recording_already_active', 'recording_active') else (
            404 if code == 'recording_not_found' else 400)
        return error(code, status)
    except OSError:
        return error('recording_storage_unavailable', 500)
