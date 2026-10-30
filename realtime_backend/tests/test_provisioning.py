import unittest
import tempfile
import socket
from unittest.mock import patch
from aiohttp.test_utils import TestClient,TestServer
from voicehub.app import create_app,HUB

class Provisioning(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.client=TestClient(TestServer(create_app(self.tmp.name)));await self.client.start_server()
 async def asyncTearDown(self):
  await self.client.close();self.tmp.cleanup()
 async def test_prepare_starts_authenticated_listener_and_is_idempotent(self):
  hub=self.client.app[HUB]
  with socket.socket() as probe:probe.bind(('127.0.0.1',0));hub.connections.device_port=probe.getsockname()[1]
  with patch('voicehub.provisioning.interfaces',return_value=[{'interface':'fixture','address':'127.0.0.1'}]),patch('voicehub.provisioning.default_address',return_value='127.0.0.1'):
   r=await self.client.post('/api/clients/echoear-s3/prepare',json={});self.assertEqual(r.status,200)
   cfg=await r.json();runner=hub.connections.device_runner
   self.assertTrue(hub.connections.device_ready);self.assertEqual(cfg['websocket']['token'],hub.auth['device_token'])
   self.assertEqual(r.headers['Cache-Control'],'no-store')
   self.assertEqual((await self.client.post('/api/clients/echoear-s3/prepare',json={})).status,200)
   self.assertIs(hub.connections.device_runner,runner)
   ota=await self.client.session.get(cfg['ota_url']);self.assertEqual(ota.status,200)
   denied=await self.client.session.get(cfg['ota_url'].replace(hub.auth['device_token'],'wrong'));self.assertEqual(denied.status,404)
 async def test_ambiguous_network_requires_selection_and_unknown_fields_rejected(self):
  choices=[{'interface':'en0','address':'192.168.1.10'},{'interface':'en1','address':'10.0.0.10'}]
  with patch('voicehub.provisioning.interfaces',return_value=choices),patch('voicehub.provisioning.default_address',return_value=None):
   r=await self.client.post('/api/clients/echoear-s3/prepare',json={});self.assertEqual(r.status,409);self.assertEqual((await r.json())['interfaces'],choices)
   self.assertEqual((await self.client.post('/api/clients/echoear-s3/prepare',json={'host':'8.8.8.8'})).status,400)
   self.assertEqual((await self.client.post('/api/clients/echoear-s3/prepare',json={'password':'never-send'})).status,400)
  self.assertFalse(self.client.app[HUB].connections.device_ready)
 async def test_cross_origin_and_untrusted_host_cannot_enable_listener(self):
  self.assertEqual((await self.client.post('/api/clients/echoear-s3/prepare',json={},headers={'Origin':'https://foreign.invalid'})).status,403)
  self.assertEqual((await self.client.post('/api/clients/echoear-s3/prepare',json={},headers={'Host':'foreign.invalid'})).status,401)
  self.assertFalse(self.client.app[HUB].connections.device_ready)
