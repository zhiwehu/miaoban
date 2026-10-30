import hashlib
import json
from pathlib import Path
import runpy
import struct
import tempfile
import unittest
from voicehub.firmware_release import inspect_application, install_metadata


def application():
    data=bytearray(336);data[0]=0xe9;data[1]=1;data[3]=0x40;data[23]=1
    struct.pack_into('<H',data,12,9);struct.pack_into('<II',data,24,0x3c190020,256)
    struct.pack_into('<I',data,32,0xabcd5432);data[48:52]=b'test';data[80:87]=b'xiaozhi'
    value=0xef
    for byte in data[32:288]:value^=byte
    data[303]=value;data[304:]=hashlib.sha256(data[:304]).digest()
    return bytes(data)


class FirmwareReleaseTests(unittest.TestCase):
    def test_valid_image_and_corruption(self):
        raw=application();self.assertEqual(inspect_application(raw)['version'],'test')
        for offset in (0,12,50,303,335):
            changed=bytearray(raw);changed[offset]^=1
            with self.assertRaises(ValueError):inspect_application(changed)

    def test_metadata_requires_explicit_hardware_validation_state(self):
        value={'board':'echoear-s3','partition_bytes':3072,'partition_sha256':'a'*64,'upgrade_from_sha256':['b'*64]}
        self.assertIsNone(install_metadata({'install':value}))
        value['validated']=False
        self.assertFalse(install_metadata({'install':value})['validated'])

    def test_packaging_never_enables_an_unverified_image(self):
        package=runpy.run_path(str(Path(__file__).resolve().parents[2]/'scripts/package_echoear_release.py'))['package']
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);app=root/'app.bin';app.write_bytes(application())
            table=bytearray(b'\xff'*3072)
            entries=[('nvs',1,2,0x9000,0x4000),('otadata',1,0,0xd000,0x2000),('phy_init',1,1,0xf000,0x1000),
                ('ota_0',0,16,0x20000,0x3f0000),('ota_1',0,17,0x410000,0x3f0000),('assets',1,130,0x800000,0x800000)]
            for i,(name,kind,sub,address,size) in enumerate(entries):
                struct.pack_into('<HBBII16sI',table,i*32,0x50aa,kind,sub,address,size,name.encode(),0)
            table[192:208]=b'\xeb\xeb'+b'\xff'*14;table[208:224]=hashlib.md5(table[:192]).digest()
            part=root/'part.bin';part.write_bytes(table)
            metadata=package(app,part,root/'candidate',['b'*64],'test-source')
            self.assertFalse(metadata['install']['validated'])
            record=root/'record.json';record.write_text(json.dumps({'sha256':metadata['sha256'],'voice_reply_verified':True}))
            with self.assertRaises(ValueError):package(app,part,root/'bad',['b'*64],'test-source',record)
            self.assertFalse((root/'bad').exists())
