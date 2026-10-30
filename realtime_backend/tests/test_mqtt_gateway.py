import asyncio
import json
import tempfile
import unittest
from voicehub.app import HUB, create_app
from aiohttp.test_utils import TestClient, TestServer
from voicehub.mqtt_gateway import MqttGateway
from tests.test_sessions import Pipeline
from voicehub.mqtt_udp import HEADER
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class Receiver(asyncio.DatagramProtocol):
    def __init__(self):self.packets = asyncio.Queue()
    def datagram_received(self, data, addr):self.packets.put_nowait(data)


class MqttGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(create_app(self.tmp.name,pipeline_factory=lambda c,h,l:Pipeline())))
        await self.client.start_server()
        self.hub = self.client.app[HUB]
        self.out = asyncio.Queue()
        async def publish(topic, value):await self.out.put((topic,value))
        self.gateway = MqttGateway(self.hub, {'udp_host':'127.0.0.1','udp_port':0,
            'devices':{'cat':{'password':'test-only'},'other':{'password':'test-only'}}}, publish)
        await self.gateway.start()
        self.receiver = Receiver()
        self.udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda:self.receiver,local_addr=('127.0.0.1',0))

    async def asyncTearDown(self):
        self.udp.close()
        await self.gateway.close()
        await self.client.close()
        self.tmp.cleanup()

    async def control(self, value, device='cat', retained=False):
        await self.gateway.control('voicehub/'+device+'/up',json.dumps(value).encode(),retained)

    async def event(self, kind):
        async with asyncio.timeout(2):
            while True:
                topic,value=await self.out.get()
                if value['type']==kind:return topic,value

    async def hello(self, device='cat'):
        await self.control({'type':'hello','version':3,'transport':'udp','features':{'playback_ready':True},
            'audio_params':{'format':'opus','sample_rate':16000,'channels':1,'frame_duration':60}},device)
        _,hello=await self.event('hello')
        return hello

    async def test_mqtt_reuses_session_history_and_waits_for_playback_ready(self):
        hello=await self.hello()
        channel=self.gateway.channels['cat']
        self.assertIn(hello['session_id'],self.hub.sessions)
        self.hub.sessions[hello['session_id']].pipeline.release.set()
        # A first uplink datagram learns the actual client UDP port while still in standby.
        header=HEADER.pack(1,0,1,channel.audio.ssrc,0,1)
        cipher=Cipher(algorithms.AES(channel.audio.key),modes.CTR(header)).encryptor()
        packet=header+cipher.update(b'x')+cipher.finalize()
        self.udp.sendto(packet,('127.0.0.1',self.gateway.port))
        await asyncio.wait_for(channel.peer_ready.wait(),1)
        await self.control({'type':'input.text','text':'MQTT测试'})
        _,start=await self.event('tts')
        self.assertEqual(start['state'],'start')
        self.assertTrue(self.receiver.packets.empty())
        await self.control({'type':'tts','state':'ready','turn_id':start['turn_id'],'device_state':6,
            'output_enabled':True,'rx_audio_packets':0,'dropped_audio_packets':0,'decoded_audio_packets':0,'played_audio_packets':0})
        packet=await asyncio.wait_for(self.receiver.packets.get(),2)
        self.assertEqual(packet[1],1)
        self.assertGreater(len(packet),16)
        messages=self.hub.store.messages(hello['session_id'])
        self.assertEqual(messages[0]['content'],'MQTT测试')
        await self.control({'type':'goodbye','session_id':hello['session_id']})
        self.assertNotIn('cat',self.gateway.channels)
        self.assertNotIn(hello['session_id'],self.hub.sessions)

    async def test_retained_unknown_and_malformed_controls_do_not_create_sessions(self):
        await self.control({'type':'hello'},retained=True)
        await self.control({'type':'hello'},device='unknown')
        await self.gateway.control('voicehub/cat/up',b'{broken',False)
        await self.control({'type':'hello','version':3,'transport':'udp','audio_params':{'sample_rate':24000}})
        self.assertEqual(self.gateway.channels,{})
        self.assertEqual(self.hub.sessions,{})

    async def test_reconnect_replaces_session_and_old_keys_are_invalid(self):
        first=await self.hello()
        second=await self.hello()
        self.assertNotEqual(first['session_id'],second['session_id'])
        self.assertNotEqual(first['udp']['key'],second['udp']['key'])
        self.assertNotEqual(first['udp']['nonce'],second['udp']['nonce'])
        self.assertNotIn(first['session_id'],self.hub.sessions)
        await self.control({'type':'goodbye','session_id':first['session_id']})
        self.assertIn('cat',self.gateway.channels)
        other=await self.hello('other')
        await self.control({'type':'goodbye','session_id':second['session_id']})
        self.assertIn(other['session_id'],self.hub.sessions)

    async def test_peer_cannot_be_rebound_by_another_udp_source(self):
        await self.hello()
        channel=self.gateway.channels['cat']
        def packet(seq):
            from voicehub.mqtt_udp import HEADER
            h=HEADER.pack(1,0,1,channel.audio.ssrc,0,seq)
            return h+channel.audio._crypt(h,b'x')
        self.gateway.datagram_received(packet(1),('127.0.0.1',10000))
        self.gateway.datagram_received(packet(2),('127.0.0.1',20000))
        self.assertEqual(channel.peer,('127.0.0.1',10000))
        self.assertEqual(channel.audio.rx_sequence,1)

    async def test_idle_closes_audio_channel_and_returns_device_to_standby(self):
        import time
        hello=await self.hello()
        session=self.hub.sessions[hello['session_id']]
        await session.activate()
        session.last_activity=time.monotonic()-31
        await session.idle()
        _,goodbye=await self.event('goodbye')
        self.assertEqual(goodbye['session_id'],hello['session_id'])
        async with asyncio.timeout(2):
            while hello['session_id'] in self.hub.sessions:await asyncio.sleep(.01)
