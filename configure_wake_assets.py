"""Repack EchoEar assets with an offline custom wake phrase, preserving visuals."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile

TABLE = struct.Struct('<32sIIHH')

def unpack(blob):
    count, checksum, length = struct.unpack_from('<III', blob)
    if length > len(blob) - 12 or count * TABLE.size > length:
        raise ValueError('Invalid asset header')
    if sum(blob[12:12 + length]) & 0xffff != checksum:
        raise ValueError('Asset checksum mismatch')
    data_start = 12 + count * TABLE.size
    assets = {}
    for i in range(count):
        raw_name, size, offset, width, height = TABLE.unpack_from(blob, 12 + i * TABLE.size)
        name = raw_name.split(b'\0')[0].decode()
        start = data_start + offset
        if name in assets or start + 2 + size > 12 + length or blob[start:start + 2] != b'ZZ':
            raise ValueError('Invalid asset entry')
        assets[name] = (blob[start + 2:start + 2 + size], width, height)
    return assets

def pack(assets):
    table, data = bytearray(), bytearray()
    for name, (content, width, height) in assets.items():
        encoded = name.encode()
        if len(encoded) >= 32:
            raise ValueError('Asset name too long')
        table.extend(TABLE.pack(encoded, len(content), len(data), width, height))
        data.extend(b'ZZ' + content)
    body = table + data
    return struct.pack('<III', len(assets), sum(body) & 0xffff, len(body)) + body

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('backup', type=Path)
    ap.add_argument('output', type=Path)
    args = ap.parse_args()
    root = Path(__file__).resolve().parent
    original = unpack(args.backup.read_bytes())
    assets = original.copy()
    script = root / 'firmware/taibai/scripts/spiffs_assets/pack_model.py'
    spec = importlib.util.spec_from_file_location('pack_model', script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as directory:
        staging = Path(directory) / 'models'; staging.mkdir()
        model = root / 'firmware/taibai/managed_components/espressif__esp-sr/model/multinet_model/mn5q8_cn'
        shutil.copytree(model, staging / model.name)
        model_bin = Path(directory) / 'srmodels.bin'
        module.pack_models(str(staging), str(model_bin))
        assets['srmodels.bin'] = (model_bin.read_bytes(), 0, 0)
    index = json.loads(assets['index.json'][0])
    index['multinet_model'] = {'language': 'cn', 'duration': 3000, 'threshold': 0.2,
        'commands': [{'command': 'xiao bao tong xue', 'text': '小宝同学', 'action': 'wake'}]}
    assets['index.json'] = (json.dumps(index, ensure_ascii=False).encode(), 0, 0)
    blob = pack(assets)
    if len(blob) > 8 * 1024 * 1024:
        raise ValueError('Assets exceed flash partition')
    restored = unpack(blob)
    for name in original:
        if name not in ('srmodels.bin', 'index.json') and restored[name] != original[name]:
            raise ValueError('Visual asset changed: ' + name)
    args.output.write_bytes(blob)
    print(f'Custom wake assets: {len(blob)} bytes; {len(assets)-2} visual/font assets preserved')

if __name__ == '__main__':
    main()
