"""Optional LAN MQTT service. Each device has an independent provisioning secret."""
import asyncio
import ipaddress
import json
import logging
import re
import secrets
import stat
from pathlib import Path
from .mqtt_gateway import MqttGateway

LOG = logging.getLogger(__name__)


def load_config(path):
    path = Path(path)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('MQTT credentials file must have permissions 0600')
    cfg = json.loads(path.read_text())
    return validate_config(cfg)


def validate_config(cfg):
    for field in ('broker_host','udp_host'):
        address = ipaddress.IPv4Address(cfg[field])
        if not address.is_private or address.is_unspecified or address.is_multicast:
            raise ValueError('MQTT requires specific private IPv4 addresses')
    for field, minimum in [('broker_port',1),('udp_port',0)]:
        if type(cfg[field]) is not int or not minimum <= cfg[field] <= 65535:
            raise ValueError('Invalid MQTT/UDP port')
    devices = cfg['devices']
    if not isinstance(devices, dict) or not 1 <= len(devices) <= 8:
        raise ValueError('MQTT supports 1 to 8 configured devices')
    usernames, provisions = {cfg['username']}, set()
    def credential(user, password):
        if (not isinstance(user,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',user) or
            not isinstance(password,str) or not 16 <= len(password) <= 128):
            raise ValueError('Invalid MQTT credentials')
    credential(cfg['username'],cfg['password'])
    for device, item in devices.items():
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',device):raise ValueError('Invalid MQTT device id')
        credential(item['username'],item['password'])
        token = item.get('provision_token','')
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}',token):
            raise ValueError('Invalid per-device provisioning secret')
        if item['username'] in usernames or token in provisions:
            raise ValueError('Duplicate MQTT identity or provisioning secret')
        usernames.add(item['username']);provisions.add(token)
        if type(item.get('enabled',False)) is not bool:raise ValueError('Invalid MQTT device selection')
    return cfg


class MqttService:
    def __init__(self, hub, config):
        self.config, self.hub = config, hub
        self.client, self.task = None, None
        self.ready = asyncio.Event()
        self.error = None
        self.gateway = MqttGateway(hub, config, self.publish)

    def device_config(self, device, provision_token):
        item = self.config['devices'].get(device)
        expected = item['provision_token'] if item else 'invalid-device'
        authenticated = isinstance(provision_token,str) and secrets.compare_digest(provision_token,expected)
        if (not authenticated or not self.ready.is_set() or not item or
            not item.get('enabled',False)):return None
        return dict(endpoint=f"{self.config['broker_host']}:{self.config['broker_port']}",
            client_id='voicehub_'+item['username'], username=item['username'],password=item['password'],
            publish_topic='voicehub/'+device+'/up',subscribe_topic='voicehub/'+device+'/down',keepalive=30)

    async def publish(self, topic, value):
        if self.client is None:raise ConnectionError('mqtt_unavailable')
        await self.client.publish(topic,json.dumps(value,ensure_ascii=False),qos=0,retain=False)

    async def start(self):
        import aiomqtt  # Fail before binding if the optional dependency is unavailable.
        await self.gateway.start()
        self.task = asyncio.create_task(self.run())

    async def run(self):
        import aiomqtt
        while True:
            try:
                async with aiomqtt.Client(self.config['broker_host'],self.config['broker_port'],
                    username=self.config['username'],password=self.config['password'],
                    identifier='voicehub_backend',timeout=5,keepalive=30,
                    max_queued_incoming_messages=128,max_queued_outgoing_messages=128,
                    max_concurrent_outgoing_calls=16) as client:
                    self.client = client
                    for device in self.config['devices']:
                        await client.subscribe('voicehub/'+device+'/up',qos=0)
                    self.error = None
                    self.ready.set()
                    self.hub.publish(dict(type='transport.status',transport='mqtt_udp',ready=True))
                    async for message in client.messages:
                        await self.gateway.control(str(message.topic),bytes(message.payload),message.retain)
            except (aiomqtt.MqttError, OSError, ConnectionError) as exc:
                self.error = 'broker_unavailable'
                LOG.warning('MQTT unavailable (%s); reconnecting',type(exc).__name__)
            finally:
                self.ready.clear()
                self.client = None
                await asyncio.gather(*(self.gateway.disconnect(d) for d in list(self.gateway.channels)))
            await asyncio.sleep(1)

    async def close(self):
        self.ready.clear()
        await self.gateway.close()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
