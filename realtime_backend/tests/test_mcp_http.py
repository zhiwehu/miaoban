import asyncio
import json
import tempfile
import unittest
from aiohttp import web
from aiohttp.test_utils import TestServer
from voicehub.mcp_extensions import ExtensionManager
from voicehub.mcp_client import MCPError

TOOL={'name':'echo','description':'原样回显文字','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}}

class HttpExtensionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.manager=ExtensionManager(self.tmp.name)
        self.messages=[];self.mode='json';self.deleted=0;self.started=asyncio.Event()
        app=web.Application();app.router.add_route('*','/mcp',self.handle)
        self.server=TestServer(app);await self.server.start_server();self.url=str(self.server.make_url('/mcp'))
    async def asyncTearDown(self):
        await self.manager.close();await self.server.close();self.tmp.cleanup()
    def register(self,**extra):
        spec={'type':'http','url':self.url,'allow_private':True,'headers':{'Authorization':'Bearer fixture-secret'},**extra}
        return self.manager.import_bytes(json.dumps({'mcpServers':{'remote-echo':spec}}).encode())
    async def handle(self,r):
        if r.headers.get('Authorization')!='Bearer fixture-secret' or self.mode=='auth':return web.Response(status=401,text='fixture-secret')
        if self.mode=='redirect':return web.Response(status=307,headers={'Location':self.url})
        if r.method=='DELETE':self.deleted+=1;return web.Response(status=204)
        msg=await r.json();self.messages.append((msg,dict(r.headers)));method=msg['method']
        modern=r.headers.get('MCP-Protocol-Version')=='2026-07-28'
        if method=='initialize':
            result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
            return web.json_response({'jsonrpc':'2.0','id':msg['id'],'result':result},headers={'Mcp-Session-Id':'fixture-session'})
        if 'id' not in msg:return web.Response(status=202)
        if not modern and r.headers.get('Mcp-Session-Id')!='fixture-session':return web.Response(status=400)
        if method=='tools/list':
            result={'tools':[],'nextCursor':'page2'} if self.mode=='pages' and not msg['params'].get('cursor') else {'tools':[TOOL]}
        else:
            self.started.set()
            if self.mode=='slow':await asyncio.sleep(.5)
            result={'content':[{'type':'text','text':msg['params']['arguments']['text']}],'isError':False}
        message={'jsonrpc':'2.0','id':msg['id'],'result':result}
        if self.mode=='badid':message['id']='wrong'
        if self.mode=='large':return web.Response(text='x'*140000,content_type='application/json')
        if self.mode in ('sse','sse-cr'):
            response=web.StreamResponse(headers={'Content-Type':'text/event-stream'});await response.prepare(r)
            await response.write(b': ping\r\n\r\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\r\n\r\n')
            raw=json.dumps(message,ensure_ascii=False).encode();split=raw.index(b',')+1
            ending=b'\r' if self.mode=='sse-cr' else b'\r\n'
            await response.write(b'event: message'+ending+b'data: '+raw[:split]+ending+b'data: '+raw[split:]+ending+ending);await response.write_eof();return response
        return web.json_response(message)
    async def test_save_discovery_call_session_and_redaction(self):
        record=self.register();self.assertFalse(record['enabled']);self.assertFalse(self.messages)
        self.assertNotIn('fixture-secret',str(self.manager.public_servers()))
        with self.assertRaises(ValueError):await self.manager.enable('remote-echo',False)
        await self.manager.enable('remote-echo',True)
        result=await self.manager.call('ext_remote-echo__echo',{'text':'远程参数'})
        self.assertEqual(result['content'][0]['text'],'远程参数');self.assertGreaterEqual(self.deleted,2)
        await self.manager.disable('remote-echo')
        with self.assertRaises(MCPError):await self.manager.call('ext_remote-echo__echo',{'text':'no'})
    async def test_sse_and_pagination(self):
        self.mode='pages';self.register();await self.manager.enable('remote-echo',True)
        for self.mode in ('sse','sse-cr'):
            result=await self.manager.call('ext_remote-echo__echo',{'text':'多行 SSE'})
            self.assertEqual(result['content'][0]['text'],'多行 SSE')
    async def test_auth_redirect_bad_response_and_size(self):
        self.register()
        for mode in ('auth','redirect','badid','large'):
            self.mode=mode
            with self.subTest(mode=mode),self.assertRaises(ValueError):await self.manager.enable('remote-echo',True)
            self.assertFalse(self.manager.catalogue());self.assertNotIn('fixture-secret',str(self.manager.public_servers()))
    async def test_timeout_and_disable_pending_call(self):
        self.register();await self.manager.enable('remote-echo',True);self.mode='slow'
        with self.assertRaises(TimeoutError):await self.manager.call('ext_remote-echo__echo',{'text':'timeout'},.1)
        self.started.clear();task=asyncio.create_task(self.manager.call('ext_remote-echo__echo',{'text':'disable'}))
        await self.started.wait();await self.manager.disable('remote-echo')
        with self.assertRaises(MCPError):await task
        self.assertFalse(self.manager.active['remote-echo'])
    async def test_explicit_2026_stateless_metadata(self):
        self.register(protocol_version='2026-07-28');await self.manager.enable('remote-echo',True)
        result=await self.manager.call('ext_remote-echo__echo',{'text':'new protocol'});self.assertEqual(result['content'][0]['text'],'new protocol')
        self.assertFalse(any(m['method']=='initialize' for m,h in self.messages))
        for m,h in self.messages:
            self.assertEqual(m['params']['_meta']['io.modelcontextprotocol/protocolVersion'],'2026-07-28')
            self.assertEqual(h['Mcp-Method'],m['method'])
            if m['method']=='tools/call':self.assertEqual(h['Mcp-Name'],'echo')
    async def test_configuration_rejected_before_network(self):
        for url in ('file:///tmp/x','https://2130706433/mcp','https://127.1/mcp','https://user:pass@example.com/mcp','https://example.com/mcp?token=secret','http://example.com/mcp','http://169.254.169.254/mcp','http://127.0.0.1:8765/api/config'):
            with self.subTest(url=url),self.assertRaises(ValueError):self.register(url=url)
        for headers in ({'Host':'evil'},{'Authorization':'secret\r\nx: y'}):
            with self.assertRaises(ValueError):self.register(headers=headers)

    async def test_resolver_rejects_private_dns_without_opt_in_and_special_ip_even_with_it(self):
        import socket
        from unittest.mock import patch,AsyncMock
        from voicehub.mcp_http import EndpointResolver
        loop=asyncio.get_running_loop()
        for ip,allow in [('127.0.0.1',False),('169.254.169.254',True),('::ffff:169.254.169.254',True)]:
            family=socket.AF_INET6 if ':' in ip else socket.AF_INET
            records=[(family,socket.SOCK_STREAM,6,'',(ip,443))]
            with patch.object(loop,'getaddrinfo',AsyncMock(return_value=records)),self.assertRaises(ValueError):
                await EndpointResolver(allow).resolve('public-name.example',443)

    async def test_2026_parameter_header_encoding_and_invalid_annotations(self):
        from unittest.mock import patch
        from voicehub.mcp_http import header_paths,header_value
        annotated={**TOOL,'inputSchema':{'type':'object','properties':{'text':{'type':'string','x-mcp-header':'Text'}}}}
        with patch.dict(TOOL,annotated):
            self.register(protocol_version='2026-07-28');await self.manager.enable('remote-echo',True)
            await self.manager.call('ext_remote-echo__echo',{'text':'你好\nheader'})
            self.assertEqual(self.messages[-1][1]['Mcp-Param-Text'],header_value('你好\nheader'))
        for schema in ({'type':'object','x-mcp-header':'Bad'}, {'type':'object','properties':{'x':{'type':'number','x-mcp-header':'Num'}}}, {'type':'object','items':{'type':'string','x-mcp-header':'Bad'}}):
            with self.assertRaises(ValueError):header_paths(schema)
        self.assertNotEqual(header_value('=?base64?literal?='),'=?base64?literal?=')

    async def test_session_expiry_does_not_replay_tool_calls(self):
        original=self.handle
        async def handler(request):
            # Replace a successful call result with expired-session status.
            if request.method=='POST':
                body=await request.json()
                if body.get('method')=='tools/call':
                    self.messages.append((body,dict(request.headers)))
                    return web.Response(status=404)
            return await original(request)
        await self.server.close()
        app=web.Application();app.router.add_route('*','/mcp',handler)
        self.server=TestServer(app);await self.server.start_server();self.url=str(self.server.make_url('/mcp'))
        self.register();await self.manager.enable('remote-echo',True)
        with self.assertRaisesRegex(MCPError,'mcp_http_session_expired'):
            await self.manager.call('ext_remote-echo__echo',{'text':'never replay'})
        self.assertEqual(sum(m['method']=='tools/call' for m,h in self.messages),1)
