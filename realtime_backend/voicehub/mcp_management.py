"""Admin-only MCP import, explicit opt-in verification and manual tool tests."""
import asyncio
import io
import json
import re
from pathlib import Path
import zipfile
from aiohttp import web
from .mcp_client import CommonToolsClient, MCPError
from .tools import validate_arguments,SCHEMAS


async def extension_api(request):
    try:return await _extension_api(request)
    except (ValueError,TypeError,RecursionError) as exc:
        code=str(exc)
        if not re.fullmatch(r'[a-z0-9_]{1,100}',code):code='file_or_arguments_invalid'
        messages={'explicit_code_trust_required':'请先确认你信任此 MCP，并允许它以当前用户权限运行。',
                  'server_already_exists':'服务 ID 已存在，请先删除旧服务或修改 ID。',
                  'archive_unsafe_path':'ZIP 含越界路径、重复路径或符号链接，已拒绝。',
                  'absolute_executable_required':'JSON command 必须是已安装可执行文件的绝对路径。',
                  'archive_manifest_required':'ZIP 根目录缺少 mcp.json。',
                  'verification_failed_check_server_dependencies_and_stdio':'服务验证失败：请检查依赖和标准 stdio MCP 响应。',
                  'tool_arguments_invalid':'参数与工具 Schema 不符。',
                  'tool_disabled':'工具尚未启用。',
                  'remote_url_invalid':'服务地址须为完整 HTTP(S) 地址，不能含用户名、密码、查询串或片段。',
                  'remote_https_required':'公网服务必须使用 HTTPS；HTTP 仅用于明确授权的本地或局域网 IP。',
                  'remote_private_opt_in_required':'连接本地或局域网服务前，请勾选允许私人网络。',
                  'remote_address_forbidden':'该地址属于禁止访问的特殊网络地址。',
                  'remote_platform_endpoint_forbidden':'不能将平台管理接口作为 MCP 服务地址。',
                  'remote_headers_invalid':'认证请求头格式无效，不能覆盖 HTTP 或 MCP 协议头。',
                  'mcp_http_auth_failed':'认证失败，请检查 Token 或服务要求的请求头。暂不支持 OAuth 登录。',
                  'mcp_http_redirect_forbidden':'服务返回重定向，请直接填写最终 MCP 端点地址。',
                  'mcp_http_transport_failed':'连接失败，请检查地址、证书、DNS 和私人网络授权。',
                  'mcp_http_status_failed':'服务返回错误状态，请检查 MCP 端点与协议版本。',
                  'mcp_http_session_expired':'服务会话已过期，请重新发起调用。',
                  'mcp_incompatible':'服务协议不兼容，请检查选择的版本。'}
        return web.json_response({'error':{'code':code,'message':messages.get(code,'文件或参数无效：'+code)}},status=400)


async def _extension_api(request):
    from .app import HUB
    hub=request.app[HUB];manager=hub.extensions
    action=request.match_info.get('action')
    if request.path=='/api/mcp/example':
        output=io.BytesIO();root=Path(__file__).resolve().parents[1]/'examples/mcp-echo'
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name in ('mcp.json','server.py'):archive.writestr(name,(root/name).read_bytes())
        return web.Response(body=output.getvalue(),content_type='application/zip',
                            headers={'Content-Disposition':'attachment; filename="local-echo-mcp.zip"'})
    if request.path=='/api/mcp/remote':
        body=await request.json()
        if not isinstance(body,dict) or set(body)-{'id','name','url','headers','allow_private','protocol_version'}:raise ValueError('remote_config_invalid')
        identity=body.get('id');name=body.pop('name',identity);body.pop('id',None)
        if not isinstance(identity,str):raise ValueError('server_id_invalid')
        if not isinstance(name,str) or not 1<=len(name)<=80:raise ValueError('server_name_invalid')
        raw=json.dumps({'mcpServers':{identity:{'type':'http','name':name,**body}}}).encode()
        async with manager.lock:
            record=manager.import_bytes(raw)
        hub.store.event(dict(type='mcp.imported',server_id=record['id']))
        return web.json_response({'server':record},status=201)
    if request.path=='/api/mcp/import':
        raw=await request.clone(client_max_size=2*1024*1024).read()
        async with manager.lock:record=manager.import_bytes(raw)
        hub.store.event(dict(type='mcp.imported',server_id=record['id']))
        return web.json_response({'server':record},status=201)
    if action:
        identity=request.match_info['server']
        if identity not in manager.records:raise web.HTTPNotFound()
        if action=='enable':
            body=await request.json();await manager.enable(identity,body.get('trust') if isinstance(body,dict) else None)
        else:await manager.disable(identity,delete=action=='delete')
        hub.store.event(dict(type='mcp.'+action,server_id=identity))
        return web.json_response({'servers':manager.public_servers()})
    name=request.match_info['tool']
    arguments=await request.json()
    timeout=hub.config.get()['tools']['timeout_seconds']
    hub.store.event(dict(type='tool.test.started',tool=name))
    try:
        async with asyncio.timeout(timeout):
            if name in SCHEMAS:
                if not hub.config.get()['tools'].get(name):raise ValueError('tool_disabled')
                validate_arguments(name,arguments)
                async with CommonToolsClient(timeout=timeout,data_root=hub.root) as client:
                    result=await client.call(name,arguments)
            else:result=await manager.call(name,arguments,timeout)
            if len(json.dumps(result))>16000:raise ValueError('tool_output_limit')
    except (MCPError,OSError,TimeoutError) as exc:
        hub.store.event(dict(type='tool.test.failed',tool=name))
        return web.json_response({'error':{'code':'tool_unavailable','message':'MCP 调用失败或超时，请检查服务。'}},status=502)
    hub.store.event(dict(type='tool.test.completed',tool=name))
    return web.json_response({'result':result})
