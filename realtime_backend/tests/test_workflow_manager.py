import asyncio
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from voicehub.config import DEFAULT
from voicehub.workflows.manager import WorkflowManager
from voicehub.workflows.mail import MailStore
from voicehub.tools import ToolNotice

PLAN={'steps':[{'id':'doc','tool':'document.create','arguments':{'title':'报告','format':'md','content':'真实内容'}},
 {'id':'draft','tool':'email.prepare','arguments':{'to':'recipient@example.com','subject':'报告','body':'请查收','attachments':[{'from':'doc.artifact_id'}]}},
 {'id':'send','tool':'email.send','arguments':{'draft_id':{'from':'draft.draft_id'}}}]}
class ManagerTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.cfg=copy.deepcopy(DEFAULT)
  self.manager=WorkflowManager(self.root,lambda:self.cfg);self.sent=[]
  self.manager.settings.save({'smtp_enabled':True,'host':'smtp.example.com','from_address':'sender@example.com','username':'sender','password':'fixture-secret'})
  self.manager.mail=MailStore(self.root,transport=lambda msg,cfg,password:self.sent.append(msg) or {'status':'submitted'})
  async def invoke(name,args):
   if name=='email_prepare':return self.manager.mail.prepare(**args)
   return self.manager.mail.send(args['draft_id'],args['approval_token'],args['task_id'])
  self.manager.email_invoker=invoke
  self.pipeline=SimpleNamespace(config=self.cfg,session_id='session-one')
 async def asyncTearDown(self):await self.manager.close();self.tmp.cleanup()
 async def start(self,plan=PLAN):
  task=self.manager.create('生成文件并发邮件','session-one');self.manager.set_plan(task['id'],plan)
  events=[e async for e in self.manager.run(task['id'],self.pipeline)]
  return self.manager.get(task['id']),events
 async def test_artifact_draft_pause_confirmation_send_and_duplicate_prevention(self):
  task,events=await self.start();self.assertEqual(task['status'],'waiting_confirmation');self.assertFalse(self.sent)
  self.assertTrue(any(isinstance(e,ToolNotice) for e in events));self.assertEqual(task['steps'][0]['status'],'completed')
  with self.assertRaises(ValueError):self.manager.approve(task['id'],'another-session')
  self.manager.approve(task['id'],'session-one');[e async for e in self.manager.run(task['id'],self.pipeline)]
  self.assertEqual(self.manager.get(task['id'])['status'],'completed');self.assertEqual(len(self.sent),1)
  with self.assertRaises(ValueError):self.manager.approve(task['id'],'session-one')
  [e async for e in self.manager.run(task['id'],self.pipeline)];self.assertEqual(len(self.sent),1)
 async def test_missing_smtp_never_claims_sent_and_contact_input_resumes(self):
  self.manager.settings.save({'smtp_enabled':False})
  plan={'steps':[{'id':'who','tool':'contacts.lookup','arguments':{'query':'张三'}},
    {'id':'draft','tool':'email.prepare','arguments':{'to':{'from':'who.email'},'subject':'测试','body':'草稿','attachments':[]}},
    {'id':'send','tool':'email.send','arguments':{'draft_id':{'from':'draft.draft_id'}}}]}
  task,_=await self.start(plan);self.assertEqual(task['status'],'waiting_input')
  self.manager.answer(task['id'],'session-one','zhangsan@example.com');[e async for e in self.manager.run(task['id'],self.pipeline)]
  self.assertEqual(self.manager.get(task['id'])['status'],'waiting_configuration');self.assertFalse(self.sent)
 async def test_changed_draft_or_expired_approval_must_be_reconfirmed(self):
  task,_=await self.start();self.manager.settings.save({'from_address':'different@example.com'})
  with self.assertRaises(ValueError):self.manager.approve(task['id'],'session-one')
  self.assertFalse(self.sent)
 async def test_cancel_prevents_later_confirmation_and_unknown_send_cannot_resume(self):
  task,_=await self.start();await self.manager.cancel(task['id'],'session-one')
  with self.assertRaises(ValueError):self.manager.approve(task['id'],'session-one')
  task,_=await self.start();self.manager.mail.transport=lambda *args:{'status':'unknown','code':'mail_delivery_unknown'}
  self.manager.approve(task['id'],'session-one');[e async for e in self.manager.run(task['id'],self.pipeline)]
  self.assertEqual(self.manager.get(task['id'])['status'],'unknown')
  with self.assertRaises(ValueError):self.manager.resume(task['id'],'session-one')
 async def test_other_tool_combinations_and_failed_step_do_not_replay_completed_artifact(self):
  plan={'steps':[{'id':'one','tool':'document.create','arguments':{'title':'文本','format':'txt','content':'one'}},
                {'id':'two','tool':'document.create','arguments':{'title':'表格','format':'csv','content':'name,count\na,2'}}]}
  task,_=await self.start(plan);self.assertEqual(task['status'],'completed')
  self.assertNotEqual(task['steps'][0]['result']['artifact_id'],task['steps'][1]['result']['artifact_id'])
 async def test_incremental_planner_references_and_duplicate_repair(self):
  import json
  responses=[{'tool':'document.create','arguments':{'title':'纪要','format':'docx','content':'内容'}},
   {'tool':'email.prepare','arguments':{'to':'demo@example.com','subject':'纪要','body':'请查收','attachments':['$step1.artifact_id']}},
   {'tool':'email.send','arguments':{'draft_id':'$step2.draft_id'}},
   {'tool':'email.send','arguments':{'draft_id':'$step2.draft_id'}},{'done':True}]
  class Planner:
   async def raw_chat(self,*args,**kwargs):yield json.dumps(responses.pop(0))
  task=self.manager.create('生成文档并发邮件','session-one');await self.manager.plan(task['id'],Planner());task=self.manager.get(task['id'])
  self.assertEqual(len(task['steps']),3);self.assertEqual(task['steps'][2]['arguments']['draft_id'],{'from':'step2.draft_id'})
 async def test_planner_cannot_pass_literal_fake_draft_id(self):
  import json
  class Planner:
   async def raw_chat(self,*args,**kwargs):yield json.dumps({'tool':'email.send','arguments':{'draft_id':'step1.draft_id'}})
  task=self.manager.create('测试错误引用','session-one');await self.manager.plan(task['id'],Planner())
  self.assertEqual(self.manager.get(task['id'])['status'],'waiting_input');self.assertFalse(self.sent)
 async def test_provider_failure_records_failed_state_instead_of_leaving_running(self):
  from voicehub.models import ProviderError
  async def invoke(*args):raise ProviderError('provider_failed')
  self.manager._invoke=invoke
  task,_=await self.start({'steps':[{'id':'text','tool':'text.compose','arguments':{'instruction':'测试'}}]})
  self.assertEqual(task['status'],'failed');self.assertEqual(task['steps'][0]['status'],'failed')
 async def test_lost_send_result_after_smtp_acceptance_is_unknown_and_never_retried(self):
  invoke=self.manager.email_invoker
  async def lose_result(name,args):
   result=await invoke(name,args)
   if name=='email_send':raise ValueError('invalid_json')
   return result
  self.manager.email_invoker=lose_result
  task,_=await self.start();self.manager.approve(task['id'],'session-one')
  [e async for e in self.manager.run(task['id'],self.pipeline)]
  self.assertEqual(self.manager.get(task['id'])['status'],'unknown');self.assertEqual(len(self.sent),1)
  with self.assertRaises(ValueError):self.manager.resume(task['id'],'session-one')
