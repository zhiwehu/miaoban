"""Prepare the device listener; Wi-Fi credentials never reach this API."""
import ipaddress
import socket
from aiohttp import web
import psutil
from .connections import ConnectionError


def interfaces():
    result=[];states=psutil.net_if_stats()
    for name,items in psutil.net_if_addrs().items():
        if name.startswith(('lo','utun','tun','tap','docker','veth','bridge','awdl','llw','tailscale')):continue
        if name in states and not states[name].isup:continue
        for item in items:
            if item.family!=socket.AF_INET:continue
            address=ipaddress.IPv4Address(item.address)
            if address.is_private and not (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast):
                result.append({'interface':name,'address':str(address)})
    return result


def default_address():
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            # Select a route without sending a packet.
            probe.connect(('192.0.2.1',9));return probe.getsockname()[0]
    except OSError:return None


async def prepare(request):
    from .app import HUB
    hub=request.app[HUB]
    if request.match_info['client']!='echoear-s3':raise web.HTTPNotFound()
    body=await request.json()
    if not isinstance(body,dict) or set(body)-{'host'}:raise ValueError('invalid_setup_request')
    choices=interfaces();addresses={v['address'] for v in choices};host=body.get('host');connections=hub.connections
    if host is not None and (not isinstance(host,str) or host not in addresses):raise ValueError('invalid_setup_address')
    if not host and connections.device_ready and connections.device_host in addresses:host=connections.device_host
    if not host:
        preferred=default_address()
        host=preferred if preferred in addresses else choices[0]['address'] if len(choices)==1 else None
    if not host:
        return web.json_response({'error':{'code':'choose_network' if choices else 'no_local_network',
            'message':'请选择喵伴要连接的本机网络。' if choices else '请先让这台电脑连接可供喵伴使用的局域网。'},'interfaces':choices},status=409)
    async with hub.provision_lock:
        try:
            if connections.device_ready and connections.device_host!=host:raise ConnectionError('listener_on_other_network')
            if not connections.device_ready:
                await connections.save_websocket({'host':host,'port':connections.device_port})
                await connections.websocket_action('start')
        except ConnectionError as exc:
            return web.json_response({'error':{'code':str(exc),'message':'设备连接服务未能准备好，请检查网卡或端口占用；已有连接服务不会自动停止。'}},status=409)
        token=hub.auth['device_token'];port=connections.device_port
        return web.json_response({'host':host,'port':port,
            'websocket':{'url':f'ws://{host}:{port}/xiaozhi/v1/','token':token,'version':1},
            'ota_url':f'http://{host}:{port}/xiaozhi/ota/{token}/'},headers={'Cache-Control':'no-store'})
