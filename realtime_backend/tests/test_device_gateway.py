"""The device MCP bridge over the real gateway: framing, correlation, volume."""
import asyncio
import tempfile
import unittest

from aiohttp.test_utils import TestClient, TestServer

from tests.test_sessions import Pipeline
from voicehub.app import HUB, create_app


class DeviceGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name, pipeline_factory=lambda c, h, l: Pipeline())
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def connect(self, device='echoear-mcp'):
        ws = await self.client.ws_connect('/ws')
        await ws.send_json({'type': 'hello', 'version': 1, 'device_id': device,
                            'audio': {'format': 'pcm_s16le', 'sample_rate': 16000},
                            'capabilities': {'echo_cancellation': True}})
        hello = await ws.receive_json()
        self.assertEqual(hello['type'], 'hello')
        return ws, self.app[HUB].sessions[hello['session_id']]

    async def serve_device(self, ws, volume):
        """Answer the bridge like the firmware's MCP server does."""
        async for message in ws:
            if message.type.name != 'TEXT':
                continue
            frame = message.json()
            if frame.get('type') != 'mcp':
                continue
            payload = frame['payload']
            method, identity = payload.get('method'), payload.get('id')
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05',
                          'capabilities': {'tools': {}}, 'serverInfo': {'name': 'echoear'}}
            elif method == 'tools/call':
                name = payload['params']['name']
                if name == 'self.get_device_status':
                    text = '{"audio_speaker": {"volume": %d}}' % volume
                elif name == 'self.audio_speaker.set_volume':
                    volume = payload['params']['arguments']['volume']
                    text = 'true'
                else:
                    text = 'unsupported'
                result = {'content': [{'type': 'text', 'text': text}]}
            else:
                result = {'tools': []}
            await ws.send_json({'session_id': frame.get('session_id'), 'type': 'mcp',
                                'payload': {'jsonrpc': '2.0', 'id': identity, 'result': result}})

    async def test_volume_runs_through_the_real_device_channel(self):
        from voicehub.mcp_tools import device_volume
        ws, session = await self.connect()
        device = session.pipeline.device
        server = asyncio.create_task(self.serve_device(ws, 40))
        try:
            result, sentence = await asyncio.wait_for(
                device_volume.run(session.pipeline, {'action': 'set', 'value': 30}), 3)
            self.assertEqual(result['volume'], 30)
            self.assertEqual(sentence, '音量调到 30 了。')

            result, sentence = await asyncio.wait_for(
                device_volume.run(session.pipeline, {'action': 'up'}), 3)
            self.assertEqual(result['volume'], 40)
            self.assertEqual(sentence, '音量调到 40 了。')

            _, sentence = await asyncio.wait_for(
                device_volume.run(session.pipeline, {'action': 'status'}), 3)
            self.assertEqual(sentence, '现在是 40。')
        finally:
            server.cancel()
            await asyncio.gather(server, return_exceptions=True)
            await ws.close()

    async def test_unresponsive_device_surfaces_a_timeout_code(self):
        from voicehub.device_mcp import DeviceMCPError
        from voicehub.mcp_tools import device_volume
        ws, session = await self.connect()
        session.pipeline.device.timeout = .2
        try:
            with self.assertRaises(DeviceMCPError) as caught:
                await device_volume.run(session.pipeline, {'action': 'status'})
            self.assertEqual(caught.exception.code, 'device_timeout')
        finally:
            await ws.close()


if __name__ == '__main__':
    unittest.main()
