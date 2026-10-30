from datetime import datetime,timedelta,timezone
from pathlib import Path
import tempfile
import unittest
from voicehub.calendar_store import CalendarStore
class CalendarStoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=CalendarStore(self.tmp.name);self.now=datetime(2026,10,29,4,tzinfo=timezone.utc).timestamp()
 def create(self,**changes):
  args={'title':'开会','start_time':'2026-10-30T15:00','remind_minutes':10,'device_id':'robot','session_id':'one','request_key':'request-one','now':self.now};args.update(changes);return self.store.create(**args)
 def test_create_query_idempotency_and_cancel_persist(self):
  event=self.create();self.assertEqual(event['start_time'],'2026-10-30T15:00:00+08:00');self.assertEqual(event['remind_at'],'2026-10-30T14:50:00+08:00')
  self.assertEqual(self.create()['event_id'],event['event_id']);self.assertEqual(len(self.store.query('2026-10-30T00:00','2026-10-31T00:00')['events']),1)
  self.store.cancel(event['event_id']);self.assertFalse(CalendarStore(self.tmp.name).query('2026-10-30T00:00','2026-10-31T00:00')['events'])
 def test_invalid_times_dst_and_repeated_key_with_changed_arguments_rejected(self):
  for value in ('2026-10-28T15:00','2026-02-30T15:00','2026-10-30','0001-01-01T00:00'):
   with self.assertRaises(ValueError):self.create(start_time=value)
  self.create()
  with self.assertRaises(ValueError):self.create(title='不同标题')
  self.store.save_settings({'timezone':'America/New_York'})
  with self.assertRaises(ValueError):self.create(start_time='2026-11-01T01:30',request_key='dst')
