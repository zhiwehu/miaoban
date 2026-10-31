"""Authenticated meeting-minutes HTTP API."""
import re

from aiohttp import web

from .inference import MinutesError

ERROR_STATUS = {
    'recording_not_found': 404,
    'minutes_not_found': 404,
    'minutes_template_not_found': 404,
    'minutes_job_active': 409,
    'minutes_recording_active': 409,
    'minutes_queue_full': 429,
    'minutes_transcript_empty': 400,
    'minutes_invalid_request': 400,
    'minutes_model_not_allowed': 400,
    'minutes_invalid_markdown': 400,
    'minutes_builtin_template': 400,
    'template_invalid': 400,
    'sections_invalid': 400,
    'template_exists': 409,
    'template_not_found': 404,
    'minutes_model_not_cached': 503,
    'minutes_model_load_failed': 503,
    'minutes_generation_failed': 502,
    'minutes_timeout': 504,
    'minutes_json_invalid': 502,
    'minutes_not_completed': 409,
}

MESSAGES = {
    'minutes_model_not_cached': '本地会议纪要模型没有缓存，无法生成。',
    'minutes_model_load_failed': '本地会议纪要模型加载失败。',
    'minutes_queue_full': '纪要生成队列已满，请稍后再试。',
    'minutes_generation_failed': '本地模型生成失败，请稍后重试。',
    'minutes_json_invalid': '本地模型返回的结构无法解析。',
    'minutes_timeout': '本地模型生成超时。',
    'minutes_not_found': '找不到这份会议纪要。',
    'minutes_not_completed': '会议纪要还没有生成完成。',
    'minutes_invalid_request': '请求格式无效。',
    'minutes_invalid_markdown': '纪要内容无效或过长。',
}


def _error(code, message=None, status=None):
    return web.json_response({
        'error': {'code': code, 'message': message or MESSAGES.get(code, code)}
    }, status=status or ERROR_STATUS.get(code, 400),
        headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


def _summary(job):
    keys = ('id', 'recording_id', 'template_id', 'template_version', 'template_name',
            'status', 'stage', 'progress', 'message', 'created_at', 'updated_at',
            'started_at', 'finished_at', 'model_id', 'transcript_sha256',
            'transcript_lines', 'chunks', 'chunks_failed', 'revision',
            'edited_markdown', 'error')
    summary = {key: job.get(key) for key in keys if key in job}
    summary['has_result'] = bool(job.get('result'))
    summary['stats'] = (job.get('result') or {}).get('stats') if job.get('result') else None
    return summary


async def _json(request):
    try:
        body = await request.json()
    except Exception:
        raise MinutesError('minutes_invalid_request', '请求格式无效。') from None
    if not isinstance(body, dict):
        raise MinutesError('minutes_invalid_request', '请求格式无效。')
    return body


def _validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value):
        raise MinutesError('recording_not_found', '找不到这条录音。')
    return value


async def minutes_api(request):
    from ..app import HUB
    hub = request.app[HUB]
    service = hub.minutes
    try:
        path = request.path
        if path == '/api/meeting-templates':
            if request.method == 'GET':
                return web.json_response({'templates': service.templates(),
                                          'model_id': service.engine.model_id})
            body = await _json(request)
            template = service.create_template(body)
            return web.json_response({'template': template}, status=201)
        if path.startswith('/api/meeting-templates/'):
            template_id = request.match_info.get('template_id', '')
            if request.method == 'PATCH':
                template = service.update_template(template_id, await _json(request))
                return web.json_response({'template': template})
            if request.method == 'DELETE':
                service.delete_template(template_id)
                return web.json_response({'ok': True})
            raise web.HTTPNotFound()

        recording_id = _validate_id(request.match_info.get('identity', ''))
        job_id = request.match_info.get('job_id')
        if job_id is not None and (not isinstance(job_id, str)
                                   or not re.fullmatch(r'[a-f0-9]{32}', job_id)):
            raise MinutesError('minutes_not_found')
        if path.endswith('/export'):
            if request.method != 'GET':
                raise web.HTTPNotFound()
            fmt = request.query.get('format', 'md')
            if fmt not in ('md', 'txt'):
                raise MinutesError('minutes_invalid_request', '只支持 md 或 txt 导出。')
            content, _ = service.export(recording_id, job_id, fmt)
            name = f"meeting-minutes-{recording_id[:8]}-{job_id[:8]}.{fmt}"
            return web.Response(
                text=content,
                content_type='text/markdown' if fmt == 'md' else 'text/plain',
                charset='utf-8',
                headers={'Content-Disposition': f'attachment; filename="{name}"',
                         'Cache-Control': 'no-store',
                         'X-Content-Type-Options': 'nosniff'})
        if job_id is not None:
            if request.method == 'GET':
                return web.json_response({'job': service.get_job(recording_id, job_id)})
            if request.method == 'PATCH':
                body = await _json(request)
                if set(body) != {'markdown'}:
                    raise MinutesError('minutes_invalid_request', '请求格式无效。')
                job = service.update_markdown(recording_id, job_id, body['markdown'])
                return web.json_response({'job': job})
            if request.method == 'DELETE':
                await service.delete_job(recording_id, job_id)
                return web.json_response({'ok': True})
            raise web.HTTPNotFound()
        if request.method == 'GET':
            jobs = [_summary(job) for job in service.list_jobs(recording_id)]
            return web.json_response({'jobs': jobs})
        if request.method == 'POST':
            body = await _json(request)
            if not isinstance(body.get('template_id'), str) or len(body['template_id']) > 40:
                raise MinutesError('minutes_invalid_request', '请选择有效的纪要模板。')
            extra = set(body)-{'template_id', 'model_id'}
            if extra:
                raise MinutesError('minutes_invalid_request', '请求包含未知字段。')
            if 'model_id' in body and body['model_id'] != service.engine.model_id:
                raise MinutesError('minutes_model_not_allowed', '只能使用本机配置的纪要模型。')
            job = await service.start_job(recording_id, body['template_id'])
            return web.json_response({'job': job}, status=202)
        raise web.HTTPNotFound()
    except web.HTTPException:
        raise
    except MinutesError as exc:
        return _error(exc.code, exc.message)
    except (ValueError, KeyError, TypeError) as exc:
        return _error(str(exc) if str(exc) in ERROR_STATUS else 'minutes_invalid_request')
    except OSError:
        return _error('minutes_failed', '会议纪要存储不可用。', 500)
