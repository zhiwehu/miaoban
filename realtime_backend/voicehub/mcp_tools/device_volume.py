"""Control the robot's own speaker through the device-side MCP server.

The firmware exposes `self.audio_speaker.set_volume`（0–100）and
`self.get_device_status`, whose payload looks like::

    {"audio_speaker": {"volume": 70}, "screen": {...}, "battery": {...}}

Unlike the bundled tools this one cannot live in the stdio tool subprocess:
it has to talk to the WebSocket of the device in the current session. The
session attaches that bridge as ``pipeline.device``.
"""
import json

from ..device_mcp import DeviceMCPError

SET_VOLUME = 'self.audio_speaker.set_volume'
DEVICE_STATUS = 'self.get_device_status'
MINIMUM, MAXIMUM, STEP = 0, 100, 10

SCHEMA = ('调整喵伴机器人扬声器的音量。action=set 时需要 value（0-100）；'
          'action=up/down 表示在当前音量基础上加减，value 可给步长；'
          'action=mute/unmute 静音或恢复；action=status 查询当前音量。', {
    'action': {'type': 'string', 'enum': ['set', 'up', 'down', 'mute', 'unmute', 'status']},
    'value': {'type': 'integer', 'minimum': 0, 'maximum': 100,
              'description': '目标音量；up/down 时作为步长，缺省 10'},
})
REQUIRED = ['action']
ACKNOWLEDGEMENT = '我看一眼音量。'
DEVICE_TOOL = True   # 在服务进程内执行，不经过 tools 子进程


def clamp(value):
    return max(MINIMUM, min(MAXIMUM, int(value)))


def _text(result):
    for item in (result or {}).get('content') or []:
        if isinstance(item, dict) and item.get('type') == 'text':
            return item.get('text') or ''
    return ''


async def read_volume(device):
    payload = _text(await device.call(DEVICE_STATUS))
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        raise DeviceMCPError('device_status_invalid') from None
    volume = (data.get('audio_speaker') or {}).get('volume') if isinstance(data, dict) else None
    if not isinstance(volume, int):
        raise DeviceMCPError('device_status_invalid')
    return clamp(volume)


async def set_volume(device, value):
    target = clamp(value)
    await device.call(SET_VOLUME, {'volume': target})
    return target


async def run(pipeline, arguments):
    """Execute one volume command; return (result, spoken sentence)."""
    device = getattr(pipeline, 'device', None)
    if device is None:
        raise DeviceMCPError('device_unavailable')
    action = str(arguments.get('action') or 'status')
    if action not in ('set', 'up', 'down', 'mute', 'unmute', 'status'):
        raise DeviceMCPError('invalid_volume_action')

    raw = arguments.get('value')
    value = clamp(raw) if isinstance(raw, int) and not isinstance(raw, bool) else None

    if action == 'status':
        current = await read_volume(device)
        return {'action': action, 'volume': current}, f'现在是 {current}。'

    if action == 'set':
        if value is None:
            raise DeviceMCPError('volume_value_required')
        level = await set_volume(device, value)
        return {'action': action, 'volume': level}, f'音量调到 {level} 了。'

    if action in ('up', 'down'):
        step = value or STEP
        length = min(MAXIMUM, max(MINIMUM, step))
        current = await read_volume(device)
        level = clamp(current + length if action == 'up' else current - length)
        if level == current:
            sentence = '已经是最大声了。' if action == 'up' else '已经是最小声了。'
            return {'action': action, 'volume': level}, sentence
        await set_volume(device, level)
        return {'action': action, 'volume': level}, f'音量调到 {level} 了。'

    if action == 'mute':
        current = await read_volume(device)
        if current > MINIMUM:
            setattr(pipeline, '_volume_before_mute', current)
        await set_volume(device, MINIMUM)
        return {'action': action, 'volume': MINIMUM}, '静音了。'

    # unmute
    remembered = getattr(pipeline, '_volume_before_mute', None)
    level = await set_volume(device, remembered if isinstance(remembered, int) and remembered > 0 else 50)
    return {'action': action, 'volume': level}, f'音量恢复到 {level} 了。'
