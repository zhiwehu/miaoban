"""Bounded Streamable HTTP tools client. Endpoints are explicitly trusted by admins."""
import asyncio
import base64
import contextlib
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit
import aiohttp
from .mcp_client import MCPError, MAX_MESSAGE

VERSIONS=('2025-03-26','2025-06-18','2025-11-25','2026-07-28')
TOKEN=re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")

def checked_ip(value,allow_private):
    ip=ipaddress.ip_address(value)
    if isinstance(ip,ipaddress.IPv6Address) and ip.ipv4_mapped:ip=ip.ipv4_mapped
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        raise ValueError('remote_address_forbidden')
    if not ip.is_global and not (allow_private and (ip.is_private or ip.is_loopback)):
        raise ValueError('remote_private_opt_in_required')
    return ip

def checked_remote(definition):
    if not isinstance(definition,dict) or set(definition)-{'type','url','headers','allow_private','protocol_version','name'}:
        raise ValueError('remote_config_invalid')
    url=definition.get('url');private=definition.get('allow_private',False)
    if not isinstance(private,bool):raise ValueError('remote_config_invalid')
    if not isinstance(url,str) or len(url)>2048 or any(ord(c)<33 or ord(c)>126 for c in url):raise ValueError('remote_url_invalid')
    try:
        parsed=urlsplit(url);host=parsed.hostname;port=parsed.port
        if parsed.scheme not in ('http','https') or not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or '\\' in url:
            raise ValueError('remote_url_invalid')
        if '%' in host:raise ValueError('remote_url_invalid')
        try:ip=ipaddress.ip_address(host)
        except ValueError:ip=None
        if ip is None and (':' in host or host.replace('.','').isdigit()):raise ValueError('remote_url_invalid')
        if ip is not None:checked_ip(host,private)
        local_literal=host=='localhost' or (ip is not None and (ip.is_loopback or ip.is_private))
        if parsed.scheme=='http' and not (private and local_literal):raise ValueError('remote_https_required')
        if port==8765 and local_literal:raise ValueError('remote_platform_endpoint_forbidden')
    except (TypeError,ValueError) as exc:
        if str(exc).startswith('remote_'):raise
        raise ValueError('remote_url_invalid') from None
    headers=definition.get('headers',{})
    if not isinstance(headers,dict) or len(headers)>10:raise ValueError('remote_headers_invalid')
    names=set()
    for key,value in headers.items():
        if (not isinstance(key,str) or not TOKEN.fullmatch(key) or len(key)>80 or key.lower() in names or
            key.lower() in ('host','cookie','connection','content-length','content-type','accept','transfer-encoding','proxy-authorization','upgrade') or
            key.lower().startswith(('mcp-','proxy-','sec-')) or not isinstance(value,str) or len(value)>4096 or
            any(ord(c)<32 or ord(c)>126 for c in value)):
            raise ValueError('remote_headers_invalid')
        names.add(key.lower())
    version=definition.get('protocol_version','2025-11-25')
    if version not in VERSIONS:raise ValueError('remote_protocol_unsupported')
    return dict(url=url,headers=headers,allow_private=private,protocol_version=version)

class EndpointResolver(aiohttp.abc.AbstractResolver):
    def __init__(self,allow_private):self.allow_private=allow_private
    async def resolve(self,host,port=0,family=socket.AF_UNSPEC):
        records=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM,family=family)
        result=[]
        for fam,_,proto,_,address in records:
            ip=checked_ip(address[0],self.allow_private)
            if ip.is_loopback and port==8765:raise ValueError('remote_platform_endpoint_forbidden')
            result.append(dict(hostname=host,host=str(ip),port=port,family=fam,proto=proto,flags=socket.AI_NUMERICHOST))
        if not result:raise MCPError('mcp_http_dns_failed')
        return result
    async def close(self):pass

def header_value(value):
    text=str(value).lower() if isinstance(value,bool) else str(value)
    if text!=text.strip() or any(ord(c)<32 or ord(c)>126 for c in text) or (text.startswith('=?base64?') and text.endswith('?=')):
        return '=?base64?'+base64.b64encode(text.encode()).decode()+'?='
    return text

