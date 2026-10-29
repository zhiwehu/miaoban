import tempfile
import unittest
from pathlib import Path
from voicehub.everyday_store import EverydayStore

class EverydayTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.store=EverydayStore(Path(self.tmp.name))
 def test_shopping_duplicate_and_atomic_ambiguity(self):
  s=self.store
  s.shopping('add',['两盒牛奶','一盒酸奶']);s.shopping('add',['两盒牛奶'])
  self.assertEqual(len(s.shopping('list')['items']),2)
  self.assertEqual(s.shopping('complete',['奶'])['status'],'needs_selection')
  self.assertTrue(all(i['state']=='pending' for i in s.shopping('list')['items']))
  s.shopping('complete',['牛奶'])
  self.assertEqual(s.shopping('list')['items'][0]['state'],'bought')
 def test_memory_persistence_no_guess_and_forget(self):
  s=self.store
  self.assertEqual(s.memory('recall','护照')['status'],'not_found')
  s.memory('remember','护照','书房第二个抽屉')
  self.assertEqual(EverydayStore(s.root).memory('recall','护照')['items'][0]['location'],'书房第二个抽屉')
  s.memory('forget','护照');self.assertEqual(s.memory('recall','护照')['status'],'not_found')
 def test_timer_scope_pause_resume_expiry(self):
  s=self.store
  t=s.timer('start','泡面',60,device='A',now=100)['items'][0]
  self.assertEqual(s.timer('status',device='B',now=110)['items'],[])
  self.assertEqual(s.timer('pause','泡面',device='A',now=110)['items'][0]['remaining_seconds'],50)
  self.assertEqual(s.due(now=1000),[])
  s.timer('resume','泡面',device='A',now=1000)
  self.assertEqual(s.due(now=1049),[])
  self.assertEqual(s.due(now=1050)[0]['id'],t['id'])
  s.timer('cancel','泡面',device='A',now=1051)
  self.assertEqual(s.due(now=1052),[])
 def test_timer_request_idempotency_and_validation(self):
  s=self.store
  a=s.timer('start','泡面',60,device='A',request='x',now=100)
  self.assertEqual(a,s.timer('start','泡面',60,device='A',request='x',now=100))
  with self.assertRaises(ValueError):s.timer('start','bad',0)
 def test_repurchase_matches_pending_item(self):
  s=self.store;s.shopping('add',['牛奶']);s.shopping('complete',['牛奶']);s.shopping('add',['牛奶'])
  self.assertEqual(s.shopping('complete',['牛奶'])['status'],'ok')
 def test_tombstones_do_not_exhaust_capacity(self):
  s=self.store
  for i in range(510):s.memory('remember','钥匙'+str(i),'抽屉');s.memory('forget','钥匙'+str(i))
  self.assertEqual(s.memory('list')['items'],[])
