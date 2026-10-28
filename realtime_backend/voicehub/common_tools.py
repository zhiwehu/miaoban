"""Bundled read-only MCP server. stdout is reserved exclusively for JSON-RPC."""
import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET

import aiohttp
from .mcp_tools import TOOLS
from .tools import catalogue, validate_arguments
from .mcp_client import VERSION, MAX_MESSAGE


async def call_tool(http, name, arguments):
    try:
        validate_arguments(name, arguments)
        async with asyncio.timeout(18):
            value = await TOOLS[name].execute(http, **arguments)
        return {'content':[{'type':'text','text':json.dumps(value, ensure_ascii=False)}], 'isError':False}
    except (ValueError, TypeError, KeyError, IndexError, ET.ParseError, aiohttp.ClientError, TimeoutError, OSError) as exc:
        if isinstance(exc, TimeoutError):
            code = 'upstream_timeout'
        elif isinstance(exc, aiohttp.ClientError):
            code = 'upstream_connection_failed'
        elif re.fullmatch(r'upstream_http_\d{3}', str(exc)):
            code = str(exc)
        else:
            code = 'tool_invalid_data'
        message = '工具查询失败，请检查参数、网络或新闻源后重试。'
        return {'content':[{'type':'text','text':message}], 'isError':True,
                'structuredContent':{'error':{'code':code, 'message':message}}}



async def serve():
    reader = asyncio.StreamReader(limit=MAX_MESSAGE)
    loop = asyncio.get_running_loop()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer)
    initialized = ready = False
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=5),
                                    headers={'User-Agent':'VoiceHub/0.1 common-tools'}) as http:
        while True:
            try:
                raw = await reader.readline()
                if not raw:
                    return
                message = json.loads(raw)
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                    raise ValueError('invalid_request')
            except (ValueError, UnicodeError):
                print(json.dumps({'jsonrpc':'2.0','id':None,
                    'error':{'code':-32700,'message':'Parse error'}}), flush=True)
                continue
            method, params = message.get('method'), message.get('params', {})
            if method == 'notifications/initialized' and initialized:
                ready = True
                continue
            if 'id' not in message:
                continue
            response = {'jsonrpc':'2.0', 'id':message['id']}
            if method == 'initialize' and not initialized:
                initialized = True
                response['result'] = {'protocolVersion':VERSION, 'capabilities':{'tools':{}},
                                      'serverInfo':{'name':'voicehub-common-tools','version':'0.1'}}
            elif method == 'ping':
                response['result'] = {}
            elif ready and method == 'tools/list':
                response['result'] = {'tools':catalogue()}
            elif ready and method == 'tools/call' and isinstance(params, dict):
                response['result'] = await call_tool(http, params.get('name'), params.get('arguments', {}))
            else:
                response['error'] = {'code':-32601, 'message':'Method not available'}
            print(json.dumps(response, ensure_ascii=False), flush=True)



if __name__ == '__main__':
    asyncio.run(serve())
