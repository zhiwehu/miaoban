"""Verified client catalog and explicitly installed, checksum-verified firmware."""
import hashlib
import json
from pathlib import Path
from functools import lru_cache

CATALOG = [
    dict(id='web',name='网页语音客户端',hardware='Chrome / 麦克风或耳机',
         verified='电脑即可使用',protocols=['websocket_v1'],
         installation='无需安装，打开网页聊天即可使用。',
         steps=['点击下方按钮，打开网页客户端。','选择要使用的麦克风，点击“开启麦克风”并允许浏览器使用。',
                '先选“手动激活”，说一句“你好”。看到识别文字并听见回复，就连接成功了。']),
    dict(id='echoear-s3',name='EchoEar 猫头',hardware='ESP32-S3 / EchoEar / 16 MB',
         verified='已验证的硬件客户端',protocols=['xiaozhi_websocket','mqtt_udp'],
         installation='只给对应型号的设备安装固件。升级前备份，并保留原有设置、唤醒词和资源。',
         source_url='https://github.com/zhiwehu/miaoban/tree/feature/voicehub-control-center/firmware/head',
         build_url='https://github.com/zhiwehu/miaoban/blob/feature/voicehub-control-center/firmware/README.md',
         steps=['先核对设备型号，并安装与这台设备匹配的固件；已有适配固件可以跳过安装。',
                '让设备和 VoiceHub 连上可互相访问的网络，按设备说明填入连接设置。',
                '唤醒设备并说“你好”。下方出现设备名称、能看到识别文字并听见回复，就接入成功了。'])
]


@lru_cache(maxsize=8)
def _firmware_cached(root, client, file_identity):
    # Stat identity invalidates cached image validation when an administrator publishes a file.
    if client != 'echoear-s3': return None
    folder = Path(root)/'firmware'/client
    try:
        manifest = json.loads((folder/'manifest.json').read_text())
        if (manifest.get('board') != client or manifest.get('kind') != 'application' or
                manifest.get('file') != 'application.bin' or not isinstance(manifest.get('version'),str)):
            return None
        path = folder/'application.bin'
        if path.is_symlink() or not 1024 <= path.stat().st_size <= 16*1024*1024: return None
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != manifest.get('sha256'): return None
        from .firmware_release import install_metadata
        install = install_metadata(manifest)
        if install:
            from .firmware_release import inspect_application
            details = inspect_application(path.read_bytes())
            if details['sha256'] != digest: return None
        return dict(version=manifest['version'],sha256=digest,size=path.stat().st_size,install=install,
                    download='/api/clients/'+client+'/firmware',path=path)
    except (OSError,ValueError,TypeError): return None


def firmware(root, client):
    if client != 'echoear-s3': return None
    folder=Path(root)/'firmware'/client
    try:
        manifest=(folder/'manifest.json').stat();image=(folder/'application.bin').stat()
        identity=(manifest.st_mtime_ns,manifest.st_size,image.st_mtime_ns,image.st_size)
        return _firmware_cached(str(Path(root).resolve()),client,identity)
    except OSError:return None


def catalog(root):
    result = []
    for client in CATALOG:
        item = dict(client)
        asset = firmware(root,item['id'])
        item['firmware'] = {k:v for k,v in asset.items() if k!='path'} if asset else None
        result.append(item)
    return result
