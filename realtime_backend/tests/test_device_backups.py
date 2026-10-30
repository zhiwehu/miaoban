import hashlib
import json
import io
import os
import struct
import tempfile
import unittest
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from voicehub.app import create_app

MAC = 'AA:BB:CC:DD:EE:FF'
DATA = b'\xff' * (16 * 1024 * 1024)
LAYOUT = hashlib.sha256(DATA[0x8000:0x8000 + 3072]).hexdigest()
META = dict(mac=MAC, layout=LAYOUT, flashSize='16MB', sha256=hashlib.sha256(DATA).hexdigest(), current_sha256='a' * 64)
HEADER = json.dumps(META).encode()
BACKUP = b'VOICEHUB-DEVICE/1\n' + struct.pack('<I', len(HEADER)) + HEADER + DATA
QUERY = dict(mac=MAC, layout=LAYOUT, flashSize='16MB')
URL = '/api/clients/echoear-s3/backups'

class DeviceBackups(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp = tempfile.TemporaryDirectory()
  self.client = TestClient(TestServer(create_app(self.tmp.name)))
  await self.client.start_server()
 async def asyncTearDown(self):
  await self.client.close()
  self.tmp.cleanup()
 async def save(self, body=BACKUP, **kwargs):
  return await self.client.post(URL, data=io.BytesIO(body), headers={'Content-Type': 'application/octet-stream', **kwargs})
 async def test_automatic_save_readback_and_persistence(self):
  response = await self.save()
  self.assertEqual(response.status, 201)
  identity = (await response.json())['id']
  files = list(Path(self.tmp.name).glob('device-backups/*/*.vhdevice'))
  self.assertEqual(len(files), 1)
  self.assertEqual(files[0].read_bytes(), BACKUP)
  self.assertEqual(os.stat(files[0]).st_mode & 0o777, 0o600)
  self.assertEqual(os.stat(files[0].parent).st_mode & 0o777, 0o700)
  await self.client.close()
  self.client = TestClient(TestServer(create_app(self.tmp.name)))
  await self.client.start_server()
  result = await self.client.get(URL + '/latest', params=QUERY)
  self.assertEqual(result.status, 200)
  self.assertEqual(result.headers['X-Backup-Id'], identity)
  self.assertEqual(result.headers['Cache-Control'], 'no-store')
  self.assertEqual(await result.read(), BACKUP)
 async def test_recent_corrupt_backup_is_skipped_without_overwriting_older_copy(self):
  first = await self.save(); first_id = (await first.json())['id']
  second = await self.save(); second_id = (await second.json())['id']
  self.assertNotEqual(first_id, second_id)
  latest = await self.client.get(URL + '/latest', params=QUERY)
  self.assertEqual(latest.headers['X-Backup-Id'], second_id)
  older = await self.client.get(URL + '/latest', params={**QUERY, 'before': second_id})
  self.assertEqual(older.headers['X-Backup-Id'], first_id)
  path = next(Path(self.tmp.name).glob('device-backups/*/' + second_id + '.vhdevice'))
  with path.open('r+b') as stream:
   stream.seek(-1, 2); stream.write(b'\x00')
  latest = await self.client.get(URL + '/latest', params=QUERY)
  self.assertEqual(latest.status, 200)
  self.assertEqual(latest.headers['X-Backup-Id'], first_id)
  self.assertEqual(await latest.read(), BACKUP)
 async def test_invalid_backup_identity_paths_and_cross_origin_are_rejected(self):
  self.assertEqual((await self.save(BACKUP[:-1])).status, 400)
  self.assertEqual((await self.save(Origin='https://foreign.invalid')).status, 403)
  self.assertEqual((await self.save(Host='foreign.invalid')).status, 401)
  self.assertEqual(list(Path(self.tmp.name).glob('device-backups/*/*.vhdevice')), [])
  self.assertEqual((await self.client.get(URL+'/latest', params={**QUERY, 'mac':'../private'})).status, 400)
  await self.save()
  for query in [{**QUERY, 'mac':'11:22:33:44:55:66'}, {**QUERY, 'layout':'b'*64}, {**QUERY, 'flashSize':'32MB'}]:
   self.assertEqual((await self.client.get(URL+'/latest', params=query)).status, 404)
  self.assertEqual((await self.client.get(URL+'/latest', params={**QUERY, 'before':'../../auth.json'})).status, 400)
  self.assertEqual((await self.client.get(URL+'/latest', params=QUERY, headers={'Host':'foreign.invalid'})).status, 401)
