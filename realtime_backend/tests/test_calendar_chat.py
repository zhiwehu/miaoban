from datetime import date,datetime,timedelta
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from voicehub.calendar_store import CalendarStore
from voicehub.calendar_chat import route,resolve_date
from voicehub.config import DEFAULT
from voicehub.tools import ToolNotice
class CalendarChatTests(unittest.IsolatedAsyncioTestCase):
 async def test_missing_time_and_recurring_request_do_not_create_events(self):
  with tempfile.TemporaryDirectory() as root:
   store=CalendarStore(root)
   class Pipeline:
    local=SimpleNamespace(calendar=store,data_root=root);session_id='one';device_id='fixture';config=DEFAULT
    async def raw_chat(self,*args,**kwargs):yield json.dumps({'title':'开会','date':'明天'})
   p=Pipeline();stream=route(p,[{'role':'user','content':'明天提醒我开会'}]);self.assertIsInstance(await anext(stream),ToolNotice)
   replies=[v async for v in stream];self.assertIn('几点',replies[-1]);self.assertFalse(store.load()['events'])
   replies=[v async for v in route(p,[{'role':'user','content':'每天提醒我开会'}])];self.assertIn('单次',replies[-1]);self.assertFalse(store.load()['events'])
   self.assertIsNone(route(p,[{'role':'user','content':'今天星期几'}]))
 def test_relative_date_uses_host_calendar_and_next_week_is_unambiguous(self):
  today=date(2026,10,30)
  self.assertEqual(resolve_date('明天',today),date(2026,10,31));self.assertEqual(resolve_date('下周一',today),date(2026,11,2));self.assertEqual(resolve_date('周一',today),date(2026,11,2))
 async def test_missing_time_can_be_completed_in_next_turn_without_losing_title(self):
  with tempfile.TemporaryDirectory() as root:
   store=CalendarStore(root)
   class Pipeline:
    local=SimpleNamespace(calendar=store,data_root=root);session_id='one';device_id='fixture';config=DEFAULT;calls=0
    async def raw_chat(self,*args,**kwargs):
     self.calls+=1
     yield json.dumps({'title':'开会','date':'明天'} if self.calls==1 else {'title':'开会','date':'明天','time':'15:00'})
   p=Pipeline();[v async for v in route(p,[{'role':'user','content':'明天提醒我开会'}])]
   self.assertTrue(p._calendar_pending)
   self.assertIsNone(route(p,[{'role':'user','content':'今天星期几'}]))
   [v async for v in route(p,[{'role':'user','content':'下午三点'}])]
   self.assertEqual(len(store.load()['events']),1);self.assertIsNone(p._calendar_pending)
 async def test_model_operation_metadata_must_match_host_cancellation_intent(self):
  from datetime import timezone
  with tempfile.TemporaryDirectory() as root:
   store=CalendarStore(root);future=datetime.now(timezone.utc)+timedelta(days=1);event=store.create('取快递',future.isoformat(),remind_minutes=0)
   class Pipeline:
    local=SimpleNamespace(calendar=store,data_root=root);session_id='one';device_id='fixture';config=DEFAULT
    async def raw_chat(self,*args,**kwargs):yield json.dumps({'operation':'cancel','title':'取快递'})
   p=Pipeline();[v async for v in route(p,[{'role':'user','content':'取消取快递的提醒'}])]
   self.assertEqual(store.get(event['event_id'])['status'],'cancelled')
   async def invalid(*args,**kwargs):yield json.dumps({'operation':'create','title':'其他事项','date':'明天','time':'15:00'})
   p.raw_chat=invalid;[v async for v in route(p,[{'role':'user','content':'查一下明天日程'}])]
   self.assertEqual(len(store.load()['events']),1)
