"""Bundled MCP server. stdout is reserved exclusively for JSON-RPC."""
import asyncio
import contextlib
import json
import re
import sys
import signal
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
        music_errors={'music_directory_required':'请先在 MCP 工具页设置音乐目录。',
                      'music_directory_unavailable':'音乐目录不存在或当前无法读取。',
                      'music_disabled':'本地音乐工具尚未启用。','music_player_unavailable':'当前主机未提供 afplay 播放器。',
                      'music_playback_failed':'播放器启动失败，请检查音频格式。','music_player_busy':'旧播放器尚未退出，请先停止后重试。'}
        known_errors={**music_errors,**TOOLS['weather'].ERROR_MESSAGES,**TOOLS['file_organizer'].ERROR_MESSAGES,**TOOLS['calendar'].ERROR_MESSAGES}
        if str(exc) in known_errors:
            code=str(exc)
        elif isinstance(exc, TimeoutError):
            code = 'upstream_timeout'
        elif isinstance(exc, aiohttp.ClientError):
            code = 'upstream_connection_failed'
        elif re.fullmatch(r'upstream_http_\d{3}', str(exc)):
            code = str(exc)
        else:
            code = 'tool_invalid_data'
        message = known_errors.get(code,'工具查询失败，请检查参数、网络或新闻源后重试。')
        return {'content':[{'type':'text','text':message}], 'isError':True,
                'structuredContent':{'error':{'code':code, 'message':message}}}



async def serve():
    reader = asyncio.StreamReader(limit=MAX_MESSAGE)
    loop = asyncio.get_running_loop()
    # Let bounded executor work commit player ownership before process exit.
    # Abruptly killing the parent between spawning afplay and saving its identity
    # would leave a playing process that subsequent controls could not own.
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGTERM,asyncio.current_task().cancel)
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
    with contextlib.suppress(asyncio.CancelledError):asyncio.run(serve())
