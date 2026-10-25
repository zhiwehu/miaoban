import tempfile
import unittest
from aiohttp.test_utils import TestClient, TestServer
from voicehub.app import create_app, create_device_app, HUB
from tests.test_sessions import Pipeline


class DeviceListenerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.control=TestClient(TestServer(create_app(self.tmp.name,pipeline_factory=lambda c,h,l:Pipeline())))
        await self.control.start_server()
        self.hub=self.control.app[HUB]
        self.device=TestClient(TestServer(create_device_app(self.hub)))
        await self.device.start_server()

    async def asyncTearDown(self):
        await self.device.close()
        await self.control.close()
        self.tmp.cleanup()

    async def test_device_listener_has_no_management_or_static_routes_even_with_admin_token(self):
        for path in ('/','/static/app.js','/api/config','/api/sessions','/api/events'):
            r=await self.device.get(path,headers={'Authorization':'Bearer '+self.hub.auth['admin_token']})
            self.assertEqual(r.status,404,path)

    async def test_device_listener_requires_device_token_even_from_loopback(self):
        ws=await self.device.ws_connect('/ws')
        await ws.send_json({'type':'hello','version':1,'device_id':'robot'})
        self.assertEqual((await ws.receive_json())['code'],'unauthorized')
        await ws.close()
        good=await self.device.ws_connect('/ws')
        await good.send_json({'type':'hello','version':1,'device_id':'robot','token':self.hub.auth['device_token']})
        hello=await good.receive_json()
        self.assertEqual(hello['type'],'hello')
        r=await self.control.get('/api/status')
        self.assertEqual((await r.json())['online'],1)
        await good.close()

    async def test_private_ota_config_points_to_device_listener(self):
        r=await self.device.post('/xiaozhi/ota/'+self.hub.auth['device_token']+'/')
        self.assertEqual(r.status,200)
        value=await r.json()
        self.assertEqual(value['websocket']['url'],str(self.device.make_url('/xiaozhi/v1/')).replace('http://','ws://'))
