import tempfile
import unittest
from unittest.mock import patch
from voicehub.workflows.mail import MailStore,MailSettings
from voicehub.workflows.artifacts import Artifacts
class MailTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=self.tmp.name
  self.settings=MailSettings(self.root);self.settings.save({'smtp_enabled':True,'host':'smtp.example.com','from_address':'sender@example.com','username':'sender','password':'fixture-only-secret'})
  self.sent=[];self.mail=MailStore(self.root,transport=lambda msg,cfg,password:self.sent.append(msg) or {'status':'submitted'})
  self.artifact=Artifacts(self.root).create('会议纪要','docx','实际内容','task-one')
 def prepare(self):return self.mail.prepare('recipient@example.com','会议纪要','请查收',[self.artifact['artifact_id']],'task-one')
 def test_secrets_not_public_and_ticket_required_once_for_exact_draft(self):
  self.assertNotIn('fixture-only-secret',str(self.settings.public()))
  draft=self.prepare()
  with self.assertRaises(ValueError):self.mail.send(draft['draft_id'],'fake','task-one')
  token=self.mail.grant(draft['draft_id'],'task-one','session-one')
  result=self.mail.send(draft['draft_id'],token,'task-one');self.assertEqual(result['status'],'submitted');self.assertEqual(len(self.sent),1)
  with self.assertRaises(ValueError):self.mail.send(draft['draft_id'],token,'task-one')
  self.assertEqual(len(self.sent),1)
 def test_expiry_different_task_and_changed_settings_invalidate_ticket(self):
  draft=self.prepare()
  with self.assertRaises(ValueError):self.mail.grant(draft['draft_id'],'different-task','session')
  token=self.mail.grant(draft['draft_id'],'task-one','session')
  self.settings.save({'from_address':'another@example.com'})
  with self.assertRaises(ValueError):self.mail.send(draft['draft_id'],token,'task-one')
  token=self.mail.grant(draft['draft_id'],'task-one','session')
  with patch('voicehub.workflows.mail.time.time',return_value=10**12),self.assertRaises(ValueError):self.mail.send(draft['draft_id'],token,'task-one')
  self.assertFalse(self.sent)
 def test_changed_attachment_and_cross_task_attachment_rejected(self):
  with self.assertRaises(ValueError):self.mail.prepare('a@example.com','subject','body',[self.artifact['artifact_id']],'other-task')
  draft=self.prepare();token=self.mail.grant(draft['draft_id'],'task-one','session')
  store=Artifacts(self.root);(store.root/(self.artifact['artifact_id']+'.docx')).write_bytes(b'changed')
  with self.assertRaises(ValueError):self.mail.send(draft['draft_id'],token,'task-one')
  self.assertFalse(self.sent)
 def test_unknown_submission_is_not_retried(self):
  draft=self.prepare();token=self.mail.grant(draft['draft_id'],'task-one','session')
  self.mail.transport=lambda *args:{'status':'unknown','code':'mail_delivery_unknown'}
  self.assertEqual(self.mail.send(draft['draft_id'],token,'task-one')['status'],'unknown')
  with self.assertRaises(ValueError):self.mail.grant(draft['draft_id'],'task-one','session')
 def test_header_injection_plaintext_security_and_invalid_recipient_refused(self):
  for address in ('not-an-email','a@example.com\r\nBcc:b@example.com'):
   with self.assertRaises(ValueError):self.mail.prepare(address,'subject','body',[],'task-one')
  with self.assertRaises(ValueError):self.mail.prepare('a@example.com','subject\r\nx:y','body',[],'task-one')
  with self.assertRaises(ValueError):self.settings.save({'security':'none'})

 def test_failed_submission_needs_a_new_ticket(self):
  draft=self.prepare();token=self.mail.grant(draft['draft_id'],'task-one','session')
  self.mail.transport=lambda *args:{'status':'failed','code':'mail_auth_failed'}
  self.assertEqual(self.mail.send(draft['draft_id'],token,'task-one')['status'],'failed')
  with self.assertRaisesRegex(ValueError,'mail_approval_required'):self.mail.send(draft['draft_id'],token,'task-one')
