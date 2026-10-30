"""MQTT control / UDP audio adapter to the existing device session handler."""
import asyncio
import contextlib
import json
import logging
import secrets
import time
from types import SimpleNamespace
from aiohttp import WSMsgType
from .app import HUB, DEVICE_ONLY, device_gateway
from .mqtt_udp import UdpAudio


class MqttSocket:
    def __init__(self, gateway, device, hello):
        self.gateway, self.device = gateway, device
        self.audio = UdpAudio(secrets.token_bytes(16), secrets.randbits(32))
        self.incoming = asyncio.Queue(maxsize=64)
        self.peer_ready = asyncio.Event()
        self.peer = self.task = None
        self.closed, self.sid = False, ''
        self.tx_samples = self.received = self.dropped = 0
        self.started = time.monotonic()
        self.feed(WSMsgType.TEXT, json.dumps(hello))

    def feed(self, kind, value):
        if self.closed:return
        if self.incoming.full():
            self.dropped += 1
            return False
        self.incoming.put_nowait(SimpleNamespace(type=kind, data=value))
        return True

    async def receive_json(self):
        message = await self.incoming.get()
        return json.loads(message.data)

    def __aiter__(self):return self

    async def __anext__(self):
        message = await self.incoming.get()
        if message.type == WSMsgType.CLOSE:raise StopAsyncIteration
        return message

    async def send_json(self, value):
        if self.closed:return
        if value['type'] == 'hello':
            self.sid = value['session_id']
            value = dict(value, version=3, transport='udp', udp=dict(
                server=self.gateway.config['udp_host'], port=self.gateway.port,
                encryption='aes-128-ctr', key=self.audio.key.hex(), nonce=self.audio.nonce.hex()))
        await asyncio.wait_for(self.gateway.publish('voicehub/'+self.device+'/down', value), 3)

    async def send_bytes(self, data):
        if self.closed:return
        await asyncio.wait_for(self.peer_ready.wait(), 2)
        if self.closed:return
        packet = self.audio.encode(data, timestamp=self.tx_samples//16)
        self.tx_samples += 960  # negotiated 16kHz / 60ms Opus frames
        self.gateway.udp.sendto(packet, self.peer)

    async def close(self, code=1000, message=b''):
        if self.closed:return
        self.closed = True
        while not self.incoming.empty():self.incoming.get_nowait()
        self.incoming.put_nowait(SimpleNamespace(type=WSMsgType.CLOSE, data=None))
        if self.sid:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.gateway.publish('voicehub/'+self.device+'/down',
                    {'type':'goodbye','session_id':self.sid}), 1)


class MqttGateway(asyncio.DatagramProtocol):
    def __init__(self, hub, config, publish):
        self.hub, self.config, self.publish = hub, config, publish
        self.channels, self.tasks, self.windows = {}, set(), {}
        self.udp, self.port = None, None

    async def start(self):
        self.udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda:self, local_addr=(self.config['udp_host'], self.config['udp_port']))
        self.port = self.udp.get_extra_info('sockname')[1]

    async def control(self, topic, payload, retained=False):
        parts = topic.split('/')
        if retained or len(parts) != 3 or parts[0] != 'voicehub' or parts[2] != 'up':return
        device = parts[1]
        if device not in self.config['devices'] or len(payload) > 32768:return
        now = time.monotonic()
        window, count = self.windows.get(device, (now, 0))
        if now-window >= 1:window, count = now, 0
        self.windows[device] = window, count+1
        if count >= 40:return
        try:
            value = json.loads(payload)
            if not isinstance(value, dict):return
            kind = value.get('type')
            if kind == 'hello':
                audio = value.get('audio_params', {})
                if (value.get('version') != 3 or value.get('transport') != 'udp' or
                    not isinstance(audio, dict) or audio.get('format') != 'opus' or
                    audio.get('sample_rate') != 16000 or audio.get('channels') != 1 or
                    audio.get('frame_duration') != 60):return
                await self.disconnect(device)
                if len(self.hub.sockets) >= 16 or len(self.hub.sessions) >= 8:return
                channel = MqttSocket(self, device, dict(value, device_id=device))
                self.channels[device] = channel
                request = SimpleNamespace(app={HUB:self.hub,DEVICE_ONLY:True}, path='/xiaozhi/v1/',
                    headers={'Authorization':'Bearer '+self.hub.auth['device_token']},
                    protocol_name='mqtt_udp')
                async def run():
                    try:
                        await device_gateway(request, channel)
                    finally:
                        if self.channels.get(device) is channel:self.channels.pop(device,None)
                        self.hub.store.event(dict(type='transport.closed', session_id=channel.sid or None,
                            turn_id=None, transport='mqtt_udp', rx_packets=channel.received,
                            tx_packets=channel.audio.tx_sequence, lost_packets=channel.audio.lost,
                            queue_dropped=channel.dropped))
                channel.task = asyncio.create_task(run())
                self.tasks.add(channel.task)
                def finished(task):
                    self.tasks.discard(task)
                    if not task.cancelled() and task.exception():
                        logging.getLogger(__name__).error('MQTT session closed after transport failure')
                channel.task.add_done_callback(finished)
            else:
                channel = self.channels.get(device)
                if not channel or (value.get('session_id') and value['session_id'] != channel.sid):return
                if kind == 'goodbye':await self.disconnect(device)
                elif not channel.feed(WSMsgType.TEXT,json.dumps(value)):await self.disconnect(device)
        except (ValueError, TypeError, UnicodeDecodeError):
            return

    def datagram_received(self, packet, addr):
        if len(packet) < 16 or len(packet) > 2064:return
        ssrc = int.from_bytes(packet[4:8],'big')
        channel = next((c for c in self.channels.values() if c.audio.ssrc == ssrc and not c.closed),None)
        if not channel or (channel.peer is not None and channel.peer != addr):return
        try:payload, _ = channel.audio.decode(packet)
        except ValueError:return
        channel.peer = addr
        channel.peer_ready.set()
        channel.received += 1
        channel.feed(WSMsgType.BINARY,payload)

    async def disconnect(self, device):
        channel = self.channels.get(device)
        if channel:
            await channel.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(channel.task), 4)
            if self.channels.get(device) is channel:self.channels.pop(device,None)

    async def close(self):
        await asyncio.gather(*(self.disconnect(d) for d in list(self.channels)))
        if self.tasks:
            for task in self.tasks:task.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)
        if self.udp:self.udp.close()
