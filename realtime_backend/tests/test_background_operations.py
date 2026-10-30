import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from voicehub.background import Operations
from voicehub.tools import ToolStatus
class OperationsTests(unittest.IsolatedAsyncioTestCase):
 async def test_detached_work_completes_and_is_replayed_to_original_scope(self):
  with tempfile.TemporaryDirectory() as root:
   ops=Operations(root);gate=asyncio.Event();release=asyncio.Event();seen=[]
   async def source():
    yield ToolStatus('slow','started');await release.wait();yield ToolStatus('slow','completed');yield '查询结果是真实的。'
   record=ops.start('one','turn','慢查询',source(),gate)
   ops.bind('other',lambda result:seen.append(result));gate.set();await asyncio.sleep(.01);self.assertEqual(ops.get(record['id'])['status'],'running')
   release.set();await asyncio.gather(*list(ops.tasks.values()));self.assertFalse(seen)
   ops.bind('one',lambda result:seen.append(result));self.assertEqual(seen[0]['text'],'查询结果是真实的。')
   ops.announced(record['id']);self.assertFalse(ops.pending('one'));await ops.close()
 async def test_explicit_cancel_and_capacity_are_bounded(self):
  with tempfile.TemporaryDirectory() as root:
   ops=Operations(root);gate=asyncio.Event()
   async def source():
    await asyncio.Event().wait();yield 'never'
   records=[ops.start('one','turn','慢查询',source(),gate) for _ in range(3)]
   extra=source()
   with self.assertRaises(ValueError):ops.start('one','turn','excess',extra,gate)
   await extra.aclose();await ops.cancel(records[0]['id']);self.assertIn(ops.get(records[0]['id'])['status'],('cancelled','unknown'));await ops.close()
 async def test_restart_marks_unfinished_work_unknown_without_replaying_tools(self):
  from voicehub.workflows.storage import atomic_json
  import uuid
  with tempfile.TemporaryDirectory() as root:
   path=Path(root)/'operations';path.mkdir();identity=uuid.uuid4().hex
   atomic_json(path/(identity+'.json'),{'id':identity,'session_id':'one','turn_id':'old','label':'旧任务','status':'running','delivery':'pending','created':1,'last_tool':{'name':'mail','phase':'started'}})
   events=[];ops=Operations(root,publish=events.append)
   self.assertFalse(ops.tasks);self.assertEqual(ops.get(identity)['status'],'unknown');self.assertTrue(any(e['type']=='tool.failed' for e in events));await ops.close()
