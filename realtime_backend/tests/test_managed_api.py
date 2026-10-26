import copy
import tempfile
import unittest
from aiohttp.test_utils import TestClient, TestServer
from voicehub.app import create_app, HUB
from voicehub.config import DEFAULT, validate
from voicehub.models import Pipeline, ProviderError


class ManagedApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(self.tmp.name)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        if hasattr(self.app[HUB], 'services'):
            await self.app[HUB].services.shutdown(stop_workers=True)
        await self.client.close()
        self.tmp.cleanup()

    async def test_builtin_config_and_service_status(self):
        cfg = copy.deepcopy(DEFAULT)
        for kind in ('asr', 'llm', 'tts'):
            cfg[kind]['provider'] = 'builtin'
        validate(cfg)
        response = await self.client.put('/api/config', json=cfg)
        self.assertEqual(response.status, 200)
        response = await self.client.get('/api/services')
        self.assertEqual(response.status, 200)
        values = await response.json()
        self.assertEqual({v['kind'] for v in values}, {'asr', 'llm', 'tts'})
        self.assertTrue(all(v['state'] == 'stopped' for v in values))
        self.assertNotIn('token', str(values))
        response = await self.client.post('/api/services/asr/stop')
        self.assertEqual(response.status, 200)

    async def test_external_and_invalid_actions_rejected(self):
        for path in ('asr/start', 'llm/shell', 'broker/stop'):
            response = await self.client.post('/api/services/' + path)
            self.assertEqual(response.status, 409)

    async def test_stopped_builtin_does_not_fall_back_to_external_url(self):
        cfg = copy.deepcopy(DEFAULT)
        cfg['llm']['provider'] = 'builtin'
        pipeline = Pipeline(cfg, self.app[HUB].http, self.app[HUB].local)
        with self.assertRaises(ProviderError):
            async for _ in pipeline.chat([]):
                pass

    async def test_running_service_requires_stop_before_switching_provider(self):
        from unittest.mock import AsyncMock
        cfg = self.app[HUB].config.get()
        self.app[HUB].services.status = AsyncMock(return_value={'pid':123})
        response = await self.client.put('/api/config', json=cfg)
        self.assertEqual(response.status, 409)
        self.assertEqual((await response.json())['error']['code'], 'service_still_running')

    async def test_service_control_rejects_foreign_origin(self):
        response = await self.client.post('/api/services/asr/stop', headers={'Origin':'https://other.invalid'})
        self.assertEqual(response.status, 403)
