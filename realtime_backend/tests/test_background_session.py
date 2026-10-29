import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from voicehub.background import Operations
from voicehub.session import Session
from voicehub.store import Store
from voicehub.tools import ToolNotice,ToolStatus
class BackgroundSessionTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'history.sqlite3');self.ops=Operations(self.tmp.name);self.events=[];self.release=asyncio.Event();self.started=asyncio.Event()
  parent=self
  class Pipeline:
   local=SimpleNamespace(operations=parent.ops)
   async def chat(self,history):
    if history[-1]['content']=='慢查询':
     yield ToolNotice('slow','收到，我来查询。');parent.started.set();yield ToolStatus('slow','started');await parent.release.wait();yield ToolStatus('slow','completed');yield '慢查询真实结果。'
    else:yield '这是新一轮回答。'
   async def speech(self,text):yield b'\0\0'*320
  self.pipeline=Pipeline()
  async def send(event):self.events.append(event)
  self.session=Session('one','fixture',self.store,self.pipeline,send,playback_ack=False)
 async def asyncTearDown(self):await self.session.close();await self.ops.close();self.store.close();self.tmp.cleanup()
 async def wait_for(self,predicate):
  async with asyncio.timeout(5):
   while not predicate():await asyncio.sleep(.01)
 async def test_new_input_does_not_cancel_tool_and_result_speaks_as_new_turn(self):
  await self.session.submit('慢查询');original=self.session.turn.id;await self.started.wait()
  await self.session.submit('新问题');await self.wait_for(lambda:any(e['type']=='reply.final' and '新一轮' in e.get('text','') for e in self.events))
  self.assertTrue(self.ops.tasks);self.release.set()
  await self.wait_for(lambda:any(e['type']=='reply.final' and '慢查询真实结果' in e.get('text','') for e in self.events))
  final=next(e for e in self.events if e['type']=='reply.final' and '慢查询真实结果' in e.get('text',''));self.assertNotEqual(final['turn_id'],original)
  self.assertTrue(any(e['type']=='tool.completed' and e.get('background') for e in self.events))
  self.assertEqual(len([m for m in self.store.messages('one') if m['role']=='user']),2)
 async def test_disconnect_keeps_work_running_and_pending_result(self):
  await self.session.submit('慢查询');await self.started.wait();await self.session.close();self.assertTrue(self.ops.tasks)
  self.release.set();await self.wait_for(lambda:not self.ops.tasks)
  self.assertEqual(self.ops.pending('one')[0]['text'],'慢查询真实结果。')
 async def test_interrupted_announcement_is_replayed_after_new_answer(self):
  announcing=asyncio.Event();allow=asyncio.Event()
  async def speech(text):
   if text.startswith('后台任务结果') and not allow.is_set():announcing.set();await allow.wait()
   yield b'\0\0'*320
  self.pipeline.speech=speech
  await self.session.submit('慢查询');await self.started.wait();self.release.set();await announcing.wait()
  await self.session.submit('插话');allow.set()
  await self.wait_for(lambda:any(e['type']=='reply.final' and '后台任务结果' in e.get('text','') for e in self.events))
  self.assertTrue(any(e['type']=='reply.final' and '新一轮' in e.get('text','') for e in self.events));self.assertFalse(self.ops.tasks)
 async def test_explicit_cancel_is_distinct_from_interruption(self):
  await self.session.submit('慢查询');await self.started.wait();await self.session.submit('取消任务')
  await self.wait_for(lambda:not self.ops.tasks)
  self.assertEqual(self.ops.recent('one')[0]['status'],'unknown');self.assertFalse(self.release.is_set())
 async def test_workflow_owner_transfers_before_ack_so_explicit_cancel_reaches_executor(self):
  from voicehub.workflows.manager import WorkflowManager
  from voicehub.config import DEFAULT
  manager=WorkflowManager(self.tmp.name,lambda:DEFAULT);self.pipeline.local.workflows=manager
  task=manager.create('慢任务','one');manager.set_plan(task['id'],{'steps':[{'id':'text','tool':'text.compose','arguments':{'instruction':'测试'}}]})
  async def invoke(*args):self.started.set();await self.release.wait();return {'content':'不应完成'}
  manager._invoke=invoke
  async def chat(history):
   async for item in manager.run(task['id'],self.pipeline):yield item
  self.pipeline.chat=chat
  await self.session.submit('慢任务');await self.started.wait()
  self.assertIn(manager.jobs[task['id']],self.ops.tasks.values())
  await manager.cancel(task['id'],'one');self.assertEqual(manager.get(task['id'])['status'],'cancelled');self.assertFalse(self.release.is_set())
  await manager.close()
 async def test_idle_timeout_keeps_channel_open_until_background_result_is_announced(self):
  import time
  await self.session.activate();await self.session.submit('慢查询');await self.started.wait()
  await self.wait_for(lambda:self.session.turn is None)
  self.session.last_activity=time.monotonic()-100;await self.session.idle()
  self.assertEqual(self.session.state,'listening');self.assertTrue(self.ops.tasks)
