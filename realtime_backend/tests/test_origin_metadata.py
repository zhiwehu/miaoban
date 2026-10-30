import unittest
import tempfile
from aiohttp.test_utils import TestClient,TestServer
from voicehub.app import create_app

class OriginMetadata(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.client=TestClient(TestServer(create_app(self.tmp.name)));await self.client.start_server()
 async def asyncTearDown(self):
  await self.client.close();self.tmp.cleanup()
 async def test_portless_loopback_origin_requires_same_origin_fetch_metadata(self):
  url='/api/connections/mqtt/stop'
  r=await self.client.post(url,headers={'Origin':'http://127.0.0.1','Sec-Fetch-Site':'same-origin'});self.assertEqual(r.status,200)
  for headers in [
   {'Origin':'http://127.0.0.1'},
   {'Origin':'http://127.0.0.1','Sec-Fetch-Site':'same-site'},
   {'Origin':'http://127.0.0.1:80','Sec-Fetch-Site':'same-origin'},
   {'Origin':'http://127.0.0.1:9000','Sec-Fetch-Site':'same-origin'},
   {'Origin':'https://127.0.0.1','Sec-Fetch-Site':'same-origin'},
   {'Origin':'http://localhost','Sec-Fetch-Site':'same-origin'},
   {'Origin':'https://foreign.invalid','Sec-Fetch-Site':'same-origin'},
   {'Origin':'null','Sec-Fetch-Site':'same-origin'},
   {'Origin':'http://127.0.0.1/path','Sec-Fetch-Site':'same-origin'},
   {'Origin':'http://127.0.0.1','Sec-Fetch-Site':'same-origin','Host':'foreign.invalid'},
  ]:
   with self.subTest(headers=headers):
    response=await self.client.post(url,headers=headers);self.assertEqual(response.status,403)
    self.assertEqual((await response.json())['error']['code'],'cross_origin_denied')
 async def test_exact_origin_remains_supported(self):
  response=await self.client.post('/api/connections/mqtt/stop',headers={'Origin':str(self.client.make_url('/')).rstrip('/')})
  self.assertEqual(response.status,200)
