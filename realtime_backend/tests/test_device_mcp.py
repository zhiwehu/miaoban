"""Device-side tools: the robot's own MCP server must be reachable and bounded."""
import asyncio
import json
import unittest

from voicehub.device_mcp import DeviceMCP, DeviceMCPError


class FakeLink:
    def __init__(self, handler):
        self.handler = handler
        self.sent = []

    async def __call__(self, payload):
        self.sent.append(payload)
        if self.handler:
            self.handler(payload)


def reply(frames, payload):
    identity = payload['id']
    if payload['method'] == 'initialize':
        frames.feed({'id': identity, 'result': {'protocolVersion': '2024-11-05',
                                                'capabilities': {'tools': {}}, 'serverInfo': {}}})
    elif payload['method'] == 'tools/list':
        frames.feed({'id': identity, 'result': {'tools': [{'name': 'self.audio_speaker.set_volume'}]}})
    elif payload['method'] == 'tools/call':
        frames.feed({'id': identity, 'result': {'content': [{'type': 'text', 'text': 'true'}]}})


class DeviceMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_then_tool_call(self):
        frames = None
        link = FakeLink(lambda payload: reply(frames, payload))
        frames = DeviceMCP(link)
        await frames.initialize()
        self.assertTrue(frames.ready)
        result = await frames.call('self.audio_speaker.set_volume', {'volume': 30})
        self.assertEqual(result['content'][0]['text'], 'true')
        self.assertEqual([frame['method'] for frame in link.sent],
                         ['initialize', 'tools/call'])

    async def test_unknown_and_stale_replies_are_ignored(self):
        frames = DeviceMCP(FakeLink(None))
        frames.feed({'id': 999, 'result': {}})
        frames.feed('not-a-dict')
        frames.feed({'id': 'x', 'result': {}})
        self.assertEqual(frames._pending, {})

    async def test_silent_device_raises_a_timeout_code(self):
        frames = DeviceMCP(FakeLink(None), timeout=.05)
        with self.assertRaises(DeviceMCPError) as caught:
            await frames.call('self.get_device_status', {})
        self.assertEqual(caught.exception.code, 'device_timeout')

    async def test_device_error_reply_surfaces_as_a_code(self):
        frames = None

        def handler(payload):
            frames.feed({'id': payload['id'], 'error': {'message': 'Missing valid argument'}})
        frames = DeviceMCP(FakeLink(handler))
        with self.assertRaises(DeviceMCPError) as caught:
            await frames.call('self.audio_speaker.set_volume', {})
        self.assertEqual(caught.exception.code, 'device_error')

    async def test_close_fails_pending_requests(self):
        frames = DeviceMCP(FakeLink(None), timeout=2)
        pending = asyncio.create_task(frames.request('tools/list', {}))
        await asyncio.sleep(0)
        frames.close()
        with self.assertRaises(DeviceMCPError) as caught:
            await pending
        self.assertEqual(caught.exception.code, 'device_disconnected')


if __name__ == '__main__':
    unittest.main()
