"""Real Mosquitto authentication, ACL, shared-session and reconnect integration."""
import asyncio
import contextlib
import json
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import tempfile
import unittest
import aiomqtt
from aiohttp.test_utils import TestClient, TestServer
from voicehub.app import HUB,create_app
from tests.test_sessions import Pipeline


class MqttBrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        with socket.socket() as s:s.bind(('127.0.0.1',0));self.port=s.getsockname()[1]
        setup=runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/setup_mqtt.py'))['create_config']
        self.cfg=setup(root/'mqtt','127.0.0.1',['cat','other'],self.port,0)
        self.broker=subprocess.Popen([shutil.which('mosquitto'),'-c',str(root/'mqtt/mosquitto.conf')],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        async with asyncio.timeout(3):
            while True:
                try:
                    reader,writer=await asyncio.open_connection('127.0.0.1',self.port)
                    writer.close();await writer.wait_closed();break
                except OSError:await asyncio.sleep(.02)
        self.web=TestClient(TestServer(create_app(root/'hub',pipeline_factory=lambda c,h,l:Pipeline(),
            mqtt_config=root/'mqtt/config.json')))
        await self.web.start_server()
        self.service=self.web.app[HUB].mqtt
        await asyncio.wait_for(self.service.ready.wait(),3)

    async def asyncTearDown(self):
        if hasattr(self,'web'):await self.web.close()
        self.broker.terminate();await asyncio.to_thread(self.broker.wait,3)
        self.tmp.cleanup()

    def client(self,device='cat',password=None):
        item=self.cfg['devices'][device]
        return aiomqtt.Client('127.0.0.1',self.port,username=item['username'],
            password=password or item['password'],identifier='test_'+device,timeout=3)

    async def test_authenticated_mqtt_creates_shared_session_and_isolated_downlink(self):
        async with self.client() as client:
            await client.subscribe('voicehub/cat/down')
            await client.publish('voicehub/cat/up',json.dumps({'type':'hello','version':3,'transport':'udp',
                'features':{'playback_ready':True},'audio_params':{'format':'opus','sample_rate':16000,
                'channels':1,'frame_duration':60}}))
            async with asyncio.timeout(3):
                message=await anext(client.messages)
            hello=json.loads(message.payload)
            self.assertEqual(hello['transport'],'udp')
            self.assertEqual(len(bytes.fromhex(hello['udp']['key'])),16)
            sid=hello['session_id']
            self.assertIn(sid,self.web.app[HUB].sessions)
            await client.publish('voicehub/cat/up',json.dumps({'type':'goodbye','session_id':sid}))
            async with asyncio.timeout(2):
                while sid in self.web.app[HUB].sessions:await asyncio.sleep(.01)

    async def test_bad_password_and_cross_device_publish_are_denied(self):
        with self.assertRaises(aiomqtt.MqttError):
            async with self.client(password='wrong-password'):pass
        async with self.client() as cat, self.client('other') as other:
            await other.subscribe('voicehub/other/down')
            hello=json.dumps({'type':'hello','version':3,'transport':'udp',
                'audio_params':{'format':'opus','sample_rate':16000,'channels':1,'frame_duration':60}})
            await cat.publish('voicehub/other/up',hello,qos=1)
            with self.assertRaises(TimeoutError):
                async with asyncio.timeout(.1):await anext(other.messages)
            self.assertEqual(self.service.gateway.channels,{})
            await other.publish('voicehub/other/up',hello,qos=1)
            async with asyncio.timeout(2):reply=json.loads((await anext(other.messages)).payload)
            self.assertEqual(reply['type'],'hello')

    async def test_broker_loss_clears_sessions_then_service_recovers(self):
        async with self.client() as client:
            await client.subscribe('voicehub/cat/down')
            await client.publish('voicehub/cat/up',json.dumps({'type':'hello','version':3,'transport':'udp',
                'audio_params':{'format':'opus','sample_rate':16000,'channels':1,'frame_duration':60}}))
            async with asyncio.timeout(3):hello=json.loads((await anext(client.messages)).payload)
            self.broker.terminate();await asyncio.to_thread(self.broker.wait,3)
            with self.assertRaises(aiomqtt.MqttError):
                async with asyncio.timeout(2):await anext(client.messages)
            async with asyncio.timeout(3):
                while self.service.ready.is_set() or self.service.gateway.channels:await asyncio.sleep(.02)
            self.assertNotIn(hello['session_id'],self.web.app[HUB].sessions)
        self.broker=subprocess.Popen([shutil.which('mosquitto'),'-c',str(Path(self.tmp.name)/'mqtt/mosquitto.conf')],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        await asyncio.wait_for(self.service.ready.wait(),4)

    async def test_console_can_stop_restart_gateway_without_restarting_backend(self):
        response=await self.web.post('/api/connections/mqtt/stop')
        self.assertEqual(response.status,200)
        self.assertIsNone(self.web.app[HUB].mqtt)
        response=await self.web.post('/api/connections/mqtt/start')
        self.assertEqual(response.status,200)
        await asyncio.wait_for(self.web.app[HUB].mqtt.ready.wait(),3)
        response=await self.web.get('/api/connections')
        self.assertEqual((await response.json())['mqtt']['state'],'running')
