"""Strict ESP32-S3 application inspection and upgrade-profile metadata."""
import hashlib
import struct

BOARD = 'echoear-s3'
APP_SIZE = 0x3f0000


def inspect_application(raw):
    if not 288 <= len(raw) <= APP_SIZE or raw[0] != 0xe9 or not 1 <= raw[1] <= 16:
        raise ValueError('invalid_application_image')
    if struct.unpack_from('<H',raw,12)[0] != 9 or raw[3] >> 4 != 4:
        raise ValueError('application_target_mismatch')
    if struct.unpack_from('<I',raw,32)[0] != 0xabcd5432 or raw[23] != 1:
        raise ValueError('invalid_application_descriptor')
    offset, checksum = 24, 0xef
    for _ in range(raw[1]):
        if offset+8 > len(raw): raise ValueError('invalid_image_segment')
        size = struct.unpack_from('<I',raw,offset+4)[0];offset += 8
        if offset+size > len(raw): raise ValueError('invalid_image_segment')
        for value in raw[offset:offset+size]: checksum ^= value
        offset += size
    check = ((offset+1+15)//16)*16-1
    if check+33 != len(raw) or raw[check] != checksum:
        raise ValueError('invalid_image_checksum')
    if hashlib.sha256(raw[:check+1]).digest() != raw[check+1:]:
        raise ValueError('invalid_image_hash')
    def text(offset): return raw[offset:offset+32].split(b'\0')[0].decode('utf-8','strict')
    if text(80) != 'xiaozhi': raise ValueError('unexpected_project')
    return dict(version=text(48),project=text(80),sha256=hashlib.sha256(raw).hexdigest(),
                size=len(raw),chip='esp32s3',flash_size='16MB')


def install_metadata(manifest):
    value = manifest.get('install')
    if not isinstance(value,dict): return None
    import re
    hashes = value.get('upgrade_from_sha256')
    if (value.get('board') != BOARD or value.get('partition_bytes') != 3072 or
        not isinstance(value.get('partition_sha256'),str) or
        not re.fullmatch('[0-9a-f]{64}',value['partition_sha256']) or
        not isinstance(hashes,list) or not 1 <= len(hashes) <= 16 or
        any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes) or
        type(value.get('validated')) is not bool): return None
    return dict(board=BOARD,partition_bytes=3072,partition_sha256=value['partition_sha256'],
                upgrade_from_sha256=hashes,validated=value['validated'])
