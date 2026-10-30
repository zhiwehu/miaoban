import tempfile
import unittest
from aiohttp.test_utils import TestClient, TestServer
from voicehub.app import HUB,create_app,create_device_app
from voicehub.mqtt_service import MqttService
from tests.test_sessions import Pipeline


class MqttProvisioningRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.control=TestClient(TestServer(create_app(self.tmp.name,pipeline_factory=lambda c,h,l:Pipeline())))
        await self.control.start_server()
        self.hub=self.control.app[HUB]
        self.hub.mqtt=MqttService(self.hub,{'broker_host':'127.0.0.1','broker_port':1883,
            'udp_host':'127.0.0.1','udp_port':0,'devices':{
                'cat':{'username':'cat','password':'test-cat-password','provision_token':'a'*43,'enabled':True},
                'other':{'username':'other','password':'test-other-password','provision_token':'b'*43,'enabled':True}}})
        self.hub.mqtt.ready.set()
        self.device=TestClient(TestServer(create_device_app(self.hub)))
        await self.device.start_server()

    async def asyncTearDown(self):
        await self.device.close();await self.control.close();self.tmp.cleanup()

    async def test_mqtt_credentials_require_both_per_device_secret_and_device_identity(self):
        for token,device in [('wrong','cat'),('a'*43,''),('a'*43,'other'),
                             (self.hub.auth['device_token'],'cat'),('b'*43,'cat')]:
            r=await self.device.post('/xiaozhi/mqtt-ota/'+token+'/',headers={'Device-Id':device})
            self.assertEqual(r.status,404)
            self.assertNotIn('test-cat-password',await r.text())
        r=await self.device.post('/xiaozhi/mqtt-ota/'+'a'*43+'/',headers={'Device-Id':'cat'})
        self.assertEqual(r.status,200)
        self.assertEqual((await r.json())['mqtt']['password'],'test-cat-password')

    async def test_existing_shared_ota_never_discloses_mqtt_credentials(self):
        r=await self.device.post('/xiaozhi/ota/'+self.hub.auth['device_token']+'/',headers={'Device-Id':'cat'})
        self.assertEqual(r.status,200)
        self.assertEqual(set(await r.json()),{'websocket'})
        r=await self.control.get('/api/config')
        self.assertNotIn('test-cat-password',await r.text())
