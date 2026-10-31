"""Minimal MCP client for the robot's own device-side server.

The EchoEar firmware runs a JSON-RPC MCP server over the same WebSocket as
audio (`mcp_server.cc`): it answers `initialize`, `tools/list` and
`tools/call`, and exposes tools such as `self.get_device_status` and
`self.audio_speaker.set_volume`. The device never initiates a request, so
this class is a plain client with an id → future map.

Frames on the wire (both directions):

    {"session_id": "...", "type": "mcp", "payload": {"jsonrpc": "2.0", ...}}
"""
import asyncio
import itertools


class DeviceMCPError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class DeviceMCP:
    def __init__(self, send, *, timeout=6, protocol_version='2024-11-05'):
        self._send = send
        self._ids = itertools.count(1)
        self._pending = {}
        self._tools = None
        self.timeout = timeout
        self.protocol_version = protocol_version
        self.ready = False

    async def request(self, method, params, *, timeout=None):
        identity = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[identity] = future
        try:
            await self._send({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params})
            return await asyncio.wait_for(future, timeout or self.timeout)
        except TimeoutError:
            raise DeviceMCPError('device_timeout') from None
        finally:
            self._pending.pop(identity, None)

    def feed(self, payload):
        """Resolve the pending request a device reply belongs to; ignore the rest."""
        if not isinstance(payload, dict):
            return
        identity = payload.get('id')
        if not isinstance(identity, int):
            return
        future = self._pending.pop(identity, None)
        if future is None or future.done():
            return
        if payload.get('error'):
            future.set_exception(DeviceMCPError('device_error'))
        else:
            future.set_result(payload.get('result') or {})

    async def initialize(self):
        result = await self.request('initialize', {
            'protocolVersion': self.protocol_version,
            'capabilities': {},
            'clientInfo': {'name': 'voicehub', 'version': '0.1'},
        })
        if not isinstance(result.get('capabilities'), dict):
            raise DeviceMCPError('device_mcp_incompatible')
        self.ready = True
        return result

    async def ensure_ready(self):
        if not self.ready:
            await self.initialize()

    async def list_tools(self, *, refresh=False):
        await self.ensure_ready()
        if self._tools is None or refresh:
            result = await self.request('tools/list', {})
            self._tools = result.get('tools') or []
        return self._tools

    async def call(self, name, arguments=None, *, timeout=None):
        await self.ensure_ready()
        result = await self.request('tools/call', {'name': name, 'arguments': arguments or {}},
                                    timeout=timeout)
        if result.get('isError'):
            raise DeviceMCPError('device_tool_error')
        return result

    def close(self):
        for future in self._pending.values():
            if not future.done():
                future.set_exception(DeviceMCPError('device_disconnected'))
        self._pending.clear()
        self.ready = False
        self._tools = None
