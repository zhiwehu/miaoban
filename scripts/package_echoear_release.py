#!/usr/bin/env python3
"""Package an application upgrade candidate; never claim unperformed hardware validation."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'realtime_backend'))
from voicehub.firmware_release import inspect_application


def package(app, partition, output, known, source_commit, validation=None, licenses=None):
    raw=Path(app).read_bytes();metadata=inspect_application(raw)
    table=Path(partition).read_bytes()
    if len(table)!=3072:raise ValueError('Unexpected partition table size')
    # Independent image test is complemented by strict browser-side table profile checks.
    expected=[('nvs',1,2,0x9000,0x4000),('otadata',1,0,0xd000,0x2000),('phy_init',1,1,0xf000,0x1000),
              ('ota_0',0,16,0x20000,0x3f0000),('ota_1',0,17,0x410000,0x3f0000),('assets',1,130,0x800000,0x800000)]
    import struct,re
    for i,(name,expected_kind,expected_sub,address,size) in enumerate(expected):
        magic,kind,sub,actual,length,label,flags=struct.unpack_from('<HBBII16sI',table,i*32)
        if (kind,sub)!=(expected_kind,expected_sub) or magic!=0x50aa or label.rstrip(b'\0').decode()!=name or (actual,length,flags)!=(address,size,0):
            raise ValueError('Partition layout does not match EchoEar upgrade profile')
    if table[192:208] != b'\xeb\xeb'+b'\xff'*14 or table[208:224]!=hashlib.md5(table[:192]).digest() or table[224:]!=b'\xff'*(3072-224):
        raise ValueError('Invalid partition-table checksum or extra partitions')
    if not known or any(not re.fullmatch('[0-9a-f]{64}',h) for h in known):raise ValueError('Invalid upgrade whitelist')
    validated=False
    if validation:
        record=json.loads(Path(validation).read_text())
        required=('identity_verified','backup_verified','upgrade_readback_verified','wifi_provisioned','voice_reply_verified','restore_verified')
        if record.get('sha256')!=metadata['sha256'] or any(record.get(k) is not True for k in required):
            raise ValueError('Incomplete or mismatched hardware validation record')
        validated=True
    manifest=dict(board='echoear-s3',kind='application',file='application.bin',**metadata,
        source_commit=source_commit,install=dict(board='echoear-s3',partition_bytes=3072,
        partition_sha256=hashlib.sha256(table).hexdigest(),upgrade_from_sha256=known,validated=validated))
    dest=Path(output)
    if dest.exists():raise FileExistsError('Refusing to overwrite an existing release')
    dest.mkdir(parents=True,mode=0o700)
    (dest/'application.bin').write_bytes(raw)
    (dest/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    (dest/'README.txt').write_text('EchoEar ESP32-S3 / 16 MB existing-device application upgrade.\n'
        'Preserve bootloader, partitions, Wi-Fi settings and assets. Do not erase all flash.\n'
        +('Hardware validation complete.\n' if validated else 'CANDIDATE: hardware validation pending; browser installation remains disabled.\n'))
    notices=dest/'licenses';notices.mkdir()
    repo=Path(__file__).resolve().parents[1]
    for name,source in [('voicehub-LICENSE.txt',repo/'LICENSE'),('firmware-LICENSE.txt',repo/'firmware/head/LICENSE'),('THIRD_PARTY_NOTICES.md',repo/'THIRD_PARTY_NOTICES.md')]:
        shutil.copy2(source,notices/name)
    if licenses:
        for file in Path(licenses).rglob('*'):
            if file.is_file() and not file.is_symlink():
                relative=file.relative_to(licenses);target=notices/'dependencies'/relative
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
    with zipfile.ZipFile(Path(str(dest)+'.zip'),'w',zipfile.ZIP_DEFLATED) as z:
        for file in dest.rglob('*'):
            if file.is_file():z.write(file,str(file.relative_to(dest)))
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--app',required=True);p.add_argument('--partition',required=True)
    p.add_argument('--output',required=True);p.add_argument('--source-commit',required=True)
    p.add_argument('--upgrade-from',action='append',required=True);p.add_argument('--validation');p.add_argument('--licenses',type=Path)
    a=p.parse_args();m=package(a.app,a.partition,a.output,a.upgrade_from,a.source_commit,a.validation,a.licenses)
    print(json.dumps({k:m[k] for k in ('version','sha256','size','install')},indent=2))
