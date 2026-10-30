import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from voicehub.config import DEFAULT
from voicehub.workflows.manager import WorkflowManager
from voicehub.workflows.chat import candidate,route
from tests.test_workflow_manager import PLAN

class ChatTests(unittest.IsolatedAsyncioTestCase):
 async def test_voice_confirmation_is_exact_session_bound_and_cancellation_stops_send(self):
  with tempfile.TemporaryDirectory() as root:
   manager=WorkflowManager(Path(root),lambda:copy.deepcopy(DEFAULT));manager.settings.save({'smtp_enabled':True,'host':'smtp.example.com','from_address':'sender@example.com'})
   from voicehub.workflows.mail import MailStore
   sent=[];manager.mail=MailStore(root,transport=lambda *a:sent.append(a) or {'status':'submitted'})
   async def invoke(name,args):
    if name=='email_prepare':return manager.mail.prepare(**args)
    return manager.mail.send(args['draft_id'],args['approval_token'],args['task_id'])
   manager.email_invoker=invoke
   pipeline=SimpleNamespace(local=SimpleNamespace(workflows=manager),session_id='one')
   task=manager.create('测试','one');manager.set_plan(task['id'],PLAN);[e async for e in manager.run(task['id'],pipeline)]
   def chat(text,p=pipeline):return route(p,[{'role':'user','content':text}])
   self.assertIsNone(chat('我还没有确认发送'))
   other=SimpleNamespace(local=pipeline.local,session_id='other');[e async for e in chat('确认发送',other)];self.assertFalse(sent)
   [e async for e in chat('确认执行任务')];self.assertFalse(sent)
   [e async for e in chat('不要发送')];self.assertEqual(manager.get(task['id'])['status'],'cancelled')
   [e async for e in chat('确认发送')];self.assertFalse(sent);await manager.close()
 def test_normal_chat_and_capability_questions_do_not_start_tasks(self):
  for text in ['今天星期几','再见','你能生成文件吗？','怎么发送邮件','介绍一下邮件功能']:self.assertFalse(candidate(text))
  for text in ['生成一份报告并发给 demo@example.com','查一下内存再写成文档']:self.assertTrue(candidate(text))
