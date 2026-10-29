import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from voicehub.file_organizer import FileOrganizer

class OrganizerTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.base=Path(self.tmp.name);self.root=self.base/'files';self.root.mkdir();self.data=self.base/'data'
  self.org=FileOrganizer(self.data,self.root)
  (self.root/'report.pdf').write_bytes(b'doc');(self.root/'photo.jpg').write_bytes(b'image')
 def test_preview_confirm_execute_and_undo(self):
  plan=self.org.preview();self.assertEqual(plan['count'],2);self.assertTrue((self.root/'report.pdf').exists())
  with self.assertRaises(ValueError):self.org.execute(plan['id'],False)
  self.assertFalse((self.root/'文档').exists());self.assertEqual(self.org.execute(plan['id'],True)['status'],'completed')
  self.assertEqual((self.root/'文档/report.pdf').read_bytes(),b'doc')
  with self.assertRaises(ValueError):self.org.undo('wrong',True)
  self.assertEqual(self.org.undo(plan['id'],True)['status'],'reverted');self.assertEqual((self.root/'report.pdf').read_bytes(),b'doc')
  self.assertTrue((self.root/'文档').is_dir())
 def test_skips_symlinks_hidden_hardlinks_and_subdirectories(self):
  outside=self.base/'outside.txt';outside.write_text('outside');(self.root/'alias.txt').symlink_to(outside)
  (self.root/'.secret').write_text('secret');(self.root/'nested').mkdir();(self.root/'nested/child.txt').write_text('child')
  os.link(self.root/'report.pdf',self.root/'hard.pdf')
  plan=self.org.preview();self.assertEqual(plan['count'],1);self.org.execute(plan['id'],True)
  self.assertEqual(outside.read_text(),'outside');self.assertTrue((self.root/'nested/child.txt').exists())
 def test_changed_file_expired_plan_and_replaced_root_refused(self):
  plan=self.org.preview();(self.root/'report.pdf').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.org.execute(plan['id'],True)
  self.assertTrue((self.root/'photo.jpg').exists());plan=self.org.preview()
  with patch('voicehub.file_organizer.time.time',return_value=plan['created']+1000),self.assertRaises(ValueError):self.org.execute(plan['id'],True)
  moved=self.base/'renamed';self.root.rename(moved);self.root.mkdir()
  with self.assertRaises(ValueError):self.org.execute(plan['id'],True)
  self.assertTrue((moved/'photo.jpg').exists())
 def test_collision_and_category_symlink_never_overwrite_or_escape(self):
  plan=self.org.preview();(self.root/'文档').mkdir();(self.root/'文档/report.pdf').write_bytes(b'keep')
  with self.assertRaises(ValueError):self.org.execute(plan['id'],True)
  self.assertEqual((self.root/'文档/report.pdf').read_bytes(),b'keep');self.assertTrue((self.root/'photo.jpg').exists())
  (self.root/'文档/report.pdf').unlink();(self.root/'文档').rmdir()
  outside=self.base/'outside';outside.mkdir();(self.root/'文档').symlink_to(outside,target_is_directory=True)
  with self.assertRaises(ValueError):self.org.execute(plan['id'],True)
  self.assertEqual(list(outside.iterdir()),[])
 def test_configuration_change_blocks_old_plan_and_undo(self):
  plan=self.org.preview();other=self.base/'other';other.mkdir()
  with self.assertRaises(ValueError):FileOrganizer(self.data,other).execute(plan['id'],True)
  self.org.execute(plan['id'],True)
  with self.assertRaises(ValueError):FileOrganizer(self.data,other).undo(plan['id'],True)
 def test_undo_conflict_keeps_both_and_can_retry(self):
  plan=self.org.preview();self.org.execute(plan['id'],True);(self.root/'report.pdf').write_bytes(b'new')
  self.assertEqual(self.org.undo(plan['id'],True)['status'],'partial')
  self.assertEqual((self.root/'report.pdf').read_bytes(),b'new');self.assertEqual((self.root/'文档/report.pdf').read_bytes(),b'doc')
  (self.root/'report.pdf').unlink();self.assertEqual(self.org.undo(plan['id'],True)['status'],'reverted')
 def test_root_and_plan_identifiers_checked(self):
  plan=self.org.preview()
  for identity in ('../escape','',plan['id']+'x'):
   with self.assertRaises(ValueError):self.org.execute(identity,True)
  for root in (Path('/'),Path.home(),self.data):
   with self.assertRaises(ValueError):FileOrganizer(self.data,root).preview()
 def test_write_ahead_recovers_rename_before_save(self):
  plan=self.org.preview();original=self.org._save;calls=0
  def broken(state):
   nonlocal calls
   calls+=1
   if calls==2:raise OSError('simulated journal failure')
   return original(state)
  with patch.object(self.org,'_save',broken),self.assertRaises(OSError):self.org.execute(plan['id'],True)
  restored=FileOrganizer(self.data,self.root);self.assertEqual(restored.undo(plan['id'],True)['status'],'reverted')
  self.assertEqual((self.root/'report.pdf').read_bytes(),b'doc');self.assertTrue((self.root/'photo.jpg').exists())

