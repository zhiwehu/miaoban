from datetime import datetime,timezone
import tempfile
from types import SimpleNamespace
import unittest
from voicehub.background import Operations
from voicehub.calendar_reminders import CalendarReminders,notification_id
class CalendarReminderTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.ops=Operations(self.tmp.name);self.seen=[];self.events=[];self.sessions={}
  self.scheduler=CalendarReminders(self.tmp.name,self.ops,self.sessions,self.events.append,lambda:True);self.now=datetime(2026,10,30,4,tzinfo=timezone.utc).timestamp()
 async def asyncTearDown(self):await self.ops.close();self.tmp.cleanup()
 def create(self,device='robot',key='one'):
  return self.scheduler.store.create('开会','2026-10-30T12:01',remind_minutes=0,device_id=device,request_key=key,now=self.now)
 async def test_due_only_targets_original_device_once_and_restart_does_not_duplicate(self):
  event=self.create();self.sessions['other']=SimpleNamespace(id='other',device_id='other-device',closed=False,notification_ids=set())
  self.ops.bind('other',self.seen.append);await self.scheduler.tick(self.now+61);self.assertFalse(self.seen)
  self.sessions['one']=SimpleNamespace(id='one',device_id='robot',closed=False,notification_ids=set());self.ops.bind('one',self.seen.append)
  await self.scheduler.tick(self.now+62);self.assertEqual(len(self.seen),1);identity=notification_id(event['event_id'])
  # A real Session deduplicates queued notifications; the durable record itself is unique.
  await self.scheduler.tick(self.now+63);self.assertEqual(len(self.ops.recent()),1)
  self.ops.announced(identity);await self.scheduler.tick(self.now+64);self.assertEqual(self.scheduler.store.get(event['event_id'])['reminder_status'],'announced')
  self.assertEqual(len(self.ops.recent()),1)
 async def test_cancelled_missed_and_page_only_reminders(self):
  event=self.create();self.scheduler.store.cancel(event['event_id']);await self.scheduler.tick(self.now+61);self.assertFalse(self.ops.recent())
  missed=self.create(key='two');await self.scheduler.tick(self.now+3700);self.assertEqual(self.scheduler.store.get(missed['event_id'])['reminder_status'],'missed')
  page=self.create(device='',key='three');await self.scheduler.tick(self.now+61);self.assertEqual(self.scheduler.store.get(page['event_id'])['reminder_status'],'page');self.assertTrue(any(e['type']=='calendar.reminder' for e in self.events))
 async def test_queued_offline_reminder_expires_instead_of_speaking_days_later(self):
  event=self.create();self.sessions['one']=SimpleNamespace(id='one',device_id='robot',closed=False,notification_ids=set());self.ops.bind('one',self.seen.append)
  await self.scheduler.tick(self.now+61);self.sessions.clear();await self.scheduler.tick(self.now+3700)
  self.assertEqual(self.scheduler.store.get(event['event_id'])['reminder_status'],'missed');self.assertEqual(self.ops.get(notification_id(event['event_id']))['delivery'],'cancelled')
