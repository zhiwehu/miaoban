import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from voicehub.background import Operations
from voicehub.timer_reminders import TimerReminders
from voicehub.session import Session
from voicehub.store import Store

class TimerReminderTests(unittest.IsolatedAsyncioTestCase):
 async def test_device_isolation_cancel_stale_resume_and_restart(self):
  with tempfile.TemporaryDirectory() as d:
   ops=Operations(d);sessions={};seen=[];timers=TimerReminders(d,ops,sessions,lambda:True)
   now=time.time();timers.store.timer('start','泡面',1,device='robot',now=now)
   sessions['x']=SimpleNamespace(id='x',device_id='other',closed=False,notification_ids=set());ops.bind('x',seen.append)
   await timers.tick(now+2);self.assertFalse(seen)
   sessions['one']=SimpleNamespace(id='one',device_id='robot',closed=False,notification_ids=set());ops.bind('one',seen.append)
   await timers.tick(now+2);self.assertEqual(len(seen),1)
   record=seen[0];self.assertTrue(timers.valid(record));ops.announced(record['id'])
   await TimerReminders(d,ops,sessions,lambda:True).tick(now+3);self.assertEqual(len(seen),1)
   timers.store.timer('cancel','泡面',device='robot',now=now+3);self.assertFalse(timers.valid(record));await ops.close()
 async def test_interrupted_timer_announcement_replays_after_quiet(self):
  with tempfile.TemporaryDirectory() as d:
   ops=Operations(d);store=Store(Path(d)/'history.sqlite3');sessions={};events=[];timers=TimerReminders(d,ops,sessions,lambda:True)
   started=asyncio.Event();release=asyncio.Event();attempts=[]
   class Pipeline:
    local=SimpleNamespace(operations=ops,timer_reminders=timers)
    async def speech(self,text):
     attempts.append(text)
     if len(attempts)==1:started.set();await release.wait()
     yield b'\0\0'*320
    async def chat(self,history):yield '好的。'
   async def send(event):events.append(event)
   session=Session('one','robot',store,Pipeline(),send,playback_ack=False);sessions['one']=session
   try:
    now=time.time();timers.store.timer('start','煮蛋',1,device='robot',session='one',now=now-2)
    await timers.tick(now);await asyncio.wait_for(started.wait(),3);await session.interrupt('user_input')
    session.last_activity=time.monotonic()-1;release.set()
    async with asyncio.timeout(5):
     while not any(e['type']=='reply.final' and '煮蛋' in e.get('text','') for e in events):await asyncio.sleep(.01)
    self.assertGreaterEqual(len(attempts),2)
   finally:await session.close();await ops.close();store.close()