class FileRoutingTests(unittest.IsolatedAsyncioTestCase):
 async def test_explicit_request_produces_real_mcp_preview_without_moves_or_names_for_model(self):
  import copy
  from types import SimpleNamespace
  from voicehub.config import DEFAULT
  from voicehub.tool_chat import chat
  from voicehub.tools import ToolNotice,ToolStatus
  with tempfile.TemporaryDirectory() as root:
   data=Path(root)/'data';data.mkdir();folder=Path(root)/'files';folder.mkdir();(folder/'private-name.pdf').write_bytes(b'doc')
   cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(planner='rex',file_organizer=True,file_directory=str(folder))
   (data/'settings.json').write_text(__import__('json').dumps(cfg))
   class P:
    config=cfg;local=SimpleNamespace(data_root=data,extensions=None)
    async def raw_chat(self,history,**kwargs):
     raise AssertionError('Explicit file command needs no extra LLM generation')
     yield ''
   result=[item async for item in chat(P(),[{'role':'user','content':'帮我整理一下指定目录里的文件'}])]
   self.assertIsInstance(result[0],ToolNotice)
   self.assertNotIn('private-name',str(result))
   self.assertIn('文件尚未移动',result[-1])
   self.assertTrue(any(isinstance(i,ToolStatus) and i.phase=='completed' and i.name=='file_organizer' for i in result))
   self.assertTrue((folder/'private-name.pdf').exists());self.assertFalse((folder/'文档').exists())
 async def test_disabled_tool_never_uses_model_or_directory(self):
  import copy
  from types import SimpleNamespace
  from voicehub.config import DEFAULT
  from voicehub.tool_chat import chat
  result=[item async for item in chat(SimpleNamespace(config=copy.deepcopy(DEFAULT)),[{'role':'user','content':'整理文件'}])]
  self.assertIn('尚未启用',result[0])
 def test_quoted_negative_and_other_intents_are_not_commands(self):
  from voicehub.extension_routing import file_plan
  for text in ('不要整理文件','解释整理文件是什么意思','查一下新闻','把“整理文件”翻译成英文','删除全部文件'):
   self.assertIsNone(file_plan(text),text)

 async def test_dynamic_file_tool_is_checked_before_old_fixed_labels(self):
  import copy,json
  from types import SimpleNamespace
  from voicehub.config import DEFAULT
  from voicehub.tool_chat import chat
  from voicehub.tools import ToolStatus
  with tempfile.TemporaryDirectory() as root:
   data=Path(root)/'data';data.mkdir();folder=Path(root)/'files';folder.mkdir();(folder/'file.pdf').write_text('sample')
   cfg=copy.deepcopy(DEFAULT);cfg['tools'].update(planner='rex',file_organizer=True,file_directory=str(folder))
   (data/'settings.json').write_text(json.dumps(cfg))
   class P:
    config=cfg;local=SimpleNamespace(data_root=data,extensions=None)
    async def raw_chat(self,history,**kwargs):
     if kwargs.get('system_prompt','').startswith('根据用户本轮'):
      yield '{"tool":"file_organizer","arguments":{"action":"preview"}}'
     else:yield '生成了待确认预览。'
   with patch('voicehub.tool_chat.rex_plan',side_effect=AssertionError('Rex has no file label')):
    result=[v async for v in chat(P(),[{'role':'user','content':'我想把目录中的文件按类型归档，先给我看看方案'}])]
   started=next(v for v in result if isinstance(v,ToolStatus) and v.phase=='started')
   self.assertEqual(started.name,'file_organizer');self.assertEqual(started.planner,'dynamic_llm')
   self.assertTrue((folder/'file.pdf').exists())


class FileSummaryTests(unittest.TestCase):
 def test_skipped_directories_are_not_described_as_files(self):
  import json
  from voicehub.mcp_tools.file_organizer import spoken_summary
  result={'content':[{'type':'text','text':json.dumps({'status':'pending_confirmation','count':3,'categories':{'文档':2,'图片':1},'skipped':4})}]}
  response=spoken_summary(result)
  self.assertIn('3个文件',response);self.assertIn('跳过4项',response)
  self.assertNotIn('4个文件',response);self.assertIn('文件尚未移动',response)
