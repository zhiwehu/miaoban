"""Bounded stdio MCP client for the bundled, fixed-command common-tools service."""
import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

VERSION = '2025-06-18'
MAX_MESSAGE = 128 * 1024


class MCPError(RuntimeError):
    pass


class CommonToolsClient:
    def __init__(self, timeout=20):
        self.timeout, self.process, self.serial = timeout, None, 0

    async def __aenter__(self):
        # Do not give a tool server model/provider credentials or management tokens.
        env = {k:v for k,v in os.environ.items() if k in
               ('PATH','HOME','LANG','LC_ALL','TMPDIR','SSL_CERT_FILE','SSL_CERT_DIR')}
        try:
            self.process = await asyncio.create_subprocess_exec(
                sys.executable, '-m', 'voicehub.common_tools',
                cwd=Path(__file__).resolve().parents[1], env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=MAX_MESSAGE)
            result = await self.request('initialize', {'protocolVersion':VERSION,
                'capabilities':{}, 'clientInfo':{'name':'voicehub','version':'0.1'}})
            if result.get('protocolVersion') != VERSION or 'tools' not in result.get('capabilities', {}):
                raise MCPError('mcp_incompatible')
            await self.notify('notifications/initialized', {})
            return self
        except BaseException:
            await self.close()
            raise

    async def send(self, message):
        raw = json.dumps(message, ensure_ascii=False).encode() + b'\n'
        if len(raw) > MAX_MESSAGE:
            raise MCPError('mcp_message_limit')
        self.process.stdin.write(raw)
        await self.process.stdin.drain()

    async def notify(self, method, params):
        await self.send({'jsonrpc':'2.0','method':method,'params':params})

    async def request(self, method, params):
        self.serial += 1
        request_id = self.serial
        async with asyncio.timeout(self.timeout):
            await self.send({'jsonrpc':'2.0','id':request_id,'method':method,'params':params})
            for _ in range(32):
                raw = await self.process.stdout.readline()
                if not raw:
                    raise MCPError('mcp_disconnected')
                message = json.loads(raw)
                if message.get('jsonrpc') != '2.0':
                    raise MCPError('mcp_invalid_response')
                if 'id' not in message:
                    continue
                if message['id'] != request_id or 'error' in message:
                    raise MCPError('mcp_request_failed')
                if not isinstance(message.get('result'), dict):
                    raise MCPError('mcp_invalid_result')
                return message['result']
            raise MCPError('mcp_notification_limit')

    async def list_tools(self):
        return (await self.request('tools/list', {}))['tools']

    async def call(self, name, arguments):
        return await self.request('tools/call', {'name':name, 'arguments':arguments})

    async def close(self):
        if self.process:
            if self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 1)
                except TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.kill()
                    await self.process.wait()
            self.process = None

    async def __aexit__(self, *args):
        await self.close()