def header_paths(schema):
    """Validate 2026 parameter mirrors; annotations outside properties are rejected."""
    paths=[];names=set()
    def visit(value,path=(),reachable=True,depth=0):
        if depth>12:raise ValueError('mcp_header_schema_depth')
        if isinstance(value,dict):
            if 'x-mcp-header' in value:
                name=value['x-mcp-header']
                if (not reachable or not path or not isinstance(name,str) or len(name)>80 or not TOKEN.fullmatch(name) or
                    name.lower() in names or value.get('type') not in ('string','integer','boolean')):
                    raise ValueError('mcp_header_schema_invalid')
                names.add(name.lower());paths.append((path,name))
            for key,item in value.items():
                if key=='properties' and reachable and isinstance(item,dict):
                    for prop,child in item.items():visit(child,(*path,prop),True,depth+1)
                elif isinstance(item,(dict,list)):visit(item,path,False,depth+1)
        elif isinstance(value,list):
            for item in value:visit(item,path,False,depth+1)
    visit(schema)
    return paths

class HttpToolsClient:
    def __init__(self,record,timeout=20):
        self.record=record;self.timeout=timeout;self.version=record['protocol_version']
        self.modern=self.version=='2026-07-28';self.http=None;self.session_id=None;self.serial=0
        self.pending=set();self.tools={};self.closing=False
    async def __aenter__(self):
        checked_remote({k:self.record[k] for k in ('url','headers','allow_private','protocol_version')})
        connector=aiohttp.TCPConnector(resolver=EndpointResolver(self.record['allow_private']),use_dns_cache=False)
        self.http=aiohttp.ClientSession(connector=connector,cookie_jar=aiohttp.DummyCookieJar(),trust_env=False,
            timeout=aiohttp.ClientTimeout(total=self.timeout),read_bufsize=16384,max_line_size=8192,max_field_size=8192)
        try:
            if not self.modern:
                result=await self.request('initialize',{'protocolVersion':self.version,'capabilities':{},'clientInfo':{'name':'voicehub','version':'0.1'}})
                version=result.get('protocolVersion')
                if version not in VERSIONS[:3] or not isinstance(result.get('capabilities'),dict) or 'tools' not in result['capabilities']:raise MCPError('mcp_incompatible')
                self.version=version
                await self.notify('notifications/initialized',{})
            return self
        except BaseException:
            await self.close();raise
    def headers(self):
        headers={**self.record['headers'],'Accept':'application/json, text/event-stream','MCP-Protocol-Version':self.version}
        if self.session_id:headers['Mcp-Session-Id']=self.session_id
        return headers
    async def notify(self,method,params):
        if not self.http or self.http.closed:return
        async with self.http.post(self.record['url'],json={'jsonrpc':'2.0','method':method,'params':params},headers=self.headers(),allow_redirects=False) as response:
            if response.status!=202:raise MCPError('mcp_http_notification_failed')
    def checked_message(self,message,identity):
        if not isinstance(message,dict) or message.get('jsonrpc')!='2.0':raise MCPError('mcp_invalid_response')
        if 'method' in message:
            if 'id' in message:raise MCPError('mcp_server_requests_unsupported')
            return None
        if message.get('id')!=identity or isinstance(message.get('id'),bool):raise MCPError('mcp_invalid_response_id')
        if 'error' in message:raise MCPError('mcp_request_failed')
        result=message.get('result')
        if not isinstance(result,dict):raise MCPError('mcp_invalid_result')
        if result.get('resultType','complete')!='complete':raise MCPError('mcp_interactive_result_unsupported')
        return result
    async def decode(self,response,identity):
        content_type=response.content_type
        if content_type=='application/json':
            raw=bytearray()
            async for chunk in response.content.iter_chunked(8192):
                raw.extend(chunk)
                if len(raw)>MAX_MESSAGE:raise MCPError('mcp_message_limit')
            return self.checked_message(json.loads(raw),identity)
        if content_type!='text/event-stream':raise MCPError('mcp_http_content_type')
        buffer=b'';data=[];total=0;events=0
        async def chunks():
            async for chunk in response.content.iter_chunked(8192):yield chunk
            yield None
        async for chunk in chunks():
            total+=len(chunk or b'')
            if total>4*MAX_MESSAGE:raise MCPError('mcp_message_limit')
            buffer+=chunk or b''
            while True:
                delimiter=re.search(rb'\r\n|\r|\n',buffer)
                if not delimiter:break
                if delimiter.group()==b'\r' and delimiter.end()==len(buffer) and chunk is not None:break
                line,buffer=buffer[:delimiter.start()],buffer[delimiter.end():]
                if len(line)>MAX_MESSAGE:raise MCPError('mcp_message_limit')
                if not line:
                    if data:
                        raw=b'\n'.join(data);data=[];events+=1
                        if len(raw)>MAX_MESSAGE or events>64:raise MCPError('mcp_message_limit')
                        if raw.strip():
                            result=self.checked_message(json.loads(raw),identity)
                            if result is not None:return result
                elif line.startswith(b'data:'):data.append(line[5:].removeprefix(b' '))
            if len(buffer)>MAX_MESSAGE:raise MCPError('mcp_message_limit')
        raise MCPError('mcp_http_stream_ended')
    async def request(self,method,params):
        self.serial+=1;identity=self.serial;params=dict(params);headers=self.headers()
        if self.modern:
            params['_meta']={'io.modelcontextprotocol/protocolVersion':self.version,
                'io.modelcontextprotocol/clientInfo':{'name':'voicehub','version':'0.1'},'io.modelcontextprotocol/clientCapabilities':{}}
            headers['Mcp-Method']=method
            if method=='tools/call':
                headers['Mcp-Name']=header_value(params['name'])
                schema=self.tools.get(params['name'],{}).get('inputSchema',{})
                for path,name in header_paths(schema):
                    value=params['arguments']
                    for key in path:
                        if not isinstance(value,dict) or key not in value:break
                        value=value[key]
                    else:
                        if isinstance(value,int) and not isinstance(value,bool) and abs(value)>2**53-1:raise MCPError('mcp_header_integer_range')
                        if not isinstance(value,(str,int,bool)):raise MCPError('mcp_header_value_invalid')
                        headers['Mcp-Param-'+name]=header_value(value)
        message={'jsonrpc':'2.0','id':identity,'method':method,'params':params}
        if len(json.dumps(message).encode())>MAX_MESSAGE:raise MCPError('mcp_message_limit')
        self.pending.add(identity)
        try:
            if self.closing or not self.http:raise MCPError('mcp_disconnected')
            async with asyncio.timeout(self.timeout),self.http.post(self.record['url'],json=message,headers=headers,allow_redirects=False) as response:
                if response.status in (401,403):raise MCPError('mcp_http_auth_failed')
                if 300<=response.status<400:raise MCPError('mcp_http_redirect_forbidden')
                if response.status==404 and self.session_id:raise MCPError('mcp_http_session_expired')
                if response.status!=200:raise MCPError('mcp_http_status_failed')
                if method=='initialize':
                    session=response.headers.get('Mcp-Session-Id')
                    if session and (len(session)>1024 or any(ord(c)<33 or ord(c)>126 for c in session)):raise MCPError('mcp_http_session_invalid')
                    self.session_id=session
                result=await self.decode(response,identity)
                if result is None:raise MCPError('mcp_invalid_result')
                return result
        except (asyncio.CancelledError,TimeoutError):
            if not self.modern and not self.closing:
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(.5):await self.notify('notifications/cancelled',{'requestId':identity,'reason':'Cancelled'})
            raise
        except (aiohttp.ClientError,OSError,ValueError,UnicodeError,RecursionError):
            raise MCPError('mcp_http_transport_failed') from None
        finally:self.pending.discard(identity)
    async def list_tools(self):
        tools=[];cursor=None;seen=set()
        for _ in range(10):
            result=await self.request('tools/list',{'cursor':cursor} if cursor else {})
            page=result.get('tools')
            if not isinstance(page,list):raise MCPError('mcp_invalid_tools')
            tools.extend(page)
            if len(tools)>20:raise MCPError('mcp_tool_limit')
            cursor=result.get('nextCursor')
            if not cursor:
                if self.modern:
                    accepted=[]
                    for tool in tools:
                        try:header_paths(tool.get('inputSchema',{}))
                        except (ValueError,AttributeError):continue
                        accepted.append(tool)
                    tools=accepted
                self.tools={t['name']:t for t in tools if isinstance(t,dict) and isinstance(t.get('name'),str)}
                return tools
            if not isinstance(cursor,str) or len(cursor)>2048 or cursor in seen:raise MCPError('mcp_cursor_invalid')
            seen.add(cursor)
        raise MCPError('mcp_page_limit')
    async def call(self,name,arguments):
        return await self.request('tools/call',{'name':name,'arguments':arguments})
    async def close(self):
        if self.closing:return
        self.closing=True
        if not self.http:return
        try:
            async with asyncio.timeout(.7):
                if not self.modern:
                    for identity in tuple(self.pending):
                        with contextlib.suppress(Exception):await self.notify('notifications/cancelled',{'requestId':identity,'reason':'Cancelled'})
                    if self.session_id:
                        with contextlib.suppress(Exception):
                            async with self.http.delete(self.record['url'],headers=self.headers(),allow_redirects=False):pass
        except TimeoutError:pass
        finally:await self.http.close()
    async def __aexit__(self,*args):await self.close()
