from datetime import datetime,timedelta,timezone
import tempfile
import unittest
from aiohttp.test_utils import TestClient,TestServer
from voicehub.app import create_app
from tests.test_sessions import Pipeline
class CalendarApiTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.client=TestClient(TestServer(create_app(self.tmp.name,pipeline_factory=lambda *args:Pipeline())));await self.client.start_server()
 async def asyncTearDown(self):await self.client.close();self.tmp.cleanup()
 async def test_settings_and_local_events_are_protected_and_invalid_time_cannot_create(self):
  r=await self.client.put('/api/calendar/settings',json={'timezone':'not/a-zone'});self.assertEqual(r.status,400)
  r=await self.client.post('/api/calendar/events',json={'title':'缺时间'});self.assertEqual(r.status,400)
  start=datetime.now(timezone.utc)+timedelta(days=1)
  r=await self.client.post('/api/calendar/events',json={'title':'测试','start_time':start.isoformat(),'remind_minutes':0});self.assertEqual(r.status,201);event=(await r.json())['event']
  r=await self.client.get('/api/calendar/events');self.assertEqual((await r.json())['count'],1)
  r=await self.client.post('/api/calendar/events/'+event['event_id']+'/cancel',json={});self.assertEqual(r.status,200)
  r=await self.client.get('/api/calendar/events');self.assertEqual((await r.json())['count'],0)
  r=await self.client.post('/api/calendar/events',json={},headers={'Origin':'https://outside.invalid'});self.assertEqual(r.status,403)
