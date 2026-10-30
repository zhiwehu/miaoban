"""Administrator transport controls; credentials never cross the management API."""
import asyncio
import copy
import errno
import ipaddress
import json
import os
from pathlib import Path
import socket
import tempfile

import psutil


class ConnectionError(RuntimeError):
    pass


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.connection-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


class Connections:
    def __init__(self, hub, mqtt_path=None, device_host=None, device_port=8766):
        self.hub = hub
        self.path = Path(mqtt_path) if mqtt_path else hub.root/'mqtt/config.json'
        self.device_host, self.device_port = device_host, device_port
        self.device_ready = False
        self.device_runner = None
        self.device_error = None
        self.device_enabled = bool(device_host)
        self.lock = asyncio.Lock()
        self.error = None
        self.preference = hub.root/'connections.json'
        self.enabled = bool(mqtt_path)
        if self.preference.exists():
            saved = json.loads(self.preference.read_text())
            self.enabled = saved.get('mqtt_enabled') is True
            self.device_enabled = saved.get('device_enabled', bool(device_host)) is True
            self.device_host = device_host or saved.get('device_host')
            self.device_port = saved.get('device_port', device_port)

    def config(self):
        from .mqtt_service import load_config
        if not self.path.exists(): raise ConnectionError('mqtt_not_configured')
        return load_config(self.path)

    def _preference(self, enabled):
        self.enabled = enabled
        self.persist()

    def persist(self):
        atomic_json(self.preference, dict(mqtt_enabled=self.enabled,device_enabled=self.device_enabled,
            device_host=self.device_host,device_port=self.device_port))

    async def websocket_action(self, action):
        from aiohttp import web
        from .app import create_device_app
        if action not in ('start','stop','restart'): raise ValueError('invalid_transport_action')
        async with self.lock:
            if action in ('stop','restart') and self.device_runner:
                # Close only sessions admitted on the device listener.
                for session in list(self.hub.sessions.values()):
                    if getattr(session,'device_listener',False):
                        ws = getattr(session,'transport_socket',None)
                        if ws: await ws.close()
                await self.device_runner.cleanup()
                self.device_runner = None
                self.device_ready = False
            if action == 'stop':
                self.device_enabled = False; self.device_error = None; self.persist(); return
            if self.device_runner: return
            try:
                address = ipaddress.IPv4Address(self.device_host or '')
                if not address.is_private or address.is_unspecified or address.is_multicast:
                    raise ValueError('invalid_device_address')
                runner = web.AppRunner(create_device_app(self.hub),access_log=None,shutdown_timeout=3)
                await runner.setup()
                try: await web.TCPSite(runner,self.device_host,self.device_port).start()
                except BaseException:
                    await runner.cleanup(); raise
                self.device_runner = runner
                self.device_ready = self.device_enabled = True
                self.device_error = None
                self.persist()
            except (OSError,ValueError) as exc:
                self.device_error = 'device_address_unavailable' if isinstance(exc,OSError) else 'invalid_device_address'
                raise ConnectionError(self.device_error) from None

    async def save_websocket(self, value):
        if not isinstance(value,dict) or set(value)!={'host','port'}: raise ValueError('invalid_device_config')
        address = ipaddress.IPv4Address(value['host'])
        if not address.is_private or address.is_unspecified or address.is_multicast:
            raise ValueError('invalid_device_address')
        if type(value['port']) is not int or not 1<=value['port']<=65535: raise ValueError('invalid_device_port')
        async with self.lock:
            if self.device_runner: raise ConnectionError('device_must_stop')
            self.device_host,self.device_port = value['host'],value['port']
            self.device_error = None; self.persist()

    async def close(self):
        # Shutdown does not change the user's startup preference.
        await self._stop()
        if self.device_runner:
            await self.device_runner.cleanup()
            self.device_runner = None
        self.device_ready = False

    async def boot(self):
        if self.device_enabled:
            try: await self.websocket_action('start')
            except (ConnectionError,ValueError): pass
        if self.enabled:
            try: await self.action('start')
            except (ConnectionError, ValueError): pass

    async def _stop(self):
        if self.hub.mqtt:
            await self.hub.mqtt.close()
            self.hub.mqtt = None

    async def action(self, action):
        if action not in ('start','stop','restart'): raise ValueError('invalid_transport_action')
        from .mqtt_service import MqttService
        async with self.lock:
            if action in ('stop','restart'): await self._stop()
            if action == 'stop':
                self.error = None; self._preference(False)
                return
            if self.hub.mqtt: return
            service = None
            try:
                cfg = self.config()
                service = MqttService(self.hub, cfg)
                await service.start()
                self.hub.mqtt = service
                self.error = None
                self._preference(True)
            except (OSError, ValueError, KeyError, TypeError, ConnectionError, ImportError) as exc:
                if service: await service.close()
                if isinstance(exc, ConnectionError): code = str(exc)
                elif isinstance(exc, ImportError): code = 'mqtt_dependency_missing'
                elif isinstance(exc, OSError):
                    code = {errno.EADDRNOTAVAIL:'udp_address_unavailable',errno.EADDRINUSE:'udp_port_in_use'}.get(exc.errno,'mqtt_start_failed')
                else: code = 'mqtt_invalid_config'
                self.error = code
                raise ConnectionError(code) from None
        self.hub.publish({'type':'transport.status','transport':'mqtt_udp'})

    async def save(self, value):
        from .mqtt_service import validate_config
        keys = {'broker_host','broker_port','udp_host','udp_port','devices'}
        if not isinstance(value, dict) or set(value) != keys: raise ValueError('invalid_transport_config')
        async with self.lock:
            if self.hub.mqtt: raise ConnectionError('mqtt_must_stop')
            cfg = copy.deepcopy(self.config())
            if not isinstance(value['devices'],dict) or set(value['devices']) != set(cfg['devices']):
                raise ValueError('invalid_device_selection')
            for device, enabled in value['devices'].items():
                if type(enabled) is not bool: raise ValueError('invalid_device_selection')
                cfg['devices'][device]['enabled'] = enabled
            cfg.update({k:value[k] for k in keys if k != 'devices'})
            validate_config(cfg)
            atomic_json(self.path,cfg)
            self.error = None

    def status(self):
        addresses = []
        for interface, items in psutil.net_if_addrs().items():
            for item in items:
                if item.family == socket.AF_INET:
                    address = ipaddress.ip_address(item.address)
                    if address.is_private and not address.is_loopback:
                        addresses.append({'interface':interface,'address':item.address})
        service = self.hub.mqtt
        cfg, config_error = None, None
        try: cfg = self.config()
        except ConnectionError: pass
        except (OSError, ValueError, KeyError, TypeError): config_error = 'mqtt_invalid_config'
        active = bool(service)
        mqtt = dict(configured=cfg is not None, enabled=self.enabled,
            state='running' if service and service.ready.is_set() else 'connecting' if active else 'failed' if self.error or config_error else 'stopped',
            error=self.error or config_error or (service.error if service else None), config=None)
        if cfg:
            mqtt['config'] = {k:cfg[k] for k in ('broker_host','broker_port','udp_host','udp_port')}
            mqtt['config']['devices'] = {k:v.get('enabled',False) for k,v in cfg['devices'].items()}
            mqtt['address_available'] = cfg['udp_host'] in [v['address'] for v in addresses] or cfg['udp_host']=='127.0.0.1'
        devices = []
        for session in list(self.hub.sessions.values()):
            item = dict(id=session.id,device_id=session.device_id,state=session.state,
                        wake_mode=session.wake_mode,protocol=getattr(session,'protocol','unknown'))
            if service:
                channel = next((c for c in service.gateway.channels.values() if c.sid==session.id),None)
                if channel:
                    item['audio'] = dict(received=channel.received,sent=channel.audio.tx_sequence,
                        lost=channel.audio.lost,dropped=channel.dropped)
            devices.append(item)
        return dict(mqtt=mqtt,devices=devices,interfaces=addresses,recent=self.hub.store.sessions()[:20],
            websocket=dict(state='running',path='/ws',legacy_path='/xiaozhi/v1/',
                device_host=self.device_host,device_port=self.device_port,device_ready=self.device_ready,
                device_error=self.device_error))
