"""Private, immutable local backups. Device writes still require browser validation."""
import asyncio
import hashlib
import json
import os
import re
import stat
import struct
import time
import uuid
from pathlib import Path
from aiohttp import web

MAGIC = b'VOICEHUB-DEVICE/1\n'
MAX_SIZE = 33 * 1024 * 1024
ID = re.compile(r'[0-9]{20}-[a-f0-9]{32}')
HEX = re.compile(r'[a-f0-9]{64}')
MAC = re.compile(r'(?:[A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2}')


def metadata(body):
    if not body.startswith(MAGIC) or not len(MAGIC) + 4 < len(body) <= MAX_SIZE:
        raise ValueError('invalid_device_backup')
    length = struct.unpack_from('<I', body, len(MAGIC))[0]
    if not 1 <= length <= 4096 or len(MAGIC) + 4 + length >= len(body):
        raise ValueError('invalid_device_backup')
    start = len(MAGIC) + 4
    value = json.loads(body[start:start + length])
    if not isinstance(value, dict) or not MAC.fullmatch(value.get('mac', '')):
        raise ValueError('invalid_device_backup')
    if any(not isinstance(value.get(key), str) or not HEX.fullmatch(value[key]) for key in ('sha256', 'layout', 'current_sha256')):
        raise ValueError('invalid_device_backup')
    data = memoryview(body)[start + length:]
    if len(data) != {'16MB': 16 << 20, '32MB': 32 << 20}.get(value.get('flashSize'), 0):
        raise ValueError('invalid_device_backup')
    if hashlib.sha256(data).hexdigest() != value['sha256'] or hashlib.sha256(data[0x8000:0x8000 + 3072]).hexdigest() != value['layout']:
        raise ValueError('backup_checksum_failed')
    return value


def device_folder(root, mac):
    folder = Path(root) / 'device-backups' / mac.replace(':', '').upper()
    for path in (folder.parent, folder):
        path.mkdir(mode=0o700, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise OSError('invalid_backup_directory')
        path.chmod(0o700)
    return folder


def read_private(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SIZE:
            raise ValueError('invalid_device_backup')
        return stream.read(MAX_SIZE + 1)


def save(root, body):
    value = metadata(body)
    folder = device_folder(root, value['mac'])
    identity = f'{time.time_ns():020d}-{uuid.uuid4().hex}'
    temporary, target = folder / (identity + '.partial'), folder / (identity + '.vhdevice')
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if read_private(temporary) != body:
            raise OSError('backup_readback_failed')
        os.replace(temporary, target)
        directory = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return identity


def latest(root, query, identity=None):
    folder = device_folder(root, query['mac'])
    paths = [folder / (identity + '.vhdevice')] if identity else sorted(folder.glob('*.vhdevice'), reverse=True)
    for path in paths:
        identity = path.stem
        if not ID.fullmatch(identity) or query.get('before') and identity >= query['before']:
            continue
        try:
            body = read_private(path)
            value = metadata(body)
            if value['mac'].upper() == query['mac'].upper() and all(value[key] == query[key] for key in ('layout', 'flashSize')):
                return identity, body
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            continue
    return None


async def backups(request):
    from .app import HUB, local_admin, same_origin
    if not local_admin(request):
        raise web.HTTPUnauthorized()
    if request.headers.get('Origin') and not same_origin(request, request.headers['Origin']):
        raise web.HTTPForbidden()
    root = request.app[HUB].root
    try:
        if request.method == 'POST':
            if request.content_type != 'application/octet-stream':
                raise ValueError('invalid_device_backup')
            body = await request.clone(client_max_size=MAX_SIZE).read()
            identity = await asyncio.to_thread(save, root, body)
            return web.json_response({'id': identity}, status=201)
        query = dict(request.query)
        if set(query) - {'mac', 'layout', 'flashSize', 'before'} or not MAC.fullmatch(query.get('mac', '')) or not HEX.fullmatch(query.get('layout', '')) or query.get('flashSize') not in ('16MB', '32MB') or 'before' in query and not ID.fullmatch(query['before']):
            raise ValueError('invalid_backup_identity')
        identity = request.match_info.get('identity')
        if identity and not ID.fullmatch(identity):
            raise ValueError('invalid_backup_identity')
        result = await asyncio.to_thread(latest, root, query, identity)
        if not result:
            return web.json_response({'error': {'code': 'backup_not_found', 'message': '这台喵伴还没有可用的自动备份。'}}, status=404)
        identity, body = result
        return web.Response(body=body, content_type='application/octet-stream', headers={'X-Backup-Id': identity})
    except (ValueError, TypeError, KeyError, UnicodeError):
        return web.json_response({'error': {'code': 'invalid_device_backup', 'message': '备份格式、设备信息或校验无效。'}}, status=400)
    except OSError:
        return web.json_response({'error': {'code': 'backup_storage_failed', 'message': '本机备份保存或读取失败，请检查磁盘空间和权限。'}}, status=507)
