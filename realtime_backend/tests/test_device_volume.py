"""Volume control through the robot's device-side MCP server."""
import copy
import json
import unittest
from types import SimpleNamespace

from voicehub.config import DEFAULT
from voicehub.device_mcp import DeviceMCPError
from voicehub.tool_chat import chat


class FakeDevice:
    def __init__(self, volume=50):
        self.volume = volume
        self.calls = []

    async def call(self, name, arguments=None, timeout=None):
        self.calls.append((name, dict(arguments or {})))
        if name == 'self.get_device_status':
            return {'content': [{'type': 'text',
                                 'text': json.dumps({'audio_speaker': {'volume': self.volume}})}]}
        if name == 'self.audio_speaker.set_volume':
            self.volume = arguments['volume']
            return {'content': [{'type': 'text', 'text': 'true'}]}
        raise AssertionError(name)


def pipeline(device=None):
    config = copy.deepcopy(DEFAULT)
    config['tools'].update(device_volume=True)
    return SimpleNamespace(config=config, device=device, local=None,
                           raw_chat=lambda *a, **k: None)


async def spoken(p, text):
    items = [item async for item in chat(p, [{'role': 'user', 'content': text}])]
    said = [item for item in items if isinstance(item, str)]
    return items, said


class VolumeCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_volume_in_one_shot(self):
        device = FakeDevice(volume=10)
        items, said = await spoken(pipeline(device), '把音量调到三十')
        self.assertEqual(device.volume, 30)
        self.assertEqual(said, ['音量调到 30 了。'])
        self.assertEqual([name for name, _ in device.calls], ['self.audio_speaker.set_volume'])
        # No acknowledgement notice: a device call is short and must answer inline.
        self.assertFalse(any(type(item).__name__ == 'ToolNotice' for item in items))

    async def test_increase_and_decrease_use_the_current_level(self):
        device = FakeDevice(volume=40)
        _, said = await spoken(pipeline(device), '声音大一点')
        self.assertEqual(device.volume, 50)
        self.assertEqual(said, ['音量调到 50 了。'])
        _, said = await spoken(pipeline(device), '音量小一点')
        self.assertEqual(device.volume, 40)
        self.assertEqual(said, ['音量调到 40 了。'])

    async def test_clamped_at_the_upper_bound(self):
        device = FakeDevice(volume=100)
        _, said = await spoken(pipeline(device), '音量大一点')
        self.assertEqual(device.volume, 100)
        self.assertEqual(said, ['已经是最大声了。'])
        self.assertEqual([name for name, _ in device.calls], ['self.get_device_status'])

    async def test_mute_remembers_the_previous_level(self):
        p = pipeline(FakeDevice(volume=65))
        _, said = await spoken(p, '静音')
        self.assertEqual(p.device.volume, 0)
        self.assertEqual(said, ['静音了。'])
        _, said = await spoken(p, '取消静音')
        self.assertEqual(p.device.volume, 65)
        self.assertEqual(said, ['音量恢复到 65 了。'])

    async def test_status_reports_without_changing_anything(self):
        device = FakeDevice(volume=72)
        _, said = await spoken(pipeline(device), '现在音量多少')
        self.assertEqual(said, ['现在是 72。'])
        self.assertEqual([name for name, _ in device.calls], ['self.get_device_status'])

    async def test_missing_device_is_reported_plainly(self):
        _, said = await spoken(pipeline(None), '把音量调到 20')
        self.assertEqual(said, ['现在没有连上喵伴，音量调不了。'])

    async def test_device_timeout_is_reported_plainly(self):
        class Silent:
            async def call(self, name, arguments=None, timeout=None):
                raise DeviceMCPError('device_timeout')
        _, said = await spoken(pipeline(Silent()), '音量调到 20')
        self.assertEqual(said, ['喵伴没有响应，音量可能没变，等下再说一次。'])

    async def test_volume_disabled_falls_through_to_chat(self):
        p = pipeline(FakeDevice(volume=10))
        p.config['tools']['device_volume'] = False
        p.raw_chat = lambda history, **kwargs: _aiter(['好的。'])
        items, said = await spoken(p, '把音量调到 20')
        self.assertEqual(p.device.volume, 10)
        self.assertFalse(any(item == '音量调到 20 了。' for item in said))


async def _aiter(values):
    for value in values:
        yield value


if __name__ == '__main__':
    unittest.main()
