import copy
import json
import stat
import tempfile
import unittest
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from voicehub.app import create_app, HUB
from tests import test_mqtt_security


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root/'mqtt/config.json'
        self.path.parent.mkdir()
        self.cfg = test_mqtt_security.MqttSecurityTests().config()
        self.path.write_text(json.dumps(self.cfg)); self.path.chmod(0o600)
        self.client = TestClient(TestServer(create_app(self.root)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def test_status_masks_secrets_and_marks_websocket_protocol(self):
        r = await self.client.get('/api/connections')
        self.assertEqual(r.status,200)
        data = await r.json()
        self.assertEqual(data['mqtt']['state'],'stopped')
        self.assertTrue(data['mqtt']['configured'])
        self.assertNotIn('password',json.dumps(data))
        self.assertNotIn('provision_token',json.dumps(data))
        ws = await self.client.ws_connect('/ws')
        await ws.send_json({'type':'hello','version':1,'device_id':'web-test',
            'audio':{'format':'pcm_s16le','sample_rate':16000}})
        await ws.receive_json()
        data = await (await self.client.get('/api/connections')).json()
        self.assertEqual(data['devices'][0]['protocol'],'websocket_v1')
        await ws.close()

    async def test_save_preserves_credentials_and_rejects_secret_changes(self):
        data = await (await self.client.get('/api/connections')).json()
        value = data['mqtt']['config']
        value['broker_port'] = 1884
        r = await self.client.put('/api/connections/mqtt',json=value)
        self.assertEqual(r.status,200)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['broker_port'],1884)
        self.assertEqual(saved['devices'],self.cfg['devices'])
        self.assertEqual(saved['password'],self.cfg['password'])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode),0o600)
        value['password']='replacement'
        self.assertEqual((await self.client.put('/api/connections/mqtt',json=value)).status,400)

    async def test_controls_stop_is_idempotent_and_cross_origin_denied(self):
        self.assertEqual((await self.client.post('/api/connections/mqtt/stop')).status,200)
        self.assertEqual((await self.client.post('/api/connections/mqtt/shell')).status,400)
        r = await self.client.post('/api/connections/mqtt/start',headers={'Origin':'https://foreign.invalid'})
        self.assertEqual(r.status,403)

    async def test_bind_failure_does_not_break_console(self):
        from unittest.mock import patch, AsyncMock
        with patch('voicehub.mqtt_service.MqttService.start',new=AsyncMock(side_effect=OSError(49,'unavailable'))):
            r = await self.client.post('/api/connections/mqtt/start')
            self.assertEqual(r.status,409)
        data = await (await self.client.get('/api/connections')).json()
        self.assertEqual(data['mqtt']['error'],'udp_address_unavailable')
        self.assertEqual((await self.client.get('/api/status')).status,200)

    async def test_device_listener_controls_keep_console_and_other_connections_alive(self):
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        r=await self.client.put('/api/connections/websocket',json={'host':'127.0.0.1','port':port})
        self.assertEqual(r.status,200)
        r=await self.client.post('/api/connections/websocket/start')
        self.assertEqual(r.status,200)
        ws=await self.client.ws_connect('/ws')
        await ws.send_json({'type':'hello','version':1,'device_id':'console-peer'})
        await ws.receive_json()
        r=await self.client.get('/api/clients/echoear-s3/connection?protocol=xiaozhi_websocket')
        self.assertEqual(r.status,200)
        self.assertEqual((await r.json())['websocket']['token'],self.client.app[HUB].auth['device_token'])
        self.assertIn('attachment',r.headers['Content-Disposition'])
        self.assertEqual((await self.client.put('/api/connections/websocket',json={'host':'127.0.0.1','port':port})).status,409)
        await self.client.post('/api/connections/websocket/stop')
        self.assertFalse(ws.closed)
        await ws.send_json({'type':'ping'})
        import asyncio
        async with asyncio.timeout(1):
            while (await ws.receive_json())['type'] != 'pong': pass
        await ws.close()
        self.assertEqual((await self.client.get('/api/status')).status,200)

    async def test_firmware_download_requires_a_matching_registered_artifact(self):
        import hashlib
        root=self.root/'firmware/echoear-s3';root.mkdir(parents=True)
        r=await self.client.get('/api/connections')
        self.assertEqual([c['id'] for c in (await r.json())['clients']],['web','echoear-s3'])
        self.assertEqual((await self.client.get('/api/clients/echoear-s3/firmware')).status,404)
        binary=b'\xe9'+b'\0'*1023
        (root/'application.bin').write_bytes(binary)
        manifest={'board':'echoear-s3','kind':'application','file':'application.bin',
            'version':'test','sha256':hashlib.sha256(binary).hexdigest()}
        (root/'manifest.json').write_text(json.dumps(manifest))
        self.assertEqual((await self.client.get('/api/clients/echoear-s3/firmware')).status,200)
        (root/'application.bin').write_bytes(binary+b'changed')
        self.assertEqual((await self.client.get('/api/clients/echoear-s3/firmware')).status,404)

    async def test_management_and_connection_download_require_administrator(self):
        from unittest.mock import patch
        with patch('voicehub.app.admin',return_value=False):
            for path in ('/api/connections','/api/clients/echoear-s3/connection?protocol=xiaozhi_websocket'):
                self.assertEqual((await self.client.get(path)).status,401)
